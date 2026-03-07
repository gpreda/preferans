"""Feature encoding: cards → numpy arrays for neural network input.

Card indexing: 32 cards total (4 suits × 8 ranks).
  index = (suit.value - 1) * 8 + (rank.value - 1)
  Suit: CLUBS=1, DIAMONDS=2, HEARTS=3, SPADES=4
  Rank: SEVEN=1, EIGHT=2, ..., ACE=8
"""

import sys
import os
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))

from models import Card, Suit, Rank, SUIT_NAMES, NAME_TO_SUIT, NAME_TO_RANK


# ---------------------------------------------------------------------------
# Card indexing
# ---------------------------------------------------------------------------

def card_to_index(card: Card) -> int:
    """Map a Card object to index 0-31."""
    return (card.suit.value - 1) * 8 + (card.rank.value - 1)


def card_id_to_index(card_id: str) -> int:
    """Map a card id string like 'A_spades' to index 0-31."""
    return card_to_index(Card.from_id(card_id))


# ---------------------------------------------------------------------------
# Hand features (56-dim)
# ---------------------------------------------------------------------------

def encode_hand(cards: list) -> np.ndarray:
    """Encode a hand of Card objects into a 56-dimensional feature vector.

    Layout:
      [0:32]  - card presence binary (32)
      [32:36] - suit distribution, each = count/10 (4)
      [36:44] - rank histogram, each = count/4 (8)
      [44:56] - derived features (12)
    """
    feat = np.zeros(56, dtype=np.float32)

    # Card presence (0-31)
    suit_counts = [0, 0, 0, 0]  # clubs, diamonds, hearts, spades
    rank_counts = [0] * 8       # seven through ace
    ace_count = 0
    high_card_count = 0  # rank >= Queen (6)
    suits_present = set()
    total_rank_sum = 0

    for c in cards:
        idx = card_to_index(c)
        feat[idx] = 1.0
        si = c.suit.value - 1
        ri = c.rank.value - 1
        suit_counts[si] += 1
        rank_counts[ri] += 1
        suits_present.add(si)
        total_rank_sum += c.rank.value
        if c.rank.value == 8:
            ace_count += 1
        if c.rank.value >= 6:
            high_card_count += 1

    # Suit distribution (32-35)
    for i in range(4):
        feat[32 + i] = suit_counts[i] / 10.0

    # Rank histogram (36-43)
    for i in range(8):
        feat[36 + i] = rank_counts[i] / 4.0

    # Derived features (44-55)
    num_suits = len(suits_present)
    longest_suit = max(suit_counts) if suit_counts else 0
    has_void = 1.0 if 0 in suit_counts else 0.0
    has_singleton = 1.0 if 1 in suit_counts else 0.0

    # Unsupported kings: king without ace in same suit
    num_unsupported_kings = 0
    # A-K combos: ace and king in same suit
    num_ak_combos = 0
    for si in range(4):
        suit_enum = Suit(si + 1)
        suit_cards = [c for c in cards if c.suit == suit_enum]
        has_ace = any(c.rank.value == 8 for c in suit_cards)
        has_king = any(c.rank.value == 7 for c in suit_cards)
        if has_king and not has_ace:
            num_unsupported_kings += 1
        if has_ace and has_king:
            num_ak_combos += 1

    # Best trump suit analysis
    best_trump_length = 0
    best_trump_top_rank = 0
    for si in range(4):
        if suit_counts[si] > best_trump_length or (
            suit_counts[si] == best_trump_length and
            _top_rank_in_suit(cards, Suit(si + 1)) > best_trump_top_rank
        ):
            best_trump_length = suit_counts[si]
            best_trump_top_rank = _top_rank_in_suit(cards, Suit(si + 1))

    feat[44] = ace_count / 4.0
    feat[45] = high_card_count / 10.0
    feat[46] = num_suits / 4.0
    feat[47] = longest_suit / 10.0
    feat[48] = has_void
    feat[49] = has_singleton
    feat[50] = num_unsupported_kings / 4.0
    feat[51] = num_ak_combos / 4.0
    feat[52] = best_trump_length / 10.0
    feat[53] = best_trump_top_rank / 8.0
    feat[54] = total_rank_sum / 80.0
    feat[55] = len(cards) / 12.0

    return feat


def _top_rank_in_suit(cards: list, suit: Suit) -> int:
    """Return highest rank value in a suit, or 0 if no cards of that suit."""
    ranks = [c.rank.value for c in cards if c.suit == suit]
    return max(ranks) if ranks else 0


# ---------------------------------------------------------------------------
# Per-card features (14-dim)
# ---------------------------------------------------------------------------

def encode_card(card: Card, suit_counts: dict | None = None,
                is_talon: bool = False, is_trump: bool = False,
                is_led_suit: bool = False) -> np.ndarray:
    """Encode a single card into a 14-dimensional feature vector.

    Layout:
      [0:8]  - rank one-hot (8)
      [8:12] - suit one-hot (4)
      [12]   - is_talon flag (1)
      [13]   - suit count normalized (1)
    """
    feat = np.zeros(14, dtype=np.float32)

    # Rank one-hot (0-7)
    feat[card.rank.value - 1] = 1.0

    # Suit one-hot (8-11)
    feat[8 + card.suit.value - 1] = 1.0

    # Talon flag
    feat[12] = 1.0 if is_talon else 0.0

    # Suit count normalized
    if suit_counts is not None:
        count = suit_counts.get(card.suit, 0)
        feat[13] = count / 10.0
    else:
        feat[13] = 0.0

    return feat


def get_suit_counts(cards: list) -> dict:
    """Count cards per suit. Returns {Suit: int}."""
    counts = {}
    for c in cards:
        counts[c.suit] = counts.get(c.suit, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Context encoders
# ---------------------------------------------------------------------------

def encode_contract_context(winner_bid_value: int, is_in_hand: bool,
                            legal_levels: list) -> np.ndarray:
    """Encode contract declaration context (4-dim).

    [0] winner_bid_value / 7
    [1] is_in_hand flag
    [2] min legal level / 7
    [3] max legal level / 7
    """
    feat = np.zeros(4, dtype=np.float32)
    feat[0] = winner_bid_value / 7.0
    feat[1] = 1.0 if is_in_hand else 0.0
    feat[2] = min(legal_levels) / 7.0 if legal_levels else 0.0
    feat[3] = max(legal_levels) / 7.0 if legal_levels else 0.0
    return feat


def _suit_to_int(suit_val) -> int:
    """Convert a suit value (Suit enum, int, or string name) to 1-4, or 0 if invalid."""
    if suit_val is None:
        return 0
    if isinstance(suit_val, Suit):
        return suit_val.value
    if isinstance(suit_val, int):
        return suit_val if 1 <= suit_val <= 4 else 0
    if isinstance(suit_val, str) and suit_val in NAME_TO_SUIT:
        return NAME_TO_SUIT[suit_val].value
    return 0


def encode_following_context(contract_type: str, trump_suit,
                             hand: list) -> np.ndarray:
    """Encode whisting/following context (8-dim).

    [0:3] contract_type one-hot (suit/betl/sans)
    [3:7] trump suit one-hot (clubs/diamonds/hearts/spades)
    [7]   trump cards in hand / 10
    """
    feat = np.zeros(8, dtype=np.float32)

    # Contract type one-hot
    ct_map = {"suit": 0, "betl": 1, "sans": 2}
    if contract_type in ct_map:
        feat[ct_map[contract_type]] = 1.0

    # Trump suit one-hot
    tv = _suit_to_int(trump_suit)
    if tv:
        feat[3 + tv - 1] = 1.0

    # Trump cards in hand
    if tv and hand:
        trump_count = sum(1 for c in hand if c.suit.value == tv)
        feat[7] = trump_count / 10.0

    return feat


def encode_calling_context(contract_type: str, trump_suit,
                           hand: list, other_defender_passed: bool,
                           is_counter_subphase: bool,
                           contract_level: int) -> np.ndarray:
    """Encode calling context (12-dim).

    [0:3]  contract_type one-hot (suit/betl/sans)
    [3:7]  trump suit one-hot (4)
    [7]    trump_count / 10
    [8]    other_defender_passed (1)
    [9]    is_counter_subphase (1)
    [10]   contract_level / 7
    [11]   num_aces / 4
    """
    feat = np.zeros(12, dtype=np.float32)

    ct_map = {"suit": 0, "betl": 1, "sans": 2}
    if contract_type in ct_map:
        feat[ct_map[contract_type]] = 1.0

    tv = _suit_to_int(trump_suit)
    if tv:
        feat[3 + tv - 1] = 1.0

    if tv and hand:
        trump_count = sum(1 for c in hand if c.suit.value == tv)
        feat[7] = trump_count / 10.0

    feat[8] = 1.0 if other_defender_passed else 0.0
    feat[9] = 1.0 if is_counter_subphase else 0.0
    feat[10] = contract_level / 7.0
    feat[11] = sum(1 for c in hand if c.rank.value == 8) / 4.0 if hand else 0.0

    return feat


def encode_countering_context(contract_type: str, trump_suit,
                              hand: list, is_declarer_responding: bool,
                              contract_level: int,
                              num_followers: int) -> np.ndarray:
    """Encode countering context (12-dim).

    [0:3]  contract_type one-hot (suit/betl/sans)
    [3:7]  trump suit one-hot (4)
    [7]    trump_count / 10
    [8]    is_declarer_responding (1)
    [9]    contract_level / 7
    [10]   num_aces / 4
    [11]   num_followers / 2
    """
    feat = np.zeros(12, dtype=np.float32)

    ct_map = {"suit": 0, "betl": 1, "sans": 2}
    if contract_type in ct_map:
        feat[ct_map[contract_type]] = 1.0

    tv = _suit_to_int(trump_suit)
    if tv:
        feat[3 + tv - 1] = 1.0

    if tv and hand:
        trump_count = sum(1 for c in hand if c.suit.value == tv)
        feat[7] = trump_count / 10.0

    feat[8] = 1.0 if is_declarer_responding else 0.0
    feat[9] = contract_level / 7.0
    feat[10] = sum(1 for c in hand if c.rank.value == 8) / 4.0 if hand else 0.0
    feat[11] = num_followers / 2.0

    return feat


# ---------------------------------------------------------------------------
# Aggressiveness scoring
# ---------------------------------------------------------------------------

AGGRESSIVENESS_SCORES = {
    "bid_pass": 0.0, "bid_game": 0.4, "bid_in_hand": 0.7,
    "bid_betl": 0.8, "bid_sans": 0.9,
    "follow_pass": 0.0, "follow_follow": 0.5,
    "call": 0.8, "counter": 1.0, "double_counter": 1.0,
}


def compute_aggressiveness(decision_keys: list) -> float:
    """Compute per-game aggressiveness from a list of decision keys.

    Each key should be like 'bid_pass', 'follow_follow', 'call', etc.
    Returns average score in [0, 1], or 0.5 if no decisions.
    """
    if not decision_keys:
        return 0.5
    total = sum(AGGRESSIVENESS_SCORES.get(k, 0.5) for k in decision_keys)
    return total / len(decision_keys)


def encode_card_play_context(is_declarer: bool, trump_suit,
                              trick_num: int, is_leading: bool,
                              trick_cards_count: int, led_suit,
                              contract_type: str, hand_size: int) -> np.ndarray:
    """Encode card play context (16-dim).

    [0]    is_declarer
    [1:5]  trump suit one-hot (4)
    [5]    trick_num / 10
    [6]    is_leading
    [7]    trick_cards_count / 3
    [8:12] led suit one-hot (4)
    [12:15] contract type one-hot (suit/betl/sans)
    [15]   hand_size / 10
    """
    feat = np.zeros(16, dtype=np.float32)

    feat[0] = 1.0 if is_declarer else 0.0

    # Trump suit one-hot
    tv = _suit_to_int(trump_suit)
    if tv:
        feat[1 + tv - 1] = 1.0

    feat[5] = trick_num / 10.0
    feat[6] = 1.0 if is_leading else 0.0
    feat[7] = trick_cards_count / 3.0

    # Led suit one-hot
    lv = _suit_to_int(led_suit)
    if lv:
        feat[8 + lv - 1] = 1.0

    # Contract type one-hot
    ct_map = {"suit": 0, "betl": 1, "sans": 2}
    if contract_type in ct_map:
        feat[12 + ct_map[contract_type]] = 1.0

    feat[15] = hand_size / 10.0

    return feat


def encode_cards_played(cards: list) -> np.ndarray:
    """Encode a list of played cards as a 32-dim binary vector."""
    feat = np.zeros(32, dtype=np.float32)
    for c in cards:
        feat[card_to_index(c)] = 1.0
    return feat
