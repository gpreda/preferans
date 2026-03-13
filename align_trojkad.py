#!/usr/bin/env python3
"""Iterative TrojkaD ↔ Sim50T move alignment.

For each iteration (20 total):
  1. Play 10 games with identical setup — one with TrojkaD, one with Sim50T
  2. Find the first move difference in each game
  3. Score = sum of move indices where first difference occurs (higher = better)
  4. Send differences + hands to Claude CLI for analysis
  5. Claude updates TrojkaD logic in PrefTestSingleGame.py
  6. Log improvements to move_alignment.txt

Uses `claude` CLI (Claude Code) for analysis and code updates.
"""
import functools
import json
import sys

# Unbuffered print for real-time output
print = functools.partial(print, flush=True)
import os
import random
import time
import subprocess
import copy

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'server'))

TROJKAD_FILE = os.path.join(os.path.dirname(__file__), 'PrefTestSingleGame.py')
ALIGNMENT_LOG = os.path.join(os.path.dirname(__file__), 'move_alignment.txt')
PATTERNS_FILE = os.path.join(os.path.dirname(__file__), 'patterns.md')
NUM_ITERATIONS = 50
GAMES_PER_ITER = 10
BASE_SEED = 42


def _reload_modules():
    """Force-reload game modules so TrojkaD code changes take effect."""
    mods_to_reload = [m for m in sys.modules if m in (
        'PrefTestSingleGame', 'game_engine_service', 'models', 'engine',
    )]
    for m in mods_to_reload:
        del sys.modules[m]

    from PrefTestSingleGame import (
        PlayerAlice, Sim3000, Trojka, TrojkaD, CardPlayContext,
        card_str, hand_str,
    )
    from game_engine_service import GameSession
    from models import (
        Card, Round, Contract, RoundPhase, ContractType, Suit,
        SUIT_NAMES, NAME_TO_SUIT, Game, GameStatus,
    )
    return {
        'PlayerAlice': PlayerAlice, 'Sim3000': Sim3000, 'Trojka': Trojka,
        'TrojkaD': TrojkaD, 'CardPlayContext': CardPlayContext,
        'card_str': card_str, 'hand_str': hand_str,
        'GameSession': GameSession, 'Card': Card, 'Round': Round,
        'Contract': Contract, 'RoundPhase': RoundPhase,
        'ContractType': ContractType, 'Suit': Suit,
        'SUIT_NAMES': SUIT_NAMES, 'NAME_TO_SUIT': NAME_TO_SUIT,
    }


def _make_sim50t(mods, name):
    return mods['Sim3000'](name, num_simulations=50, helper_cls=mods['Trojka'])


def _build_trick_order(game, trick, active_ids):
    ccw = [1, 3, 2, 1, 3, 2]
    lead_pos = game.get_player(trick.lead_player_id).position
    start = ccw.index(lead_pos)
    trick_order = []
    for p_pos in ccw[start:start + 3]:
        pid = next((pl.id for pl in game.players if pl.position == p_pos), None)
        if pid and pid in active_ids:
            trick_order.append(pid)
    return trick_order


def play_game(mods, seed, target_strategy, trojkad_pos):
    """Play a full game.

    target_strategy: 'TrojkaD' or 'Sim50T' — what to use at trojkad_pos.
    trojkad_pos: position (1, 2, or 3) where the target strategy plays.
    Other positions use Alice.

    Returns dict with card_plays, or None if redeal/no-play.
    """
    random.seed(seed)

    session = mods['GameSession'](["P1", "P2", "P3"])
    engine = session.engine
    game = engine.game
    rnd = game.current_round

    pos_to_player = {p.position: p for p in game.players}

    # Create strategies: target at trojkad_pos, Alice elsewhere
    strategies = {}
    for p in game.players:
        if p.position == trojkad_pos:
            if target_strategy == 'TrojkaD':
                strategies[p.id] = mods['TrojkaD'](f"TrojkaD-P{p.position}", seed=seed + 100)
            else:
                strategies[p.id] = _make_sim50t(mods, f"Sim50T-P{p.position}")
        else:
            strategies[p.id] = mods['PlayerAlice'](f"Alice-P{p.position}")

    # Record initial hands
    initial_hands = {}
    for p in game.players:
        initial_hands[p.position] = [c.id for c in p.hand]

    # --- AUCTION ---
    bids = []
    max_steps = 30
    while rnd.phase == mods['RoundPhase'].AUCTION and max_steps > 0:
        max_steps -= 1
        auction = rnd.auction
        if auction.phase.value == 'complete':
            break
        bidder_id = auction.current_bidder_id
        if bidder_id is None:
            break
        legal_bids = engine.get_legal_bids(bidder_id)
        if not legal_bids:
            break

        player = game.get_player(bidder_id)
        strat = strategies[bidder_id]
        strat._hand = player.hand
        chosen = strat.choose_bid(legal_bids)

        bids.append({
            'position': player.position,
            'bid_type': chosen['bid_type'],
            'value': chosen.get('value', 0),
        })
        engine.place_bid(bidder_id, chosen['bid_type'], chosen.get('value', 0))

    rnd = game.current_round
    if rnd.phase == mods['RoundPhase'].REDEAL:
        return None

    # --- EXCHANGING ---
    discard_ids = None
    contract_info = None

    if rnd.phase == mods['RoundPhase'].EXCHANGING:
        declarer_id = rnd.declarer_id
        declarer = game.get_player(declarer_id)
        winner_bid = rnd.auction.get_winner_bid()

        strat_d = strategies[declarer_id]
        strat_d._winner_bid = winner_bid
        hand_ids = [c.id for c in declarer.hand]
        talon_ids = [c.id for c in rnd.talon]
        discard_ids = strat_d.choose_discard(hand_ids, talon_ids)

        engine.complete_exchange(declarer_id, discard_ids)

        legal_levels = engine.get_legal_contract_levels(declarer_id)
        ctype, trump, level = strat_d.choose_contract(
            legal_levels, declarer.hand, winner_bid)
        engine.announce_contract(declarer_id, ctype, trump_suit=trump, level=level)

        contract_info = {'type': ctype, 'trump': trump, 'level': level}

    elif rnd.phase == mods['RoundPhase'].PLAYING and rnd.contract is None:
        declarer_id = rnd.declarer_id
        declarer = game.get_player(declarer_id)
        winner_bid = rnd.auction.get_winner_bid()

        strat_d = strategies[declarer_id]
        legal_levels = engine.get_legal_contract_levels(declarer_id)
        ctype, trump, level = strat_d.choose_contract(
            legal_levels, declarer.hand, winner_bid)
        engine.announce_contract(declarer_id, ctype, trump_suit=trump, level=level)

        contract_info = {'type': ctype, 'trump': trump, 'level': level}

    rnd = game.current_round
    declarer_id = rnd.declarer_id
    declarer_pos = game.get_player(declarer_id).position

    # --- WHISTING ---
    whist_actions = []
    while rnd.phase == mods['RoundPhase'].WHISTING:
        defender_id = rnd.whist_current_id
        if defender_id is None:
            break
        actions = engine.get_legal_whist_actions(defender_id)
        if not actions:
            break

        player = game.get_player(defender_id)
        strat = strategies[defender_id]
        strat._hand = player.hand
        strat._contract_type = rnd.contract.type.value if rnd.contract else None
        strat._trump_suit = rnd.contract.trump_suit if rnd.contract else None
        action = strat.choose_whist_action(actions)

        whist_actions.append({
            'position': player.position,
            'action': action,
            'is_counter_phase': rnd.whist_declaring_done,
        })

        if rnd.whist_declaring_done:
            engine.declare_counter_action(defender_id, action)
        else:
            engine.declare_whist(defender_id, action)

    rnd = game.current_round
    if rnd.phase != mods['RoundPhase'].PLAYING:
        return None

    # Record hands before play
    hands_before_play = {}
    for p in game.players:
        hands_before_play[p.position] = [c.id for c in p.hand]

    followers = [game.get_player(fid).position for fid in rnd.whist_followers]

    # --- PLAYING ---
    contract = rnd.contract
    ContractType = mods['ContractType']
    ctx_trump = contract.trump_suit if contract.type == ContractType.SUIT else None
    ctx_contract_type = contract.type.value
    ctx_is_in_hand = contract.is_in_hand
    ctx_talon_cards = [] if ctx_is_in_hand else list(rnd.original_talon)
    active_ids = [p.id for p in sorted(game.players, key=lambda p: p.position)
                  if not p.has_dropped_out]
    all_game_card_ids = set()
    for p in game.players:
        if not p.has_dropped_out:
            for c in p.hand:
                all_game_card_ids.add(c.id)

    played_cards_history = []
    completed_tricks = []
    tricks_completed = 0
    card_plays = []
    move_index = 0

    while rnd.phase == mods['RoundPhase'].PLAYING:
        trick = rnd.current_trick
        if trick is None:
            break

        next_id = engine._get_next_player_in_trick(trick)
        player = game.get_player(next_id)
        legal_cards = engine.get_legal_cards(next_id)
        if not legal_cards:
            break

        trick_order = _build_trick_order(game, trick, active_ids)

        Card = mods['Card']
        played_ids = set(c.id for c in played_cards_history)
        trick_ids = set(c.id for _, c in trick.cards)
        my_ids = set(c.id for c in player.hand)
        remaining = [Card.from_id(cid) for cid in all_game_card_ids
                     if cid not in played_ids and cid not in trick_ids and cid not in my_ids]
        ctx = mods['CardPlayContext'](
            trick_cards=list(trick.cards),
            declarer_id=declarer_id,
            my_id=next_id,
            active_players=trick_order,
            played_cards=list(played_cards_history),
            trump_suit=ctx_trump,
            contract_type=ctx_contract_type,
            is_declarer=(next_id == declarer_id),
            tricks_played=tricks_completed,
            my_hand=list(player.hand),
            talon_cards=ctx_talon_cards,
            is_in_hand=ctx_is_in_hand,
            remaining_cards=remaining,
            played_tricks=list(completed_tricks),
        )

        strat = strategies[next_id]
        strat._rnd = rnd
        strat._player_id = next_id
        strat._ctx = ctx
        strat._hand = list(player.hand)
        strat._contract_type = ctx_contract_type
        strat._trump_suit = ctx_trump
        strat._is_declarer = (next_id == declarer_id)
        if not hasattr(strat, '_cards_played'):
            strat._cards_played = 0
        strat._total_hand_size = len(player.hand) + strat._cards_played
        card_id = strat.choose_card(legal_cards)

        rule_name = getattr(strat, '_last_rule', None)
        ranked = getattr(strat, '_ranked_cards', [])

        card_plays.append({
            'position': player.position,
            'card_id': card_id,
            'rule': rule_name,
            'trick_num': trick.number,
            'move_index': move_index,
            'trick_cards_before': [(pid, c.id) for pid, c in trick.cards],
            'hand_before': [c.id for c in player.hand],
            'legal_cards': [c.id for c in legal_cards],
            'ranked_cards': list(ranked),
        })

        result = engine.play_card(next_id, card_id)

        if result.get("trick_complete"):
            completed_tricks.append(list(trick.cards))
            for _pid, _card in trick.cards:
                played_cards_history.append(_card)
            tricks_completed += 1

        move_index += 1

    return {
        'seed': seed,
        'trojkad_pos': trojkad_pos,
        'initial_hands': initial_hands,
        'bids': bids,
        'discard_ids': discard_ids,
        'contract_info': contract_info,
        'declarer_pos': declarer_pos,
        'whist_actions': whist_actions,
        'hands_before_play': hands_before_play,
        'followers': followers,
        'card_plays': card_plays,
        'ctx_trump': str(ctx_trump) if ctx_trump else None,
        'ctx_contract_type': ctx_contract_type,
    }


def replay_and_find_diff(mods, trojkad_result):
    """Replay the game with Sim50T replacing TrojkaD, stop at first difference.

    Forces identical auction/exchange/whisting from trojkad_result.
    Stops as soon as the target position's move differs.

    Returns (move_index, trojkad_play, sim50t_play) or None if identical.
    """
    seed = trojkad_result['seed']
    trojkad_pos = trojkad_result['trojkad_pos']
    bids = trojkad_result['bids']
    discard_ids = trojkad_result['discard_ids']
    contract_info = trojkad_result['contract_info']
    whist_actions = trojkad_result['whist_actions']
    trojkad_plays = trojkad_result['card_plays']

    random.seed(seed)

    session = mods['GameSession'](["P1", "P2", "P3"])
    engine = session.engine
    game = engine.game
    rnd = game.current_round

    pos_to_player = {p.position: p for p in game.players}

    # Sim50T at trojkad_pos, Alice elsewhere (same as first game)
    strategies = {}
    for p in game.players:
        if p.position == trojkad_pos:
            strategies[p.id] = _make_sim50t(mods, f"Sim50T-P{p.position}")
        else:
            strategies[p.id] = mods['PlayerAlice'](f"Alice-P{p.position}")

    # --- REPLAY AUCTION ---
    for bid_rec in bids:
        pos = bid_rec['position']
        player = pos_to_player[pos]
        auction = rnd.auction
        if auction.phase.value == 'complete':
            break
        engine.place_bid(player.id, bid_rec['bid_type'], bid_rec['value'])

    rnd = game.current_round

    # --- REPLAY EXCHANGE ---
    if rnd.phase == mods['RoundPhase'].EXCHANGING and discard_ids:
        declarer_id = rnd.declarer_id
        engine.complete_exchange(declarer_id, discard_ids)

        ci = contract_info
        engine.announce_contract(declarer_id, ci['type'],
                                 trump_suit=ci['trump'], level=ci['level'])
    elif rnd.phase == mods['RoundPhase'].PLAYING and rnd.contract is None:
        declarer_id = rnd.declarer_id
        ci = contract_info
        engine.announce_contract(declarer_id, ci['type'],
                                 trump_suit=ci['trump'], level=ci['level'])

    rnd = game.current_round
    declarer_id = rnd.declarer_id

    # --- REPLAY WHISTING ---
    for wa in whist_actions:
        if rnd.phase != mods['RoundPhase'].WHISTING:
            break
        defender_id = rnd.whist_current_id
        if defender_id is None:
            break
        if wa['is_counter_phase']:
            engine.declare_counter_action(defender_id, wa['action'])
        else:
            engine.declare_whist(defender_id, wa['action'])

    rnd = game.current_round
    if rnd.phase != mods['RoundPhase'].PLAYING:
        return None

    # --- PLAYING — stop at first difference at trojkad_pos ---
    contract = rnd.contract
    ContractType = mods['ContractType']
    ctx_trump = contract.trump_suit if contract.type == ContractType.SUIT else None
    ctx_contract_type = contract.type.value
    ctx_is_in_hand = contract.is_in_hand
    ctx_talon_cards = [] if ctx_is_in_hand else list(rnd.original_talon)
    active_ids = [p.id for p in sorted(game.players, key=lambda p: p.position)
                  if not p.has_dropped_out]
    all_game_card_ids = set()
    for p in game.players:
        if not p.has_dropped_out:
            for c in p.hand:
                all_game_card_ids.add(c.id)

    played_cards_history = []
    completed_tricks = []
    tricks_completed = 0
    move_index = 0  # sequential index across all players (matches trojkad_plays)

    while rnd.phase == mods['RoundPhase'].PLAYING:
        trick = rnd.current_trick
        if trick is None:
            break

        next_id = engine._get_next_player_in_trick(trick)
        player = game.get_player(next_id)
        legal_cards = engine.get_legal_cards(next_id)
        if not legal_cards:
            break

        if move_index >= len(trojkad_plays):
            break

        trick_order = _build_trick_order(game, trick, active_ids)

        Card = mods['Card']
        played_ids = set(c.id for c in played_cards_history)
        trick_ids = set(c.id for _, c in trick.cards)
        my_ids = set(c.id for c in player.hand)
        remaining = [Card.from_id(cid) for cid in all_game_card_ids
                     if cid not in played_ids and cid not in trick_ids and cid not in my_ids]
        ctx = mods['CardPlayContext'](
            trick_cards=list(trick.cards),
            declarer_id=declarer_id,
            my_id=next_id,
            active_players=trick_order,
            played_cards=list(played_cards_history),
            trump_suit=ctx_trump,
            contract_type=ctx_contract_type,
            is_declarer=(next_id == declarer_id),
            tricks_played=tricks_completed,
            my_hand=list(player.hand),
            talon_cards=ctx_talon_cards,
            is_in_hand=ctx_is_in_hand,
            remaining_cards=remaining,
            played_tricks=list(completed_tricks),
        )

        strat = strategies[next_id]
        strat._rnd = rnd
        strat._player_id = next_id
        strat._ctx = ctx
        strat._hand = list(player.hand)
        strat._contract_type = ctx_contract_type
        strat._trump_suit = ctx_trump
        strat._is_declarer = (next_id == declarer_id)
        if not hasattr(strat, '_cards_played'):
            strat._cards_played = 0
        strat._total_hand_size = len(player.hand) + strat._cards_played
        card_id = strat.choose_card(legal_cards)

        orig = trojkad_plays[move_index]

        if player.position == trojkad_pos:
            # Target position — check for difference
            if orig['card_id'] != card_id:
                sim50t_play = {
                    'position': player.position,
                    'card_id': card_id,
                    'rule': None,
                    'trick_num': trick.number,
                    'move_index': move_index,
                    'trick_cards_before': [(pid, c.id) for pid, c in trick.cards],
                    'hand_before': [c.id for c in player.hand],
                    'legal_cards': [c.id for c in legal_cards],
                }
                return (orig['move_index'], orig, sim50t_play)
        else:
            # Alice position — verify same move, stop if diverged
            if orig['card_id'] != card_id:
                return None

        result = engine.play_card(next_id, card_id)

        if result.get("trick_complete"):
            completed_tricks.append(list(trick.cards))
            for _pid, _card in trick.cards:
                played_cards_history.append(_card)
            tricks_completed += 1

        move_index += 1

    return None  # no difference found


def format_card_id(mods, card_id):
    c = mods['Card'].from_id(card_id)
    return mods['card_str'](c)


def format_hand(mods, card_ids):
    cards = [mods['Card'].from_id(cid) for cid in card_ids]
    return mods['hand_str'](cards)


def format_contract(mods, info):
    if not info:
        return "unknown"
    ctype = info['type']
    trump = info['trump']
    level = info['level']
    SUIT_NAMES = mods['SUIT_NAMES']
    if trump:
        trump_name = SUIT_NAMES[trump] if trump in SUIT_NAMES else str(trump)
        return f"{ctype} ({trump_name}) level {level}"
    return f"{ctype} level {level}"


def run_iteration(iteration, seeds, mods):
    """Run one iteration: play GAMES_PER_ITER games, find differences.

    Returns (score, differences_list, games_played).
    """
    differences = []
    score = 0
    games_played = 0
    iter_rng = random.Random(iteration * 1000)

    for seed in seeds:
        if games_played >= GAMES_PER_ITER:
            break

        # Random position for TrojkaD (1, 2, or 3)
        trojkad_pos = iter_rng.randint(1, 3)

        # Game 1: TrojkaD + Alice + Alice
        trojkad_result = play_game(mods, seed, 'TrojkaD', trojkad_pos)
        if trojkad_result is None:
            continue

        # Skip games where target player didn't participate (dropped out)
        target_moves = sum(1 for p in trojkad_result['card_plays']
                           if p['position'] == trojkad_pos)
        if target_moves == 0:
            continue

        # Game 2: Sim50T + Alice + Alice (same position, stops at first diff)
        diff = replay_and_find_diff(mods, trojkad_result)

        games_played += 1

        if diff is None:
            # No difference found — full alignment
            target_moves = sum(1 for p in trojkad_result['card_plays']
                               if p['position'] == trojkad_pos)
            score += target_moves
        else:
            move_idx, tp, sp = diff
            score += move_idx  # higher = later divergence = better

            # Collect diff context
            weight_to_cat = {999: 'must', 500: 'BEST', 100: 'safe', 20: 'risky', 1: 'dumb'}
            ranked = tp.get('ranked_cards', [])
            card_cats = {}
            for cid, w in ranked:
                card_cats[cid] = weight_to_cat.get(w, f'w={w}')

            differences.append({
                'seed': seed,
                'trojkad_pos': trojkad_pos,
                'move_index': move_idx,
                'trick_num': tp['trick_num'],
                'is_declarer': (trojkad_result['declarer_pos'] == trojkad_pos),
                'contract': format_contract(mods, trojkad_result['contract_info']),
                'trump': trojkad_result.get('ctx_trump'),
                'trojkad_card': format_card_id(mods, tp['card_id']),
                'sim50t_card': format_card_id(mods, sp['card_id']),
                'trojkad_rule': tp['rule'],
                'trojkad_cat': card_cats.get(tp['card_id'], '?'),
                'sim50t_cat': card_cats.get(sp['card_id'], '?'),
                'hand': format_hand(mods, tp['hand_before']),
                'legal_cards': format_hand(mods, tp['legal_cards']),
                'trick_cards_before': [
                    (pid, format_card_id(mods, cid))
                    for pid, cid in tp['trick_cards_before']
                ],
                'all_classifications': [
                    (format_card_id(mods, cid), cat)
                    for cid, cat in sorted(ranked, key=lambda x: -x[1])
                ],
            })

    return score, differences, games_played


def call_claude(prompt, max_retries=3):
    """Call claude CLI with a prompt, return response text. Retries on timeout."""
    env = os.environ.copy()
    env.pop('CLAUDECODE', None)
    for attempt in range(1, max_retries + 1):
        try:
            result = subprocess.run(
                ['claude', '-p'],
                input=prompt,
                capture_output=True, text=True, timeout=600,
                env=env,
            )
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            print(f"  Claude timeout (attempt {attempt}/{max_retries})")
            if attempt == max_retries:
                raise
            print(f"  Retrying...")
    return ""


def load_patterns():
    """Load accumulated patterns from patterns.md, or empty string if none."""
    if os.path.exists(PATTERNS_FILE):
        with open(PATTERNS_FILE) as f:
            return f.read()
    return ""


def save_pattern(iteration, score, pattern, fix, summary):
    """Append a new pattern entry to patterns.md."""
    with open(PATTERNS_FILE, 'a') as f:
        f.write(f"## Iteration {iteration} (score: {score})\n\n")
        f.write(f"**Pattern:** {pattern}\n\n")
        f.write(f"**Fix:** {fix}\n\n")
        f.write(f"**Summary:** {summary}\n\n")
        f.write("---\n\n")


def build_analysis_prompt(iteration, score, differences, trojkad_source_snippet):
    """Build the prompt for Claude to analyze differences and suggest fixes."""
    diff_text = ""
    for i, d in enumerate(differences):
        trick_cards = ", ".join(f"P{pid}:{c}" for pid, c in d['trick_cards_before'])
        classif = ", ".join(f"{c}={cat}" for c, cat in d['all_classifications'])
        diff_text += f"""
Diff {i+1} (seed={d['seed']}, move #{d['move_index']}, trick {d['trick_num']}):
  Role: {'DECLARER' if d['is_declarer'] else 'DEFENDER'}
  Contract: {d['contract']}
  TrojkaD rule: {d['trojkad_rule']}
  TrojkaD played: {d['trojkad_card']} (category: {d['trojkad_cat']})
  Sim50T played:  {d['sim50t_card']} (category: {d['sim50t_cat']})
  Hand: {d['hand']}
  Legal cards: {d['legal_cards']}
  Trick cards before: [{trick_cards}]
  Classifications: {classif}
"""

    patterns_context = load_patterns()
    patterns_section = ""
    if patterns_context:
        patterns_section = f"""
PREVIOUSLY IDENTIFIED PATTERNS AND FIXES:
The following patterns were identified and fixed in earlier iterations. Your suggested
fix MUST NOT regress on any of these. If the current differences overlap with a previous
pattern, note that and suggest a refinement rather than a reversal.

{patterns_context}
"""

    prompt = f"""You are analyzing a card game AI (Preferans / Preference).

ITERATION {iteration} — Alignment score: {score} (higher = better alignment)
{patterns_section}
TrojkaD is a deterministic heuristic player. Sim50T is a Monte Carlo simulation
player (50 simulations using Trojka as helper). We want TrojkaD to make the same
moves as Sim50T. Below are the first differences found in {len(differences)} games.

DIFFERENCES:
{diff_text}

CURRENT TrojkaD source (relevant classification rules):
```python
{trojkad_source_snippet}
```

TASK:
1. Analyze WHY TrojkaD chose differently from Sim50T in each case
2. Check if any difference relates to a previously fixed pattern (regression)
3. Identify the most common NEW pattern causing misalignment
4. Suggest a SPECIFIC, MINIMAL code change to TrojkaD's heuristic rules that would
   align it with Sim50T's choices for the most impactful pattern
5. Ensure your fix does NOT undo or conflict with any previously applied fix
6. Provide your response in this exact format:

ANALYSIS:
<your analysis of the patterns>

REGRESSIONS:
<any regressions on previous patterns, or "None">

PATTERN:
<the most common misalignment pattern>

FIX:
<description of the specific code change>

SUMMARY:
<one-line summary of the improvement>

Do NOT output code. Only analyze and describe the fix in plain English.
Focus on the single most impactful change that would fix the most differences.
"""
    return prompt


def build_fix_prompt(analysis, trojkad_file):
    """Build prompt for Claude to actually apply the fix."""
    patterns_context = load_patterns()
    patterns_warning = ""
    if patterns_context:
        patterns_warning = f"""
CRITICAL — PREVIOUSLY APPLIED PATTERNS (do NOT regress on these):
{patterns_context}

Your fix must be ADDITIVE — it must not undo, weaken, or conflict with any of
the fixes listed above. If you see a conflict, find an alternative approach that
satisfies both the new fix and all previous fixes.
"""

    prompt = f"""Based on this analysis of TrojkaD vs Sim50T alignment in Preferans:

{analysis}
{patterns_warning}
Apply the suggested fix to the TrojkaD class (and its parent Trojka's classification
rules if needed) in the file: {trojkad_file}

Rules:
- Make the MINIMAL change needed
- Only modify TrojkaD or Trojka classification methods
- Do NOT change Sim3000, PlayerAlice, or other classes
- Do NOT add new imports
- Do NOT change the choose_card method structure of TrojkaD
- Keep changes focused on the single pattern identified
- Do NOT undo any previously applied fixes

Use the Edit tool to make the change.
"""
    return prompt


def main():
    print("=" * 80)
    print("ALIGN TROJKAD: Iterative move alignment with Sim50T")
    print(f"  {NUM_ITERATIONS} iterations x {GAMES_PER_ITER} games per iteration")
    print("=" * 80)

    # Reset patterns file for fresh run
    with open(PATTERNS_FILE, 'w') as f:
        f.write("# TrojkaD Alignment Patterns\n\n")
        f.write("Accumulated patterns and fixes from iterative alignment with Sim50T.\n")
        f.write("Each entry documents a discovered misalignment and the fix applied.\n")
        f.write("CRITICAL: New fixes must NOT regress on any of these patterns.\n\n")
    print(f"  Patterns file initialized: {PATTERNS_FILE}")

    # Generate stable seeds for all iterations (same games each time for fair comparison)
    rng = random.Random(BASE_SEED)
    all_seeds = []
    for _ in range(NUM_ITERATIONS):
        # Generate more seeds than needed to account for redeals
        iter_seeds = [rng.randint(1, 1000000) for _ in range(GAMES_PER_ITER * 5)]
        all_seeds.append(iter_seeds)

    scores = []
    t_total_start = time.time()

    for iteration in range(1, NUM_ITERATIONS + 1):
        print(f"\n{'='*60}")
        print(f"ITERATION {iteration}/{NUM_ITERATIONS}")
        print(f"{'='*60}")

        # Reload modules to pick up any code changes
        t_iter_start = time.time()
        mods = _reload_modules()

        # Run games
        score, differences, games_played = run_iteration(
            iteration, all_seeds[iteration - 1], mods)
        scores.append(score)

        iter_time = time.time() - t_iter_start
        print(f"  Games played: {games_played}")
        print(f"  Differences found: {len(differences)}")
        print(f"  Alignment score: {score} (higher = better)")
        print(f"  Time: {iter_time:.1f}s")

        if not differences:
            print("  Perfect alignment! No differences found.")
            # Log to file
            with open(ALIGNMENT_LOG, 'a') as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"Iteration {iteration}: PERFECT ALIGNMENT\n")
                f.write(f"Score: {score}, Games: {games_played}\n")
                f.write(f"{'='*60}\n")
            continue

        # Read current TrojkaD source for context
        with open(TROJKAD_FILE) as f:
            source = f.read()

        # Extract Trojka + TrojkaD class source
        trojka_start = source.find('class Trojka(PlayerAlice):')
        trojkad_end = source.find("\n# ---------------------------------------------------------------------------\n# Main", trojka_start)
        if trojkad_end == -1:
            trojkad_end = source.find("\n# ---------------------", trojka_start + 100)
        if trojka_start >= 0 and trojkad_end >= 0:
            trojkad_snippet = source[trojka_start:trojkad_end]
        else:
            trojkad_snippet = "Could not extract source"

        # Truncate snippet if too long (keep first 6000 chars)
        if len(trojkad_snippet) > 6000:
            trojkad_snippet = trojkad_snippet[:6000] + "\n... (truncated)"

        # Step 1: Ask Claude for analysis
        print("\n  Asking Claude for analysis...")
        analysis_prompt = build_analysis_prompt(
            iteration, score, differences, trojkad_snippet)

        analysis = call_claude(analysis_prompt)
        print(f"  Analysis received ({len(analysis)} chars)")

        if not analysis:
            print("  WARNING: Empty analysis from Claude, skipping fix")
            continue

        # Extract pattern, fix, and summary from analysis for patterns.md
        def _extract_section(text, header):
            import re
            pattern = rf'{header}:\s*\n(.*?)(?=\n(?:ANALYSIS|REGRESSIONS|PATTERN|FIX|SUMMARY):|\Z)'
            m = re.search(pattern, text, re.DOTALL)
            return m.group(1).strip() if m else ""

        extracted_pattern = _extract_section(analysis, 'PATTERN')
        extracted_fix = _extract_section(analysis, 'FIX')
        extracted_summary = _extract_section(analysis, 'SUMMARY')

        # Step 2: Ask Claude to apply the fix
        print("  Asking Claude to apply fix...")
        fix_prompt = build_fix_prompt(analysis, TROJKAD_FILE)

        env = os.environ.copy()
        env.pop('CLAUDECODE', None)
        fix_result = subprocess.run(
            ['claude', '-p', '--allowedTools', 'Read,Edit'],
            input=fix_prompt,
            capture_output=True, text=True, timeout=600,
            cwd=os.path.dirname(__file__),
            env=env,
        )
        fix_output = fix_result.stdout.strip()
        print(f"  Fix applied ({len(fix_output)} chars response)")

        # Step 3: Save pattern to patterns.md for future iterations
        if extracted_pattern:
            save_pattern(iteration, score, extracted_pattern, extracted_fix, extracted_summary)
            print(f"  Pattern saved to {PATTERNS_FILE}")

        # Step 4: Log to alignment file
        with open(ALIGNMENT_LOG, 'a') as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"Iteration {iteration}\n")
            f.write(f"Score: {score}, Differences: {len(differences)}, Games: {games_played}\n")
            f.write(f"Time: {iter_time:.1f}s\n\n")

            # Brief diff summary
            for d in differences[:5]:
                f.write(f"  [{d['trojkad_rule']}] TrojkaD:{d['trojkad_card']}({d['trojkad_cat']}) "
                        f"vs Sim50T:{d['sim50t_card']}({d['sim50t_cat']}) "
                        f"{'DECL' if d['is_declarer'] else 'DEF'} trick {d['trick_num']}\n")
            if len(differences) > 5:
                f.write(f"  ... and {len(differences) - 5} more\n")

            f.write(f"\nAnalysis:\n{analysis}\n")
            f.write(f"\nFix output:\n{fix_output}\n")
            f.write(f"{'='*60}\n")

        print(f"  Logged to {ALIGNMENT_LOG}")

    # --- FINAL SUMMARY ---
    total_time = time.time() - t_total_start
    print(f"\n{'='*80}")
    print(f"FINAL SUMMARY")
    print(f"{'='*80}")
    print(f"Scores by iteration: {scores}")
    if len(scores) >= 2:
        print(f"Improvement: {scores[0]} → {scores[-1]} ({scores[-1] - scores[0]:+d})")
    print(f"Total time: {total_time:.0f}s")

    # Append final summary to log
    with open(ALIGNMENT_LOG, 'a') as f:
        f.write(f"\n{'='*80}\n")
        f.write(f"FINAL SUMMARY\n")
        f.write(f"Scores: {scores}\n")
        if len(scores) >= 2:
            f.write(f"Improvement: {scores[0]} → {scores[-1]} ({scores[-1] - scores[0]:+d})\n")
        f.write(f"Total time: {total_time:.0f}s\n")
        f.write(f"{'='*80}\n")


if __name__ == '__main__':
    main()
