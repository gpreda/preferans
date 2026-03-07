#!/usr/bin/env python3
"""Compute win probabilities for each hand combination via simulation."""

import os, sys, random, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server"))

# Suppress engine debug prints during import
import builtins
_real_print = builtins.print
builtins.print = lambda *a, **k: None

from models import Card, Suit, Rank
from PrefTestSingleGame import (
    BasePlayer, PlayerAlice,
    _sim_get_legal_cards, _sim_determine_winner, CardPlayContext,
)

builtins.print = _real_print

# ── Constants ────────────────────────────────────────────────────────

SUIT_MAP = [Suit.SPADES, Suit.DIAMONDS, Suit.HEARTS, Suit.CLUBS]
RANK_MAP = {'A': Rank.ACE, 'K': Rank.KING, 'D': Rank.QUEEN, 'J': Rank.JACK}
X_RANKS = [Rank.SEVEN, Rank.EIGHT, Rank.NINE, Rank.TEN]

ALL_CARDS = [Card(rank=r, suit=s) for s in Suit for r in Rank]

NUM_SIMS = 20
COMBOS_FILE = 'hand_combinations.txt'
OUTPUT_FILE = 'win_probabilities.txt'

HEADER = '\t'.join([
    'combination',
    'strongest_suit_win_prob_all_follow_P1',
    'strongest_suit_win_prob_all_follow_not_P1',
    'strongest_suit_win_prob_single_follow',
    'strongest_suit_follow_prob',
    'betl_win_prob',
    'sans_win_prob',
])


# ── Card utilities ───────────────────────────────────────────────────

def encoding_to_cards(encoding, rng):
    """Convert encoding to 10 Card objects, randomly assigning x cards."""
    parts = encoding.split('-')
    cards = []
    for i, pat in enumerate(parts):
        suit = SUIT_MAP[i]
        x_count = pat.count('x')
        for ch in pat:
            if ch != 'x' and ch in RANK_MAP:
                cards.append(Card(rank=RANK_MAP[ch], suit=suit))
        if x_count > 0:
            chosen = rng.sample(X_RANKS, x_count)
            for r in chosen:
                cards.append(Card(rank=r, suit=suit))
    return cards


def random_deal(declarer_cards, rng):
    """Distribute remaining 22 cards: 2 talon, 10 to P2, 10 to P3."""
    declarer_ids = {c.id for c in declarer_cards}
    remaining = [c for c in ALL_CARDS if c.id not in declarer_ids]
    rng.shuffle(remaining)
    return remaining[:2], remaining[2:12], remaining[12:22]


def discard_for(hand_12, contract_type, trump_suit=None):
    """Pick 2 cards to discard using score_discard_cards."""
    ids = [c.id for c in hand_12]
    scores = BasePlayer.score_discard_cards(ids, contract_type, trump_suit)
    sorted_ids = sorted(ids, key=lambda x: scores[x], reverse=True)
    discard_ids = set(sorted_ids[:2])
    return [c for c in hand_12 if c.id not in discard_ids]


# ── Game playout ─────────────────────────────────────────────────────

def _rotate(players, lead):
    idx = players.index(lead)
    return players[idx:] + players[:idx]


def playout(hands, trump_suit, contract_type, declarer_id, first_lead,
            player_classes, rng, active_players=None):
    """Play a full 10-trick game. Returns {pid: tricks_won}."""
    active = active_players or [1, 2, 3]
    sim_hands = {p: list(h) for p, h in hands.items()}
    tricks_won = {p: 0 for p in active}
    played_cards = []

    helpers = {}
    for pid in active:
        cls = player_classes.get(pid, PlayerAlice)
        s = rng.randint(0, 10**9)
        h = cls(seed=s)
        h._contract_type = contract_type
        h._trump_suit = trump_suit
        h._is_declarer = (pid == declarer_id)
        helpers[pid] = h

    lead = first_lead
    for trick_num in range(10):
        order = _rotate(active, lead)
        trick_cards = []

        for pid in order:
            hand = sim_hands[pid]
            if not hand:
                continue
            legal = _sim_get_legal_cards(hand, trick_cards, trump_suit)
            if not legal:
                continue

            h = helpers[pid]
            h._hand = hand
            ctx = CardPlayContext.__new__(CardPlayContext)
            ctx.trick_cards = trick_cards
            ctx.declarer_id = declarer_id
            ctx.my_id = pid
            ctx.active_players = order
            ctx.played_cards = played_cards
            ctx.trump_suit = trump_suit
            ctx.contract_type = contract_type
            ctx.is_declarer = (pid == declarer_id)
            ctx.tricks_played = trick_num
            ctx.my_hand = hand
            h._ctx = ctx
            h._rnd = None
            h._player_id = pid

            card_id = h.choose_card(legal)
            card_obj = next(c for c in legal if c.id == card_id)
            sim_hands[pid].remove(card_obj)
            trick_cards.append((pid, card_obj))

        winner = _sim_determine_winner(trick_cards, trump_suit)
        if winner is None:
            break
        tricks_won[winner] += 1

        for _, c in trick_cards:
            played_cards.append(c)

        if contract_type == 'betl' and winner == declarer_id:
            break
        if contract_type != 'betl':
            ftricks = sum(v for k, v in tricks_won.items() if k != declarer_id)
            if ftricks >= 5:
                break

        lead = winner

    return tricks_won


# ── Follow probability ───────────────────────────────────────────────

def would_follow(hand_cards, trump_suit):
    """Check if Alice would follow (whist) given this hand and trump suit."""
    alice = PlayerAlice("eval")
    alice._hand = hand_cards
    legal = [{"action": "follow"}, {"action": "pass"}]
    decision = alice.following_decision(hand_cards, 'suit', trump_suit, legal)
    return decision["action"] == "follow"


# ── Main simulation ─────────────────────────────────────────────────

def simulate_combination(encoding, seed=42):
    rng = random.Random(seed)
    trump_suit = Suit.SPADES  # strongest suit is always first = spades

    all_follow_cls = {1: PlayerAlice, 2: PlayerAlice, 3: PlayerAlice}

    wins_p1 = 0
    wins_not_p1 = 0
    wins_single = 0
    follow_count = 0
    follow_total = 0
    wins_betl = 0
    wins_sans = 0
    wins_in_hand = 0

    for _ in range(NUM_SIMS):
        # Random deal
        decl_10 = encoding_to_cards(encoding, rng)
        talon, p2_hand, p3_hand = random_deal(decl_10, rng)
        decl_12 = decl_10 + talon

        # ── In-hand suit contract (no exchange) ──────────────────
        hands_ih = {1: decl_10, 2: p2_hand, 3: p3_hand}
        tw = playout(hands_ih, trump_suit, 'suit', 1, 1, all_follow_cls, rng)
        if tw[1] >= 6:
            wins_in_hand += 1

        # ── Suit contract (spades) ───────────────────────────────
        hand_suit = discard_for(decl_12, 'suit', 'spades')
        hands = {1: hand_suit, 2: p2_hand, 3: p3_hand}

        # all_follow_P1: declarer leads
        tw = playout(hands, trump_suit, 'suit', 1, 1, all_follow_cls, rng)
        if tw[1] >= 6:
            wins_p1 += 1

        # all_follow_not_P1: random opponent leads
        lead = rng.choice([2, 3])
        tw = playout(hands, trump_suit, 'suit', 1, lead, all_follow_cls, rng)
        if tw[1] >= 6:
            wins_not_p1 += 1

        # single_follow: play 2 games (1v1, passer sits out)
        # Game A: P1 vs P2
        tw_a = playout(hands, trump_suit, 'suit', 1, 1, all_follow_cls, rng,
                        active_players=[1, 2])
        # Game B: P1 vs P3
        tw_b = playout(hands, trump_suit, 'suit', 1, 1, all_follow_cls, rng,
                        active_players=[1, 3])
        if tw_a[1] >= 6 and tw_b[1] >= 6:
            wins_single += 1

        # follow_prob: would each opponent follow?
        if would_follow(p2_hand, trump_suit):
            follow_count += 1
        follow_total += 1
        if would_follow(p3_hand, trump_suit):
            follow_count += 1
        follow_total += 1

        # ── Betl ─────────────────────────────────────────────────
        hand_betl = discard_for(decl_12, 'betl')
        hands_b = {1: hand_betl, 2: p2_hand, 3: p3_hand}
        tw = playout(hands_b, None, 'betl', 1, 1, all_follow_cls, rng)
        if tw[1] == 0:
            wins_betl += 1

        # ── Sans ─────────────────────────────────────────────────
        hand_sans = discard_for(decl_12, 'sans')
        hands_s = {1: hand_sans, 2: p2_hand, 3: p3_hand}
        tw = playout(hands_s, None, 'sans', 1, 1, all_follow_cls, rng)
        if tw[1] >= 6:
            wins_sans += 1

    return {
        'strongest_suit_win_prob_all_follow_P1': wins_p1 / NUM_SIMS,
        'strongest_suit_win_prob_all_follow_not_P1': wins_not_p1 / NUM_SIMS,
        'strongest_suit_win_prob_single_follow': wins_single / NUM_SIMS,
        'strongest_suit_follow_prob': follow_count / follow_total if follow_total else 0,
        'betl_win_prob': wins_betl / NUM_SIMS,
        'sans_win_prob': wins_sans / NUM_SIMS,
        'in_hand_win_prob': wins_in_hand / NUM_SIMS,
    }


def simulate_with_known_cards(hand_ids, discard_ids, num_sims=NUM_SIMS, seed=42):
    """Simulate with known 10-card hand and 2 discarded cards.

    Opponents get random cards from the remaining 20.
    No discard phase — hand is played as-is for each contract type.
    """
    rng = random.Random(seed)

    trump_suit = Suit.SPADES

    hand_cards = [c for c in ALL_CARDS if c.id in set(hand_ids)]
    excluded = set(hand_ids) | set(discard_ids)
    remaining_pool = [c for c in ALL_CARDS if c.id not in excluded]

    all_follow_cls = {1: PlayerAlice, 2: PlayerAlice, 3: PlayerAlice}

    wins_p1 = 0
    wins_not_p1 = 0
    wins_single = 0
    follow_count = 0
    follow_total = 0
    wins_betl = 0
    wins_sans = 0

    for _ in range(num_sims):
        pool = list(remaining_pool)
        rng.shuffle(pool)
        p2_hand = pool[:10]
        p3_hand = pool[10:20]

        # Suit
        hands = {1: hand_cards, 2: p2_hand, 3: p3_hand}
        tw = playout(hands, trump_suit, 'suit', 1, 1, all_follow_cls, rng)
        if tw[1] >= 6:
            wins_p1 += 1

        lead = rng.choice([2, 3])
        tw = playout(hands, trump_suit, 'suit', 1, lead, all_follow_cls, rng)
        if tw[1] >= 6:
            wins_not_p1 += 1

        tw_a = playout(hands, trump_suit, 'suit', 1, 1, all_follow_cls, rng,
                        active_players=[1, 2])
        tw_b = playout(hands, trump_suit, 'suit', 1, 1, all_follow_cls, rng,
                        active_players=[1, 3])
        if tw_a[1] >= 6 and tw_b[1] >= 6:
            wins_single += 1

        if would_follow(p2_hand, trump_suit):
            follow_count += 1
        follow_total += 1
        if would_follow(p3_hand, trump_suit):
            follow_count += 1
        follow_total += 1

        # Betl
        hands_b = {1: hand_cards, 2: p2_hand, 3: p3_hand}
        tw = playout(hands_b, None, 'betl', 1, 1, all_follow_cls, rng)
        if tw[1] == 0:
            wins_betl += 1

        # Sans
        hands_s = {1: hand_cards, 2: p2_hand, 3: p3_hand}
        tw = playout(hands_s, None, 'sans', 1, 1, all_follow_cls, rng)
        if tw[1] >= 6:
            wins_sans += 1

    return {
        'strongest_suit_win_prob_all_follow_P1': wins_p1 / num_sims,
        'strongest_suit_win_prob_all_follow_not_P1': wins_not_p1 / num_sims,
        'strongest_suit_win_prob_single_follow': wins_single / num_sims,
        'strongest_suit_follow_prob': follow_count / follow_total if follow_total else 0,
        'betl_win_prob': wins_betl / num_sims,
        'sans_win_prob': wins_sans / num_sims,
        'in_hand_win_prob': wins_p1 / num_sims,
    }


def load_done(output_file):
    """Load already-processed combinations from output file."""
    done = set()
    if os.path.exists(output_file):
        with open(output_file) as f:
            for line in f:
                if line.startswith('combination'):
                    continue
                combo = line.split('\t')[0].strip()
                if combo:
                    done.add(combo)
    return done


def main():
    with open(COMBOS_FILE) as f:
        combos = [line.strip() for line in f if line.strip()]

    done = load_done(OUTPUT_FILE)
    remaining = [c for c in combos if c not in done]

    _real_print(f"Total combinations: {len(combos)}")
    _real_print(f"Already processed: {len(done)}")
    _real_print(f"Remaining: {len(remaining)}")

    # Write header if new file
    if not os.path.exists(OUTPUT_FILE) or os.path.getsize(OUTPUT_FILE) == 0:
        with open(OUTPUT_FILE, 'w') as f:
            f.write(HEADER + '\n')

    t0 = time.time()
    for i, combo in enumerate(remaining):
        t1 = time.time()
        result = simulate_combination(combo, seed=hash(combo) & 0x7FFFFFFF)
        elapsed = time.time() - t1

        line = '\t'.join([combo] + [f"{result[k]:.2f}" for k in [
            'strongest_suit_win_prob_all_follow_P1',
            'strongest_suit_win_prob_all_follow_not_P1',
            'strongest_suit_win_prob_single_follow',
            'strongest_suit_follow_prob',
            'betl_win_prob',
            'sans_win_prob',
        ]])

        with open(OUTPUT_FILE, 'a') as f:
            f.write(line + '\n')

        if (i + 1) % 10 == 0 or i == 0:
            rate = (i + 1) / (time.time() - t0)
            eta = (len(remaining) - i - 1) / rate if rate > 0 else 0
            _real_print(f"[{i+1}/{len(remaining)}] {combo}  "
                        f"{elapsed:.1f}s  rate={rate:.1f}/s  "
                        f"ETA={eta/60:.0f}m")


if __name__ == '__main__':
    main()
