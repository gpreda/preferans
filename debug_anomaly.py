#!/usr/bin/env python3
"""Debug: find games where declarer wins with both followers but loses with one."""
import os, sys, random
sys.path.insert(0, 'server')

import builtins
_real_print = builtins.print
builtins.print = lambda *a, **k: None

from models import Card, Suit, Rank
from PrefTestSingleGame import (
    BasePlayer, PlayerAlice, RandomMovePlayer,
    _sim_get_legal_cards, _sim_determine_winner, CardPlayContext,
)

builtins.print = _real_print

from compute_probabilities import encoding_to_cards, random_deal, discard_for, playout

def card_str(c):
    rank_map = {1:'7',2:'8',3:'9',4:'10',5:'J',6:'Q',7:'K',8:'A'}
    suit_map = {1:'C',2:'D',3:'H',4:'S'}
    return rank_map[c.rank.value] + suit_map[c.suit.value]

def verbose_playout(hands, trump_suit, contract_type, declarer_id, first_lead, player_classes, rng, label):
    active = [1, 2, 3]
    sim_hands = {p: list(h) for p, h in hands.items()}
    tricks_won = {p: 0 for p in active}
    played_cards = []
    helpers = {}
    for pid in active:
        cls = player_classes.get(pid, PlayerAlice)
        s = rng.randint(0, 10**9)
        if cls == RandomMovePlayer:
            h = cls(f'sim_{pid}', seed=s)
        else:
            h = cls(seed=s)
        h._contract_type = contract_type
        h._trump_suit = trump_suit
        h._is_declarer = (pid == declarer_id)
        helpers[pid] = h

    def _rotate(players, lead):
        idx = players.index(lead)
        return players[idx:] + players[:idx]

    _real_print(f'=== {label} ===')
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
        trick_str = ' '.join(f'P{pid}:{card_str(c)}' for pid, c in trick_cards)
        cls_name = {PlayerAlice: 'Alice', RandomMovePlayer: 'Rand'}[player_classes.get(winner, PlayerAlice)]
        _real_print(f'  T{trick_num+1}: {trick_str} -> P{winner}({cls_name}) | {dict(tricks_won)}')
        if contract_type != 'betl':
            ftricks = sum(v for k, v in tricks_won.items() if k != declarer_id)
            if ftricks >= 5:
                break
        lead = winner
    _real_print(f'  FINAL: {dict(tricks_won)}')
    _real_print()
    return tricks_won


combo = 'AJxx-KDJ-Kx-J'
trump_suit = Suit.SPADES
all_follow_cls = {1: PlayerAlice, 2: PlayerAlice, 3: PlayerAlice}
cls_a = {1: PlayerAlice, 2: PlayerAlice, 3: RandomMovePlayer}
cls_b = {1: PlayerAlice, 2: RandomMovePlayer, 3: PlayerAlice}

# Try many seeds to find a reproducible anomaly
for seed in range(1000):
    rng = random.Random(seed)
    decl_10 = encoding_to_cards(combo, rng)
    talon, p2_hand, p3_hand = random_deal(decl_10, rng)
    decl_12 = decl_10 + talon
    hand_suit = discard_for(decl_12, 'suit', 'spades')
    hands = {1: hand_suit, 2: p2_hand, 3: p3_hand}

    # Use deterministic separate seeds for each playout
    tw_all = playout(hands, trump_suit, 'suit', 1, 1, all_follow_cls, random.Random(seed * 10))
    tw_a = playout(hands, trump_suit, 'suit', 1, 1, cls_a, random.Random(seed * 10))
    tw_b = playout(hands, trump_suit, 'suit', 1, 1, cls_b, random.Random(seed * 10))

    won_all = tw_all[1] >= 6
    won_a = tw_a[1] >= 6
    won_b = tw_b[1] >= 6

    if won_all and (not won_a or not won_b):
        _real_print(f'*** ANOMALY at seed={seed} ***')
        _real_print(f'P1(decl): {sorted([card_str(c) for c in hand_suit])}')
        _real_print(f'P2: {sorted([card_str(c) for c in p2_hand])}')
        _real_print(f'P3: {sorted([card_str(c) for c in p3_hand])}')
        _real_print(f'all_follow: P1={tw_all[1]} P2={tw_all[2]} P3={tw_all[3]}')
        _real_print(f'single_A:   P1={tw_a[1]} P2={tw_a[2]} P3={tw_a[3]}')
        _real_print(f'single_B:   P1={tw_b[1]} P2={tw_b[2]} P3={tw_b[3]}')
        _real_print()

        verbose_playout(hands, trump_suit, 'suit', 1, 1, all_follow_cls, random.Random(seed * 10), 'ALL FOLLOW')
        lost = 'A' if not won_a else 'B'
        if not won_a:
            verbose_playout(hands, trump_suit, 'suit', 1, 1, cls_a, random.Random(seed * 10), 'SINGLE A (P2=Alice, P3=Random)')
        if not won_b:
            verbose_playout(hands, trump_suit, 'suit', 1, 1, cls_b, random.Random(seed * 10), 'SINGLE B (P2=Random, P3=Alice)')
        break
else:
    _real_print('No anomaly found in 1000 seeds')
