"""RLNeuralPlayer — NeuralPlayer variant that records log-probs for REINFORCE.

Overrides all decision methods to use temperature sampling (not argmax)
and keeps the computation graph alive (no torch.no_grad()).
"""

import os
import sys
import random
import numpy as np

import torch
import torch.nn.functional as F
from torch.distributions import Categorical

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

from models import Card, SUIT_NAMES
import neural.features as feat
from neural.model import PrefNet


class RLNeuralPlayer:
    """NeuralPlayer that records log-probs for RL training.

    All 3 positions share the same model (same weights), so this is self-play.
    Trajectory entries: (head_name, log_prob, entropy)
    """

    BID_TYPES = ["pass", "game", "in_hand", "betl", "sans"]
    FOLLOWING_ACTIONS = ["pass", "follow", "call", "counter", "start_game", "double_counter"]

    def __init__(self, model: PrefNet, temperature: float = 0.5, name: str = ""):
        self.model = model
        self.temperature = temperature
        self.name = name
        self.last_bid_intent = ""
        self.trajectory = []  # [(head_name, log_prob, entropy), ...]

        # State set by play_game during gameplay
        self._hand = []
        self._rnd = None
        self._contract_type = None
        self._trump_suit = None
        self._observed_cards = []
        self._cards_played = 0
        self._is_declarer = False

    def reset_trajectory(self):
        """Clear trajectory for a new episode."""
        self.trajectory = []
        self._observed_cards = []
        self._cards_played = 0
        self._is_declarer = False

    def _sample_and_record(self, logits, head_name):
        """Sample from logits with temperature, record log_prob and entropy."""
        probs = F.softmax(logits / self.temperature, dim=-1)
        dist = Categorical(probs)
        action = dist.sample()
        log_prob = dist.log_prob(action)
        entropy = dist.entropy()
        self.trajectory.append((head_name, log_prob, entropy))
        return action.item()

    # ------------------------------------------------------------------
    # Bidding
    # ------------------------------------------------------------------

    def choose_bid(self, legal_bids):
        hand = getattr(self, '_hand', [])

        hand_feat = torch.from_numpy(feat.encode_hand(hand)).unsqueeze(0)
        mask = torch.zeros(1, 5)
        bid_type_map = {}
        for b in legal_bids:
            bt = b.get("bid_type")
            if bt in self.BID_TYPES:
                idx = self.BID_TYPES.index(bt)
                mask[0, idx] = 1.0
                bid_type_map[idx] = b

        # No torch.no_grad() — keep graph alive for REINFORCE
        logits = self.model.forward_bid(hand_feat, mask)[0]
        chosen_idx = self._sample_and_record(logits, "bid")

        if chosen_idx in bid_type_map:
            chosen = bid_type_map[chosen_idx]
        else:
            chosen = legal_bids[0]  # fallback to first legal bid

        self.last_bid_intent = f"rl (bid={self.BID_TYPES[chosen_idx]})"
        return chosen

    # ------------------------------------------------------------------
    # Discarding
    # ------------------------------------------------------------------

    def choose_discard(self, hand_card_ids, talon_card_ids):
        self._is_declarer = True

        all_ids = list(hand_card_ids) + list(talon_card_ids)
        all_cards = [Card.from_id(cid) for cid in all_ids]
        talon_set = set(talon_card_ids)

        hand_feat = torch.from_numpy(feat.encode_hand(all_cards)).unsqueeze(0)
        suit_counts = feat.get_suit_counts(all_cards)

        card_feats = []
        for i, card in enumerate(all_cards):
            is_talon = all_ids[i] in talon_set
            card_feats.append(feat.encode_card(card, suit_counts, is_talon=is_talon))

        card_feats_t = torch.from_numpy(
            np.array(card_feats, dtype=np.float32)
        ).unsqueeze(0)

        scores = self.model.forward_discard(hand_feat, card_feats_t)[0]

        # Treat discard as independent Bernoulli per card (sigmoid probabilities)
        # Sample top-2 from the distribution
        probs = torch.sigmoid(scores / self.temperature)

        # Use Gumbel trick for differentiable top-k: add noise to logits, pick top 2
        gumbel_noise = -torch.log(-torch.log(torch.rand_like(scores) + 1e-8) + 1e-8)
        noisy_scores = scores / self.temperature + gumbel_noise
        top2_indices = noisy_scores.topk(2).indices.tolist()

        # Record log-prob for each selected card (Bernoulli log-prob)
        for idx in top2_indices:
            p = probs[idx].clamp(1e-7, 1 - 1e-7)
            log_prob = torch.log(p)
            entropy = -(p * torch.log(p) + (1 - p) * torch.log(1 - p))
            self.trajectory.append(("discard", log_prob, entropy))

        return [all_ids[i] for i in top2_indices]

    # ------------------------------------------------------------------
    # Contract declaration
    # ------------------------------------------------------------------

    def choose_contract(self, legal_levels, hand, winner_bid):
        hand_feat = torch.from_numpy(feat.encode_hand(hand)).unsqueeze(0)

        bid_value = getattr(winner_bid, 'value', 2)
        bid_type = getattr(winner_bid, 'bid_type', None)
        is_ih = False
        if hasattr(bid_type, 'value'):
            is_ih = bid_type.value == "in_hand"
        elif isinstance(bid_type, str):
            is_ih = bid_type == "in_hand"

        context = torch.from_numpy(
            feat.encode_contract_context(bid_value, is_ih, legal_levels)
        ).unsqueeze(0)

        type_logits, trump_logits = self.model.forward_contract(hand_feat, context)

        # Sample contract type
        type_idx = self._sample_and_record(type_logits[0], "contract_type")
        contract_types = ["suit", "betl", "sans"]
        ctype = contract_types[type_idx]

        if ctype == "suit":
            # Sample trump suit
            trump_idx = self._sample_and_record(trump_logits[0], "contract_trump")
            suit_names = ["clubs", "diamonds", "hearts", "spades"]
            suit_levels = {"clubs": 5, "diamonds": 3, "hearts": 4, "spades": 2}

            trump = suit_names[trump_idx]
            # If invalid level, find the closest valid suit by model preference
            if suit_levels[trump] < bid_value:
                sorted_idx = trump_logits[0].argsort(descending=True).tolist()
                trump = None
                for idx in sorted_idx:
                    s = suit_names[idx]
                    if suit_levels[s] >= bid_value:
                        trump = s
                        break
                if trump is None:
                    trump = suit_names[sorted_idx[0]]
        else:
            trump = None

        level = min(legal_levels) if legal_levels else 2
        return ctype, trump, level

    # ------------------------------------------------------------------
    # Whisting / following
    # ------------------------------------------------------------------

    def choose_whist_action(self, legal_actions):
        hand = getattr(self, '_hand', [])
        contract_type = getattr(self, '_contract_type', None)
        trump_suit = getattr(self, '_trump_suit', None)

        hand_feat = torch.from_numpy(feat.encode_hand(hand)).unsqueeze(0)
        context = torch.from_numpy(
            feat.encode_following_context(contract_type, trump_suit, hand)
        ).unsqueeze(0)

        mask = torch.zeros(1, 6)
        action_map = {}
        for a in legal_actions:
            act = a.get("action") if isinstance(a, dict) else a
            if act in self.FOLLOWING_ACTIONS:
                idx = self.FOLLOWING_ACTIONS.index(act)
                mask[0, idx] = 1.0
                action_map[idx] = act

        logits = self.model.forward_following(hand_feat, context, mask)[0]
        chosen_idx = self._sample_and_record(logits, "following")

        if chosen_idx in action_map:
            return action_map[chosen_idx]

        # Fallback
        a = legal_actions[0]
        return a.get("action") if isinstance(a, dict) else a

    # ------------------------------------------------------------------
    # Card play
    # ------------------------------------------------------------------

    def choose_card(self, legal_cards):
        hand = getattr(self, '_hand', legal_cards)
        rnd = getattr(self, '_rnd', None)
        contract_type_str = getattr(self, '_contract_type', "suit")
        trump_suit = getattr(self, '_trump_suit', None)

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
                led_suit = trick.cards[0][1].suit

        hand_feat = torch.from_numpy(feat.encode_hand(hand)).unsqueeze(0)
        play_ctx = torch.from_numpy(feat.encode_card_play_context(
            self._is_declarer, trump_suit, trick_num, is_leading,
            trick_cards_count, led_suit, contract_type_str, len(hand),
        )).unsqueeze(0)
        played_vec = torch.from_numpy(
            feat.encode_cards_played(self._observed_cards)
        ).unsqueeze(0)

        suit_counts = feat.get_suit_counts(hand)
        card_feats = []
        for card in legal_cards:
            card_feats.append(feat.encode_card(card, suit_counts))
        card_feats_t = torch.from_numpy(
            np.array(card_feats, dtype=np.float32)
        ).unsqueeze(0)

        scores = self.model.forward_card_play(
            hand_feat, play_ctx, played_vec, card_feats_t)[0]

        n = len(legal_cards)
        chosen_idx = self._sample_and_record(scores[:n], "card_play")
        chosen_card = legal_cards[chosen_idx]

        # Track played cards
        self._observed_cards.append(chosen_card)
        self._cards_played += 1

        return chosen_card.id
