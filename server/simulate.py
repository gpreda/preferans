"""Game simulation generator for Preferans.

Each simulation is driven by random target bids assigned to players.
Players bid up to their target, then pass. Follow/counter/double-counter
decisions use fixed probabilities.
"""
import random
import os

from models import (
    Game, Player, Alice, Bob, Carol,
    Card, Suit, Rank, RoundPhase, AuctionPhase,
    ContractType, BidType, PlayerType,
    SUIT_NAMES, RANK_NAMES,
)
from engine import GameEngine
from game_engine_service import GameSession


# Unicode suit symbols
SUIT_SYMBOL = {
    Suit.SPADES: "\u2660",
    Suit.DIAMONDS: "\u2666",
    Suit.HEARTS: "\u2665",
    Suit.CLUBS: "\u2663",
}

LEVEL_TO_TRUMP = {2: "spades", 3: "diamonds", 4: "hearts", 5: "clubs"}

# Target bid probability table: (target_type, target_value) → weight
TARGET_PROBS = [
    (("pass",       0), 34),
    (("game",       2), 10), (("game",       3), 10),
    (("game",       4), 10), (("game",       5), 10),
    (("in_hand",    2),  5), (("in_hand",    3),  5),
    (("in_hand",    4),  5), (("in_hand",    5),  5),
    (("betl",       0),  2), (("in_hand_betl", 0), 1),
    (("sans",       0),  2), (("in_hand_sans", 0), 1),
]

# Suit-only variant: no in_hand / betl / sans targets
TARGET_PROBS_SUIT_ONLY = [
    (("pass", 0), 34),
    (("game", 2), 10), (("game", 3), 10),
    (("game", 4), 10), (("game", 5), 10),
]

PROB_FOLLOW         = 0.50
PROB_COUNTER        = 0.10
PROB_DOUBLE_COUNTER = 0.10


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def card_str(card: Card) -> str:
    return f"{RANK_NAMES[card.rank]}{SUIT_SYMBOL[card.suit]}"


def hand_str(cards: list[Card]) -> str:
    return " ".join(card_str(c) for c in cards)


def bid_label(bid_dict: dict) -> str:
    bt = bid_dict["bid_type"]
    v  = bid_dict["value"]
    if bt == "pass":     return "pass"
    if bt == "game":     return f"game_{v}"
    if bt == "in_hand":  return f"in_hand_{v}" if v > 0 else "in_hand"
    return bt  # betl, sans


def target_label(target: tuple) -> str:
    t, v = target
    if t == "game":     return f"game_{v}"
    if t == "in_hand":  return f"in_hand_{v}"
    return t  # pass, betl, sans, in_hand_betl, in_hand_sans


def format_step(step_num: int, game: Game, engine: GameEngine,
                commands: list[str], chosen: str,
                initial_talon: list[Card]) -> str:
    """Render one step as the text format."""
    rnd = game.current_round
    lines = [f"--- step {step_num} ---"]

    # Game header line
    talon_s = hand_str(initial_talon)
    bidder = "-"
    if rnd.phase == RoundPhase.AUCTION and rnd.auction.current_bidder_id:
        bidder = f"P{rnd.auction.current_bidder_id}"
    contract_s = "-"
    if rnd.contract:
        c = rnd.contract
        if c.type == ContractType.SUIT:
            contract_s = f"{c.bid_value}{SUIT_SYMBOL[c.trump_suit]}"
        elif c.type == ContractType.BETL:
            contract_s = "betl"
        else:
            contract_s = "sans"
        if c.is_in_hand:
            contract_s += "/ih"
    discarded_s = hand_str(rnd.discarded) if rnd.discarded else "-"
    trick_s = "-"
    if rnd.current_trick and rnd.current_trick.cards:
        trick_s = " ".join(f"P{pid}:{card_str(c)}" for pid, c in rnd.current_trick.cards)
    lines.append(
        f"game: talon={talon_s} | bidder={bidder} | contract={contract_s}"
        f" | discarded={discarded_s} | trick={trick_s}"
    )

    # Player lines
    for p in sorted(game.players, key=lambda x: x.id):
        on_move = "no"
        if rnd.phase == RoundPhase.AUCTION:
            on_move = "yes" if rnd.auction.current_bidder_id == p.id else "no"
        elif rnd.phase == RoundPhase.PLAYING and not rnd.current_trick and rnd.contract is None:
            on_move = "yes" if rnd.declarer_id == p.id else "no"
        elif rnd.phase == RoundPhase.PLAYING and rnd.current_trick:
            next_id = engine._get_next_player_in_trick(rnd.current_trick)
            on_move = "yes" if next_id == p.id else "no"
        elif rnd.phase == RoundPhase.WHISTING:
            on_move = "yes" if rnd.whist_current_id == p.id else "no"
        elif rnd.phase == RoundPhase.EXCHANGING:
            on_move = "yes" if rnd.declarer_id == p.id else "no"
        lines.append(f"P{p.id}: hand={hand_str(p.hand)} | tricks={p.tricks_won} | on_move={on_move}")

    lines.append(f"commands: {' '.join(commands)}")
    lines.append(f"> {chosen}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Target-based bidding
# ---------------------------------------------------------------------------

def assign_targets(rng, suit_only: bool = False) -> list[tuple]:
    """Draw a target bid for each of the 3 players."""
    pool = TARGET_PROBS_SUIT_ONLY if suit_only else TARGET_PROBS
    bids, weights = zip(*pool)
    return [rng.choices(bids, weights=weights, k=1)[0] for _ in range(3)]


def _find(legal_bids: list, bid_type: str, value: int):
    """Return the first bid dict matching bid_type and value, or None."""
    for b in legal_bids:
        if b["bid_type"] == bid_type and b["value"] == value:
            return b
    return None


def choose_bid(target: tuple, legal_bids: list, auction, player_id: int) -> dict:
    """Return the bid to play based on the player's target."""
    target_type, target_value = target
    pass_bid = _find(legal_bids, "pass", 0)
    phase = auction.phase
    player_has_bid = any(b.player_id == player_id for b in auction.bids)

    if target_type == "pass":
        return pass_bid

    # IN_HAND_DECLARING: in_hand targets declare their level
    if phase == AuctionPhase.IN_HAND_DECLARING:
        if target_type != "in_hand":
            return pass_bid
        valid = [b for b in legal_bids if b["bid_type"] == "in_hand" and b["value"] <= target_value]
        return max(valid, key=lambda x: x["value"]) if valid else pass_bid

    # IN_HAND_DECIDING: in_hand-track targets join or escalate
    if phase == AuctionPhase.IN_HAND_DECIDING:
        if target_type == "in_hand":
            return _find(legal_bids, "in_hand", 0) or pass_bid
        if target_type == "in_hand_betl":
            return (_find(legal_bids, "betl", 6) or
                    _find(legal_bids, "in_hand", 0) or pass_bid)
        if target_type == "in_hand_sans":
            return (_find(legal_bids, "sans", 7) or
                    _find(legal_bids, "betl", 6) or
                    _find(legal_bids, "in_hand", 0) or pass_bid)
        return pass_bid  # game-track targets pass in in_hand phase

    # INITIAL or GAME_BIDDING
    # In_hand-track targets bid their intent as first bid
    if target_type == "in_hand":
        if not player_has_bid:
            return _find(legal_bids, "in_hand", 0) or pass_bid
        return pass_bid

    if target_type == "in_hand_betl":
        if not player_has_bid:
            return (_find(legal_bids, "betl", 6) or
                    _find(legal_bids, "in_hand", 0) or pass_bid)
        return pass_bid

    if target_type == "in_hand_sans":
        if not player_has_bid:
            return (_find(legal_bids, "sans", 7) or
                    _find(legal_bids, "betl", 6) or
                    _find(legal_bids, "in_hand", 0) or pass_bid)
        return pass_bid

    # Game-track targets (game_N, betl, sans): bid up to effective target value
    target_eff = (target_value if target_type == "game"
                  else 6 if target_type == "betl" else 7)
    current_high = auction.highest_game_bid.effective_value if auction.highest_game_bid else 0

    if current_high > target_eff:
        return pass_bid  # already outbid

    # Collect game-track candidates ≤ target_eff.
    # Skip betl/sans when player hasn't bid yet — those become in_hand bids.
    # Skip betl/sans when circle_can_hold is True — hold first, raise to betl/sans later.
    holding = getattr(auction, 'circle_can_hold', False)
    candidates = []
    for b in legal_bids:
        bt = b["bid_type"]
        if bt == "game":
            beff = b["value"]
        elif bt == "betl":
            if not player_has_bid or holding:
                continue
            beff = 6
        elif bt == "sans":
            if not player_has_bid or holding:
                continue
            beff = 7
        else:
            continue
        if beff <= target_eff:
            candidates.append((beff, b))

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    return pass_bid


def choose_contract_level(target: tuple, legal_levels: list) -> int:
    """Choose the contract level to announce based on the declarer's target."""
    t, v = target
    level = (v if t in ("game", "in_hand")
             else 6 if t in ("betl", "in_hand_betl")
             else 7 if t in ("sans", "in_hand_sans")
             else min(legal_levels))
    return level if level in legal_levels else min(legal_levels)


# ---------------------------------------------------------------------------
# Whisting decisions
# ---------------------------------------------------------------------------

def choose_whist_action(rng, actions: list) -> dict:
    """Choose a whist action using fixed probabilities."""
    amap = {a["action"]: a for a in actions}

    # Declarer responding to a counter
    if "double_counter" in amap:
        return amap["double_counter"] if rng.random() < PROB_DOUBLE_COUNTER else amap["start_game"]

    # Counter/call sub-phase (follower)
    if "start_game" in amap and "counter" in amap:
        return amap["counter"] if rng.random() < PROB_COUNTER else amap["start_game"]

    # Declaration phase: always has pass + follow; may have counter
    r = rng.random()
    if r < PROB_FOLLOW:
        return amap["follow"]
    if "counter" in amap and r < PROB_FOLLOW + PROB_COUNTER:
        return amap["counter"]
    return amap["pass"]


# ---------------------------------------------------------------------------
# Round simulation
# ---------------------------------------------------------------------------

def simulate_round(seed: int, suit_only: bool = False) -> list[str]:
    """Simulate one complete round. Returns list of text blocks (params + steps)."""
    rng = random.Random(seed)

    # Use seed for shuffle so deck is deterministic, then restore global rng
    old_state = random.getstate()
    random.setstate(rng.getstate())

    session = GameSession(["Alice", "Bob", "Carol"])
    engine = session.engine
    game = engine.game

    random.setstate(old_state)

    # Assign target bids
    targets = assign_targets(rng, suit_only)
    players_sorted = sorted(game.players, key=lambda p: p.id)
    player_targets = {p.id: t for p, t in zip(players_sorted, targets)}

    # Parameters block
    param_lines = ["--- parameters ---"]
    for p in players_sorted:
        param_lines.append(f"P{p.id}: target={target_label(player_targets[p.id])}")
    blocks = ["\n".join(param_lines)]

    initial_talon = list(game.current_round.talon)
    step_num = 1

    # --- Auction ---
    max_steps = 30
    while game.current_round.phase == RoundPhase.AUCTION and max_steps > 0:
        max_steps -= 1
        auction = game.current_round.auction
        if auction.phase == AuctionPhase.COMPLETE:
            break
        bidder_id = auction.current_bidder_id
        if bidder_id is None:
            break

        legal = engine.get_legal_bids(bidder_id)
        if not legal:
            break

        commands = [f"{i+1}:{bid_label(b)}" for i, b in enumerate(legal)]
        choice = choose_bid(player_targets[bidder_id], legal, auction, bidder_id)
        idx = next(i for i, b in enumerate(legal)
                   if b["bid_type"] == choice["bid_type"] and b["value"] == choice["value"])
        chosen = f"{idx+1}:{bid_label(choice)} (P{bidder_id})"

        blocks.append(format_step(step_num, game, engine, commands, chosen, initial_talon))
        step_num += 1

        engine.place_bid(bidder_id, choice["bid_type"], choice["value"])

    # --- Exchange (regular game, declarer picks up talon) ---
    rnd = game.current_round
    if rnd.phase == RoundPhase.EXCHANGING:
        declarer_id = rnd.declarer_id
        declarer = engine._get_player(declarer_id)

        combined_ids = [c.id for c in declarer.hand] + [c.id for c in rnd.talon]
        discard_ids = rng.sample(combined_ids, 2)
        all_cards_map = {c.id: c for c in declarer.hand}
        for c in rnd.talon:
            all_cards_map[c.id] = c
        discard_labels = [card_str(all_cards_map[cid]) for cid in discard_ids]

        commands = [f"discard:{' '.join(discard_labels)}"]
        chosen  = f"{commands[0]} (P{declarer_id})"

        blocks.append(format_step(step_num, game, engine, commands, chosen, initial_talon))
        step_num += 1

        engine.complete_exchange(declarer_id, discard_ids)

        # Announce contract
        legal_levels = engine.get_legal_contract_levels(declarer_id)
        if suit_only:
            legal_levels = [l for l in legal_levels if l <= 5]
        level = choose_contract_level(player_targets[declarer_id], legal_levels)
        ct = "suit" if level <= 5 else "betl" if level == 6 else "sans"
        ts = LEVEL_TO_TRUMP.get(level)

        commands = [f"contract:{level}"]
        chosen  = f"contract:{level} (P{declarer_id})"

        blocks.append(format_step(step_num, game, engine, commands, chosen, initial_talon))
        step_num += 1

        engine.announce_contract(declarer_id, ct, ts, level=level)

    # --- Undeclared in-hand: announce contract ---
    elif rnd.phase == RoundPhase.PLAYING and rnd.contract is None:
        declarer_id = rnd.declarer_id
        legal_levels = engine.get_legal_contract_levels(declarer_id)
        level = choose_contract_level(player_targets[declarer_id], legal_levels)
        ct = "suit" if level <= 5 else "betl" if level == 6 else "sans"
        ts = LEVEL_TO_TRUMP.get(level)

        commands = [f"contract:{level}"]
        chosen  = f"contract:{level} (P{declarer_id})"

        blocks.append(format_step(step_num, game, engine, commands, chosen, initial_talon))
        step_num += 1

        engine.announce_contract(declarer_id, ct, ts, level=level)

    # --- Whisting ---
    rnd = game.current_round
    while rnd.phase == RoundPhase.WHISTING:
        defender_id = rnd.whist_current_id
        if defender_id is None:
            break

        actions = engine.get_legal_whist_actions(defender_id)
        if not actions:
            break

        commands = [f"{i+1}:{a['label'].lower()}" for i, a in enumerate(actions)]
        choice = choose_whist_action(rng, actions)
        idx = next(i for i, a in enumerate(actions) if a["action"] == choice["action"])
        chosen = f"{idx+1}:{choice['label'].lower()} (P{defender_id})"

        blocks.append(format_step(step_num, game, engine, commands, chosen, initial_talon))
        step_num += 1

        if rnd.whist_declaring_done:
            engine.declare_counter_action(defender_id, choice["action"])
        else:
            engine.declare_whist(defender_id, choice["action"])

    # --- Trick play ---
    max_steps = 50
    while game.current_round.phase == RoundPhase.PLAYING and max_steps > 0:
        max_steps -= 1
        trick = game.current_round.current_trick
        if not trick:
            break

        player_id = engine._get_next_player_in_trick(trick)
        legal_cards = engine.get_legal_cards(player_id)
        if not legal_cards:
            break

        commands = [f"{i+1}:{card_str(c)}" for i, c in enumerate(legal_cards)]
        card = rng.choice(legal_cards)
        idx  = legal_cards.index(card)
        chosen = f"{idx+1}:{card_str(card)} (P{player_id})"

        blocks.append(format_step(step_num, game, engine, commands, chosen, initial_talon))
        step_num += 1

        engine.play_card(player_id, card.id)

    # --- Scoring ---
    rnd = game.current_round
    score_lines = [f"--- step {step_num} ---", "phase: scoring"]
    if rnd.contract:
        c = rnd.contract
        if c.type == ContractType.SUIT:
            score_lines.append(f"contract: {c.bid_value}{SUIT_SYMBOL[c.trump_suit]}"
                               f"{'/ih' if c.is_in_hand else ''}")
        elif c.type == ContractType.BETL:
            score_lines.append(f"contract: betl{'/ih' if c.is_in_hand else ''}")
        else:
            score_lines.append(f"contract: sans{'/ih' if c.is_in_hand else ''}")
    for p in sorted(game.players, key=lambda x: x.id):
        score_lines.append(f"P{p.id}: tricks={p.tricks_won} score={p.score}")
    blocks.append("\n".join(score_lines))

    return blocks


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    sim_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "simulations",
    )
    os.makedirs(sim_dir, exist_ok=True)

    # Suppress engine debug prints during generation
    import builtins
    original_print = builtins.print
    builtins.print = lambda *a, **k: None

    try:
        for i in range(1, 11):
            blocks = simulate_round(seed=i)
            path = os.path.join(sim_dir, f"game_{i:03d}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n\n".join(blocks) + "\n")
            builtins.print = original_print
            print(f"  wrote {path} ({len(blocks) - 1} steps)")  # -1 for params block
            builtins.print = lambda *a, **k: None

        for i in range(11, 21):
            blocks = simulate_round(seed=i, suit_only=True)
            path = os.path.join(sim_dir, f"game_{i:03d}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n\n".join(blocks) + "\n")
            builtins.print = original_print
            print(f"  wrote {path} ({len(blocks) - 1} steps)")
            builtins.print = lambda *a, **k: None
    finally:
        builtins.print = original_print

    print(f"Done. 20 simulations in {sim_dir}")


if __name__ == "__main__":
    main()
