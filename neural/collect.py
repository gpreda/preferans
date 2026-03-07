"""Data collection: wrap expert players, play games, save .npz files.

Usage:
    python neural/collect.py --num-games 10000 --output-dir neural/data/
"""

import sys
import os
import argparse
import random
import time
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# Suppress engine debug prints
import builtins
_real_print = builtins.print
def _quiet_print(*args, **kwargs):
    import inspect
    frame = inspect.currentframe().f_back
    caller_file = frame.f_code.co_filename if frame else ""
    if "engine" in caller_file or "models" in caller_file:
        return
    _real_print(*args, **kwargs, flush=True)
builtins.print = _quiet_print

from models import Card, Suit, Rank, ContractType, SUIT_NAMES, NAME_TO_SUIT
from neural.features import (
    encode_hand, encode_card, get_suit_counts, card_to_index,
    encode_contract_context, encode_following_context,
    encode_calling_context, encode_countering_context,
    encode_card_play_context, encode_cards_played,
    compute_aggressiveness,
)


# ---------------------------------------------------------------------------
# Bid type ↔ index mapping
# ---------------------------------------------------------------------------

BID_TYPE_TO_IDX = {"pass": 0, "game": 1, "in_hand": 2, "betl": 3, "sans": 4}
FOLLOWING_ACTION_TO_IDX = {"pass": 0, "follow": 1}
CALLING_ACTION_TO_IDX = {"pass": 0, "follow": 1, "call": 2, "counter": 3}
COUNTERING_ACTION_TO_IDX = {"start_game": 0, "counter": 1, "double_counter": 2}


# ---------------------------------------------------------------------------
# DataRecorder — accumulates examples in memory
# ---------------------------------------------------------------------------

class DataRecorder:
    """Accumulates training examples for all 7 heads."""

    def __init__(self):
        self.bid_examples = []       # (hand[56], mask[5], label_idx)
        self.discard_examples = []   # (hand[56], card_feats[12,14], labels[12])
        self.contract_examples = []  # (hand[56], context[4], type_label, trump_label)
        self.following_examples = [] # (hand[56], context[8], mask[2], label_idx)
        self.calling_examples = []   # (hand[56], context[12], mask[4], label_idx)
        self.countering_examples = [] # (hand[56], context[12], mask[3], label_idx)
        self.card_play_examples = [] # (hand[56], ctx[16], played[32], card_feats[N,14], label_idx, N)

        # Per-player aggressiveness tracking: {player_id: [decision_keys]}
        self._player_decisions = {}
        # Pending examples before aggressiveness is computed: {player_id: [(list_ref, index)]}
        self._pending = {}

    def start_game(self, player_ids):
        """Reset per-game aggressiveness tracking."""
        self._player_decisions = {pid: [] for pid in player_ids}
        self._pending = {pid: [] for pid in player_ids}

    def end_game(self):
        """Compute per-game aggressiveness and label all pending examples."""
        for pid, decisions in self._player_decisions.items():
            aggr = compute_aggressiveness(decisions)
            for (example_list, idx) in self._pending.get(pid, []):
                example_list[idx] = example_list[idx] + (aggr,)
        self._player_decisions = {}
        self._pending = {}

    def _track_decision(self, player_id, decision_key):
        """Track a decision for aggressiveness scoring."""
        if player_id in self._player_decisions:
            self._player_decisions[player_id].append(decision_key)

    def _register_pending(self, player_id, example_list):
        """Register the last appended example as pending aggressiveness labeling."""
        if player_id in self._pending:
            self._pending[player_id].append((example_list, len(example_list) - 1))

    def record_bid(self, hand, legal_bids, chosen_bid, player_id=None):
        hand_feat = encode_hand(hand)
        mask = np.zeros(5, dtype=np.float32)
        for b in legal_bids:
            bt = b.get("bid_type")
            if bt in BID_TYPE_TO_IDX:
                mask[BID_TYPE_TO_IDX[bt]] = 1.0

        chosen_type = chosen_bid.get("bid_type")
        if chosen_type not in BID_TYPE_TO_IDX:
            return
        label = BID_TYPE_TO_IDX[chosen_type]
        self.bid_examples.append((hand_feat, mask, label))

        # Track aggressiveness
        self._track_decision(player_id, f"bid_{chosen_type}")
        self._register_pending(player_id, self.bid_examples)

    def record_discard(self, hand_cards, talon_cards, discard_ids, player_id=None):
        all_cards = list(hand_cards) + list(talon_cards)
        if len(all_cards) != 12:
            return

        hand_feat = encode_hand(all_cards)
        suit_counts = get_suit_counts(all_cards)

        card_feats = np.zeros((12, 14), dtype=np.float32)
        labels = np.zeros(12, dtype=np.float32)

        discard_set = set(discard_ids)

        for i, card in enumerate(all_cards):
            is_talon = card in talon_cards
            card_feats[i] = encode_card(card, suit_counts, is_talon=is_talon)
            if card.id in discard_set:
                labels[i] = 1.0

        self.discard_examples.append((hand_feat, card_feats, labels))
        self._register_pending(player_id, self.discard_examples)

    def record_contract(self, hand, legal_levels, winner_bid, contract_type, trump_name, player_id=None):
        hand_feat = encode_hand(hand)

        bid_value = getattr(winner_bid, 'value', 2)
        is_in_hand = getattr(winner_bid, 'bid_type', None)
        is_ih = False
        if hasattr(is_in_hand, 'value'):
            is_ih = is_in_hand.value == "in_hand"
        elif isinstance(is_in_hand, str):
            is_ih = is_in_hand == "in_hand"

        context = encode_contract_context(bid_value, is_ih, legal_levels)

        ct_map = {"suit": 0, "betl": 1, "sans": 2}
        type_label = ct_map.get(contract_type, 0)

        trump_label = 0
        if trump_name and trump_name in NAME_TO_SUIT:
            trump_label = NAME_TO_SUIT[trump_name].value - 1

        self.contract_examples.append((hand_feat, context, type_label, trump_label))
        self._register_pending(player_id, self.contract_examples)

    def record_following(self, hand, contract_type, trump_suit, legal_actions, chosen_action, player_id=None):
        """Route to following, calling, or countering based on legal actions."""
        action_strs = set()
        for a in legal_actions:
            act = a.get("action") if isinstance(a, dict) else a
            action_strs.add(act)

        if "call" in action_strs:
            self._record_calling(hand, contract_type, trump_suit, legal_actions, chosen_action, player_id)
        elif "start_game" in action_strs and ("counter" in action_strs or "double_counter" in action_strs):
            self._record_countering(hand, contract_type, trump_suit, legal_actions, chosen_action, player_id)
        else:
            self._record_following_only(hand, contract_type, trump_suit, legal_actions, chosen_action, player_id)

    def _record_following_only(self, hand, contract_type, trump_suit, legal_actions, chosen_action, player_id):
        hand_feat = encode_hand(hand)
        context = encode_following_context(contract_type, trump_suit, hand)

        mask = np.zeros(2, dtype=np.float32)
        for a in legal_actions:
            act = a.get("action") if isinstance(a, dict) else a
            if act in FOLLOWING_ACTION_TO_IDX:
                mask[FOLLOWING_ACTION_TO_IDX[act]] = 1.0

        act_str = chosen_action.get("action") if isinstance(chosen_action, dict) else chosen_action
        if act_str not in FOLLOWING_ACTION_TO_IDX:
            return
        label = FOLLOWING_ACTION_TO_IDX[act_str]
        self.following_examples.append((hand_feat, context, mask, label))

        self._track_decision(player_id, f"follow_{act_str}")
        self._register_pending(player_id, self.following_examples)

    def _record_calling(self, hand, contract_type, trump_suit, legal_actions, chosen_action, player_id):
        hand_feat = encode_hand(hand)

        # Derive calling-specific context fields
        action_strs = set()
        for a in legal_actions:
            act = a.get("action") if isinstance(a, dict) else a
            action_strs.add(act)

        other_defender_passed = "follow" not in action_strs and "pass" in action_strs
        is_counter_subphase = "counter" in action_strs
        contract_level = 2  # default; will be overridden if available from game state

        context = encode_calling_context(
            contract_type, trump_suit, hand,
            other_defender_passed, is_counter_subphase, contract_level,
        )

        mask = np.zeros(4, dtype=np.float32)
        for a in legal_actions:
            act = a.get("action") if isinstance(a, dict) else a
            if act in CALLING_ACTION_TO_IDX:
                mask[CALLING_ACTION_TO_IDX[act]] = 1.0

        act_str = chosen_action.get("action") if isinstance(chosen_action, dict) else chosen_action
        if act_str not in CALLING_ACTION_TO_IDX:
            return
        label = CALLING_ACTION_TO_IDX[act_str]
        self.calling_examples.append((hand_feat, context, mask, label))

        self._track_decision(player_id, act_str if act_str in ("call", "counter") else f"follow_{act_str}")
        self._register_pending(player_id, self.calling_examples)

    def _record_countering(self, hand, contract_type, trump_suit, legal_actions, chosen_action, player_id):
        hand_feat = encode_hand(hand)

        action_strs = set()
        for a in legal_actions:
            act = a.get("action") if isinstance(a, dict) else a
            action_strs.add(act)

        is_declarer_responding = "double_counter" in action_strs
        contract_level = 2  # default
        num_followers = 1  # default

        context = encode_countering_context(
            contract_type, trump_suit, hand,
            is_declarer_responding, contract_level, num_followers,
        )

        mask = np.zeros(3, dtype=np.float32)
        for a in legal_actions:
            act = a.get("action") if isinstance(a, dict) else a
            if act in COUNTERING_ACTION_TO_IDX:
                mask[COUNTERING_ACTION_TO_IDX[act]] = 1.0

        act_str = chosen_action.get("action") if isinstance(chosen_action, dict) else chosen_action
        if act_str not in COUNTERING_ACTION_TO_IDX:
            return
        label = COUNTERING_ACTION_TO_IDX[act_str]
        self.countering_examples.append((hand_feat, context, mask, label))

        self._track_decision(player_id, act_str if act_str in ("counter", "double_counter") else act_str)
        self._register_pending(player_id, self.countering_examples)

    def record_card_play(self, hand, legal_cards, chosen_card_id,
                         is_declarer, trump_suit, trick_num, is_leading,
                         trick_cards_count, led_suit, contract_type,
                         cards_played_list, player_id=None):
        hand_feat = encode_hand(hand)
        play_ctx = encode_card_play_context(
            is_declarer, trump_suit, trick_num, is_leading,
            trick_cards_count, led_suit, contract_type, len(hand),
        )
        played_vec = encode_cards_played(cards_played_list)

        suit_counts = get_suit_counts(hand)
        card_feats = np.zeros((len(legal_cards), 14), dtype=np.float32)
        label_idx = 0

        for i, card in enumerate(legal_cards):
            card_feats[i] = encode_card(card, suit_counts)
            if card.id == chosen_card_id:
                label_idx = i

        self.card_play_examples.append((
            hand_feat, play_ctx, played_vec, card_feats, label_idx, len(legal_cards),
        ))
        self._register_pending(player_id, self.card_play_examples)

    def save(self, output_dir):
        os.makedirs(output_dir, exist_ok=True)

        if self.bid_examples:
            hands = np.array([e[0] for e in self.bid_examples])
            masks = np.array([e[1] for e in self.bid_examples])
            labels = np.array([e[2] for e in self.bid_examples], dtype=np.int64)
            aggr = np.array([e[3] if len(e) > 3 else 0.5 for e in self.bid_examples], dtype=np.float32)
            np.savez(os.path.join(output_dir, "bid_data.npz"),
                     hands=hands, masks=masks, labels=labels, aggressiveness=aggr)
            print(f"  Saved {len(self.bid_examples)} bid examples")

        if self.discard_examples:
            hands = np.array([e[0] for e in self.discard_examples])
            card_feats = np.array([e[1] for e in self.discard_examples])
            labels = np.array([e[2] for e in self.discard_examples])
            aggr = np.array([e[3] if len(e) > 3 else 0.5 for e in self.discard_examples], dtype=np.float32)
            np.savez(os.path.join(output_dir, "discard_data.npz"),
                     hands=hands, card_feats=card_feats, labels=labels, aggressiveness=aggr)
            print(f"  Saved {len(self.discard_examples)} discard examples")

        if self.contract_examples:
            hands = np.array([e[0] for e in self.contract_examples])
            contexts = np.array([e[1] for e in self.contract_examples])
            type_labels = np.array([e[2] for e in self.contract_examples], dtype=np.int64)
            trump_labels = np.array([e[3] for e in self.contract_examples], dtype=np.int64)
            aggr = np.array([e[4] if len(e) > 4 else 0.5 for e in self.contract_examples], dtype=np.float32)
            np.savez(os.path.join(output_dir, "contract_data.npz"),
                     hands=hands, contexts=contexts,
                     type_labels=type_labels, trump_labels=trump_labels, aggressiveness=aggr)
            print(f"  Saved {len(self.contract_examples)} contract examples")

        if self.following_examples:
            hands = np.array([e[0] for e in self.following_examples])
            contexts = np.array([e[1] for e in self.following_examples])
            masks = np.array([e[2] for e in self.following_examples])
            labels = np.array([e[3] for e in self.following_examples], dtype=np.int64)
            aggr = np.array([e[4] if len(e) > 4 else 0.5 for e in self.following_examples], dtype=np.float32)
            np.savez(os.path.join(output_dir, "following_data.npz"),
                     hands=hands, contexts=contexts, masks=masks, labels=labels,
                     aggressiveness=aggr)
            print(f"  Saved {len(self.following_examples)} following examples")

        if self.calling_examples:
            hands = np.array([e[0] for e in self.calling_examples])
            contexts = np.array([e[1] for e in self.calling_examples])
            masks = np.array([e[2] for e in self.calling_examples])
            labels = np.array([e[3] for e in self.calling_examples], dtype=np.int64)
            aggr = np.array([e[4] if len(e) > 4 else 0.5 for e in self.calling_examples], dtype=np.float32)
            np.savez(os.path.join(output_dir, "calling_data.npz"),
                     hands=hands, contexts=contexts, masks=masks, labels=labels,
                     aggressiveness=aggr)
            print(f"  Saved {len(self.calling_examples)} calling examples")

        if self.countering_examples:
            hands = np.array([e[0] for e in self.countering_examples])
            contexts = np.array([e[1] for e in self.countering_examples])
            masks = np.array([e[2] for e in self.countering_examples])
            labels = np.array([e[3] for e in self.countering_examples], dtype=np.int64)
            aggr = np.array([e[4] if len(e) > 4 else 0.5 for e in self.countering_examples], dtype=np.float32)
            np.savez(os.path.join(output_dir, "countering_data.npz"),
                     hands=hands, contexts=contexts, masks=masks, labels=labels,
                     aggressiveness=aggr)
            print(f"  Saved {len(self.countering_examples)} countering examples")

        if self.card_play_examples:
            # Variable-length card features: pad to max
            max_cards = max(e[5] for e in self.card_play_examples)
            hands = np.array([e[0] for e in self.card_play_examples])
            play_ctxs = np.array([e[1] for e in self.card_play_examples])
            played_vecs = np.array([e[2] for e in self.card_play_examples])
            card_feats_padded = np.zeros(
                (len(self.card_play_examples), max_cards, 14), dtype=np.float32)
            labels = np.array([e[4] for e in self.card_play_examples], dtype=np.int64)
            num_legal = np.array([e[5] for e in self.card_play_examples], dtype=np.int64)
            aggr = np.array([e[6] if len(e) > 6 else 0.5 for e in self.card_play_examples], dtype=np.float32)
            for i, e in enumerate(self.card_play_examples):
                n = e[5]
                card_feats_padded[i, :n, :] = e[3]
            np.savez(os.path.join(output_dir, "card_play_data.npz"),
                     hands=hands, play_ctxs=play_ctxs, played_vecs=played_vecs,
                     card_feats=card_feats_padded, labels=labels, num_legal=num_legal,
                     aggressiveness=aggr)
            print(f"  Saved {len(self.card_play_examples)} card play examples")


# ---------------------------------------------------------------------------
# DataCollectingPlayer — wraps any expert player
# ---------------------------------------------------------------------------

class DataCollectingPlayer:
    """Wraps an existing player strategy to intercept and record decisions.

    Forwards all attribute assignments to the wrapped player so that
    _hand, _contract_type, _trump_suit, _rnd etc. are properly set.
    """

    def __init__(self, wrapped_player, recorder: DataRecorder, player_id=None):
        # Use object.__setattr__ to avoid triggering our __setattr__
        object.__setattr__(self, '_wrapped', wrapped_player)
        object.__setattr__(self, '_recorder', recorder)
        object.__setattr__(self, '_player_id', player_id)
        object.__setattr__(self, '_observed_cards_played', [])
        object.__setattr__(self, '_is_declarer', False)
        object.__setattr__(self, '_trick_num', 0)

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

    def __setattr__(self, name, value):
        # Forward game-state attributes to the wrapped player
        if name.startswith('_'):
            setattr(self._wrapped, name, value)
            # Also store locally for recording context
            object.__setattr__(self, name, value)
        else:
            object.__setattr__(self, name, value)

    def choose_bid(self, legal_bids):
        hand = getattr(self._wrapped, '_hand', [])
        chosen = self._wrapped.choose_bid(legal_bids)
        self._recorder.record_bid(hand, legal_bids, chosen, self._player_id)
        return chosen

    def choose_discard(self, hand_card_ids, talon_card_ids):
        # Build Card objects for recording
        hand_cards = [Card.from_id(cid) for cid in hand_card_ids]
        talon_cards = [Card.from_id(cid) for cid in talon_card_ids]

        discard_ids = self._wrapped.choose_discard(hand_card_ids, talon_card_ids)
        self._recorder.record_discard(hand_cards, talon_cards, discard_ids, self._player_id)
        object.__setattr__(self, '_is_declarer', True)
        return discard_ids

    def choose_contract(self, legal_levels, hand, winner_bid):
        ctype, trump, level = self._wrapped.choose_contract(legal_levels, hand, winner_bid)
        self._recorder.record_contract(hand, legal_levels, winner_bid, ctype, trump, self._player_id)
        return ctype, trump, level

    def choose_whist_action(self, legal_actions):
        hand = getattr(self._wrapped, '_hand', [])
        contract_type = getattr(self._wrapped, '_contract_type', None)
        trump_suit = getattr(self._wrapped, '_trump_suit', None)

        action = self._wrapped.choose_whist_action(legal_actions)
        self._recorder.record_following(
            hand, contract_type, trump_suit, legal_actions, action, self._player_id,
        )
        return action

    def choose_card(self, legal_cards):
        hand = getattr(self._wrapped, '_hand', legal_cards)
        rnd = getattr(self._wrapped, '_rnd', None)
        is_declarer = getattr(self, '_is_declarer', False)
        contract_type_str = getattr(self._wrapped, '_contract_type', "suit")
        trump_suit = getattr(self._wrapped, '_trump_suit', None)

        # Determine trick context
        trick_num = 1
        is_leading = True
        trick_cards_count = 0
        led_suit = None

        if rnd and rnd.current_trick:
            trick = rnd.current_trick
            trick_num = trick.number
            trick_cards_count = len(trick.cards)
            is_leading = trick_cards_count == 0
            if trick.cards:
                # trick.cards is list of (player_id, Card) tuples
                led_suit = trick.cards[0][1].suit

        card_id = self._wrapped.choose_card(legal_cards)

        # Track observed cards
        played_card = next((c for c in legal_cards if c.id == card_id), None)
        cards_played_snapshot = list(self._observed_cards_played)
        if played_card:
            self._observed_cards_played.append(played_card)

        self._recorder.record_card_play(
            hand, legal_cards, card_id,
            is_declarer, trump_suit, trick_num, is_leading,
            trick_cards_count, led_suit, contract_type_str,
            cards_played_snapshot, self._player_id,
        )

        return card_id


# ---------------------------------------------------------------------------
# Collection script
# ---------------------------------------------------------------------------

def collect_data(num_games: int, output_dir: str, seed: int = 42):
    """Play num_games with expert players and collect training data."""
    from PrefTestSingleGame import PlayerAlice, Sim3000, NoisyPlayer, play_game

    # Build helper class for S20-N10A: NoisyPlayer(noise=0.1, helper=Alice)
    class _Noisy10Alice(NoisyPlayer):
        def __init__(self, name="N10A", seed=None):
            super().__init__(name, noise=0.1, helper_cls=PlayerAlice, seed=seed)

    experts = [
        ("Sim50-Alice", lambda s: Sim3000("Sim50-Alice", num_simulations=50, helper_cls=PlayerAlice, seed=s)),
        ("S20-N10A",    lambda s: Sim3000("S20-N10A", num_simulations=20, helper_cls=_Noisy10Alice, seed=s)),
        ("Sim10-Alice", lambda s: Sim3000("Sim10-Alice", num_simulations=10, helper_cls=PlayerAlice, seed=s)),
    ]

    recorder = DataRecorder()
    rng = random.Random(seed)

    completed = 0
    errors = 0

    for i in range(num_games):
        game_seed = rng.randint(1, 999999)

        # Rotate positions across games
        rotation = i % 6
        if rotation == 0:
            order = [0, 1, 2]
        elif rotation == 1:
            order = [0, 2, 1]
        elif rotation == 2:
            order = [1, 0, 2]
        elif rotation == 3:
            order = [1, 2, 0]
        elif rotation == 4:
            order = [2, 0, 1]
        else:
            order = [2, 1, 0]

        players = [experts[order[j]][1](game_seed + j) for j in range(3)]
        wrapped = {
            pid: DataCollectingPlayer(players[idx], recorder, player_id=pid)
            for idx, pid in enumerate([1, 2, 3])
        }

        recorder.start_game([1, 2, 3])
        try:
            _, _, _ = play_game(wrapped, seed=game_seed)
            completed += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  Game {i+1} error: {e}")
        recorder.end_game()

        if (i + 1) % 500 == 0:
            print(f"  Progress: {i+1}/{num_games} games "
                  f"({completed} ok, {errors} errors)")

    print(f"\nCollection complete: {completed}/{num_games} games successful")
    print(f"Saving data to {output_dir}...")
    recorder.save(output_dir)
    print("Done!")


def main():
    parser = argparse.ArgumentParser(description="Collect training data from expert players")
    parser.add_argument("--num-games", type=int, default=10000,
                        help="Number of games to play (default: 10000)")
    parser.add_argument("--output-dir", type=str, default="neural/data/",
                        help="Output directory for .npz files (default: neural/data/)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed (default: 42)")
    args = parser.parse_args()
    collect_data(args.num_games, args.output_dir, args.seed)


if __name__ == "__main__":
    main()
