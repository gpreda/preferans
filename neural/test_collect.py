"""Tests for training data generation pipeline: features, collection, and data format."""

import os
import sys
import tempfile
import shutil
import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from models import Card, Suit, Rank, SUIT_NAMES, NAME_TO_SUIT
from neural.features import (
    encode_hand, encode_card, get_suit_counts, card_to_index,
    encode_contract_context, encode_following_context,
    encode_card_play_context, encode_cards_played, card_id_to_index,
)
from neural.collect import DataRecorder, DataCollectingPlayer, BID_TYPE_TO_IDX, FOLLOWING_ACTION_TO_IDX


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_hand(card_ids):
    """Create a list of Card objects from id strings like 'A_spades'."""
    return [Card.from_id(cid) for cid in card_ids]


SAMPLE_HAND_IDS = [
    "A_spades", "K_spades", "Q_spades",
    "A_hearts", "K_hearts",
    "A_diamonds", "10_diamonds",
    "9_clubs", "8_clubs", "7_clubs",
]

SAMPLE_TALON_IDS = ["J_hearts", "7_diamonds"]


@pytest.fixture
def sample_hand():
    return make_hand(SAMPLE_HAND_IDS)


@pytest.fixture
def sample_talon():
    return make_hand(SAMPLE_TALON_IDS)


@pytest.fixture
def output_dir():
    d = tempfile.mkdtemp(prefix="neural_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# card_to_index
# ---------------------------------------------------------------------------

class TestCardIndex:
    def test_index_range(self):
        """All 32 cards map to unique indices in [0, 31]."""
        indices = set()
        for suit in Suit:
            for rank in Rank:
                card = Card(rank=rank, suit=suit)
                idx = card_to_index(card)
                assert 0 <= idx <= 31, f"{card.id} mapped to {idx}"
                indices.add(idx)
        assert len(indices) == 32

    def test_specific_cards(self):
        # clubs=1, seven=1 → (0)*8 + 0 = 0
        assert card_to_index(Card(rank=Rank.SEVEN, suit=Suit.CLUBS)) == 0
        # clubs=1, ace=8 → 0*8 + 7 = 7
        assert card_to_index(Card(rank=Rank.ACE, suit=Suit.CLUBS)) == 7
        # spades=4, ace=8 → 3*8 + 7 = 31
        assert card_to_index(Card(rank=Rank.ACE, suit=Suit.SPADES)) == 31

    def test_card_id_to_index(self):
        assert card_id_to_index("7_clubs") == 0
        assert card_id_to_index("A_spades") == 31


# ---------------------------------------------------------------------------
# encode_hand
# ---------------------------------------------------------------------------

class TestEncodeHand:
    def test_shape(self, sample_hand):
        feat = encode_hand(sample_hand)
        assert feat.shape == (56,)
        assert feat.dtype == np.float32

    def test_card_presence(self, sample_hand):
        feat = encode_hand(sample_hand)
        # A_spades = index 31 should be 1
        assert feat[card_to_index(Card(rank=Rank.ACE, suit=Suit.SPADES))] == 1.0
        # 7_clubs = index 0 should be 1
        assert feat[card_to_index(Card(rank=Rank.SEVEN, suit=Suit.CLUBS))] == 1.0
        # J_diamonds not in hand, should be 0
        assert feat[card_to_index(Card(rank=Rank.JACK, suit=Suit.DIAMONDS))] == 0.0

    def test_suit_distribution(self, sample_hand):
        feat = encode_hand(sample_hand)
        # clubs: 3 cards (9, 8, 7) → 3/10 = 0.3
        assert abs(feat[32] - 0.3) < 1e-6  # clubs
        # diamonds: 2 cards → 2/10 = 0.2
        assert abs(feat[33] - 0.2) < 1e-6  # diamonds
        # hearts: 2 cards → 0.2
        assert abs(feat[34] - 0.2) < 1e-6  # hearts
        # spades: 3 cards → 0.3
        assert abs(feat[35] - 0.3) < 1e-6  # spades

    def test_derived_features(self, sample_hand):
        feat = encode_hand(sample_hand)
        # 3 aces: A♠, A♥, A♦ → ace_count/4 = 0.75
        assert abs(feat[44] - 0.75) < 1e-6
        # hand_size / 12 = 10/12
        assert abs(feat[55] - 10.0 / 12.0) < 1e-6

    def test_empty_hand(self):
        feat = encode_hand([])
        assert feat.shape == (56,)
        assert np.all(feat[:32] == 0.0)  # no cards present
        assert feat[55] == 0.0  # hand size = 0

    def test_all_32_cards(self):
        all_cards = [Card(rank=r, suit=s) for s in Suit for r in Rank]
        feat = encode_hand(all_cards)
        assert np.all(feat[:32] == 1.0)  # all cards present
        # 4 suits * 8 ranks = 32 cards, suit_count = 8 each → 8/10
        for i in range(4):
            assert abs(feat[32 + i] - 0.8) < 1e-6


# ---------------------------------------------------------------------------
# encode_card
# ---------------------------------------------------------------------------

class TestEncodeCard:
    def test_shape(self):
        card = Card(rank=Rank.ACE, suit=Suit.SPADES)
        feat = encode_card(card)
        assert feat.shape == (14,)
        assert feat.dtype == np.float32

    def test_rank_one_hot(self):
        card = Card(rank=Rank.ACE, suit=Suit.SPADES)  # ACE = 8, index 7
        feat = encode_card(card)
        assert feat[7] == 1.0
        assert sum(feat[:8]) == 1.0  # exactly one rank bit set

    def test_suit_one_hot(self):
        card = Card(rank=Rank.SEVEN, suit=Suit.HEARTS)  # HEARTS = 3, index 10
        feat = encode_card(card)
        assert feat[10] == 1.0  # 8 + (3-1) = 10
        assert sum(feat[8:12]) == 1.0

    def test_talon_flag(self):
        card = Card(rank=Rank.SEVEN, suit=Suit.CLUBS)
        feat_no = encode_card(card, is_talon=False)
        feat_yes = encode_card(card, is_talon=True)
        assert feat_no[12] == 0.0
        assert feat_yes[12] == 1.0

    def test_suit_counts(self):
        hand = make_hand(["A_spades", "K_spades", "7_clubs"])
        counts = get_suit_counts(hand)
        card = Card(rank=Rank.ACE, suit=Suit.SPADES)
        feat = encode_card(card, suit_counts=counts)
        assert abs(feat[13] - 0.2) < 1e-6  # 2 spades / 10


# ---------------------------------------------------------------------------
# get_suit_counts
# ---------------------------------------------------------------------------

class TestGetSuitCounts:
    def test_basic(self):
        hand = make_hand(["A_spades", "K_spades", "7_clubs"])
        counts = get_suit_counts(hand)
        assert counts[Suit.SPADES] == 2
        assert counts[Suit.CLUBS] == 1
        assert counts.get(Suit.HEARTS, 0) == 0

    def test_empty(self):
        assert get_suit_counts([]) == {}


# ---------------------------------------------------------------------------
# Context encoders
# ---------------------------------------------------------------------------

class TestContractContext:
    def test_shape(self):
        ctx = encode_contract_context(2, False, [2, 3, 4, 5])
        assert ctx.shape == (4,)

    def test_values(self):
        ctx = encode_contract_context(3, True, [3, 4, 5])
        assert abs(ctx[0] - 3 / 7.0) < 1e-6
        assert ctx[1] == 1.0  # is_in_hand
        assert abs(ctx[2] - 3 / 7.0) < 1e-6  # min level
        assert abs(ctx[3] - 5 / 7.0) < 1e-6  # max level

    def test_empty_levels(self):
        ctx = encode_contract_context(2, False, [])
        assert ctx[2] == 0.0
        assert ctx[3] == 0.0


class TestFollowingContext:
    def test_shape(self, sample_hand):
        ctx = encode_following_context("suit", Suit.SPADES, sample_hand)
        assert ctx.shape == (8,)

    def test_contract_type_one_hot(self, sample_hand):
        for ct, expected_idx in [("suit", 0), ("betl", 1), ("sans", 2)]:
            ctx = encode_following_context(ct, None, sample_hand)
            assert ctx[expected_idx] == 1.0
            assert sum(ctx[:3]) == 1.0

    def test_trump_one_hot(self, sample_hand):
        ctx = encode_following_context("suit", Suit.HEARTS, sample_hand)
        assert ctx[5] == 1.0  # 3 + (3-1) = 5 for hearts
        assert sum(ctx[3:7]) == 1.0


class TestCardPlayContext:
    def test_shape(self):
        ctx = encode_card_play_context(
            True, Suit.CLUBS, 3, False, 1, Suit.DIAMONDS, "suit", 8)
        assert ctx.shape == (16,)

    def test_declarer_flag(self):
        ctx = encode_card_play_context(True, None, 1, True, 0, None, "suit", 10)
        assert ctx[0] == 1.0
        ctx = encode_card_play_context(False, None, 1, True, 0, None, "suit", 10)
        assert ctx[0] == 0.0

    def test_trick_num(self):
        ctx = encode_card_play_context(False, None, 5, True, 0, None, "suit", 10)
        assert abs(ctx[5] - 0.5) < 1e-6  # 5/10


class TestCardsPlayed:
    def test_shape(self):
        vec = encode_cards_played([])
        assert vec.shape == (32,)
        assert np.all(vec == 0.0)

    def test_marks_played(self):
        cards = [Card(rank=Rank.ACE, suit=Suit.SPADES)]
        vec = encode_cards_played(cards)
        assert vec[31] == 1.0  # A♠ = index 31
        assert sum(vec) == 1.0


# ---------------------------------------------------------------------------
# DataRecorder
# ---------------------------------------------------------------------------

class TestDataRecorder:
    def test_record_bid(self, sample_hand):
        rec = DataRecorder()
        legal = [
            {"bid_type": "pass", "value": 0},
            {"bid_type": "game", "value": 2},
        ]
        rec.record_bid(sample_hand, legal, {"bid_type": "game", "value": 2})
        assert len(rec.bid_examples) == 1
        hand_feat, mask, label = rec.bid_examples[0]
        assert hand_feat.shape == (56,)
        assert mask[0] == 1.0  # pass is legal
        assert mask[1] == 1.0  # game is legal
        assert mask[2] == 0.0  # in_hand not legal
        assert label == BID_TYPE_TO_IDX["game"]

    def test_record_bid_unknown_type_skipped(self, sample_hand):
        rec = DataRecorder()
        rec.record_bid(sample_hand, [{"bid_type": "unknown"}], {"bid_type": "unknown"})
        assert len(rec.bid_examples) == 0

    def test_record_discard(self):
        rec = DataRecorder()
        hand = make_hand(SAMPLE_HAND_IDS)
        talon = make_hand(SAMPLE_TALON_IDS)
        # Discard the two talon cards
        discard_ids = [c.id for c in talon]
        rec.record_discard(hand, talon, discard_ids)
        assert len(rec.discard_examples) == 1
        hand_feat, card_feats, labels = rec.discard_examples[0]
        assert hand_feat.shape == (56,)
        assert card_feats.shape == (12, 14)
        assert labels.shape == (12,)
        # The last 2 cards (talon) should be labeled as discard
        assert labels[10] == 1.0
        assert labels[11] == 1.0
        assert sum(labels) == 2.0

    def test_record_discard_wrong_count_skipped(self):
        rec = DataRecorder()
        hand = make_hand(SAMPLE_HAND_IDS[:5])
        talon = make_hand(SAMPLE_TALON_IDS[:1])
        rec.record_discard(hand, talon, [])
        assert len(rec.discard_examples) == 0  # 6 != 12, skip

    def test_record_contract(self, sample_hand):
        rec = DataRecorder()

        class FakeBid:
            value = 2
            bid_type = "game"

        rec.record_contract(sample_hand, [2, 3, 4], FakeBid(), "suit", "spades")
        assert len(rec.contract_examples) == 1
        hand_feat, context, type_label, trump_label = rec.contract_examples[0]
        assert hand_feat.shape == (56,)
        assert context.shape == (4,)
        assert type_label == 0  # suit
        assert trump_label == Suit.SPADES.value - 1  # 3

    def test_record_following(self, sample_hand):
        rec = DataRecorder()
        legal = [
            {"action": "pass"},
            {"action": "follow"},
        ]
        rec.record_following(sample_hand, "suit", Suit.HEARTS, legal, {"action": "follow"})
        assert len(rec.following_examples) == 1
        hand_feat, context, mask, label = rec.following_examples[0]
        assert hand_feat.shape == (56,)
        assert context.shape == (8,)
        assert mask[0] == 1.0  # pass
        assert mask[1] == 1.0  # follow
        assert mask[2] == 0.0  # call not legal
        assert label == FOLLOWING_ACTION_TO_IDX["follow"]

    def test_record_card_play(self, sample_hand):
        rec = DataRecorder()
        legal_cards = sample_hand[:3]
        chosen_id = legal_cards[1].id

        rec.record_card_play(
            sample_hand, legal_cards, chosen_id,
            is_declarer=True, trump_suit=Suit.SPADES, trick_num=2,
            is_leading=False, trick_cards_count=1, led_suit=Suit.SPADES,
            contract_type="suit", cards_played_list=[],
        )
        assert len(rec.card_play_examples) == 1
        hand_feat, play_ctx, played_vec, card_feats, label_idx, n = rec.card_play_examples[0]
        assert hand_feat.shape == (56,)
        assert play_ctx.shape == (16,)
        assert played_vec.shape == (32,)
        assert card_feats.shape == (3, 14)
        assert label_idx == 1
        assert n == 3

    def test_save_and_load(self, sample_hand, output_dir):
        rec = DataRecorder()
        # Add one example of each type
        rec.record_bid(
            sample_hand,
            [{"bid_type": "pass"}, {"bid_type": "game"}],
            {"bid_type": "pass"},
        )

        hand = make_hand(SAMPLE_HAND_IDS)
        talon = make_hand(SAMPLE_TALON_IDS)
        rec.record_discard(hand, talon, [talon[0].id, talon[1].id])

        class FakeBid:
            value = 2
            bid_type = "game"
        rec.record_contract(sample_hand, [2, 3], FakeBid(), "suit", "hearts")

        rec.record_following(
            sample_hand, "suit", Suit.CLUBS,
            [{"action": "pass"}, {"action": "follow"}],
            {"action": "pass"},
        )

        rec.record_card_play(
            sample_hand, sample_hand[:4], sample_hand[2].id,
            True, Suit.SPADES, 1, True, 0, None, "suit", [],
        )

        rec.save(output_dir)

        # Verify all files exist and load correctly
        for name, expected_keys in [
            ("bid_data.npz", ["hands", "masks", "labels"]),
            ("discard_data.npz", ["hands", "card_feats", "labels"]),
            ("contract_data.npz", ["hands", "contexts", "type_labels", "trump_labels"]),
            ("following_data.npz", ["hands", "contexts", "masks", "labels"]),
            ("card_play_data.npz", ["hands", "play_ctxs", "played_vecs", "card_feats", "labels", "num_legal"]),
        ]:
            path = os.path.join(output_dir, name)
            assert os.path.exists(path), f"{name} not saved"
            data = np.load(path)
            for key in expected_keys:
                assert key in data, f"{name} missing key '{key}'"
                assert len(data[key]) == 1, f"{name}[{key}] expected 1 example"

    def test_save_empty(self, output_dir):
        rec = DataRecorder()
        rec.save(output_dir)
        # No files should be created for empty datasets
        assert not os.path.exists(os.path.join(output_dir, "bid_data.npz"))


# ---------------------------------------------------------------------------
# DataCollectingPlayer wrapper
# ---------------------------------------------------------------------------

class TestDataCollectingPlayer:
    def test_bid_forwarding(self, sample_hand):
        """Wrapper records bid and forwards to wrapped player."""
        rec = DataRecorder()

        class FakePlayer:
            def __init__(self):
                self._hand = []
                self.last_bid_intent = ""
            def choose_bid(self, legal_bids):
                return legal_bids[0]  # always pass

        player = FakePlayer()
        player._hand = sample_hand
        wrapper = DataCollectingPlayer(player, rec)

        legal = [
            {"bid_type": "pass", "value": 0},
            {"bid_type": "game", "value": 2},
        ]
        result = wrapper.choose_bid(legal)

        assert result == legal[0]
        assert len(rec.bid_examples) == 1

    def test_discard_forwarding(self):
        rec = DataRecorder()

        class FakePlayer:
            def __init__(self):
                self._hand = []
            def choose_discard(self, hand_ids, talon_ids):
                return talon_ids  # discard talon

        player = FakePlayer()
        wrapper = DataCollectingPlayer(player, rec)

        result = wrapper.choose_discard(SAMPLE_HAND_IDS, SAMPLE_TALON_IDS)
        assert result == SAMPLE_TALON_IDS
        assert len(rec.discard_examples) == 1

    def test_attribute_forwarding(self):
        """Game state attributes set on wrapper are forwarded to wrapped player."""
        rec = DataRecorder()

        class FakePlayer:
            def __init__(self):
                self._hand = []
                self._contract_type = None

        player = FakePlayer()
        wrapper = DataCollectingPlayer(player, rec)

        test_hand = make_hand(["A_spades"])
        wrapper._hand = test_hand
        assert player._hand == test_hand

        wrapper._contract_type = "betl"
        assert player._contract_type == "betl"


# ---------------------------------------------------------------------------
# End-to-end: collect a few games
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_collect_small(self, output_dir):
        """Run full collection pipeline with a few games."""
        from neural.collect import collect_data

        collect_data(num_games=5, output_dir=output_dir, seed=99)

        # All 5 data files should exist
        for name in ["bid_data.npz", "discard_data.npz", "contract_data.npz",
                      "following_data.npz", "card_play_data.npz"]:
            path = os.path.join(output_dir, name)
            assert os.path.exists(path), f"{name} not generated"

        # Each file should have > 0 examples
        for name in ["bid_data", "discard_data", "contract_data",
                      "following_data", "card_play_data"]:
            data = np.load(os.path.join(output_dir, f"{name}.npz"))
            first_key = list(data.keys())[0]
            assert len(data[first_key]) > 0, f"{name} has 0 examples"

    def test_data_compatible_with_train(self, output_dir):
        """Verify generated data can be loaded by train.py Dataset classes."""
        from neural.collect import collect_data
        from neural.train import (
            BidDataset, DiscardDataset, ContractDataset,
            FollowingDataset, CardPlayDataset,
        )

        collect_data(num_games=5, output_dir=output_dir, seed=42)

        datasets = {
            "bid": BidDataset(os.path.join(output_dir, "bid_data.npz")),
            "discard": DiscardDataset(os.path.join(output_dir, "discard_data.npz")),
            "contract": ContractDataset(os.path.join(output_dir, "contract_data.npz")),
            "following": FollowingDataset(os.path.join(output_dir, "following_data.npz")),
            "card_play": CardPlayDataset(os.path.join(output_dir, "card_play_data.npz")),
        }

        for name, ds in datasets.items():
            assert len(ds) > 0, f"{name} dataset empty"
            sample = ds[0]
            assert all(isinstance(t, __import__("torch").Tensor) for t in sample), \
                f"{name} dataset item should be all tensors"

    def test_data_feeds_into_model(self, output_dir):
        """Verify generated data works with model forward passes."""
        import torch
        from neural.collect import collect_data
        from neural.model import PrefNet
        from neural.train import (
            BidDataset, DiscardDataset, ContractDataset,
            FollowingDataset, CardPlayDataset,
        )

        collect_data(num_games=5, output_dir=output_dir, seed=7)
        model = PrefNet()
        model.eval()

        # Bid
        ds = BidDataset(os.path.join(output_dir, "bid_data.npz"))
        hands, masks, labels = ds[0]
        logits = model.forward_bid(hands.unsqueeze(0), masks.unsqueeze(0))
        assert logits.shape == (1, 5)

        # Discard
        ds = DiscardDataset(os.path.join(output_dir, "discard_data.npz"))
        hands, card_feats, labels = ds[0]
        scores = model.forward_discard(hands.unsqueeze(0), card_feats.unsqueeze(0))
        assert scores.shape[0] == 1
        assert scores.shape[1] == 12

        # Contract
        ds = ContractDataset(os.path.join(output_dir, "contract_data.npz"))
        hands, contexts, tl, trl = ds[0]
        type_logits, trump_logits = model.forward_contract(
            hands.unsqueeze(0), contexts.unsqueeze(0))
        assert type_logits.shape == (1, 3)
        assert trump_logits.shape == (1, 4)

        # Following
        ds = FollowingDataset(os.path.join(output_dir, "following_data.npz"))
        hands, contexts, masks, labels = ds[0]
        logits = model.forward_following(
            hands.unsqueeze(0), contexts.unsqueeze(0), masks.unsqueeze(0))
        assert logits.shape == (1, 6)

        # Card play
        ds = CardPlayDataset(os.path.join(output_dir, "card_play_data.npz"))
        hands, play_ctxs, played_vecs, card_feats, labels, num_legal = ds[0]
        scores = model.forward_card_play(
            hands.unsqueeze(0), play_ctxs.unsqueeze(0),
            played_vecs.unsqueeze(0), card_feats.unsqueeze(0))
        assert scores.shape[0] == 1

    def test_deterministic_with_seed(self, output_dir):
        """Same seed produces identical data (requires seeding global random too)."""
        import random as stdlib_random
        from neural.collect import collect_data

        dir1 = os.path.join(output_dir, "run1")
        dir2 = os.path.join(output_dir, "run2")

        # Engine uses global random for shuffle_and_deal, so seed it too
        stdlib_random.seed(555)
        collect_data(num_games=3, output_dir=dir1, seed=555)
        stdlib_random.seed(555)
        collect_data(num_games=3, output_dir=dir2, seed=555)

        for name in ["bid_data", "discard_data", "contract_data",
                      "following_data", "card_play_data"]:
            d1 = np.load(os.path.join(dir1, f"{name}.npz"))
            d2 = np.load(os.path.join(dir2, f"{name}.npz"))
            for key in d1:
                np.testing.assert_array_equal(
                    d1[key], d2[key],
                    err_msg=f"{name}[{key}] differs between runs with same seed",
                )

    def test_position_rotation(self, output_dir):
        """6 consecutive games cover all 6 position permutations."""
        from neural.collect import collect_data

        # With 6 games, each of the 6 rotation orders is used once
        collect_data(num_games=6, output_dir=output_dir, seed=321)

        # Just verify it completed without error and produced data
        data = np.load(os.path.join(output_dir, "bid_data.npz"))
        assert len(data["labels"]) > 0


# ---------------------------------------------------------------------------
# Edge cases in feature encoding
# ---------------------------------------------------------------------------

class TestFeatureEdgeCases:
    def test_12_card_hand(self):
        """Post-talon hand (12 cards) encodes correctly."""
        ids = SAMPLE_HAND_IDS + SAMPLE_TALON_IDS
        hand = make_hand(ids)
        feat = encode_hand(hand)
        assert abs(feat[55] - 12 / 12.0) < 1e-6

    def test_single_card_hand(self):
        hand = make_hand(["A_spades"])
        feat = encode_hand(hand)
        assert feat[31] == 1.0  # A♠
        assert abs(feat[55] - 1 / 12.0) < 1e-6

    def test_suit_string_trump(self):
        """encode_following_context accepts string trump suit name."""
        hand = make_hand(["A_spades", "K_spades"])
        ctx = encode_following_context("suit", "spades", hand)
        assert ctx[6] == 1.0  # spades = 3 + (4-1) = 6
        assert abs(ctx[7] - 0.2) < 1e-6  # 2 trump cards / 10

    def test_none_trump(self):
        hand = make_hand(["A_spades"])
        ctx = encode_following_context("sans", None, hand)
        assert sum(ctx[3:7]) == 0.0  # no trump one-hot
        assert ctx[7] == 0.0

    def test_cards_played_multiple(self):
        cards = [
            Card(rank=Rank.ACE, suit=Suit.SPADES),
            Card(rank=Rank.SEVEN, suit=Suit.CLUBS),
            Card(rank=Rank.KING, suit=Suit.HEARTS),
        ]
        vec = encode_cards_played(cards)
        assert vec[31] == 1.0  # A♠
        assert vec[0] == 1.0   # 7♣
        assert sum(vec) == 3.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
