"""Play a single Preferans game with three RandomMove players and log every move."""

import os
import sys
import random
import datetime
import time
from dataclasses import dataclass, field
from functools import cmp_to_key

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server"))

from models import (
    Game, Player, PlayerType, RoundPhase, ContractType, Suit,
    SUIT_NAMES, RANK_NAMES, Card,
)
from game_engine_service import GameSession


# ---------------------------------------------------------------------------
# Player base & RandomMove strategy
# ---------------------------------------------------------------------------

class BasePlayer:
    """Base class for player strategies."""

    def __init__(self, name: str):
        self.name = name
        self.last_bid_intent = ""

    # ------------------------------------------------------------------
    # Decision routines — override these for strategic behaviour
    # ------------------------------------------------------------------

    def bid_intent(self, hand, legal_bids) -> dict:
        """Decide bidding intent.

        Args:
            hand: list of Card objects in the player's hand.
            legal_bids: list of dicts describing legal bid options.

        Returns a dict with:
            "bid"    : the chosen bid dict (from *legal_bids*)
            "intent" : short human-readable reason string
        """
        raise NotImplementedError

    def discard_decision(self, hand_card_ids, talon_card_ids) -> dict:
        """Decide which 2 cards to discard from the 12 (hand + talon).

        Args:
            hand_card_ids: list of card id strings in hand.
            talon_card_ids: list of card id strings in the talon.

        Returns a dict with:
            "discard" : list of 2 card id strings to discard
            "intent"  : short human-readable reason string
        """
        raise NotImplementedError

    def bid_decision(self, hand, legal_levels, winner_bid) -> dict:
        """Declare the contract after discarding cards.

        Args:
            hand: list of Card objects in the player's hand (after exchange).
            legal_levels: list of legal contract levels.
            winner_bid: the winning auction bid object.

        Returns a dict with:
            "contract_type" : str ("suit", "sans", "betl")
            "trump"         : trump suit name (str) or None for sans/betl
            "level"         : int contract level
            "intent"        : short human-readable reason string
        """
        raise NotImplementedError

    def _should_follow_heuristic(self, hand, trump_suit):
        """Deterministic follow heuristic based on trump tricks and side-suit reasons.

        Returns (should_follow: bool, n_trump_tricks: int, sum_reasons: float)
        or (False, 0, 0.0) if not applicable (no hand or no trump).

        Rules (any player):
          follow if trump_tricks >= 2
          follow if sum_reasons >= 3
          follow if trump_tricks >= 1 AND sum_reasons >= 2
        """
        if not hand or trump_suit is None:
            return False, 0, 0.0
        from simulate import _cards_by_suit, _count_trump_tricks, _suit_reason
        by_suit = _cards_by_suit(hand)
        trump_cards = by_suit.get(trump_suit, [])
        n_trump_tricks = _count_trump_tricks(trump_cards)
        sum_reasons = 0.0
        for s, cards in by_suit.items():
            if s != trump_suit:
                sum_reasons += _suit_reason(cards)
        if len(trump_cards) >= 3 and n_trump_tricks == 0:
            sum_reasons += 0.25
        should = (n_trump_tricks >= 2
                  or sum_reasons >= 3
                  or (n_trump_tricks >= 1 and sum_reasons >= 2))
        return should, n_trump_tricks, sum_reasons

    def following_decision(self, hand, contract_type, trump_suit, legal_actions) -> dict:
        """Decide whether to follow or pass a declaration.

        Args:
            hand: list of Card objects in the player's hand.
            contract_type: str  ("suit", "sans", "betl").
            trump_suit: suit enum value or None for sans/betl.
            legal_actions: list of dicts with "action" key.

        Returns a dict with:
            "action" : one of the action strings from *legal_actions*
            "intent" : short human-readable reason string
        """
        raise NotImplementedError

    def decide_to_call(self, hand, contract_type, trump_suit, legal_actions) -> dict:
        """Decide whether to 'call' when the other defender passed.

        Called when this player is the second defender and the first
        defender passed, making "call" available alongside pass/follow/counter.
        Calling means this player becomes principal follower, responsible
        for both defenders' combined tricks — higher risk than follow.

        Args:
            hand: list of Card objects in the player's hand.
            contract_type: str  ("suit", "sans", "betl").
            trump_suit: suit enum value or None for sans/betl.
            legal_actions: list of dicts with "action" key.

        Returns a dict with:
            "action" : one of "call", "follow", "pass", "counter"
            "intent" : short human-readable reason string
        """
        raise NotImplementedError

    def decide_to_counter(self, hand, contract_type, trump_suit, legal_actions) -> dict:
        """Decide whether to 'counter' (double the stakes).

        Called in two contexts:
        - Counter sub-phase: after all initial declarations, before play.
          Legal actions: start_game, counter (+ call for SUIT if a defender passed).
        - Declarer responding: start_game or double_counter.

        Args:
            hand: list of Card objects in the player's hand.
            contract_type: str  ("suit", "sans", "betl").
            trump_suit: suit enum value or None for sans/betl.
            legal_actions: list of dicts with "action" key.

        Returns a dict with:
            "action" : one of the action strings from *legal_actions*
            "intent" : short human-readable reason string
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Discard scoring
    # ------------------------------------------------------------------

    @staticmethod
    def score_discard_cards(card_ids, contract_type, trump_suit=None):
        """Score each card for discard desirability. Higher score = discard first.

        Args:
            card_ids: list of card id strings (e.g. 'A_spades')
            contract_type: 'suit', 'sans', or 'betl'
            trump_suit: suit name for suit contracts (trump cards get score ~0)

        Returns: dict mapping card_id -> float score
        """
        _RV = {'7': 0, '8': 1, '9': 2, '10': 3, 'J': 4, 'Q': 5, 'K': 6, 'A': 7}

        def ct(rank):
            return 'x' if rank in ('7', '8', '9', '10') else rank

        # Group by suit, sorted ascending
        suits = {}
        for cid in card_ids:
            rank, suit = cid.split('_')
            suits.setdefault(suit, []).append((rank, cid))
        for s in suits:
            suits[s].sort(key=lambda x: _RV[x[0]])

        scores = {}

        if contract_type == 'betl':
            BETL_REF = [0, 2, 4, 6, 7, 7, 7, 7]  # rank values for 7, 9, J, K, A, A, A, A
            BETL_1 = {'8': 10, '9': 30, '10': 50, 'J': 60, 'Q': 80, 'K': 90, 'A': 100}

            for suit, cards in suits.items():
                sl = len(cards)
                # Find gap-free prefix length
                plen = 0
                for i, (rank, _) in enumerate(cards):
                    if i < len(BETL_REF) and _RV[rank] <= BETL_REF[i]:
                        plen = i + 1
                    else:
                        break

                # Reduction table by suit length
                if sl >= 5:
                    red = [5 * j for j in range(sl)]
                elif sl == 4:
                    red = [0, 10, 20, 25]
                elif sl == 3:
                    red = [0, 10, 20]
                elif sl == 2:
                    red = [0, 10]
                else:
                    red = [0]

                for i, (rank, cid) in enumerate(cards):
                    if i < plen:
                        base = 0.0
                    else:
                        base = float(BETL_1.get(rank, 100))
                        base -= red[i] if i < len(red) else 5 * i
                        base = max(base, 0.0)
                    scores[cid] = base + i * 0.1 + (10 - sl) / 10

        else:
            # Suit / Sans scoring
            S1 = {'x': 100, 'J': 90, 'Q': 80, 'K': 50, 'A': 0}
            S2 = {
                ('x', 'x'): (80, 80), ('x', 'J'): (70, 60),
                ('x', 'Q'): (60, 50), ('J', 'Q'): (60, 50),
                ('x', 'K'): (20, 10), ('J', 'K'): (20, 10),
                ('x', 'A'): (70, 0),  ('J', 'A'): (50, 0),
                ('Q', 'K'): (10, 5),  ('Q', 'A'): (30, 0),
                ('K', 'A'): (5, 0),
            }
            S3 = {'A': 0, 'K': 10, 'Q': 20, 'J': 40, 'x': 50}
            S4 = {'A': 0, 'K': 5, 'Q': 10, 'J': 20, 'x': 25}

            for suit, cards in suits.items():
                sl = len(cards)
                if contract_type == 'suit' and suit == trump_suit:
                    for i, (rank, cid) in enumerate(cards):
                        scores[cid] = (2 * sl - i) * 0.1
                    continue

                if sl == 1:
                    rank, cid = cards[0]
                    scores[cid] = float(S1[ct(rank)]) + (2 * sl - 0) * 0.1
                elif sl == 2:
                    lo_t, hi_t = ct(cards[0][0]), ct(cards[1][0])
                    s_lo, s_hi = S2.get((lo_t, hi_t), (50, 50))
                    scores[cards[0][1]] = float(s_lo) + (2 * sl - 0) * 0.1
                    scores[cards[1][1]] = float(s_hi) + (2 * sl - 1) * 0.1
                elif sl == 3:
                    for i, (rank, cid) in enumerate(cards):
                        scores[cid] = float(S3[ct(rank)]) + (2 * sl - i) * 0.1
                else:
                    for i, (rank, cid) in enumerate(cards):
                        scores[cid] = float(S4[ct(rank)]) + (2 * sl - i) * 0.1

                # Bonus to lower cards when leading card is A or K
                if sl >= 2:
                    top_rank = cards[-1][0]
                    bonus = 10 if top_rank == 'A' else 5 if top_rank == 'K' else 0
                    if bonus:
                        for rank, cid in cards[:-1]:
                            scores[cid] += bonus

                # Additional bonus when two leading cards are AK or KQ
                if sl >= 3:
                    top2 = (cards[-2][0], cards[-1][0])
                    bonus2 = 5 if top2 in (('K', 'A'), ('Q', 'K')) else 0
                    if bonus2:
                        for rank, cid in cards[:-2]:
                            scores[cid] += bonus2

        return scores

    # ------------------------------------------------------------------
    # Action routines — these call the decision routines above
    # ------------------------------------------------------------------

    def choose_bid(self, legal_bids: list[dict]) -> dict:
        raise NotImplementedError

    def choose_discard(self, hand_card_ids: list[str], talon_card_ids: list[str]) -> list[str]:
        all_ids = hand_card_ids + talon_card_ids
        scores = self.score_discard_cards(all_ids, 'suit')
        sorted_cards = sorted(all_ids, key=lambda c: scores[c], reverse=True)
        return sorted_cards[:2]

    def choose_contract(self, legal_levels: list[int], hand, winner_bid) -> tuple[str, str | None, int]:
        """Return (contract_type, trump_suit_name_or_None, level)."""
        raise NotImplementedError

    def choose_whist_action(self, legal_actions: list[dict]) -> str:
        raise NotImplementedError

    def choose_card(self, legal_cards) -> str:
        raise NotImplementedError

    # Ranked candidates — populated by subclasses that support it
    # _ranked_cards: list of card id strings, best first
    # _ranked_discards: list of (discard_pair, contract, score) tuples, best first


class RandomMovePlayer(BasePlayer):
    """Picks a random legal move in every situation."""

    def __init__(self, name: str, seed: int | None = None):
        super().__init__(name)
        self.rng = random.Random(seed)

    def bid_intent(self, hand, legal_bids):
        bid = self.rng.choice(legal_bids)
        return {"bid": bid, "intent": "random"}

    def discard_decision(self, hand_card_ids, talon_card_ids):
        all_ids = hand_card_ids + talon_card_ids
        return {"discard": self.rng.sample(all_ids, 2), "intent": "random"}

    def bid_decision(self, hand, legal_levels, winner_bid):
        level = self.rng.choice(legal_levels)
        if level == 6:
            return {"contract_type": "betl", "trump": None, "level": 6, "intent": "random betl"}
        if level == 7:
            return {"contract_type": "sans", "trump": None, "level": 7, "intent": "random sans"}
        min_bid = winner_bid.effective_value if winner_bid else 0
        suit_bid = {Suit.SPADES: 2, Suit.DIAMONDS: 3, Suit.HEARTS: 4, Suit.CLUBS: 5}
        valid_suits = [SUIT_NAMES[c.suit] for c in hand if suit_bid.get(c.suit, 0) >= min_bid]
        valid_suits = list(set(valid_suits))
        if not valid_suits:
            valid_suits = [SUIT_NAMES[s] for s, v in suit_bid.items() if v >= min_bid]
        trump = self.rng.choice(valid_suits) if valid_suits else "spades"
        return {"contract_type": "suit", "trump": trump, "level": level, "intent": "random suit"}

    def following_decision(self, hand, contract_type, trump_suit, legal_actions):
        action = self.rng.choice(legal_actions)["action"]
        return {"action": action, "intent": "random"}

    def decide_to_call(self, hand, contract_type, trump_suit, legal_actions):
        action = self.rng.choice(legal_actions)["action"]
        return {"action": action, "intent": "random call decision"}

    def decide_to_counter(self, hand, contract_type, trump_suit, legal_actions):
        action = self.rng.choice(legal_actions)["action"]
        return {"action": action, "intent": "random counter decision"}

    def choose_bid(self, legal_bids):
        hand = getattr(self, '_hand', [])
        decision = self.bid_intent(hand, legal_bids)
        self.last_bid_intent = decision["intent"]
        return decision["bid"]

    def choose_discard(self, hand_card_ids, talon_card_ids):
        decision = self.discard_decision(hand_card_ids, talon_card_ids)
        return decision["discard"]

    def choose_contract(self, legal_levels, hand, winner_bid):
        decision = self.bid_decision(hand, legal_levels, winner_bid)
        return decision["contract_type"], decision["trump"], decision["level"]

    def choose_whist_action(self, legal_actions):
        hand = getattr(self, '_hand', [])
        contract_type = getattr(self, '_contract_type', None)
        trump_suit = getattr(self, '_trump_suit', None)
        action_types = [a["action"] for a in legal_actions]

        if "double_counter" in action_types:
            # Declarer responding to a counter
            decision = self.decide_to_counter(hand, contract_type, trump_suit, legal_actions)
        elif "start_game" in action_types and "counter" in action_types:
            # Counter sub-phase for a defender
            decision = self.decide_to_counter(hand, contract_type, trump_suit, legal_actions)
        elif "call" in action_types:
            # Second defender, first passed: pass/follow/call/counter available
            decision = self.decide_to_call(hand, contract_type, trump_suit, legal_actions)
        else:
            # Basic follow/pass (first defender or no special actions)
            decision = self.following_decision(hand, contract_type, trump_suit, legal_actions)

        return decision["action"]

    def choose_card(self, legal_cards):
        return self.rng.choice(legal_cards).id


class RandomMoveNoBetlPlayer(RandomMovePlayer):
    """Picks a random legal move, but never bids Betl. Uses weighted bidding."""

    W_PASS = 50
    W_GAME = 40
    W_IN_HAND = 15
    W_SANS = 5

    def bid_intent(self, hand, legal_bids):
        filtered = [b for b in legal_bids if b.get("bid_type") != "betl"]
        if not filtered:
            filtered = legal_bids
        weights = []
        for b in filtered:
            bt = b.get("bid_type")
            if bt == "pass":
                weights.append(self.W_PASS)
            elif bt == "game" or bt == "in_hand" and b.get("value", 0) > 0:
                weights.append(self.W_GAME)
            elif bt == "in_hand":
                weights.append(self.W_IN_HAND)
            elif bt == "sans":
                weights.append(self.W_SANS)
            else:
                weights.append(self.W_GAME)
        bid = self.rng.choices(filtered, weights=weights, k=1)[0]
        return {"bid": bid, "intent": "weighted random (no betl)"}


class WeightedRandomPlayer(RandomMovePlayer):
    """Random player with custom weights for each bid type."""

    def __init__(self, name: str, seed: int | None = None,
                 w_pass: int = 50, w_game: int = 40,
                 w_in_hand: int = 15, w_betl: int = 5, w_sans: int = 5):
        super().__init__(name, seed=seed)
        self.W_PASS = w_pass
        self.W_GAME = w_game
        self.W_IN_HAND = w_in_hand
        self.W_BETL = w_betl
        self.W_SANS = w_sans

    def _weight_for(self, bid):
        bt = bid.get("bid_type")
        if bt == "pass":
            return self.W_PASS
        elif bt == "game" or bt == "in_hand" and bid.get("value", 0) > 0:
            return self.W_GAME
        elif bt == "in_hand":
            return self.W_IN_HAND
        elif bt == "betl":
            return self.W_BETL
        elif bt == "sans":
            return self.W_SANS
        return self.W_GAME

    def bid_intent(self, hand, legal_bids):
        weights = [self._weight_for(b) for b in legal_bids]
        bid = self.rng.choices(legal_bids, weights=weights, k=1)[0]
        return {"bid": bid, "intent": "weighted random"}

    def weights_str(self):
        return (f"pass={self.W_PASS} game={self.W_GAME} in_hand={self.W_IN_HAND} "
                f"betl={self.W_BETL} sans={self.W_SANS}")


def _random_weights(rng):
    """Generate random bid weights: pass 30-70, game 20-60, in_hand 5-30, betl 1-15, sans 1-15."""
    return {
        "w_pass": rng.randint(30, 70),
        "w_game": rng.randint(20, 60),
        "w_in_hand": rng.randint(5, 30),
        "w_betl": rng.randint(1, 15),
        "w_sans": rng.randint(1, 15),
    }


# ---------------------------------------------------------------------------
# Betl hand analysis helpers (gap-based suit safety)
# ---------------------------------------------------------------------------

def _ids_to_cards(card_ids):
    """Convert card id strings to Card objects."""
    return [Card.from_id(cid) for cid in card_ids]


# Suit bid values: the inherent level of each suit contract
_SUIT_BID_VALUE = {Suit.SPADES: 2, Suit.DIAMONDS: 3, Suit.HEARTS: 4, Suit.CLUBS: 5}


def betl_suit_safety(held_ranks):
    """Per-suit safety analysis for betl.

    held_ranks: sorted list of Rank int values (1=7, 2=8, ..., 8=A).

    A card is "dangerous" if opponents cannot beat it — i.e. the highest
    held card has no opponent card above it (gap to top = 0). Consecutive
    cards forming an unbroken chain from the top are all dangerous.

    Examples:
      {1,3,5} (7,9,J): highest=5, gap=8-5=3 → safe
      {2} (8):          highest=2, gap=8-2=6 → safe
      {8} (A):          highest=8, gap=8-8=0 → danger=[8]
      {7,8} (K,A):      highest=8, gap=0, chain K-A → danger=[7,8]
      {1,3,5,8} (7,9,J,A): highest=8, gap=0 → danger=[8]
      {7} (K):          highest=7, gap=8-7=1 → safe (A covers)
      {1,2,3} (7,8,9):  highest=3, gap=8-3=5 → safe
    """
    if not held_ranks:
        return {"safe": True, "danger_cards": [], "can_lead": True, "num_cards": 0}

    ranks = sorted(held_ranks)
    danger_cards = []

    # Check highest card: if gap to top is 0, it's dangerous
    highest = ranks[-1]
    highest_gap = 8 - highest
    if highest_gap == 0:
        # Ace (rank 8) — always dangerous, and check for downward chain
        danger_cards.append(highest)
        # Walk down: consecutive cards below the highest are also dangerous
        for i in range(len(ranks) - 2, -1, -1):
            if ranks[i] == ranks[i + 1] - 1:
                danger_cards.append(ranks[i])
            else:
                break
        danger_cards.sort()

    can_lead = highest_gap >= 1

    return {
        "safe": len(danger_cards) == 0,
        "danger_cards": danger_cards,
        "can_lead": can_lead,
        "num_cards": len(ranks),
    }


def betl_hand_analysis(hand):
    """Aggregate per-suit betl safety analysis.

    hand: list of Card objects with .suit and .rank attributes.
    Returns dict with safe_suits, danger_count, danger_list, has_ace,
    can_lead, void_count, max_suit_len, details.
    """
    all_ranks = [c.rank for c in hand]
    suit_ranks = {}
    for c in hand:
        suit_ranks.setdefault(c.suit, []).append(c.rank)

    all_suits = {1, 2, 3, 4}  # Clubs, Diamonds, Hearts, Spades
    void_suits = all_suits - set(suit_ranks.keys())

    details = {}
    safe_suits = len(void_suits)  # voids are safe
    danger_count = 0
    danger_list = []
    has_ace = False
    any_can_lead = False
    max_suit_len = 0

    suit_name_map = {1: "clubs", 2: "diamonds", 3: "hearts", 4: "spades"}

    for suit_val, ranks in suit_ranks.items():
        analysis = betl_suit_safety(ranks)
        details[suit_val] = analysis
        if analysis["safe"]:
            safe_suits += 1
        danger_count += len(analysis["danger_cards"])
        for r in analysis["danger_cards"]:
            danger_list.append((suit_name_map.get(suit_val, str(suit_val)), r))
        if analysis["can_lead"] and analysis["num_cards"] > 0:
            any_can_lead = True
        if analysis["num_cards"] > max_suit_len:
            max_suit_len = analysis["num_cards"]
        if 8 in ranks:
            has_ace = True

    max_rank = max(all_ranks) if all_ranks else 0
    high_card_count = sum(1 for r in all_ranks if r >= 6)  # Q/K/A

    return {
        "safe_suits": safe_suits,
        "danger_count": danger_count,
        "danger_list": danger_list,
        "has_ace": has_ace,
        "can_lead": any_can_lead,
        "void_count": len(void_suits),
        "max_suit_len": max_suit_len,
        "max_rank": max_rank,
        "high_card_count": high_card_count,
        "details": details,
    }


# ---------------------------------------------------------------------------
# Shared helper_ functions for player strategy tuning
# ---------------------------------------------------------------------------

def _helper_suit_groups(hand):
    """Group cards by suit → {suit_value: [Card, ...]} sorted high→low."""
    groups = {}
    for c in hand:
        groups.setdefault(c.suit, []).append(c)
    for s in groups:
        groups[s].sort(key=lambda c: c.rank, reverse=True)
    return groups


def helper_hand_shape(hand):
    """Distribution pattern sorted by length desc.

    Returns {"shape": [5,3,1,1], "suit_lengths": {suit: len},
             "is_balanced": bool, "is_distributional": bool}.
    Balanced = no suit >4, no void, no singleton.
    """
    groups = _helper_suit_groups(hand)
    suit_lengths = {}
    for s in (1, 2, 3, 4):
        suit_lengths[s] = len(groups.get(s, []))
    lengths = sorted(suit_lengths.values(), reverse=True)
    max_len = lengths[0] if lengths else 0
    min_len = lengths[-1] if lengths else 0
    has_void = min_len == 0
    has_singleton = not has_void and lengths[-1] <= 1
    is_balanced = max_len <= 4 and not has_void and not has_singleton
    is_distributional = max_len >= 5 or has_void or has_singleton
    return {
        "shape": lengths,
        "suit_lengths": suit_lengths,
        "is_balanced": is_balanced,
        "is_distributional": is_distributional,
    }


def helper_losing_trick_count(hand, trump_suit=None):
    """Classic Losing Trick Count from bridge theory.

    Per suit: void=0, singleton A=0 else 1, doubleton AK=0 / Ax or Kx=1 / else 2,
    3+ cards: count missing from {A,K,Q} capped at 3.
    With trump: side voids/singletons count 0 losers (ruffable).
    Returns {"total": int, "per_suit": {suit: int}, "ltc_without_trump": int}.
    """
    groups = _helper_suit_groups(hand)
    per_suit = {}
    total = 0
    ltc_no_trump = 0

    for s in (1, 2, 3, 4):
        cards = groups.get(s, [])
        ranks = set(c.rank for c in cards)
        n = len(cards)

        if n == 0:
            losers = 0
        elif n == 1:
            losers = 0 if 8 in ranks else 1
        elif n == 2:
            if 8 in ranks and 7 in ranks:      # AK
                losers = 0
            elif 8 in ranks or 7 in ranks:      # Ax or Kx
                losers = 1
            else:
                losers = 2
        else:
            # 3+ cards: count missing top honors {A=8, K=7, Q=6}
            losers = sum(1 for h in (8, 7, 6) if h not in ranks)

        ltc_no_trump += losers

        # With trump, side voids and singletons are ruffable
        if trump_suit is not None and s != trump_suit:
            if n <= 1:
                losers = 0

        per_suit[s] = losers
        total += losers

    return {"total": total, "per_suit": per_suit, "ltc_without_trump": ltc_no_trump}


def helper_quick_tricks(hand):
    """Guaranteed winners regardless of opponent play.

    AK in same suit = 2, lone A (no K) = 1, KQ in same suit = 0.5.
    Returns {"total": float, "per_suit": {suit: float}}.
    """
    groups = _helper_suit_groups(hand)
    per_suit = {}
    total = 0.0

    for s in (1, 2, 3, 4):
        cards = groups.get(s, [])
        ranks = set(c.rank for c in cards)
        qt = 0.0
        if 8 in ranks and 7 in ranks:      # AK
            qt = 2.0
        elif 8 in ranks:                    # A without K
            qt = 1.0
        elif 7 in ranks and 6 in ranks:    # KQ
            qt = 0.5
        per_suit[s] = qt
        total += qt

    return {"total": total, "per_suit": per_suit}


def helper_control_count(hand):
    """Ace=2 controls, King=1 control per suit. Max 2 per suit.

    Returns {"total": int, "per_suit": {suit: int}, "suits_controlled": int}.
    """
    groups = _helper_suit_groups(hand)
    per_suit = {}
    total = 0
    suits_controlled = 0

    for s in (1, 2, 3, 4):
        cards = groups.get(s, [])
        ranks = set(c.rank for c in cards)
        ctrl = 0
        if 8 in ranks:
            ctrl += 2
        if 7 in ranks and ctrl < 2:
            ctrl += 1
        per_suit[s] = ctrl
        total += ctrl
        if ctrl > 0:
            suits_controlled += 1

    return {"total": total, "per_suit": per_suit, "suits_controlled": suits_controlled}


def helper_honor_strength(hand, suit_val=None):
    """Weighted HCP: A=4, K=3, Q=2, J=1.

    Returns {"total": int, "per_suit": {suit: int}}.
    If suit_val given, only that suit.
    """
    _HCP = {8: 4, 7: 3, 6: 2, 5: 1}  # A=8, K=7, Q=6, J=5
    groups = _helper_suit_groups(hand)
    per_suit = {}
    total = 0

    suits = [suit_val] if suit_val is not None else [1, 2, 3, 4]
    for s in suits:
        cards = groups.get(s, [])
        pts = sum(_HCP.get(c.rank, 0) for c in cards)
        per_suit[s] = pts
        total += pts

    return {"total": total, "per_suit": per_suit}


def helper_honor_concentration(hand):
    """Measures whether honors are in long suits (good) or scattered (bad).

    For each suit: honor_points * suit_length_weight where weight scales
    with length (longer suit = higher weight).
    Returns {"score": float, "concentrated": bool}.
    Concentrated = top 2 suits hold >= 70% of total weighted honor points.
    """
    groups = _helper_suit_groups(hand)
    _HCP = {8: 4, 7: 3, 6: 2, 5: 1}
    total_hcp = sum(_HCP.get(c.rank, 0) for c in hand)
    if total_hcp == 0:
        return {"score": 0.0, "concentrated": False}

    # Weight: suit_length / total_cards gives natural concentration bias
    n_cards = len(hand) or 1
    suit_scores = []
    for s in (1, 2, 3, 4):
        cards = groups.get(s, [])
        suit_hcp = sum(_HCP.get(c.rank, 0) for c in cards)
        weight = len(cards) / n_cards
        suit_scores.append(suit_hcp * (1.0 + weight))

    suit_scores.sort(reverse=True)
    total_weighted = sum(suit_scores)
    if total_weighted == 0:
        return {"score": 0.0, "concentrated": False}

    top2 = sum(suit_scores[:2])
    score = top2 / total_weighted
    return {"score": round(score, 3), "concentrated": score >= 0.70}


def helper_suit_texture(hand, suit_val):
    """Evaluate intermediate cards — touching sequences and fillers.

    Touching sequences: AKQ, KQJ, QJ10, J109.
    Fillers: 10 or 9 backing up a higher honor in the same suit.
    Returns {"sequences": int, "fillers": int, "texture_score": float}.
    """
    groups = _helper_suit_groups(hand)
    cards = groups.get(suit_val, [])
    ranks = sorted([c.rank for c in cards], reverse=True)
    rank_set = set(ranks)

    # Count touching sequences of 3+ consecutive honors (rank >= 5 = J+)
    # Sequences: AKQ (8,7,6), KQJ (7,6,5), QJ10 (6,5,4), J109 (5,4,3)
    _SEQUENCES = [
        (8, 7, 6),  # AKQ
        (7, 6, 5),  # KQJ
        (6, 5, 4),  # QJ10
        (5, 4, 3),  # J109
    ]
    sequences = sum(1 for seq in _SEQUENCES if all(r in rank_set for r in seq))

    # Fillers: 10 (rank 4) or 9 (rank 3) backing up a higher honor (rank >= 5)
    fillers = 0
    has_honor = any(r >= 5 for r in ranks)
    if has_honor:
        if 4 in rank_set:  # 10
            fillers += 1
        if 3 in rank_set:  # 9
            fillers += 1

    texture_score = sequences * 1.0 + fillers * 0.5
    return {"sequences": sequences, "fillers": fillers, "texture_score": texture_score}


def helper_ruffing_potential(hand, trump_suit):
    """Count short/void non-trump suits and available trumps for ruffing.

    Returns {"void_count": int, "singleton_count": int, "doubleton_count": int,
             "trump_length": int, "ruff_tricks": float}.
    Ruff tricks: each void with trump available = 1.0, singleton = 0.5.
    """
    groups = _helper_suit_groups(hand)
    trump_length = len(groups.get(trump_suit, []))
    void_count = 0
    singleton_count = 0
    doubleton_count = 0

    for s in (1, 2, 3, 4):
        if s == trump_suit:
            continue
        n = len(groups.get(s, []))
        if n == 0:
            void_count += 1
        elif n == 1:
            singleton_count += 1
        elif n == 2:
            doubleton_count += 1

    # Ruff tricks: limited by available trumps beyond what's needed for trump tricks
    ruff_opportunities = void_count * 1.0 + singleton_count * 0.5
    # Can't ruff more times than we have trumps (minus 1 for leading)
    usable_trumps = max(0, trump_length - 1)
    ruff_tricks = min(ruff_opportunities, usable_trumps)

    return {
        "void_count": void_count,
        "singleton_count": singleton_count,
        "doubleton_count": doubleton_count,
        "trump_length": trump_length,
        "ruff_tricks": ruff_tricks,
    }


def helper_suit_stoppers(hand):
    """Classify stopper quality for each suit.

    "sure" = A, "probable" = Kx+, "possible" = Qxx+ or J10xx+, "none".
    Returns {"stoppers": {suit: str}, "sure_count": int, "total_stopped": int}.
    """
    groups = _helper_suit_groups(hand)
    stoppers = {}
    sure_count = 0
    total_stopped = 0

    for s in (1, 2, 3, 4):
        cards = groups.get(s, [])
        ranks = set(c.rank for c in cards)
        n = len(cards)

        if 8 in ranks:                          # Ace
            stoppers[s] = "sure"
            sure_count += 1
            total_stopped += 1
        elif 7 in ranks and n >= 2:             # Kx+
            stoppers[s] = "probable"
            total_stopped += 1
        elif 6 in ranks and n >= 3:             # Qxx+
            stoppers[s] = "possible"
            total_stopped += 1
        elif 5 in ranks and 4 in ranks and n >= 4:  # J10xx+
            stoppers[s] = "possible"
            total_stopped += 1
        elif n == 0:
            stoppers[s] = "none"  # void — no stopper needed in sans
        else:
            stoppers[s] = "none"

    return {"stoppers": stoppers, "sure_count": sure_count, "total_stopped": total_stopped}


def helper_expected_whist_ev(est_tricks, game_value, is_solo=False):
    """Calculate EV of whisting vs passing using actual scoring rules.

    Solo whister: >=2 tricks → +tricks*gv, <2 → -(gv*10) + tricks*gv.
    Paired (both follow): >=4 combined needed, but per-player need >=2.
    Pass = 0.
    Returns {"ev_whist": float, "ev_pass": 0, "should_whist": bool,
             "breakeven_tricks": float}.
    """
    gv = game_value

    if is_solo:
        # Solo whist: need 2 tricks to break even
        # Expected payoff with est_tricks
        if est_tricks >= 2.0:
            ev_whist = min(est_tricks, 5) * gv
        else:
            # Weighted: probability of getting 2+ vs less
            # Simple model: if est < 2, interpolate the penalty
            frac_above = max(0.0, est_tricks / 2.0)
            ev_win = min(est_tricks, 5) * gv * frac_above
            ev_lose = (-(gv * 10) + est_tricks * gv) * (1.0 - frac_above)
            ev_whist = ev_win + ev_lose
        breakeven = 2.0
    else:
        # Paired whist: combined need 4, individual need 2
        if est_tricks >= 2.0:
            ev_whist = min(est_tricks, 5) * gv
        else:
            frac_above = max(0.0, est_tricks / 2.0)
            ev_win = min(est_tricks, 5) * gv * frac_above
            ev_lose = (-(gv * 10) + est_tricks * gv) * (1.0 - frac_above)
            ev_whist = ev_win + ev_lose
        breakeven = 2.0

    return {
        "ev_whist": round(ev_whist, 2),
        "ev_pass": 0,
        "should_whist": ev_whist > 0,
        "breakeven_tricks": breakeven,
    }


def helper_declarer_ev(est_tricks, game_value, tricks_needed=6):
    """EV of declaring: P(make) * (+gv*10) + P(fail) * (-gv*10).

    Uses simple sigmoid approximation for probability of making.
    Returns {"ev_declare": float, "prob_make": float, "breakeven_tricks": float}.
    """
    import math
    gv = game_value

    # Sigmoid: at est_tricks == tricks_needed, prob = 0.5
    # Each extra trick above needed adds ~20% probability
    diff = est_tricks - tricks_needed
    prob_make = 1.0 / (1.0 + math.exp(-2.0 * diff))
    prob_make = max(0.01, min(0.99, prob_make))

    ev_declare = prob_make * (gv * 10) + (1.0 - prob_make) * (-(gv * 10))

    return {
        "ev_declare": round(ev_declare, 2),
        "prob_make": round(prob_make, 3),
        "breakeven_tricks": float(tricks_needed),
    }


def helper_whist_hand_classification(hand, declarer_trump=None):
    """Classify overall defensive quality.

    "strong": 2+ aces, AK combos, est >= 2.5 tricks.
    "moderate": 1 ace + shape, est >= 1.5 tricks.
    "weak": 1 ace or 0 aces, est >= 0.7 tricks.
    "junk": scattered, no aces, est < 0.7 tricks.
    Returns {"class": str, "aces": int, "ak_combos": int,
             "has_void": bool, "trump_length": int}.
    """
    groups = _helper_suit_groups(hand)
    aces = sum(1 for c in hand if c.rank == 8)
    ak_combos = 0
    has_void = False
    trump_length = 0

    for s in (1, 2, 3, 4):
        cards = groups.get(s, [])
        ranks = set(c.rank for c in cards)
        if len(cards) == 0:
            has_void = True
        if 8 in ranks and 7 in ranks:
            ak_combos += 1
        if declarer_trump is not None and s == declarer_trump:
            trump_length = len(cards)

    # Estimate defensive tricks (simplified)
    qt = helper_quick_tricks(hand)
    est = qt["total"]
    # Add partial credit for long suits and voids
    if has_void and declarer_trump is not None:
        est += 0.5
    for s in (1, 2, 3, 4):
        cards = groups.get(s, [])
        if len(cards) >= 4:
            est += 0.3

    if aces >= 2 and est >= 2.5:
        cls = "strong"
    elif aces >= 2 or (aces >= 1 and ak_combos >= 1 and est >= 1.5):
        cls = "moderate"
    elif aces >= 1 or est >= 0.7:
        cls = "weak"
    else:
        cls = "junk"

    return {
        "class": cls,
        "aces": aces,
        "ak_combos": ak_combos,
        "has_void": has_void,
        "trump_length": trump_length,
    }


def helper_hand_summary(hand, trump_suit=None):
    """One-call comprehensive analysis combining all helper functions.

    Returns a single dict with all key metrics so players can call this
    once per decision and use individual fields.
    """
    shape = helper_hand_shape(hand)
    ltc = helper_losing_trick_count(hand, trump_suit)
    qt = helper_quick_tricks(hand)
    controls = helper_control_count(hand)
    hcp = helper_honor_strength(hand)
    concentration = helper_honor_concentration(hand)
    stoppers = helper_suit_stoppers(hand)
    whist_class = helper_whist_hand_classification(hand, trump_suit)

    result = {
        "shape": shape["shape"],
        "suit_lengths": shape["suit_lengths"],
        "is_balanced": shape["is_balanced"],
        "is_distributional": shape["is_distributional"],
        "ltc": ltc["total"],
        "ltc_per_suit": ltc["per_suit"],
        "ltc_without_trump": ltc["ltc_without_trump"],
        "quick_tricks": qt["total"],
        "quick_tricks_per_suit": qt["per_suit"],
        "controls": controls["total"],
        "controls_per_suit": controls["per_suit"],
        "suits_controlled": controls["suits_controlled"],
        "hcp": hcp["total"],
        "hcp_per_suit": hcp["per_suit"],
        "honor_concentration": concentration["score"],
        "honors_concentrated": concentration["concentrated"],
        "stoppers": stoppers["stoppers"],
        "sure_stoppers": stoppers["sure_count"],
        "total_stopped": stoppers["total_stopped"],
        "whist_class": whist_class["class"],
        "aces": whist_class["aces"],
        "ak_combos": whist_class["ak_combos"],
        "has_void": whist_class["has_void"],
    }

    if trump_suit is not None:
        ruff = helper_ruffing_potential(hand, trump_suit)
        result["trump_length"] = ruff["trump_length"]
        result["void_count"] = ruff["void_count"]
        result["singleton_count"] = ruff["singleton_count"]
        result["doubleton_count"] = ruff["doubleton_count"]
        result["ruff_tricks"] = ruff["ruff_tricks"]

        # Suit texture for trump suit
        tex = helper_suit_texture(hand, trump_suit)
        result["trump_sequences"] = tex["sequences"]
        result["trump_fillers"] = tex["fillers"]
        result["trump_texture"] = tex["texture_score"]

    return result


# ---------------------------------------------------------------------------
# Shared Card Play Context and Strategy Functions
# ---------------------------------------------------------------------------

@dataclass
class CardPlayContext:
    """Context object passed to shared card play functions."""
    trick_cards: list          # [(player_id, Card), ...] current trick
    declarer_id: int           # who is the declarer
    my_id: int                 # this player's ID
    active_players: list       # ordered active player IDs for current trick
    played_cards: list         # all cards played in previous tricks
    trump_suit: object         # trump suit Enum value or None
    contract_type: str         # "suit", "betl", "sans"
    is_declarer: bool
    tricks_played: int         # completed tricks count
    my_hand: list              # full hand for suit counting


def _ctx_trick_winner(ctx):
    """Return (player_id, Card) of who's currently winning the trick, or None."""
    if not ctx.trick_cards:
        return None
    led_suit = ctx.trick_cards[0][1].suit
    winner_pid, winner_card = ctx.trick_cards[0]
    for pid, card in ctx.trick_cards[1:]:
        if card.suit == ctx.trump_suit and winner_card.suit != ctx.trump_suit:
            winner_pid, winner_card = pid, card
        elif card.suit == winner_card.suit and card.rank > winner_card.rank:
            winner_pid, winner_card = pid, card
    return (winner_pid, winner_card)


def _ctx_is_trick_winnable(legal_cards, ctx):
    """Can any of our legal cards beat the current trick winner?"""
    if not ctx.trick_cards:
        return True
    winner = _ctx_trick_winner(ctx)
    if winner is None:
        return True
    _wpid, winner_card = winner
    for c in legal_cards:
        # Can trump a non-trump winner
        if c.suit == ctx.trump_suit and winner_card.suit != ctx.trump_suit:
            return True
        # Can overrank in same suit
        if c.suit == winner_card.suit and c.rank > winner_card.rank:
            return True
    return False


def _ctx_other_follower_winning(ctx):
    """True if another follower (not declarer, not me) is currently winning."""
    if not ctx.trick_cards:
        return False
    winner = _ctx_trick_winner(ctx)
    if winner is None:
        return False
    winner_pid = winner[0]
    return winner_pid != ctx.declarer_id and winner_pid != ctx.my_id


def _ctx_is_through_declarer(ctx):
    """True if I'm leading and declarer plays immediately after me."""
    if ctx.trick_cards:
        return False
    if ctx.my_id not in ctx.active_players or len(ctx.active_players) < 2:
        return False
    my_idx = ctx.active_players.index(ctx.my_id)
    next_idx = (my_idx + 1) % len(ctx.active_players)
    return ctx.active_players[next_idx] == ctx.declarer_id


def _ctx_is_unsupported_king(card, hand):
    """True if card is a king and it's the only card of that suit in hand."""
    if card.rank != 7:
        return False
    return sum(1 for c in hand if c.suit == card.suit) == 1


def _ctx_suit_remaining(suit, ctx):
    """Cards of this suit unaccounted for (not in hand, not played, not in trick)."""
    total_in_suit = 8
    in_hand = sum(1 for c in ctx.my_hand if c.suit == suit)
    played = sum(1 for c in ctx.played_cards if c.suit == suit)
    in_trick = sum(1 for _, c in ctx.trick_cards if c.suit == suit)
    return total_in_suit - in_hand - played - in_trick


def _ctx_trumps_remaining(ctx):
    """Count trumps still in opponents' hands (not in our hand, not yet played)."""
    if ctx.trump_suit is None:
        return 0
    return _ctx_suit_remaining(ctx.trump_suit, ctx)


def _ctx_is_master_trump(card, ctx):
    """True if card is the highest remaining trump (all higher trumps accounted for).

    A trump is 'master' when every trump with a higher rank has been played,
    is in our hand, or is in the current trick.
    """
    if ctx.trump_suit is None or card.suit != ctx.trump_suit:
        return False
    # Collect all accounted-for trump ranks (our hand + played + trick)
    accounted = set()
    for c in ctx.my_hand:
        if c.suit == ctx.trump_suit:
            accounted.add(c.rank)
    for c in ctx.played_cards:
        if c.suit == ctx.trump_suit:
            accounted.add(c.rank)
    for _, c in ctx.trick_cards:
        if c.suit == ctx.trump_suit:
            accounted.add(c.rank)
    # Check if all ranks above this card are accounted for
    for r in range(card.rank + 1, 9):  # ranks go up to 8 (ACE)
        if r not in accounted:
            return False
    return True


def _ctx_is_master_in_suit(card, ctx):
    """True if card is the highest remaining in its suit (all higher ranks accounted for).

    Generalized version of _ctx_is_master_trump — works for ANY suit.
    A card is 'master' when every card of the same suit with a higher rank
    has been played, is in our hand, or is in the current trick.
    """
    suit = card.suit
    accounted = set()
    for c in ctx.my_hand:
        if c.suit == suit:
            accounted.add(c.rank)
    for c in ctx.played_cards:
        if c.suit == suit:
            accounted.add(c.rank)
    for _, c in ctx.trick_cards:
        if c.suit == suit:
            accounted.add(c.rank)
    for r in range(card.rank + 1, 9):  # ranks go up to 8 (ACE)
        if r not in accounted:
            return False
    return True


def _ctx_higher_unaccounted(card, ctx):
    """Count how many higher ranks in this card's suit are unaccounted for.

    'Unaccounted' = not in our hand, not played, not in current trick.
    E.g., Q♠ with K♠ and A♠ both unaccounted → returns 2.
    K♠ with only A♠ unaccounted → returns 1.
    """
    suit = card.suit
    accounted = set()
    for c in ctx.my_hand:
        if c.suit == suit:
            accounted.add(c.rank)
    for c in ctx.played_cards:
        if c.suit == suit:
            accounted.add(c.rank)
    for _, c in ctx.trick_cards:
        if c.suit == suit:
            accounted.add(c.rank)
    count = 0
    for r in range(card.rank + 1, 9):  # ranks go up to 8 (ACE)
        if r not in accounted:
            count += 1
    return count


def _ctx_count_sequential_winners(suit, hand, ctx):
    """Count consecutive guaranteed winners in a suit from this hand.

    Starting from the highest card in the suit, count how many cards
    in sequence are masters. E.g., K♥+Q♥ with A♥ played = 2 winners,
    but K♣+J♣ with A♣ played = 1 winner (Q♣ unaccounted blocks J♣).
    """
    suit_cards = sorted([c for c in hand if c.suit == suit],
                        key=lambda c: c.rank, reverse=True)
    if not suit_cards:
        return 0
    count = 0
    for card in suit_cards:
        if _ctx_is_master_in_suit(card, ctx):
            count += 1
        else:
            break
    return count


# --- Shared strategy functions ---

def _shared_whister_lead(legal_cards, ctx, trump_val):
    """Whister leading with position awareness and unsupported-king avoidance.

    Strategy 3: Through declarer → lead low from weak suit.
    Strategy 4: Through other follower → lead high.
    Strategy 7: Don't lead unsupported kings.
    """
    # R22: Fall back to ctx.trump_suit when player class didn't set trump_val
    if trump_val is None and ctx is not None and ctx.trump_suit is not None:
        trump_val = ctx.trump_suit
    groups = _helper_suit_groups(legal_cards)
    hand = ctx.my_hand

    # R10/R15: Lead master trump to draw declarer's trumps — guaranteed winner +
    # removes a declarer trump, protecting future side-suit leads from ruffing.
    # 2-player: lead when 1+ trumps out (always worth drawing).
    # 3-player: lead when 1-2 trumps out (low risk of drawing partner's trump;
    # with 3+ trumps out, partner may also have trumps and drawing them is costly).
    # Game 4 iter7: Carol had K♦ master (1 trump out) but skipped because 3-player.
    # Leading K♦ draws Alice's 10♦, making A♠ safe next trick.
    if trump_val is not None and ctx:
        trumps_out = _ctx_trumps_remaining(ctx)
        should_draw = (trumps_out >= 1 and
                       (len(ctx.active_players) == 2 or trumps_out <= 2))
        if should_draw:
            my_master_trumps = [c for c in legal_cards if c.suit == trump_val
                                and _ctx_is_master_trump(c, ctx)]
            if my_master_trumps:
                return my_master_trumps[0]

    # Strategy 3: Through declarer → prefer safe aces, then lead low from weak suit
    # NEVER lead trumps through declarer — they likely hold the top trumps
    if _ctx_is_through_declarer(ctx):
        # R3/R13: Lead safe aces first — guaranteed winners regardless of declarer's hand.
        # When many trumps remain (3+), declarer likely has voids in short suits →
        # require aces from 3+ card suits (matching general cautious-ace logic).
        # Game 8 iter6: Bob led A♦ from 2-card suit through declarer → ruffed.
        # When fewer trumps out, 2+ card suits are safe enough.
        through_aces = [c for c in legal_cards if c.rank == 8 and c.suit != trump_val]
        if through_aces:
            through_trumps_out = _ctx_trumps_remaining(ctx) if trump_val is not None else 0
            # R26: Prefer ace from suit with HIGHEST suit_remaining — declarer is
            # more likely to hold cards in suits with more unaccounted cards.
            # Old logic used hand suit length (longer = safer), but that's a poor
            # proxy: we may hold 3 clubs yet declarer is void (all remaining in
            # non-player's hand). suit_remaining directly measures how many cards
            # of that suit exist outside our hand/played, so higher = more likely
            # the declarer has some.
            # Game 6 iter19: Bob had A♣(rem=3) vs A♥(rem=5). Picked A♣ → trumped.
            # A♥(rem=5) → declarer had 4 hearts → guaranteed win.
            # Game 2 iter19: Carol had A♦(rem=5) vs A♣(rem=6). Picked A♦ → trumped.
            # A♣(rem=6) → declarer had 2 clubs → guaranteed win.
            safe_through = [c for c in through_aces
                            if _ctx_suit_remaining(c.suit, ctx) > 0]
            if safe_through:
                safe_through.sort(key=lambda c: -_ctx_suit_remaining(c.suit, ctx))
                return safe_through[0]  # ace from suit opponent most likely has
            # All aces from depleted suits — skip to other leads when many trumps
            if through_trumps_out < 3:
                return through_aces[0]

        # R6: Lead master non-trump cards through declarer — promoted queens/kings
        # that are now highest remaining are guaranteed if opponent follows suit.
        # R11: Skip masters from nearly-depleted suits (remaining < 2) when trumps
        # are still out — opponent is likely void and will ruff instead of following.
        # Game 8 iter6: Bob had master Q♠ (remaining=1) but Carol was void, ruffed.
        trumps_out_through = _ctx_trumps_remaining(ctx) if trump_val is not None else 0
        master_through = [c for c in legal_cards if c.suit != trump_val and c.rank < 8
                          and _ctx_is_master_in_suit(c, ctx)]
        if trumps_out_through >= 2:
            master_through = [c for c in master_through
                              if _ctx_suit_remaining(c.suit, ctx) >= 2]
        if master_through:
            # R7: Prefer suits with more sequential winners (K♥+Q♥ = 2 > K♣ alone = 1)
            # Game 6 iter4: K♥ (seq=2) beats K♣ (seq=1) — leads to 2 guaranteed tricks
            # Then by remaining cards (opponent likely follows), then by rank
            master_through.sort(key=lambda c: (
                -_ctx_count_sequential_winners(c.suit, ctx.my_hand, ctx),
                -_ctx_suit_remaining(c.suit, ctx),
                -c.rank))
            return master_through[0]

        # No aces or masters — fall back to low from weak non-trump suit
        # R19: Skip suits where suit_remaining=0 and trumps are out — all opponents
        # are void and will ruff. Game 2 iter9: Carol's J♠ led from 1-card spades
        # (remaining=0) got trumped by Bob. Leading 8♣ instead let Alice win with J♣.
        weak_suits = {}
        for s, cards in groups.items():
            if s == trump_val:
                continue  # never lead trumps through declarer
            has_ace = any(c.rank == 8 for c in cards)
            if not has_ace:
                weak_suits[s] = cards
        if weak_suits and trump_val is not None:
            trumps_out_ws = _ctx_trumps_remaining(ctx)
            if trumps_out_ws > 0:
                safe_weak = {s: c for s, c in weak_suits.items()
                             if _ctx_suit_remaining(s, ctx) > 0}
                if safe_weak:
                    weak_suits = safe_weak
        if weak_suits:
            # R21: Break length ties by preferring suit where our highest card is
            # lowest rank (weaker suit → partner likely has the high cards).
            # Game 1 iter10: Carol had spades(3,Q=6) and diamonds(3,10=4) tied.
            # Old code picked spades (suit value tiebreaker) → 7♠ got trumped.
            # Diamonds (weaker, max_rank=4) finds Bob's A♦ K♦ Q♦.
            shortest = min(weak_suits.keys(), key=lambda s: (
                len(weak_suits[s]),
                max(c.rank for c in weak_suits[s])))
            return weak_suits[shortest][-1]  # lowest from shortest weak suit

    # Lead aces first — but be cautious when declarer likely has trumps to ruff
    aces = [c for c in legal_cards if c.rank == 8]
    trumps_out = _ctx_trumps_remaining(ctx) if trump_val is not None else 0
    if aces:
        # If many trumps still out (3+), only lead aces from long suits (3+ cards)
        # where opponents likely have cards and won't ruff.
        # Defer short-suit aces; lead from longest suit to find partner's strength.
        if trumps_out >= 3:
            safe_aces = [c for c in aces if len(groups.get(c.suit, [])) >= 3
                         and c.suit != trump_val]
            if safe_aces:
                safe_aces.sort(key=lambda c: len(groups.get(c.suit, [])))
                return safe_aces[0]
            # No safe aces — defer to longest non-trump suit lead below
        else:
            def ace_priority(c):
                suit_cards = groups.get(c.suit, [])
                has_king = any(x.rank == 7 for x in suit_cards)
                is_trump = (trump_val is not None and c.suit == trump_val)
                priority = 0
                if is_trump:
                    priority -= 200
                if has_king:
                    priority -= 100
                return priority + len(suit_cards)
            aces.sort(key=ace_priority)
            return aces[0]

    # When many trumps out, prefer leading from longest non-trump suit
    # to find partner's strength instead of short suit leads
    if trumps_out >= 3:
        # R23: Check for master non-trump cards before defaulting to longest suit.
        # Master cards from non-depleted suits (remaining >= 2) are guaranteed wins
        # if opponent follows suit. The trumps_out >= 3 path previously bypassed the
        # R6 master check entirely.
        # Game 4 iter12: Alice had K♥ (master, A♥ played T1) with remaining=4, but
        # led 8♠ (lost to Bob's K♠). K♥ was a guaranteed trick.
        if ctx:
            master_cautious = [c for c in legal_cards if c.suit != trump_val
                               and c.rank < 8 and _ctx_is_master_in_suit(c, ctx)
                               and _ctx_suit_remaining(c.suit, ctx) >= 2]
            if master_cautious:
                master_cautious.sort(key=lambda c: (
                    -_ctx_count_sequential_winners(c.suit, ctx.my_hand, ctx),
                    -_ctx_suit_remaining(c.suit, ctx),
                    -c.rank))
                return master_cautious[0]

        non_trump_suits = {s: cards for s, cards in groups.items() if s != trump_val}
        if non_trump_suits:
            # R21: Break length ties by preferring suit where our highest card
            # is lowest rank — weaker suit → partner likely has high cards.
            # Game 1 iter10: Carol had spades(3,Q=6) and diamonds(3,10=4).
            # Old code picked spades → 7♠ got trumped by void Alice.
            # Picking diamonds → 7♦ lets Bob win with A♦.
            longest = max(non_trump_suits.keys(), key=lambda s: (
                len(non_trump_suits[s]),
                -max(c.rank for c in non_trump_suits[s])))
            # Lead low from longest suit to find partner
            return non_trump_suits[longest][-1]

    # R6: Lead master non-trump cards — promoted winners are safe tricks
    # After aces have been played, queens/kings may become highest remaining
    # R11: Skip masters from depleted suits (remaining < 2) when trumps are out
    master_leads = [c for c in legal_cards if c.suit != trump_val and c.rank < 8
                    and _ctx_is_master_in_suit(c, ctx)]
    trumps_out_gen = _ctx_trumps_remaining(ctx) if trump_val is not None else 0
    if trumps_out_gen >= 2:
        master_leads = [c for c in master_leads
                        if _ctx_suit_remaining(c.suit, ctx) >= 2]
    if master_leads:
        # R7: Prefer suits with sequential winners, then more remaining cards
        master_leads.sort(key=lambda c: (
            -_ctx_count_sequential_winners(c.suit, ctx.my_hand, ctx),
            -_ctx_suit_remaining(c.suit, ctx),
            -c.rank))
        return master_leads[0]

    # Lead kings — but skip unsupported (singleton) kings (Strategy 7)
    kings = [c for c in legal_cards if c.rank == 7]
    supported_kings = [k for k in kings if not _ctx_is_unsupported_king(k, hand)]
    if supported_kings:
        supported_kings.sort(key=lambda c: len(groups.get(c.suit, [])), reverse=True)
        return supported_kings[0]

    # Lead from shortest suit to create voids for ruffing
    non_ace_suits = {s: cards for s, cards in groups.items()
                     if not any(c.rank == 8 for c in cards)}
    # Exclude unsupported kings from candidates when choosing lead
    if non_ace_suits:
        shortest = min(non_ace_suits.keys(), key=lambda s: len(non_ace_suits[s]))
        candidates = non_ace_suits[shortest]
        # Avoid leading singleton king if other options exist
        if len(candidates) == 1 and _ctx_is_unsupported_king(candidates[0], hand) and len(non_ace_suits) > 1:
            del non_ace_suits[shortest]
            shortest = min(non_ace_suits.keys(), key=lambda s: len(non_ace_suits[s]))
            candidates = non_ace_suits[shortest]
        return candidates[0]

    # Absolute fallback: lead highest from shortest suit (may be unsupported king)
    shortest_suit = min(groups.keys(), key=lambda s: len(groups[s]))
    return groups[shortest_suit][0]


def _shared_declarer_lead(legal_cards, ctx, trump_val, trump_leads_counter):
    """Declarer leading with smart trump management (from Carol's strategy).

    Strategy 6: Don't exhaust trumps if side suit potential.
    Returns (card, new_trump_leads_counter).
    """
    # R22: Fall back to ctx.trump_suit when player class didn't set trump_val
    if trump_val is None and ctx is not None and ctx.trump_suit is not None:
        trump_val = ctx.trump_suit
    groups = _helper_suit_groups(legal_cards)
    trump_cards = groups.get(trump_val, [])

    # Find longest non-trump suit
    side_suits = {s: cards for s, cards in groups.items() if s != trump_val}
    longest_side_suit = None
    longest_side_len = 0
    if side_suits:
        # R25: Break length ties by highest top card — more promotion potential.
        # Game 2 iter18: hearts (K♥=7) > spades (Q♠=6) when both len=3.
        # Leading from hearts establishes K♥+Q♥ as sequential masters after A♥
        # drawn; spades Q♠ still faces both K♠ and A♠.
        longest_side_suit = max(side_suits.keys(),
                                key=lambda s: (len(side_suits[s]),
                                               max(c.rank for c in side_suits[s])))
        longest_side_len = len(side_suits[longest_side_suit])

    has_trump_ace = any(c.rank == 8 for c in trump_cards)

    # R4: Lead master trumps immediately — they're guaranteed winners
    # A trump is master when all higher trumps have been played or are in hand
    if trump_cards and ctx:
        master_trumps = [c for c in trump_cards if _ctx_is_master_trump(c, ctx)]
        if master_trumps:
            return master_trumps[0], trump_leads_counter + 1

    # Smart trump management for long side suits (3+)
    if trump_cards and longest_side_len >= 3:
        if trump_leads_counter < 2:
            ace = next((c for c in trump_cards if c.rank == 8), None)
            if ace:
                return ace, trump_leads_counter + 1
            # R5: Without trump ace, cash side-suit aces before leading trumps
            # Leading Q/K into a probable ace wastes our best trump
            side_aces = [c for c in legal_cards if c.rank == 8 and c.suit != trump_val]
            if side_aces:
                side_aces.sort(key=lambda c: len(groups.get(c.suit, [])))
                return side_aces[0], trump_leads_counter
            # R14: No side aces — check for master side-suit winners before trump
            # Game 4 iter7: Alice had K♥ (master after A♥ played) but led Q♦ instead,
            # which lost to A♦. K♥ was a guaranteed trick.
            if ctx:
                master_sides = [c for c in legal_cards if c.suit != trump_val
                                and _ctx_is_master_in_suit(c, ctx)]
                # R18: Skip masters from depleted suits when trumps are out
                trumps_out_r14 = _ctx_trumps_remaining(ctx) if trump_val is not None else 0
                if trumps_out_r14 >= 2:
                    master_sides = [c for c in master_sides
                                    if _ctx_suit_remaining(c.suit, ctx) >= 2]
                if master_sides:
                    master_sides.sort(key=lambda c: (
                        -_ctx_count_sequential_winners(c.suit, ctx.my_hand, ctx),
                        -c.rank, len(groups.get(c.suit, []))))
                    return master_sides[0], trump_leads_counter
            # R16: Don't lead non-ace trump with 2+ higher unaccounted — guaranteed loss.
            # Game 4 iter7: Q♦ with K♦+A♦ unaccounted → lost. Fall through to side suit.
            if ctx:
                top_trump = trump_cards[0]
                if _ctx_higher_unaccounted(top_trump, ctx) >= 2:
                    pass  # fall through to side suit lead below
                else:
                    return trump_cards[0], trump_leads_counter + 1
            else:
                return trump_cards[0], trump_leads_counter + 1
        # With 4+ trumps remaining after 2 leads, lead one more
        if trump_leads_counter == 2 and len(trump_cards) >= 4:
            return trump_cards[0], trump_leads_counter + 1
        # R6: Cash guaranteed side-suit winners before speculative side-suit leads
        # Aces and promoted master cards are guaranteed tricks; leading from longest
        # suit risks losing to higher cards (Game 6 iter3: K♠ lost to A♠, had A♥)
        if ctx:
            side_winners = [c for c in legal_cards if c.suit != trump_val
                            and (c.rank == 8 or _ctx_is_master_in_suit(c, ctx))]
            # R18: Skip masters from depleted suits when trumps are out
            trumps_out_r6 = _ctx_trumps_remaining(ctx) if trump_val is not None else 0
            if trumps_out_r6 >= 2:
                side_winners = [c for c in side_winners
                                if c.rank == 8 or _ctx_suit_remaining(c.suit, ctx) >= 2]
            if side_winners:
                # R7: Prefer sequential winners, then aces, then by suit length
                side_winners.sort(key=lambda c: (
                    -_ctx_count_sequential_winners(c.suit, ctx.my_hand, ctx),
                    -c.rank, len(groups.get(c.suit, []))))
                return side_winners[0], trump_leads_counter
        # Switch to forcing long side suit
        # R17: Lead LOW from forcing suit — preserves high cards as future masters.
        # Leading K♥ into unaccounted A♥ wastes the king. Leading 9♥ draws the ace
        # equally, then K♥ becomes master for a guaranteed later trick.
        # Game 7 iter8: Alice led K♥ (lost to A♥) but leading 9♥ preserves K♥+Q♥
        # as sequential masters after A♥ is drawn.
        return side_suits[longest_side_suit][-1], trump_leads_counter

    # Continue forcing a side suit already started
    if trump_cards and longest_side_len >= 2 and trump_leads_counter >= 2:
        # R6: Cash guaranteed side-suit winners first
        if ctx:
            side_winners = [c for c in legal_cards if c.suit != trump_val
                            and (c.rank == 8 or _ctx_is_master_in_suit(c, ctx))]
            # R18: Skip masters from depleted suits when trumps are out
            trumps_out_r6a = _ctx_trumps_remaining(ctx) if trump_val is not None else 0
            if trumps_out_r6a >= 2:
                side_winners = [c for c in side_winners
                                if c.rank == 8 or _ctx_suit_remaining(c.suit, ctx) >= 2]
            if side_winners:
                # R7: Prefer sequential winners, then aces, then by suit length
                side_winners.sort(key=lambda c: (
                    -_ctx_count_sequential_winners(c.suit, ctx.my_hand, ctx),
                    -c.rank, len(groups.get(c.suit, []))))
                return side_winners[0], trump_leads_counter
        # R17: Lead low from forcing suit
        return side_suits[longest_side_suit][-1], trump_leads_counter

    # Original strategy for hands without a long side suit

    # Phase 1: Lead high trumps — but only the ace or master trumps
    if trump_val in groups:
        for c in trump_cards:
            if c.rank == 8:
                return c, trump_leads_counter
        # R5: Without trump ace, don't lead trumps yet — cash side winners first

    # Phase 2: Cash side-suit aces (shortest suit first)
    aces = [c for c in legal_cards if c.rank == 8 and c.suit != trump_val]
    if aces:
        aces.sort(key=lambda c: len(groups.get(c.suit, [])))
        return aces[0], trump_leads_counter

    # Phase 2.3: R6 — Cash master side-suit cards (promoted queens/kings)
    # After aces are cashed, lower cards may become highest remaining in suit
    if ctx:
        master_sides = [c for c in legal_cards if c.suit != trump_val and c.rank < 8
                        and _ctx_is_master_in_suit(c, ctx)]
        # R18: Skip masters from depleted suits when trumps are out — opponents
        # are void and will ruff. Game 7 iter8: 10♥ was "master" but suit empty,
        # both opponents trumped it.
        trumps_out_p23 = _ctx_trumps_remaining(ctx) if trump_val is not None else 0
        if trumps_out_p23 >= 2:
            master_sides = [c for c in master_sides
                            if _ctx_suit_remaining(c.suit, ctx) >= 2]
        if master_sides:
            # R7: Prefer sequential winners, then rank, then suit length
            master_sides.sort(key=lambda c: (
                -_ctx_count_sequential_winners(c.suit, ctx.my_hand, ctx),
                -c.rank, len(groups.get(c.suit, []))))
            return master_sides[0], trump_leads_counter

    # Phase 2.5: Cash promoted kings from short non-trump suits
    # R20: Only lead kings that are master (ace accounted for). Non-master kings
    # get captured by the ace — a guaranteed loss. Game 2 iter9: Bob led K♣ into
    # Carol's A♣. With fix, K♣ is skipped (A♣ unaccounted), falls to Phase 2.7.
    short_kings = [c for c in legal_cards if c.rank == 7 and c.suit != trump_val
                   and len(groups.get(c.suit, [])) <= 2]
    if short_kings and ctx:
        short_kings = [c for c in short_kings if _ctx_is_master_in_suit(c, ctx)]
    if short_kings:
        short_kings.sort(key=lambda c: len(groups.get(c.suit, [])))
        return short_kings[0], trump_leads_counter

    # Phase 2.7: Now lead non-ace trumps if we have them (after cashing winners)
    # R12: Only lead if at most 1 higher trump is unaccounted for.
    # Leading Q♠ when both K♠ and A♠ are out is a guaranteed loss — better to
    # lead side cards and preserve the trump for ruffing or later promotion.
    # Game 7 iter6: Alice led Q♠ with K♠+A♠ unaccounted → lost to A♠.
    if trump_val in groups and not has_trump_ace:
        top_trump = trump_cards[0]
        unaccounted = _ctx_higher_unaccounted(top_trump, ctx) if ctx else 0
        if unaccounted <= 1 and top_trump.rank >= 6:
            return top_trump, trump_leads_counter

    # Phase 3: Probe side suits with length
    non_trump = {s: cards for s, cards in groups.items() if s != trump_val}
    non_trump_with_length = {s: cards for s, cards in non_trump.items()
                             if len(cards) >= 2}
    if non_trump_with_length and trump_val in groups:
        # R25: Break length ties by highest top card
        longest = max(non_trump_with_length.keys(),
                      key=lambda s: (len(non_trump_with_length[s]),
                                     max(c.rank for c in non_trump_with_length[s])))
        # R17: Lead low from probing suit to preserve high cards
        return non_trump_with_length[longest][-1], trump_leads_counter

    # Phase 4: Lead master trumps only — non-master trumps lose to higher trumps
    # R9: Don't lead non-master trumps when side cards remain.
    # Game 10 iter5: Alice led J♥ (non-master, Q♥ out) instead of 9♠ — lost the
    # trick AND the subsequent tricks. Leading 9♠ first (opponent void = free win)
    # then later J♥ might become master or lose no worse than leading it now.
    if trump_val in groups and ctx:
        master_trumps = [c for c in groups[trump_val] if _ctx_is_master_trump(c, ctx)]
        if master_trumps:
            return master_trumps[0], trump_leads_counter

    # Phase 5: Lead from longest off-suit before non-master trumps
    # R17: Lead low to preserve high cards for later promotion
    if non_trump:
        # R25: Break length ties by highest top card
        longest = max(non_trump.keys(),
                      key=lambda s: (len(non_trump[s]),
                                     max(c.rank for c in non_trump[s])))
        return non_trump[longest][-1], trump_leads_counter

    # Phase 6: Lead non-master trumps (last resort — no side suits left)
    if trump_val in groups:
        return groups[trump_val][0], trump_leads_counter

    return max(legal_cards, key=lambda c: c.rank), trump_leads_counter


def _shared_must_follow(legal_cards, ctx, played, is_declarer, trump_val, params):
    """Following suit with winnability check and follower coordination.

    Strategy 1: Unwinnable trick → play lowest card.
    Strategy 5: Don't steal trick from other follower.
    params: king_duck_tricks (int), default 2.
    """
    # R22: Fall back to ctx.trump_suit when player class didn't set trump_val
    if trump_val is None and ctx is not None and ctx.trump_suit is not None:
        trump_val = ctx.trump_suit
    king_duck_tricks = params.get('king_duck_tricks', 2)
    by_rank_desc = sorted(legal_cards, key=lambda c: c.rank, reverse=True)

    led_suit = played[0][1].suit if played else None

    # Detect trumping: legal cards are all trumps but led suit is different
    if led_suit is not None and by_rank_desc[0].suit != led_suit:
        best_trump_in_trick = max(
            (c for _, c in played if c.suit == by_rank_desc[0].suit),
            key=lambda c: c.rank, default=None)
        if best_trump_in_trick:
            beaters = [c for c in legal_cards if c.rank > best_trump_in_trick.rank]
            return min(beaters, key=lambda c: c.rank) if beaters else min(legal_cards, key=lambda c: c.rank)
        else:
            return min(legal_cards, key=lambda c: c.rank)

    # Strategy 1: Comprehensive winnability check using context
    if ctx and not _ctx_is_trick_winnable(legal_cards, ctx):
        return min(legal_cards, key=lambda c: c.rank)

    # Strategy 5: Don't steal from other follower
    if ctx and not is_declarer and _ctx_other_follower_winning(ctx):
        return min(legal_cards, key=lambda c: c.rank)

    # R8: Economy of force — last player plays cheapest winning card
    # When no one plays after us, the minimum card that beats the current
    # winner is sufficient. Preserves high cards for future tricks.
    if ctx and len(ctx.trick_cards) == len(ctx.active_players) - 1:
        winner = _ctx_trick_winner(ctx)
        if winner:
            w_card = winner[1]
            if w_card.suit == by_rank_desc[0].suit:
                beaters = [c for c in legal_cards if c.rank > w_card.rank]
                if beaters:
                    return min(beaters, key=lambda c: c.rank)

    # If we have the ace, play it (guaranteed winner)
    if by_rank_desc[0].rank == 8:
        return by_rank_desc[0]

    # If best card is below Queen, unlikely to win — play lowest
    if by_rank_desc[0].rank < 6:
        return min(legal_cards, key=lambda c: c.rank)

    # Fallback winnability check without context (trick already played)
    if not ctx and played:
        best_in_trick = max((c for _, c in played if c.suit == by_rank_desc[0].suit),
                            key=lambda c: c.rank, default=None)
        if best_in_trick and best_in_trick.rank > by_rank_desc[0].rank:
            return min(legal_cards, key=lambda c: c.rank)

    # King handling: duck as whister in early tricks
    tricks_played = ctx.tricks_played if ctx else 0
    if not is_declarer and tricks_played < king_duck_tricks and by_rank_desc[0].rank == 7:
        return min(legal_cards, key=lambda c: c.rank)

    # Whister with K+Q: play Q before K (probe play)
    if not is_declarer and by_rank_desc[0].rank == 7:
        has_queen = any(c.rank == 6 for c in legal_cards)
        if has_queen:
            return next(c for c in legal_cards if c.rank == 6)

    # Play highest to try to win
    return by_rank_desc[0]


def _shared_cant_follow(legal_cards, ctx, is_declarer, trump_val, params):
    """Can't follow suit — ruff or discard with follower awareness.

    Strategy 5: Don't steal from other follower.
    params: whister_trump_pref ("highest" or "lowest"), default "highest".
    """
    # R22: Fall back to ctx.trump_suit when player class didn't set trump_val
    if trump_val is None and ctx is not None and ctx.trump_suit is not None:
        trump_val = ctx.trump_suit
    whister_trump_pref = params.get('whister_trump_pref', 'highest')
    suits_in_legal = {c.suit for c in legal_cards}

    if is_declarer and trump_val is not None:
        trumps = [c for c in legal_cards if c.suit == trump_val]
        if trumps:
            # R27: If an opponent already trumped, play minimum trump that beats it.
            # Playing a trump below the existing trump wastes it.
            if ctx and ctx.trick_cards:
                best_trump_in_trick = max(
                    (c for _, c in ctx.trick_cards if c.suit == trump_val),
                    key=lambda c: c.rank, default=None)
                if best_trump_in_trick:
                    beaters = [c for c in trumps if c.rank > best_trump_in_trick.rank]
                    if beaters:
                        return min(beaters, key=lambda c: c.rank)
                    # Can't overtrump — play lowest trump (forced to trump)
                    return min(trumps, key=lambda c: c.rank)
            return min(trumps, key=lambda c: c.rank)

    if not is_declarer and trump_val is not None:
        # Strategy 5: Don't ruff if other follower is winning
        if ctx and _ctx_other_follower_winning(ctx):
            # Discard lowest instead of ruffing
            non_trumps = [c for c in legal_cards if c.suit != trump_val]
            if non_trumps:
                groups = _helper_suit_groups(non_trumps)
                longest_suit = max(groups.keys(), key=lambda s: len(groups[s]))
                return groups[longest_suit][-1]

        trumps = [c for c in legal_cards if c.suit == trump_val]
        if trumps:
            # R27: Smart ruffing — consider existing trumps in trick and position.
            if ctx and ctx.trick_cards:
                best_trump_in_trick = max(
                    (c for _, c in ctx.trick_cards if c.suit == trump_val),
                    key=lambda c: c.rank, default=None)
                if best_trump_in_trick:
                    # Must overtrump if possible
                    beaters = [c for c in trumps if c.rank > best_trump_in_trick.rank]
                    if beaters:
                        return min(beaters, key=lambda c: c.rank)
                    # Can't overtrump — play lowest trump (forced)
                    return min(trumps, key=lambda c: c.rank)
                # No trump in trick yet — use economy when playing last
                if len(ctx.trick_cards) == len(ctx.active_players) - 1:
                    return min(trumps, key=lambda c: c.rank)
            if whister_trump_pref == 'highest':
                return max(trumps, key=lambda c: c.rank)
            else:
                return min(trumps, key=lambda c: c.rank)

    # All one suit (forced trump)
    if len(suits_in_legal) == 1:
        return min(legal_cards, key=lambda c: c.rank)

    # Discard lowest from longest off-suit
    groups = _helper_suit_groups(legal_cards)
    longest_suit = max(groups.keys(), key=lambda s: len(groups[s]))
    return groups[longest_suit][-1]


def _shared_betl_defender_lead(legal_cards, hand):
    """Betl defender lead from shortest suit (Strategy: Betl 1).

    Instead of always leading highest, lead highest from shortest suit
    to help void out and let the other follower continue.
    R24: Avoid leading aces — they always win for the defender, never
    forcing the declarer to take the trick. Non-ace leads can force the
    declarer to beat them with high cards they wanted to keep hidden.
    """
    groups = _helper_suit_groups(legal_cards)
    if len(groups) == 1:
        # R24: Prefer non-ace — ace wins for us (bad), non-ace may force declarer
        non_aces = [c for c in legal_cards if c.rank < 8]
        if non_aces:
            return max(non_aces, key=lambda c: c.rank)
        return max(legal_cards, key=lambda c: c.rank)
    # Pick shortest suit and lead highest non-ace from it
    # R24: Aces are counterproductive — declarer ALWAYS ducks under them.
    # Non-aces (K, Q, J, 10, etc.) can catch the declarer when their only
    # remaining cards in the suit are above our lead.
    # Game 3 iter17: Carol had [A♠, 10♠] and led A♠ → Alice safely ducked Q♠.
    # Leading 10♠ instead → Alice's J♠ and Q♠ both above 10♠ → forced to win!
    shortest = min(groups.keys(), key=lambda s: len(groups[s]))
    cards = groups[shortest]
    non_aces = [c for c in cards if c.rank < 8]
    if non_aces:
        return max(non_aces, key=lambda c: c.rank)
    return cards[0]  # only aces in this suit — lead the ace


# ---------------------------------------------------------------------------
# Scoring versions of shared card-play functions
# Each returns {card_id: float_score} for ALL legal cards.
# Higher score = better play.  Scores reflect genuine strategic quality so
# that adding random noise in simulations picks alternatives proportional
# to how close they are in strength.
# ---------------------------------------------------------------------------

def _score_must_follow(legal_cards, ctx, played, is_declarer, trump_val, params):
    """Score each legal card when following suit."""
    if trump_val is None and ctx is not None and ctx.trump_suit is not None:
        trump_val = ctx.trump_suit
    king_duck_tricks = params.get('king_duck_tricks', 2)
    by_rank_desc = sorted(legal_cards, key=lambda c: c.rank, reverse=True)
    led_suit = played[0][1].suit if played else None
    scores = {}

    # Detect trumping: legal cards are all trumps but led suit is different
    if led_suit is not None and by_rank_desc[0].suit != led_suit:
        best_trump_in_trick = max(
            (c for _, c in played if c.suit == by_rank_desc[0].suit),
            key=lambda c: c.rank, default=None)
        if best_trump_in_trick:
            for c in legal_cards:
                if c.rank > best_trump_in_trick.rank:
                    # Cheapest overtrump is best; more expensive ones slightly worse
                    scores[c.id] = 70.0 - c.rank * 0.5
                else:
                    # Can't overtrump — lower is better (save high trumps)
                    scores[c.id] = 10.0 - c.rank
        else:
            # First to trump — play lowest trump
            for c in legal_cards:
                scores[c.id] = 50.0 - c.rank
        return scores

    # Check winnability
    can_win = not ctx or _ctx_is_trick_winnable(legal_cards, ctx)
    other_winning = ctx and not is_declarer and _ctx_other_follower_winning(ctx)

    if not can_win or other_winning:
        # Losing trick — play lowest to save high cards
        for c in legal_cards:
            scores[c.id] = 20.0 - c.rank * 2.0
        return scores

    # Economy of force — last player plays cheapest winning card
    last_player = ctx and len(ctx.trick_cards) == len(ctx.active_players) - 1
    if last_player:
        winner = _ctx_trick_winner(ctx)
        if winner:
            w_card = winner[1]
            if w_card.suit == by_rank_desc[0].suit:
                for c in legal_cards:
                    if c.rank > w_card.rank:
                        # Winning — cheaper winner = better
                        scores[c.id] = 80.0 - c.rank * 0.5
                    else:
                        # Losing — lower is better
                        scores[c.id] = 20.0 - c.rank * 2.0
                return scores

    tricks_played = ctx.tricks_played if ctx else 0

    # Assign scores based on strategic value
    for c in legal_cards:
        if c.rank == 8:
            # Ace — guaranteed winner, best play
            scores[c.id] = 90.0
        elif c.rank < 6:
            # Below queen — unlikely to win, play lowest
            scores[c.id] = 15.0 - c.rank * 2.0
        elif c.rank == 7:
            # King handling
            if not is_declarer and tricks_played < king_duck_tricks:
                # Duck early — save king for later
                scores[c.id] = 25.0
            elif not is_declarer:
                has_queen = any(x.rank == 6 for x in legal_cards)
                if has_queen:
                    # K+Q: prefer queen probe
                    scores[c.id] = 60.0
                else:
                    scores[c.id] = 70.0
            else:
                scores[c.id] = 70.0
        elif c.rank == 6:
            # Queen
            if not is_declarer and by_rank_desc[0].rank == 7:
                has_queen = any(x.rank == 6 for x in legal_cards)
                if has_queen:
                    # K+Q: queen is the probe card — preferred
                    scores[c.id] = 65.0
                else:
                    scores[c.id] = 55.0
            else:
                scores[c.id] = 55.0
        else:
            # Jack (5), 10 (4) — moderate
            scores[c.id] = 40.0 + c.rank * 2.0

    return scores


def _score_cant_follow(legal_cards, ctx, is_declarer, trump_val, params):
    """Score each legal card when can't follow suit (ruff or discard)."""
    if trump_val is None and ctx is not None and ctx.trump_suit is not None:
        trump_val = ctx.trump_suit
    whister_trump_pref = params.get('whister_trump_pref', 'highest')
    suits_in_legal = {c.suit for c in legal_cards}
    scores = {}
    groups = _helper_suit_groups(legal_cards)

    other_winning = ctx and not is_declarer and _ctx_other_follower_winning(ctx)

    # All one suit (forced)
    if len(suits_in_legal) == 1:
        for c in legal_cards:
            scores[c.id] = 30.0 - c.rank
        return scores

    trumps = [c for c in legal_cards if c.suit == trump_val] if trump_val is not None else []
    non_trumps = [c for c in legal_cards if c.suit != trump_val] if trump_val is not None else list(legal_cards)

    if is_declarer and trumps:
        # Declarer ruffing
        if ctx and ctx.trick_cards:
            best_trump_in_trick = max(
                (c for _, c in ctx.trick_cards if c.suit == trump_val),
                key=lambda c: c.rank, default=None)
            if best_trump_in_trick:
                for c in legal_cards:
                    if c.suit == trump_val and c.rank > best_trump_in_trick.rank:
                        scores[c.id] = 70.0 - c.rank * 0.5  # cheapest overtrump
                    elif c.suit == trump_val:
                        scores[c.id] = 10.0 - c.rank  # can't overtrump
                    else:
                        scores[c.id] = 5.0 - c.rank * 0.5  # discard
                return scores
        # Simple declarer ruff — lowest trump
        for c in legal_cards:
            if c.suit == trump_val:
                scores[c.id] = 60.0 - c.rank  # lower trump = better
            else:
                scores[c.id] = 5.0 - c.rank * 0.5
        return scores

    if not is_declarer and other_winning:
        # Other follower winning — discard lowest non-trump
        for c in legal_cards:
            if c.suit != trump_val:
                suit_len = len(groups.get(c.suit, []))
                # Prefer lowest from longest suit
                scores[c.id] = 50.0 + suit_len * 2.0 - c.rank * 3.0
            else:
                scores[c.id] = 10.0 - c.rank  # don't waste trumps
        return scores

    if not is_declarer and trumps:
        # Defender ruffing
        if ctx and ctx.trick_cards:
            best_trump_in_trick = max(
                (c for _, c in ctx.trick_cards if c.suit == trump_val),
                key=lambda c: c.rank, default=None)
            if best_trump_in_trick:
                for c in legal_cards:
                    if c.suit == trump_val and c.rank > best_trump_in_trick.rank:
                        scores[c.id] = 70.0 - c.rank * 0.5
                    elif c.suit == trump_val:
                        scores[c.id] = 10.0 - c.rank
                    else:
                        scores[c.id] = 5.0 - c.rank * 0.5
                return scores
            # No trump in trick yet — economy when playing last
            if len(ctx.trick_cards) == len(ctx.active_players) - 1:
                for c in legal_cards:
                    if c.suit == trump_val:
                        scores[c.id] = 60.0 - c.rank  # lowest trump
                    else:
                        scores[c.id] = 5.0 - c.rank * 0.5
                return scores
        # General ruff
        for c in legal_cards:
            if c.suit == trump_val:
                if whister_trump_pref == 'highest':
                    scores[c.id] = 50.0 + c.rank  # higher trump preferred
                else:
                    scores[c.id] = 50.0 - c.rank  # lower trump preferred
            else:
                scores[c.id] = 5.0 - c.rank * 0.5
        return scores

    # No trumps — discard lowest from longest off-suit
    for c in legal_cards:
        suit_len = len(groups.get(c.suit, []))
        scores[c.id] = 30.0 + suit_len * 2.0 - c.rank * 3.0
    return scores


def _score_whister_lead(legal_cards, ctx, trump_val):
    """Score each legal card when whister is leading."""
    if trump_val is None and ctx is not None and ctx.trump_suit is not None:
        trump_val = ctx.trump_suit
    groups = _helper_suit_groups(legal_cards)
    hand = ctx.my_hand if ctx else legal_cards
    scores = {c.id: 0.0 for c in legal_cards}

    # Helpers for scoring bonuses
    def _master_trump_bonus(c):
        if trump_val is not None and ctx and c.suit == trump_val and _ctx_is_master_trump(c, ctx):
            return 95.0
        return 0.0

    def _ace_bonus(c, through_declarer):
        if c.rank != 8 or c.suit == trump_val:
            return 0.0
        if not ctx:
            return 80.0
        remaining = _ctx_suit_remaining(c.suit, ctx)
        if remaining <= 0:
            return 30.0  # depleted suit ace — risky
        if through_declarer:
            return 75.0 + remaining * 2.0  # prefer more remaining
        return 80.0 + remaining

    def _master_bonus(c):
        if c.rank >= 8 or c.suit == trump_val or not ctx:
            return 0.0
        if not _ctx_is_master_in_suit(c, ctx):
            return 0.0
        remaining = _ctx_suit_remaining(c.suit, ctx)
        trumps_out = _ctx_trumps_remaining(ctx) if trump_val is not None else 0
        if trumps_out >= 2 and remaining < 2:
            return 20.0  # risky — depleted suit with trumps out
        seq = _ctx_count_sequential_winners(c.suit, hand, ctx)
        return 65.0 + seq * 5.0 + remaining * 1.5

    # Check if leading through declarer
    through_declarer = _ctx_is_through_declarer(ctx) if ctx else False

    # Check master trump opportunity
    if trump_val is not None and ctx:
        trumps_out = _ctx_trumps_remaining(ctx)
        should_draw = (trumps_out >= 1 and
                       (len(ctx.active_players) == 2 or trumps_out <= 2))
        for c in legal_cards:
            if should_draw and c.suit == trump_val and _ctx_is_master_trump(c, ctx):
                scores[c.id] = max(scores[c.id], 95.0)

    for c in legal_cards:
        # Master trump (already scored above, but ensure it's considered)
        mt = _master_trump_bonus(c)
        if mt > scores[c.id]:
            scores[c.id] = mt

        # Ace leads
        ab = _ace_bonus(c, through_declarer)
        if ab > scores[c.id]:
            scores[c.id] = ab

        # Master non-ace leads
        mb = _master_bonus(c)
        if mb > scores[c.id]:
            scores[c.id] = mb

        # King leads
        if c.rank == 7 and c.suit != trump_val:
            is_supported = not _ctx_is_unsupported_king(c, hand)
            if is_supported:
                suit_len = len(groups.get(c.suit, []))
                scores[c.id] = max(scores[c.id], 45.0 + suit_len * 2.0)
            else:
                scores[c.id] = max(scores[c.id], 15.0)

        # Trump leads (non-master) — generally bad through declarer
        if c.suit == trump_val and scores[c.id] < 10.0:
            if through_declarer:
                scores[c.id] = max(scores[c.id], 5.0)
            else:
                scores[c.id] = max(scores[c.id], 25.0 + c.rank * 0.5)

        # Low cards from weak suits — reasonable through declarer
        if scores[c.id] < 10.0:
            suit_len = len(groups.get(c.suit, []))
            has_ace = any(x.rank == 8 for x in groups.get(c.suit, []))
            if c.suit != trump_val and not has_ace:
                if through_declarer:
                    # Prefer shortest weak suit, lowest card
                    remaining = _ctx_suit_remaining(c.suit, ctx) if ctx else 3
                    if remaining > 0 or (trump_val is None):
                        scores[c.id] = max(scores[c.id], 40.0 - suit_len * 3.0 - c.rank * 0.5)
                    else:
                        scores[c.id] = max(scores[c.id], 10.0 - suit_len)
                else:
                    scores[c.id] = max(scores[c.id], 30.0 - suit_len * 2.0 - c.rank * 0.5)
            elif c.suit != trump_val:
                # Have ace in this suit but card isn't ace — low priority
                scores[c.id] = max(scores[c.id], 20.0 - c.rank)

    return scores


def _score_declarer_lead(legal_cards, ctx, trump_val, trump_leads_counter):
    """Score each legal card when declarer is leading."""
    if trump_val is None and ctx is not None and ctx.trump_suit is not None:
        trump_val = ctx.trump_suit
    groups = _helper_suit_groups(legal_cards)
    trump_cards = groups.get(trump_val, [])
    scores = {c.id: 0.0 for c in legal_cards}

    side_suits = {s: cards for s, cards in groups.items() if s != trump_val}
    longest_side_suit = None
    longest_side_len = 0
    if side_suits:
        longest_side_suit = max(side_suits.keys(),
                                key=lambda s: (len(side_suits[s]),
                                               max(c.rank for c in side_suits[s])))
        longest_side_len = len(side_suits[longest_side_suit])

    has_trump_ace = any(c.rank == 8 for c in trump_cards)

    for c in legal_cards:
        # Master trumps — guaranteed winners, highest priority
        if c.suit == trump_val and ctx and _ctx_is_master_trump(c, ctx):
            scores[c.id] = 95.0

        # Trump ace (non-master but still very strong)
        elif c.suit == trump_val and c.rank == 8:
            scores[c.id] = 90.0

        # Side-suit aces — guaranteed winners
        elif c.rank == 8 and c.suit != trump_val:
            suit_len = len(groups.get(c.suit, []))
            # Prefer shorter suits (cash quick, then switch)
            scores[c.id] = 82.0 - suit_len * 0.5

        # Master side-suit cards (promoted queens/kings)
        elif c.suit != trump_val and ctx and _ctx_is_master_in_suit(c, ctx):
            remaining = _ctx_suit_remaining(c.suit, ctx)
            trumps_out = _ctx_trumps_remaining(ctx) if trump_val is not None else 0
            if trumps_out >= 2 and remaining < 2:
                scores[c.id] = 30.0  # risky — opponent may ruff
            else:
                seq = _ctx_count_sequential_winners(c.suit, ctx.my_hand, ctx)
                scores[c.id] = 75.0 + seq * 3.0 + remaining * 1.0

        # Non-ace trumps
        elif c.suit == trump_val:
            if ctx:
                unaccounted = _ctx_higher_unaccounted(c, ctx)
            else:
                unaccounted = 8 - c.rank
            if unaccounted == 0:
                scores[c.id] = 85.0  # effectively master
            elif unaccounted == 1 and c.rank >= 6:
                scores[c.id] = 55.0  # reasonable gamble
            elif unaccounted >= 2:
                scores[c.id] = 15.0 + c.rank  # likely loses
            else:
                scores[c.id] = 40.0 + c.rank

            # Bonus: early trump leads to draw opponents
            if trump_leads_counter < 2 and longest_side_len >= 3:
                scores[c.id] += 5.0

        # Side-suit kings (non-master)
        elif c.rank == 7 and c.suit != trump_val:
            suit_len = len(groups.get(c.suit, []))
            if suit_len <= 2:
                scores[c.id] = 40.0  # short suit king — probe
            else:
                scores[c.id] = 35.0  # long suit king

        # Low side-suit cards — forcing leads
        elif c.suit != trump_val:
            suit_len = len(groups.get(c.suit, []))
            is_longest = (longest_side_suit is not None and c.suit == longest_side_suit)
            if is_longest and longest_side_len >= 3:
                # Low card from long suit = forcing lead (good)
                # Lower rank = better (preserve high cards)
                scores[c.id] = 45.0 - c.rank * 1.5 + suit_len * 2.0
            elif suit_len >= 2:
                scores[c.id] = 35.0 - c.rank * 1.0 + suit_len * 1.5
            else:
                scores[c.id] = 20.0 + c.rank * 0.5  # singleton — speculative

    return scores


def _score_sans_declarer_lead(legal_cards, ctx):
    """Score each legal card for sans declarer leading."""
    groups = _helper_suit_groups(legal_cards)
    scores = {}

    for c in legal_cards:
        if c.rank == 8:
            # Aces — guaranteed winners; prefer from shorter suits
            suit_len = len(groups.get(c.suit, []))
            scores[c.id] = 95.0 - suit_len * 1.5
        elif c.rank == 7:
            suit_len = len(groups.get(c.suit, []))
            if suit_len <= 2:
                scores[c.id] = 75.0 - suit_len  # short suit king — likely promoted
            else:
                scores[c.id] = 60.0 - suit_len * 0.5
        elif c.rank == 6:
            suit_len = len(groups.get(c.suit, []))
            if suit_len <= 2:
                scores[c.id] = 55.0  # short suit queen — may be promoted
            else:
                scores[c.id] = 35.0
        else:
            # Low cards — value based on suit length (long suit = more winners)
            suit_len = len(groups.get(c.suit, []))
            if suit_len >= 5:
                # Long suit run — high value even for low cards
                scores[c.id] = 50.0 + suit_len * 3.0 - (8 - c.rank) * 0.5
            elif suit_len >= 4:
                scores[c.id] = 35.0 + suit_len * 2.0 - (8 - c.rank) * 0.5
            else:
                scores[c.id] = 15.0 + c.rank * 1.0

    return scores


def _score_betl_play(legal_cards, played, is_leading, must_follow,
                     is_declarer, declarer_id, active_players):
    """Score each legal card for betl contract play."""
    scores = {}

    if is_declarer:
        if is_leading:
            groups = _helper_suit_groups(legal_cards)
            for c in legal_cards:
                gap = 8 - c.rank
                suit_len = len(groups.get(c.suit, []))
                if gap > 0:
                    # Higher rank that still loses — burns a card safely
                    scores[c.id] = 50.0 + c.rank * 5.0 + suit_len * 2.0 + gap * 1.0
                else:
                    # Ace/top card — forced win, terrible
                    scores[c.id] = -50.0 - c.rank * 5.0
        elif must_follow:
            # Must follow suit — play highest that still loses
            if played:
                best_in_trick = max((c for _, c in played if c.suit == legal_cards[0].suit),
                                     key=lambda c: c.rank, default=None)
                for c in legal_cards:
                    if best_in_trick and c.rank < best_in_trick.rank:
                        # Still loses — higher is better (saves lower for later)
                        scores[c.id] = 60.0 + c.rank * 3.0
                    elif best_in_trick and c.rank > best_in_trick.rank:
                        # Wins the trick — terrible. Lower overshoot is less bad.
                        scores[c.id] = -30.0 - c.rank * 3.0
                    else:
                        scores[c.id] = 0.0  # ties — neutral
            else:
                for c in legal_cards:
                    scores[c.id] = 30.0 - c.rank * 2.0  # lower is safer
        else:
            # Can throw off — play highest from longest suit (can't win)
            groups = _helper_suit_groups(legal_cards)
            for c in legal_cards:
                suit_len = len(groups.get(c.suit, []))
                scores[c.id] = 50.0 + c.rank * 3.0 + suit_len * 2.0
    else:
        # Defender in betl — want declarer to win tricks
        if is_leading:
            groups = _helper_suit_groups(legal_cards)
            for c in legal_cards:
                suit_len = len(groups.get(c.suit, []))
                if c.rank == 8:
                    # Aces always win for us (bad) — avoid
                    scores[c.id] = 10.0 - suit_len
                else:
                    # Non-ace from shortest suit — may catch declarer
                    scores[c.id] = 60.0 + c.rank * 2.0 - suit_len * 5.0
        elif must_follow:
            # Try to play just below declarer's card to not waste high cards
            if played:
                decl_card = next((c for pid, c in played if pid == declarer_id), None)
                for c in legal_cards:
                    if decl_card and c.rank > decl_card.rank:
                        # Over declarer — good (they take trick)... wait, no.
                        # We want declarer to win, so we play LOW to let them win
                        scores[c.id] = 30.0 - c.rank  # save high cards
                    elif decl_card and c.rank < decl_card.rank:
                        # Under declarer — they'd win with their card
                        # Play highest under to waste less
                        scores[c.id] = 50.0 + c.rank * 2.0
                    else:
                        scores[c.id] = 40.0
            else:
                for c in legal_cards:
                    scores[c.id] = 40.0 - c.rank  # play low
        else:
            # Can throw off
            for c in legal_cards:
                scores[c.id] = 30.0 - c.rank  # play low, save high

    return scores


class PlayerAlice(WeightedRandomPlayer):
    """Alice: AGGRESSIVE Preferans player aiming for HIGH scores.

    Key strategies (iteration 48):
    - Iter4 50-game: +831 across 29 games (28.7/game). Declaring 19/20
      wins (+1067). 95% declaring win rate. ZERO whisting losses.
    - Card play: declarer leads short-suit kings after aces (Phase 2.5);
      whister prioritizes trump aces to strip declarer's ruffing power.
    - Whist rates bumped 2-5% (zero losses justify): 1A est>=1.5 97%,
      1A est>=1.0 74%, 0A est>=1.0 65%, 0A est>=1.5 82%.
    - 0A high-card dense bid rate 38→42%: captures more marginal 0A bids.
    """

    # Default probability thresholds for bid_intent
    _DEFAULT_BETL_THRESHOLD = 0.80
    _DEFAULT_IN_HAND_THRESHOLD = 0.80
    _DEFAULT_SANS_THRESHOLD = 0.70
    _DEFAULT_SUIT_THRESHOLD = 0.60

    def __init__(self, seed: int | None = None, name: str = "Alice", *,
                 betl_threshold: float | None = None,
                 in_hand_threshold: float | None = None,
                 sans_threshold: float | None = None,
                 suit_threshold: float | None = None):
        super().__init__(name, seed=seed,
                         w_pass=45, w_game=45, w_in_hand=5, w_betl=1, w_sans=1)
        self.BETL_THRESHOLD = betl_threshold if betl_threshold is not None else self._DEFAULT_BETL_THRESHOLD
        self.IN_HAND_THRESHOLD = in_hand_threshold if in_hand_threshold is not None else self._DEFAULT_IN_HAND_THRESHOLD
        self.SANS_THRESHOLD = sans_threshold if sans_threshold is not None else self._DEFAULT_SANS_THRESHOLD
        self.SUIT_THRESHOLD = suit_threshold if suit_threshold is not None else self._DEFAULT_SUIT_THRESHOLD
        self._cards_played = 0
        self._total_hand_size = 10
        self._is_declarer = False
        self._trump_suit_val = None   # suit enum value when we're declarer
        self._highest_bid_seen = 0    # track auction escalation
        self._betl_intent = False     # True when bidding in_hand with betl in mind
        self._whist_call_count = 0    # how many times we called whist this round
        self._trump_leads = 0         # track trump leads as declarer for smart management
        self._ctx = None              # CardPlayContext set before choose_card
        self._bid_intent_type = None  # set by bid_intent: 'betl','in_hand','sans','suit', or None
        self._strongest_suit = None   # real Suit enum of strongest suit

    # ------------------------------------------------------------------
    # Hand evaluation helpers
    # ------------------------------------------------------------------

    def _suit_groups(self, hand):
        """Group cards by suit. Returns {suit_value: [Card, ...]} sorted high-to-low."""
        groups = {}
        for c in hand:
            groups.setdefault(c.suit, []).append(c)
        for s in groups:
            groups[s].sort(key=lambda c: c.rank, reverse=True)
        return groups

    def _count_aces(self, hand):
        return sum(1 for c in hand if c.rank == 8)

    def _count_high_cards(self, hand):
        """Count cards rank >= Queen (6)."""
        return sum(1 for c in hand if c.rank >= 6)

    def _best_trump_suit(self, hand):
        """Find best suit for trump: longest suit, break ties by total rank."""
        groups = self._suit_groups(hand)
        best_suit = None
        best_score = -1
        for suit, cards in groups.items():
            score = len(cards) * 100 + sum(c.rank for c in cards)
            if score > best_score:
                best_score = score
                best_suit = suit
        return best_suit, best_score

    def _hand_strength_for_suit(self, hand, trump_suit):
        """Estimate how many tricks we can win with this trump suit."""
        groups = self._suit_groups(hand)
        tricks = 0.0
        trump_cards = groups.get(trump_suit, [])
        has_trump_ace = any(c.rank == 8 for c in trump_cards)
        has_trump_king = any(c.rank == 7 for c in trump_cards)

        # Pre-compute gap detection before the loop (needed inside loop)
        has_trump_queen = any(c.rank == 6 for c in trump_cards)
        has_trump_jack = any(c.rank == 5 for c in trump_cards)
        trump_has_gap = has_trump_ace and not has_trump_king and not has_trump_queen

        # Count trump tricks
        for c in trump_cards:
            if c.rank == 8:  # Ace
                tricks += 1.0
            elif c.rank == 7:  # King
                if has_trump_ace:
                    tricks += 0.95  # A draws opponents, K nearly guaranteed
                elif len(trump_cards) >= 3:
                    tricks += 0.75  # G13 iter1: K without ace overvalued at 0.85
                elif len(trump_cards) >= 2:
                    tricks += 0.55
                else:
                    tricks += 0.2
            elif c.rank >= 5:  # J/Q
                if len(trump_cards) >= 4 and has_trump_ace and has_trump_king:
                    tricks += 0.75  # AK draw opponents' honors first
                elif len(trump_cards) >= 4:
                    # Trump gap: A but no K/Q means opponents hold BOTH K and Q
                    # above our J/Q. Ace draws one, but the other remains.
                    # Game 7: A-J-8-7♥, J valued 0.5 but lost to Q♥ → only
                    # 3/6 tricks → -66. J/Q contribute ~0.15 with gap.
                    if trump_has_gap:
                        tricks += 0.15
                    else:
                        tricks += 0.5
                elif len(trump_cards) >= 3:
                    tricks += 0.25
            elif c.rank >= 3 and len(trump_cards) >= 5:  # low trump with 5+ length
                tricks += 0.35

        # 4th+ trump with Ace control: distribution value after Ace draws
        # Trump gap penalty: A without K/Q means opponents hold KQJ above our
        # 10/9/8. After ace clears one card, remaining trumps still lose to 2+
        # opponent honors. Game 50: A-10-9-8♥ → only 1 trick (ace), lost -66.
        # AK gap below: AK but no Q or J — 3rd+ trumps (10/9/8/7) face
        # opponent Q/J/10 after A and K draw 2 rounds. Game 21: AK98♣ →
        # A,K won tricks 1-2, but 9♣ lost to J♣. Est 4.85, actual 3 tricks.
        has_ak_gap_below = (has_trump_ace and has_trump_king
                            and not has_trump_queen and not has_trump_jack
                            and len(trump_cards) >= 4)
        if has_trump_ace and len(trump_cards) >= 4:
            if trump_has_gap:
                tricks += 0.20  # reduced from 0.50: filler trumps are weak
            elif has_ak_gap_below:
                tricks += 0.30  # AK but gap below: 3rd+ trumps are weak
            else:
                tricks += 0.50

        # Long trump suit bonus (extra trumps can ruff)
        # Reduced when trump has gap (A but no K/Q) — filler trumps lose to
        # opponent honors. Game 50: 4♥ A-10-9-8 got +0.3 but all 3 filler lost.
        if len(trump_cards) >= 5:
            gap_factor = 0.5 if trump_has_gap else (0.7 if has_ak_gap_below else 1.0)
            tricks += (len(trump_cards) - 4) * 0.7 * gap_factor
        elif len(trump_cards) >= 4:
            if trump_has_gap:
                tricks += 0.15
            elif has_ak_gap_below:
                tricks += 0.20
            else:
                tricks += 0.3

        # Side suits
        for suit, cards in groups.items():
            if suit == trump_suit:
                continue
            has_ace = any(c.rank == 8 for c in cards)
            for c in cards:
                if c.rank == 8:  # Ace
                    tricks += 0.9
                elif c.rank == 7:  # King
                    if has_ace:
                        tricks += 0.95  # A cashes first, K is master
                    elif len(cards) >= 2:
                        tricks += 0.80  # Guarded K, declarer controls tempo
                    else:
                        # Iter73: singleton K as declarer still ~50% trick —
                        # declarer controls tempo and can lead to it.
                        tricks += 0.35

        # Side-suit length bonus: long suits generate length winners.
        # 8 cards per suit total; with 4+ cards, opponents exhaust sooner.
        # Ace-headed suits are best, K-headed still good, others modest.
        for suit, cards in groups.items():
            if suit == trump_suit:
                continue
            if len(cards) >= 4:
                has_ace = any(c.rank == 8 for c in cards)
                has_king = any(c.rank == 7 for c in cards)
                if has_ace:
                    tricks += (len(cards) - 3) * 0.5
                elif has_king:
                    tricks += (len(cards) - 3) * 0.35
                else:
                    tricks += (len(cards) - 3) * 0.2

        # Void suits can ruff
        num_suits = len(groups)
        if num_suits <= 2 and len(trump_cards) >= 4:
            tricks += 1.5
        elif num_suits <= 3 and len(trump_cards) >= 3:
            tricks += 0.5

        # Multi-ace bonus: 2+ aces make the hand much more reliable
        total_aces = sum(1 for c in hand if c.rank == 8)
        if total_aces >= 2:
            tricks += 0.5

        # Short-trump side-suit vulnerability: with ≤3 trumps, opponents
        # share 5+ trumps and will ruff side-suit winners after 1-2 rounds.
        # Game 30 iter2: 3 clubs (A,J,9) + A♠ + A♥K♥ → est=6.0 but got 5
        # tricks because opponents trumped side aces with their 5 clubs.
        # Game 12 iter2: 4 clubs (K,Q,J,9) no ace + side aces → est too high.
        if len(trump_cards) <= 3:
            side_winners = sum(1 for c in hand if c.rank >= 7 and c.suit != trump_suit)
            if side_winners >= 3:
                tricks -= 1.2  # massive ruffing risk
            elif side_winners >= 2:
                tricks -= 0.6

        # No trump control: when trump has neither ace nor king, opponents
        # hold A,K above our best trump. Penalty reduced from iter3→4 levels
        # (-0.7/-1.0/-1.5) which made Alice too conservative — she passed too
        # many marginal declarations and suffered passive defender penalties.
        # Talon exchange often provides the missing honor.
        if not has_trump_ace and not has_trump_king:
            if len(trump_cards) >= 4:
                tricks -= 0.4  # opponents have A,K but length compensates
            elif len(trump_cards) >= 3:
                tricks -= 0.6
            else:
                tricks -= 1.0  # short + no honors = genuinely weak

        return tricks

    def _estimate_tricks_as_whister(self, hand, declarer_trump=None):
        """Estimate tricks we can take as a whisting defender (no trump of our own).

        Be conservative: declarer has trump and can ruff our side-suit winners.
        Only aces are near-guaranteed; kings/queens depend heavily on position.
        Penalize hands spread across many weak suits (easy for declarer to trump).
        Trump-aware: cards in declarer's trump suit are worth less (declarer has length).
        """
        tricks = 0.0
        groups = self._suit_groups(hand)
        unsupported_kings = 0  # kings without ace in same suit
        trump_suit_length = 0  # how many cards we hold in declarer's trump
        for suit, cards in groups.items():
            in_trump = (declarer_trump is not None and suit == declarer_trump)
            if in_trump:
                trump_suit_length = len(cards)
            has_ace = any(c.rank == 8 for c in cards)
            has_king = any(c.rank == 7 for c in cards)
            for c in cards:
                if c.rank == 8:  # Ace
                    # Ace in trump still good but slightly less reliable.
                    # 5+ card non-trump suit: only 3 cards remain for 2
                    # opponents → high void probability → ace gets trumped.
                    # Game 31 iter6: A♦ in 5 diamonds → declarer void → trumped → 0 tricks.
                    if in_trump:
                        # Trump ace is unbeatable — guaranteed 1 trick as whister.
                        # Previous 0.60 undervalued it; no card can beat the trump ace.
                        tricks += 0.85
                    elif len(cards) >= 5:
                        tricks += 0.65  # reduced from 0.85: ~25% trumping risk
                    else:
                        tricks += 0.85
                elif c.rank == 7:  # King
                    if in_trump:
                        if has_ace:
                            # Iter60: AKQ♣ in trump, est=0.70 → passed whist.
                            # After A clears one opponent trump, K is master.
                            tricks += 0.50
                        else:
                            # G6 iter10: long spades as trump → -80. King in trump
                            # is nearly worthless — declarer has trump length advantage.
                            tricks += 0.05
                    elif has_ace:
                        tricks += 0.65  # A-K in same suit is strong
                    elif len(cards) >= 3:
                        tricks += 0.30
                        unsupported_kings += 1
                    elif len(cards) >= 2:
                        tricks += 0.20
                        unsupported_kings += 1
                    else:
                        tricks += 0.1  # singleton King easily trumped
                        unsupported_kings += 1
                elif c.rank == 6 and len(cards) >= 3:  # Queen with length
                    if in_trump and has_ace and has_king:
                        # Iter60: AKQ♣ in trump → after AK clear 2 opponent trumps,
                        # Q is master or near-master. ~0.35 trick value.
                        tricks += 0.35
                    else:
                        tricks += 0.05 if in_trump else 0.15
                elif in_trump and c.rank >= 4:  # J/10 in trump suit
                    tricks += 0.05  # near-worthless in declarer's trump

        # Penalize hands with many weak short suits (singletons/doubletons without aces).
        # These are easily trumped by declarer and contribute no tricks.
        weak_short_suits = sum(
            1 for s, cards in groups.items()
            if len(cards) <= 2 and not any(c.rank == 8 for c in cards)
        )
        if weak_short_suits >= 3:
            tricks -= 0.3  # Very spread out, hard to take tricks
        elif weak_short_suits >= 2:
            tricks -= 0.1

        # Multiple unsupported kings compound overestimation: declarer can
        # ruff them all. G10 iter9: 3 unsupported kings → est ~1.85, won ~0.
        if unsupported_kings >= 3:
            tricks -= 0.4
        elif unsupported_kings >= 2:
            tricks -= 0.2

        # Jack-heavy hands look "full" but can't take tricks as whister.
        # G7 iter14: [[J,10,9,8],[A,J],[J,8],[10,8]] — 3 jacks, only ace
        # contributed. Jacks lose to K/Q/A and waste space.
        # G10 iter9: 1A + 3 jacks + 2 unsup queens → est ~1.0-1.2, lost -40.
        # -0.15 was too weak; bumped to -0.25 for 3+ jacks.
        total_jacks = sum(1 for c in hand if c.rank == 5)
        if total_jacks >= 3:
            tricks -= 0.25

        # Penalty for holding many cards in declarer's trump suit: these cards
        # are wasted — declarer has length advantage and will overtrump us.
        # G10 iter12: 4 spades in declarer's trump → -100. Cards are dead weight,
        # they crowd out useful cards and can't win tricks.
        if trump_suit_length >= 5:
            tricks -= 0.8  # Nearly impossible to take tricks
        elif trump_suit_length >= 4:
            tricks -= 0.5  # Raised from -0.3: G10 iter12 proved 4 trump = dead hand
        elif trump_suit_length >= 3:
            tricks -= 0.15

        # Low trump coverage: 0-2 cards in declarer's trump means our non-trump
        # aces are much more likely to be trumped. Declarer with 5+ trumps will
        # be void in some of our suits. Game 47 iter4: 0 diamonds vs diamonds
        # declarer, A♠ got trumped → -56. Game 23 iter4: 1 trump, 2 aces, 0 tricks.
        # Game 47 iter2: 2 trumps, A♥K♥ in 4-card suit → 0 tricks → -106.
        if declarer_trump is not None and trump_suit_length <= 2:
            non_trump_aces = sum(
                1 for c in hand if c.rank == 8 and c.suit != declarer_trump
            )
            # Game 36 iter5: 2 non-trump aces (A♦,A♥) with tc=1. Est ~2.4 but
            # P3 was void in diamonds → A♦ trumped → only 1 trick → -93.
            # Old penalty for 2A tc=1 was just -0.15 (generic 1-ace path).
            # With tc <= 1, each ace has ~30-40% chance of being trumped.
            if non_trump_aces >= 2 and trump_suit_length == 0:
                tricks -= 0.50  # both aces very likely get trumped
            elif non_trump_aces >= 2 and trump_suit_length <= 1:
                tricks -= 0.35  # 2 aces with 1 trump still very risky
            elif non_trump_aces >= 2 and trump_suit_length == 2:
                # 2 aces with only 2 trump cards: declarer has 6+ trumps,
                # likely void in 1+ suit. Game 1 iter12: 2A (A♠,A♥) + 2
                # diamonds (trump) → est=2.50, whisted at 100% → only 1
                # trick → -38. A♠ led into void declarer, trumped.
                tricks -= 0.25
            elif non_trump_aces >= 1 and trump_suit_length == 0:
                tricks -= 0.20  # ace vulnerable
            elif non_trump_aces >= 1 and trump_suit_length <= 1:
                tricks -= 0.15  # 1 trump doesn't protect aces
            elif non_trump_aces >= 1 and trump_suit_length == 2:
                # 2 low trumps: declarer has 6+ trumps, likely void in 1+ suit
                # Game 47 iter2: 2♣ (8♣,J♣) vs clubs → A♥ trumped → 0 tricks
                tricks -= 0.10

        # Penalty for multiple unsupported queens — G5 iter19: 3 queens scattered
        # across suits without aces contributed nothing, inflated est ~1.0-1.2.
        # Queens can't beat K/A as whister; same penalty as Bob/Carol.
        unsupported_queens = 0
        for suit, cards in groups.items():
            in_trump = (declarer_trump is not None and suit == declarer_trump)
            if in_trump:
                continue
            has_ace = any(c.rank == 8 for c in cards)
            has_king = any(c.rank == 7 for c in cards)
            has_queen = any(c.rank == 6 for c in cards)
            # Iter68: Q♦ with K♦ is NOT unsupported — K protects Q and may
            # promote it. Only truly lone queens (no A or K) are unreliable.
            if has_queen and not has_ace and not has_king:
                unsupported_queens += 1
        if unsupported_queens >= 3:
            tricks -= 0.25
        elif unsupported_queens >= 2:
            tricks -= 0.15

        # Bonus for A-K in same non-trump suit: guaranteed ~1.5 tricks together.
        # G5 iter12: Alice had AK hearts but passed whist; AK combo is very strong.
        # EXCEPTION: 5+ card suits — length is dead weight as whister (declarer
        # trumps after 2 rounds). Game 16 iter5: AK♥ in 5 hearts → est 1.75 →
        # whisted at 100% → 0 tricks → -66. AK gives ~1.5 tricks max regardless
        # of suit length; bonus only applies to short/medium AK suits.
        for suit, cards in groups.items():
            in_trump = (declarer_trump is not None and suit == declarer_trump)
            if in_trump:
                continue
            has_ace = any(c.rank == 8 for c in cards)
            has_king = any(c.rank == 7 for c in cards)
            if has_ace and has_king and len(cards) <= 4:
                tricks += 0.25  # Extra bonus on top of individual A/K values

        # 4+ card ace-headed non-trump suit (without king): length winners.
        # Iter27: A♠ J♠ 9♠ 8♠ vs clubs trump → Carol took 3 extra tricks beyond
        # ace after declarer exhausted trumps. Est was 0.90 but actual was 5 tricks.
        # After ace cashes, remaining 3+ cards promote when opponents exhaust suit
        # and declarer runs out of trumps. +0.25 bonus (conservative).
        # GATE: only applies with 3+ trumps. With 0-2 trumps, declarer will
        # trump the ace itself. Game 47 iter2: 4♥ AKJ10 + 2 trumps → ace trumped.
        if declarer_trump is not None and trump_suit_length >= 3:
            for suit, cards in groups.items():
                if suit != declarer_trump and len(cards) >= 4:
                    has_ace = any(c.rank == 8 for c in cards)
                    has_king = any(c.rank == 7 for c in cards)
                    if has_ace and not has_king:
                        tricks += 0.25
                        break  # Only count once

        # Long non-trump suit penalty: 5+ cards in one non-trump suit = dead weight.
        # G8 iter19: AKDJ8 in suit 1 (5 cards, non-trump). Declarer ruffs after
        # first 1-2 tricks; remaining 3 cards are wasted. Bob/Carol already have this.
        # EXCEPTION: AK-headed long suits are genuine trick sources (A + promoted K),
        # not dead weight. G15 iter5: [[A,K,J,9,8,7],...] — AK anchor = ~1.5 tricks
        # from that suit alone. Don't penalize these.
        # G12 iter8: [[A,D,9,8,7],...] — 5-card suit with ace but no king. Ace
        # takes 1 trick but remaining 4 cards (D,9,8,7) are dead weight that
        # declarer ruffs. Increased penalty for ace-only long suits.
        if declarer_trump is not None:
            total_aces_check = sum(1 for c in hand if c.rank == 8)
            for suit, cards in groups.items():
                if suit != declarer_trump and len(cards) >= 5:
                    has_ace = any(c.rank == 8 for c in cards)
                    has_king = any(c.rank == 7 for c in cards)
                    if has_ace and has_king:
                        # AK anchor: A and K give ~1.5 tricks, but remaining
                        # 3+ cards are dead weight. With 5+ cards, only 3 remain
                        # for opponents — declarer likely void, aces get trumped.
                        # Game 31 iter6: AK♦ in 5 diamonds, est 1.05 → followed
                        # → A♦ trumped trick 1 → 0 tricks → -100.
                        tricks -= 0.45
                    elif has_ace and not has_king:
                        tricks -= 0.55  # Ace-only 5+ card suit: high void risk + dead weight
                        break
                    else:
                        # G12 iter9: [[D,J,10,9,8],[A,J,8],[K,7],[]] — 5-card
                        # suit without A/K, only 1 ace in hand. Junk suit crowds
                        # out useful cards and gets trumped after 0-1 tricks.
                        # G18 iter5: K-headed 5-card non-trump [[K,D,J,9,7]] —
                        # K adds 0.30 but suit is dead weight after 0-1 tricks.
                        # Bumped from -0.40 to -0.50 for 1A hands.
                        tricks -= 0.50 if total_aces_check <= 1 else 0.35
                        break  # Only penalize once

        # Void-suit bonus: void in a non-trump suit = ruffing potential.
        # Bob already has this (+0.25). Ruffing lets us win tricks even with
        # low trumps, making the hand more actionable for whisting.
        if declarer_trump is not None:
            all_suits = {1, 2, 3, 4}
            held_suits = set(groups.keys())
            void_suits = all_suits - held_suits - {declarer_trump}
            if void_suits:
                tricks += 0.25

        # Lone-ace penalty: when 1 ace is the only card rank >= Queen (6) and
        # remaining cards are scattered across 3+ non-trump suits, the ace is
        # isolated — junk cards (J/10/9/8/7) can't convert tricks as whister.
        # Iter23 G3: [[D,10,9,7],[K,D,J],[10,7],[A]] — 1A, rest is J/10/9/7.
        # Iter23 G7: [[A,J,9],[9,8,7],[K,7],[10,7]] — 1A, rest is J/9/8/7.
        # Both had inflated est ~1.0-1.2 and lost -100/-80.
        total_aces = sum(1 for c in hand if c.rank == 8)
        total_high = sum(1 for c in hand if c.rank >= 6)  # Q/K/A
        if total_aces == 1 and total_high <= 2:
            # Only 1 ace + at most 1 other high card, rest is junk
            non_trump_suits = sum(
                1 for s in groups if s != declarer_trump
            ) if declarer_trump is not None else len(groups)
            if non_trump_suits >= 3:
                tricks -= 0.20

        return max(tricks, 0.0)

    def _best_available_est(self, hand, min_game_val):
        """Estimate tricks for the best suit available at a given game level.

        At game_val=4, only hearts(4) and clubs(5) are available.
        At game_val=5, only clubs(5) is available.
        Returns the max est_tricks across available suits.
        Prevents bidding based on a strong suit that can't be chosen."""
        best = 0.0
        for suit, bid_val in _SUIT_BID_VALUE.items():
            if bid_val >= min_game_val:
                est = self._hand_strength_for_suit(hand, suit)
                if est > best:
                    best = est
        return best

    def _is_good_betl_hand(self, hand):
        """AGGRESSIVE betl: trust talon to fix 1-2 dangers."""
        a = betl_hand_analysis(hand)
        if a["danger_count"] == 0:
            return True
        # Allow up to 2 dangers if enough safe suits (talon can discard them)
        if a["danger_count"] <= 2 and a["safe_suits"] >= 2:
            return True
        return False

    def _is_good_betl_hand_in_hand(self, hand):
        """In-hand betl (no talon): must be zero danger with 3+ safe suits."""
        a = betl_hand_analysis(hand)
        return a["danger_count"] == 0 and a["safe_suits"] >= 3

    def _is_good_sans_hand(self, hand):
        """Check if hand has enough aces and high cards for sans (need 6 tricks).

        Aggressive: 3 aces + 7 high cards is viable (G5 iter9: 3A+K+K+D scored
        +140 with sans). Relaxed from strict 4 aces / 7 high.
        """
        aces = self._count_aces(hand)
        high = self._count_high_cards(hand)
        return (aces >= 4 and high >= 6) or (aces >= 3 and high >= 7)

    def _compute_hand_probabilities(self, hand):
        """Compute win probabilities and determine strongest suit via simulation.

        Returns (probs_dict, strongest_real_suit_enum) or (None, None) on failure.
        """
        from compute_probabilities import simulate_combination

        card_ids = [c.id for c in hand]

        # Canonical encoding (same logic as preferans_server._cards_to_canonical)
        RANK_CH = {'7': 'x', '8': 'x', '9': 'x', '10': 'x',
                   'J': 'J', 'Q': 'D', 'K': 'K', 'A': 'A'}
        CARD_ORDER = {'A': 0, 'K': 1, 'D': 2, 'J': 3, 'x': 4}

        suits = {}
        for cid in card_ids:
            rank, suit = cid.split('_')
            suits.setdefault(suit, []).append(RANK_CH[rank])
        for s in suits:
            suits[s].sort(key=lambda c: CARD_ORDER[c])

        pairs = [(s, ''.join(suits[s])) for s in suits]
        for s in ['spades', 'diamonds', 'hearts', 'clubs']:
            if s not in suits:
                pairs.append((s, ''))

        pairs.sort(key=lambda p: (-len(p[1]), [CARD_ORDER[c] for c in p[1]]))

        # First suit in canonical order = strongest suit
        strongest_suit_name = pairs[0][0]
        _NAME_TO_SUIT = {v: k for k, v in SUIT_NAMES.items()}
        strongest_suit = _NAME_TO_SUIT.get(strongest_suit_name)

        encoding = '-'.join(pat for _, pat in pairs if pat)
        seed = hash(encoding) & 0x7FFFFFFF
        probs = simulate_combination(encoding, seed=seed)

        return probs, strongest_suit

    # ------------------------------------------------------------------
    # Bidding — probability-driven
    # ------------------------------------------------------------------

    _SUIT_TO_IN_HAND_VALUE = {Suit.SPADES: 2, Suit.DIAMONDS: 3, Suit.HEARTS: 4, Suit.CLUBS: 5}

    def bid_intent(self, hand, legal_bids):
        bid_types = {b["bid_type"] for b in legal_bids}

        if bid_types == {"pass"}:
            return {"bid": legal_bids[0], "intent": "forced pass (no other options)"}

        # IN_HAND_DECLARING phase: all bids are in_hand with value > 0
        # Pick the value matching our strongest suit
        in_hand_declaring = all(
            b["bid_type"] == "in_hand" and b.get("value", 0) > 0
            for b in legal_bids if b["bid_type"] != "pass"
        ) and any(b["bid_type"] == "in_hand" and b.get("value", 0) > 0 for b in legal_bids)

        if in_hand_declaring:
            # Compute strongest suit if not already known
            if not self._strongest_suit and hand:
                _, strongest_suit = self._compute_hand_probabilities(hand)
                self._strongest_suit = strongest_suit

            target_value = self._SUIT_TO_IN_HAND_VALUE.get(self._strongest_suit, 2)
            # Find the bid with our target value, or the lowest available
            best_bid = None
            for b in legal_bids:
                if b["bid_type"] == "in_hand" and b.get("value") == target_value:
                    best_bid = b
                    break
            if not best_bid:
                # Target suit not available (too low); pick lowest available
                ih_bids = [b for b in legal_bids if b["bid_type"] == "in_hand" and b.get("value", 0) > 0]
                if ih_bids:
                    best_bid = ih_bids[0]
                else:
                    # Only pass available
                    best_bid = next(b for b in legal_bids if b["bid_type"] == "pass")
            suit_name = {2: 'spades', 3: 'diamonds', 4: 'hearts', 5: 'clubs'}.get(best_bid.get("value"), '?')
            return {"bid": best_bid,
                    "intent": f"in_hand declaring {suit_name} (strongest={SUIT_NAMES.get(self._strongest_suit, '?')})"}

        # Reset per-round state on first bid call
        self._cards_played = 0
        self._is_declarer = False
        self._highest_bid_seen = 0
        self._trump_suit_val = None
        self._betl_intent = False
        self._whist_call_count = 0
        self._trump_leads = 0
        self._bid_intent_type = None
        self._strongest_suit = None

        # Track auction escalation
        game_bids = [b for b in legal_bids if b["bid_type"] == "game"]
        if game_bids:
            min_val = min(b.get("value", 2) for b in game_bids)
            self._highest_bid_seen = max(self._highest_bid_seen, min_val - 1)

        if not hand:
            pass_bid = next((b for b in legal_bids if b["bid_type"] == "pass"), None)
            if pass_bid:
                return {"bid": pass_bid, "intent": "pass — no hand"}
            return super().bid_intent(hand, legal_bids)

        # Compute probabilities via simulation
        probs, strongest_suit = self._compute_hand_probabilities(hand)
        self._strongest_suit = strongest_suit

        if probs is None:
            pass_bid = next((b for b in legal_bids if b["bid_type"] == "pass"), None)
            if pass_bid:
                return {"bid": pass_bid, "intent": "pass — probability computation failed"}
            return super().bid_intent(hand, legal_bids)

        p_betl = probs.get('betl_win_prob', 0)
        p_in_hand = probs.get('in_hand_win_prob', 0)
        p_sans = probs.get('sans_win_prob', 0)
        p_suit = probs.get('strongest_suit_win_prob_all_follow_P1', 0)

        prob_str = (f"suit={p_suit:.0%} inH={p_in_hand:.0%} "
                    f"betl={p_betl:.0%} sans={p_sans:.0%}")

        # Check thresholds in priority order
        if p_betl >= self.BETL_THRESHOLD:
            self._bid_intent_type = 'betl'
            betl_bids = [b for b in legal_bids if b["bid_type"] == "betl"]
            in_hand_bids = [b for b in legal_bids if b["bid_type"] == "in_hand"]
            if betl_bids:
                return {"bid": betl_bids[0],
                        "intent": f"betl — prob {p_betl:.0%} >= {self.BETL_THRESHOLD:.0%} ({prob_str})"}
            if in_hand_bids:
                self._betl_intent = True
                return {"bid": in_hand_bids[0],
                        "intent": f"in_hand (betl intent) — prob {p_betl:.0%} >= {self.BETL_THRESHOLD:.0%} ({prob_str})"}

        if p_in_hand >= self.IN_HAND_THRESHOLD:
            self._bid_intent_type = 'in_hand'
            in_hand_bids = [b for b in legal_bids if b["bid_type"] == "in_hand"]
            if in_hand_bids:
                return {"bid": in_hand_bids[0],
                        "intent": f"in_hand — prob {p_in_hand:.0%} >= {self.IN_HAND_THRESHOLD:.0%} ({prob_str})"}

        if p_sans >= self.SANS_THRESHOLD:
            self._bid_intent_type = 'sans'
            sans_bids = [b for b in legal_bids if b["bid_type"] == "sans"]
            if sans_bids:
                return {"bid": sans_bids[0],
                        "intent": f"sans — prob {p_sans:.0%} >= {self.SANS_THRESHOLD:.0%} ({prob_str})"}

        if p_suit >= self.SUIT_THRESHOLD:
            self._bid_intent_type = 'suit'
            if game_bids:
                return {"bid": game_bids[0],
                        "intent": f"game — suit prob {p_suit:.0%} >= {self.SUIT_THRESHOLD:.0%} ({prob_str})"}

        # Below all thresholds — pass
        self._bid_intent_type = None
        pass_bid = next((b for b in legal_bids if b["bid_type"] == "pass"), None)
        if pass_bid:
            return {"bid": pass_bid,
                    "intent": f"pass — below thresholds ({prob_str})"}

        return super().bid_intent(hand, legal_bids)

    # ------------------------------------------------------------------
    # Exchange — smart discard: keep trump suit + aces, create voids
    # ------------------------------------------------------------------

    def discard_decision(self, hand_card_ids, talon_card_ids):
        """Discard 2 cards from 12 using BasePlayer.score_discard_cards.
        Uses betl scoring if bid_intent was betl, otherwise suit scoring
        with the strongest suit as trump."""
        all_ids = hand_card_ids + talon_card_ids

        if self._bid_intent_type == 'betl':
            scores = self.score_discard_cards(all_ids, 'betl')
            contract_label = 'betl'
            trump_name = None
        else:
            trump_name = SUIT_NAMES.get(self._strongest_suit, 'spades')
            scores = self.score_discard_cards(all_ids, 'suit', trump_suit=trump_name)
            contract_label = f'suit ({trump_name})'

        sorted_cards = sorted(all_ids, key=lambda c: scores[c], reverse=True)
        return {"discard": sorted_cards[:2],
                "intent": f"discard by score — {contract_label}"}

    def _try_betl_discard(self, all_ids, card_rank, card_suit):
        """Try discarding for betl. Returns discard decision if resulting hand
        would be good for betl, otherwise None.

        Only triggers when the 12-card pool already has ≤ 3 high cards (Q/K/A)
        AND the resulting 10-card hand has zero danger, max_rank ≤ 6 (Queen),
        and no Aces.
        """
        from itertools import combinations

        suit_map_vals = {"spades": 4, "diamonds": 2, "hearts": 3, "clubs": 1}

        # Quick pre-check: if pool has too many high cards, skip betl discard
        high_count = sum(1 for c in all_ids if card_rank(c) >= 6)
        if high_count > 3:
            return None

        class FakeCard:
            def __init__(self, rank, suit):
                self.rank = rank
                self.suit = suit

        def ids_to_cards(ids):
            cards = []
            for cid in ids:
                r = card_rank(cid)
                sv = suit_map_vals.get(card_suit(cid))
                if sv is not None:
                    cards.append(FakeCard(r, sv))
            return cards

        # Sort by rank desc — try discarding the 2 most dangerous cards
        sorted_by_rank = sorted(all_ids, key=lambda c: card_rank(c), reverse=True)
        best_discard = None
        best_analysis = None
        best_score = -1
        for pair in combinations(range(min(6, len(sorted_by_rank))), 2):
            discard = [sorted_by_rank[i] for i in pair]
            remaining = [c for c in all_ids if c not in discard]
            hand = ids_to_cards(remaining)
            a = betl_hand_analysis(hand)
            if a["danger_count"] == 0 and not a["has_ace"] and a["max_rank"] <= 6:
                # Score: prefer lower max_rank, more safe suits, fewer high cards
                score = (7 - a["max_rank"]) * 100 + a["safe_suits"] * 10 - a["high_card_count"]
                if score > best_score:
                    best_score = score
                    best_discard = discard
                    best_analysis = a

        if best_discard:
            return {"discard": best_discard,
                    "intent": f"betl discard — safe (max_rank={best_analysis['max_rank']}, "
                              f"safe_suits={best_analysis['safe_suits']})"}
        return None

    def choose_discard(self, hand_card_ids, talon_card_ids):
        self._is_declarer = True
        decision = self.discard_decision(hand_card_ids, talon_card_ids)
        return decision["discard"]

    # ------------------------------------------------------------------
    # 12-card evaluation — unified discard + contract selection
    # ------------------------------------------------------------------

    def _score_hand_for_contract(self, hand, contract_type, trump_suit=None):
        """Score a 10-card hand for a specific contract.
        Returns a numeric score (higher = better hand for that contract)."""
        if contract_type == "betl":
            a = betl_hand_analysis(hand)
            # CRITICAL: Aces are guaranteed losers in betl — declarer MUST
            # lose every trick. G2/G12/G13 iter25: -360 total from declaring
            # betl with 1-3 aces. Any ace → disqualify betl entirely.
            if a["has_ace"]:
                return -200
            # Also reject if too many high cards (K/Q) — they win tricks
            if a["high_card_count"] >= 3:
                return -150
            # Exposed high cards in short suits: Q/J/K in a suit with only
            # 1-2 cards means after playing the lower card(s), the high card
            # is forced to win against opponent leads below it.
            # Game 48 iter15: 7♠Q♠ → played 7♠, left with Q♠ → Carol led
            # 10♠ → Q♠ won → instant betl loss (-120).
            # betl_suit_safety doesn't catch this — it only flags Aces.
            groups = {}
            for c in hand:
                groups.setdefault(c.suit, []).append(c)
            exposed_dangers = 0
            for suit, cards in groups.items():
                if len(cards) <= 2:
                    highest = max(c.rank for c in cards)
                    # Q(6), K(7) in a 1-2 card suit: after low card(s) used,
                    # high card wins against 8/9/10/J leads
                    if highest >= 6:
                        exposed_dangers += 1
            if exposed_dangers >= 2:
                return -140  # Multiple exposed dangers = very risky betl
            # Betl score: zero danger is great, fewer dangers = better
            # Base: 100 if zero danger, penalize each danger heavily
            score = 100 - a["danger_count"] * 40 - exposed_dangers * 30
            score += a["safe_suits"] * 5
            score -= a["max_rank"] * 3
            # Alice aggressive: low cards + spread also good
            if a["max_rank"] <= 5:
                score += 20
            if a["max_rank"] <= 6 and a["max_suit_len"] <= 3:
                score += 10
            return score
        elif contract_type == "sans":
            if not self._is_good_sans_hand(hand):
                return -100  # not viable
            aces = self._count_aces(hand)
            high = self._count_high_cards(hand)
            # Iter13: Sans discard must value suit length. N-aggressive kept
            # 7 clubs (AKQJ97♣) and won all 10 tricks. Alice discarded J♣/7♣
            # keeping 7♠/8♥ (useless singletons) → only 4 clubs → lost 2 tricks.
            # Sans scoring was 80+aces*15+high*5 = identical for both discards.
            # Long suits are the #1 trick source in sans — after opponents run
            # out, all remaining cards in that suit are guaranteed winners.
            groups = self._suit_groups(hand)
            long_suit_bonus = 0
            for suit, cards in groups.items():
                if len(cards) >= 5:
                    has_ace = any(c.rank == 8 for c in cards)
                    long_suit_bonus += len(cards) * (4 if has_ace else 2)
                elif len(cards) >= 4:
                    has_ace = any(c.rank == 8 for c in cards)
                    long_suit_bonus += len(cards) * (2 if has_ace else 1)
            return 80 + aces * 15 + high * 5 + long_suit_bonus
        else:
            # Suit contract
            strength = self._hand_strength_for_suit(hand, trump_suit)
            groups = self._suit_groups(hand)
            trump_len = len(groups.get(trump_suit, []))
            # Scale: 6 tricks needed. strength * 15 gives a good range.
            # Bonus for trump length, penalty for suit cost
            cost_penalty = (_SUIT_BID_VALUE.get(trump_suit, 2) - 2) * 2
            score = strength * 15 + trump_len * 3 - cost_penalty
            # Threshold cliff: suit contracts need 6 tricks. Penalize weak
            # hands but not as steeply as iter4 (-25 per trick) which made
            # Alice too conservative, passing marginal declarations.
            if strength < 4.5:
                score -= (4.5 - strength) * 18  # steep but not crippling
            elif strength < 5.5:
                score -= (5.5 - strength) * 6   # moderate caution zone
            return score

    def _evaluate_12_card_contracts(self, hand_card_ids, talon_card_ids, winner_bid):
        """Evaluate all 66 discard combos × all legal contracts.
        Returns {"discard": [id, id], "contract": (type, trump, level)}.
        Also stores self._ranked_discards: list of (discard, contract, score) sorted best-first."""
        from itertools import combinations
        import heapq

        all_ids = hand_card_ids + talon_card_ids
        min_bid = winner_bid.effective_value if winner_bid else 0

        pool_aces = sum(1 for cid in all_ids if cid.startswith("A_"))
        skip_betl = pool_aces >= 1

        # Collect top-N candidates using a heap (keep top 10)
        top_n = []
        TOP_K = 10

        def _push(score, discard, contract):
            entry = (score, discard, contract)
            if len(top_n) < TOP_K:
                heapq.heappush(top_n, entry)
            elif score > top_n[0][0]:
                heapq.heapreplace(top_n, entry)

        for pair in combinations(range(len(all_ids)), 2):
            discard = [all_ids[i] for i in pair]
            remaining_ids = [cid for cid in all_ids if cid not in discard]
            hand = _ids_to_cards(remaining_ids)

            if not skip_betl:
                betl_sc = self._score_hand_for_contract(hand, "betl")
                _push(betl_sc, discard, ("betl", None, 6))

            if min_bid <= 7:
                sans_sc = self._score_hand_for_contract(hand, "sans")
                _push(sans_sc, discard, ("sans", None, 7))

            for suit, suit_level in _SUIT_BID_VALUE.items():
                if suit_level < min_bid:
                    continue
                level = max(suit_level, min_bid)
                sc = self._score_hand_for_contract(hand, "suit", trump_suit=suit)
                _push(sc, discard, ("suit", SUIT_NAMES[suit], level))

        # Sort best-first
        ranked = sorted(top_n, key=lambda x: -x[0])
        self._ranked_discards = [(d, c, s) for s, d, c in ranked]

        best = ranked[0] if ranked else None
        if best:
            return {"discard": best[1], "contract": best[2]}
        return {"discard": None, "contract": None}

    # ------------------------------------------------------------------
    # Contract — use pre-evaluated choice or fall back to heuristic
    # ------------------------------------------------------------------

    def bid_decision(self, hand, legal_levels, winner_bid):
        """Return contract matching bid_intent."""
        # In-hand betl intent
        if self._betl_intent and 6 in legal_levels:
            return {"contract_type": "betl", "trump": None, "level": 6,
                    "intent": "betl — bid_intent"}

        if self._bid_intent_type == 'betl' and 6 in legal_levels:
            return {"contract_type": "betl", "trump": None, "level": 6,
                    "intent": "betl — bid_intent"}

        if self._bid_intent_type == 'sans' and 7 in legal_levels:
            return {"contract_type": "sans", "trump": None, "level": 7,
                    "intent": "sans — bid_intent"}

        # Suit contract (default): use strongest suit
        min_bid = winner_bid.effective_value if winner_bid else 0
        suit_bid = {Suit.SPADES: 2, Suit.DIAMONDS: 3, Suit.HEARTS: 4, Suit.CLUBS: 5}

        # Prefer the strongest suit from probability computation if available
        best_suit = self._strongest_suit
        if best_suit and suit_bid.get(best_suit, 0) < min_bid:
            best_suit = None

        # Fallback: find best available suit by length + rank
        if best_suit is None:
            groups = self._suit_groups(hand)
            best_score = -1
            for suit, cards in groups.items():
                if suit_bid.get(suit, 0) < min_bid:
                    continue
                score = len(cards) * 100 + sum(c.rank for c in cards)
                cost_penalty = (suit_bid[suit] - 2) * 10
                score -= cost_penalty
                if score > best_score:
                    best_score = score
                    best_suit = suit
            if best_suit is None:
                best_suit = min((s for s, v in suit_bid.items() if v >= min_bid),
                                key=lambda s: suit_bid[s])

        trump_name = SUIT_NAMES[best_suit] if best_suit else "spades"
        self._trump_suit_val = best_suit

        suit_levels = [l for l in legal_levels if l not in (6, 7)]
        if suit_levels:
            level = min(suit_levels)
        elif legal_levels:
            level = min(legal_levels)
        else:
            level = 2
        return {"contract_type": "suit", "trump": trump_name, "level": level,
                "intent": f"suit {trump_name} level {level} — bid_intent"}

    def choose_contract(self, legal_levels, hand, winner_bid):
        decision = self.bid_decision(hand, legal_levels, winner_bid)
        return decision["contract_type"], decision["trump"], decision["level"]

    # ------------------------------------------------------------------
    # Whisting — hand-strength aware whisting
    # ------------------------------------------------------------------

    def decide_to_call(self, hand, contract_type, trump_suit, legal_actions):
        """Decide whether to call when the other defender passed — AGGRESSIVE.

        Call means taking responsibility for BOTH defenders' combined tricks.
        Need ~4 tricks alone to justify. Higher bar than follow.
        """
        # Store declarer's trump suit for card play ruffing
        if not self._is_declarer and trump_suit is not None:
            self._trump_suit_val = trump_suit

        # Aggressive rule 3: first defender passed, follow if sum_reasons >= 2
        action_types_call = [a["action"] for a in legal_actions]
        if "follow" in action_types_call:
            should, n_tt, s_reasons = self._should_follow_heuristic(hand, trump_suit)
            if should or s_reasons >= 2:
                self._whist_call_count += 1
                return {"action": "follow",
                        "intent": f"follow — heuristic (2nd def): {n_tt} trump tricks, {s_reasons:.1f} reasons"}

        aces = self._count_aces(hand) if hand else 0
        est = self._estimate_tricks_as_whister(hand, trump_suit) if hand else 0.0

        # Sans: very hard to call alone — declarer dominates all suits
        if contract_type == "sans":
            if aces >= 4:
                self._whist_call_count += 1
                return {"action": "call",
                        "intent": f"call sans — {aces} aces powerhouse ({est:.1f} tricks)"}
            if aces >= 3 and est >= 3.0:
                rate = 0.60
                if self.rng.random() < rate:
                    self._whist_call_count += 1
                    return {"action": "call",
                            "intent": f"call sans — {aces}A strong {int(rate*100)}% ({est:.1f} tricks)"}
            # Not strong enough to call — fall back to follow/pass
            return self.following_decision(hand, contract_type, trump_suit, legal_actions)

        # Suit contracts: need ~4 tricks alone to justify call
        # Iter9: P2 Alice had 3A [[A,J,8],[A,9,8],[A,9],[Q,9]] vs clubs.
        # est=2.6 (0.85 per non-trump ace) but each ace takes a full trick
        # in practice. Passed because est < 3.5 gate. Games 3&4 proved
        # calling was profitable (+36.67). Auto-call with 3+ aces.
        if aces >= 3:
            self._whist_call_count += 1
            return {"action": "call",
                    "intent": f"call — {aces}A auto-call ({est:.1f} est tricks)"}
        if aces >= 2 and est >= 4.0:
            rate = 0.70
            if self.rng.random() < rate:
                self._whist_call_count += 1
                return {"action": "call",
                        "intent": f"call — {aces}A strong shape {int(rate*100)}% ({est:.1f} tricks)"}
        if aces >= 2 and est >= 3.0:
            rate = 0.35
            if self.rng.random() < rate:
                self._whist_call_count += 1
                return {"action": "call",
                        "intent": f"call — {aces}A speculative {int(rate*100)}% ({est:.1f} tricks)"}

        # Follow (not call) when hand is strong enough to whist but not solo call.
        # Game 29: 2A hand (A♠ A♥) passed because est=2.0 < call threshold 3.0,
        # but "follow" (shared whisting) is much lower risk. Both defenders
        # passing costs -33 each; follow with 2A nearly always takes 1-2 tricks.
        if "follow" in action_types_call:
            if aces >= 2:
                self._whist_call_count += 1
                return {"action": "follow",
                        "intent": f"follow (not call) — {aces}A strong for shared whist ({est:.1f} tricks)"}
            if aces >= 1 and est >= 0.8:
                cf_rate = 0.55
                if self.rng.random() < cf_rate:
                    self._whist_call_count += 1
                    return {"action": "follow",
                            "intent": f"follow (not call) — {aces}A decent {int(cf_rate*100)}% ({est:.1f} tricks)"}

        # G19 iter11: 2A [[A,D,9,8],[A,J],[J,10],[9,8]] est ~2.0 fell through
        # to following_decision which uses ~2 trick thresholds. Called and lost -80.
        # Calling means taking ALL defense tricks alone — need ~4 tricks.
        # following_decision's 94% rate for 2A est >= 2.0 is way too high for calling.
        # Gate: pass if we didn't meet the call thresholds above.
        call_action_types = [a["action"] for a in legal_actions]
        if "pass" in call_action_types:
            return {"action": "pass",
                    "intent": f"pass — not strong enough to call alone ({aces}A, {est:.1f} tricks)"}
        # No pass option — prefer any non-call action to avoid forced solo whist.
        # NEVER delegate to following_decision from here — it uses paired-whist
        # rates that are way too high for solo calling context.
        # Iter1 50-game: G1/G2/G7 all lost big (-93/-60/-74) from forced calls
        # when following_decision returned "follow" in the call context.
        for preferred in ("start_game", "follow"):
            if preferred in call_action_types:
                return {"action": preferred,
                        "intent": f"{preferred} — not strong enough to call ({aces}A, {est:.1f} tricks)"}
        # Truly forced to call — only option available
        return {"action": "call",
                "intent": f"forced call — only legal option ({aces}A, {est:.1f} tricks)"}

    def decide_to_counter(self, hand, contract_type, trump_suit, legal_actions):
        """Decide whether to counter (double stakes) — AGGRESSIVE.

        Counter doubles game_value, meaning both wins and losses are doubled.
        Only justified with extreme confidence.
        """
        # Store declarer's trump suit for card play ruffing
        if not self._is_declarer and trump_suit is not None:
            self._trump_suit_val = trump_suit
        action_types = [a["action"] for a in legal_actions]
        aces = self._count_aces(hand) if hand else 0
        est = self._estimate_tricks_as_whister(hand, trump_suit) if hand else 0.0

        # Declarer responding to a counter
        if "double_counter" in action_types:
            # As declarer: double_counter if very confident in contract
            if est >= 4.0 and aces >= 2:
                return {"action": "double_counter",
                        "intent": f"double counter — confident declarer ({aces}A, {est:.1f} tricks)"}
            return {"action": "start_game",
                    "intent": f"start game — accept counter ({aces}A, {est:.1f} tricks)"}

        # Defender in counter sub-phase or initial phase
        if aces >= 4:
            return {"action": "counter",
                    "intent": f"counter — {aces} aces powerhouse ({est:.1f} tricks)"}
        if aces >= 3 and est >= 4.0:
            rate = 0.50
            if self.rng.random() < rate:
                return {"action": "counter",
                        "intent": f"counter — {aces}A + {est:.1f} tricks {int(rate*100)}%"}

        # Not strong enough to counter — start game or fall back
        if "start_game" in action_types:
            return {"action": "start_game",
                    "intent": f"start game — not strong enough to counter ({aces}A, {est:.1f} tricks)"}
        # In initial phase with call option — fall back to call/follow/pass
        if "call" in action_types:
            return self.decide_to_call(hand, contract_type, trump_suit, legal_actions)
        return self.following_decision(hand, contract_type, trump_suit, legal_actions)

    def following_decision(self, hand, contract_type, trump_suit, legal_actions):
        """Hand-strength-aware whisting — AGGRESSIVE style.

        Key insight: we HAVE access to our hand here. Use ace count + est tricks.
        Scoring math for single whister (game level 2, game_value=4):
          2 tricks -> +8, 3 tricks -> +12, 4 tricks -> +16
          1 trick  -> -36, 0 tricks -> -40
          pass     ->  0 (but declarer gets +40/+80 for free)

        Iter 3 result: Alice scored 0 from whisting in 6/7 non-declaring games.
        Carol got 460 points from essentially unopposed declaring.
        Must whist more aggressively, especially with trick potential.
        """
        # Store declarer's trump suit for card play ruffing
        if not self._is_declarer and trump_suit is not None:
            self._trump_suit_val = trump_suit
        action_types = [a["action"] for a in legal_actions]

        if "start_game" in action_types:
            return {"action": "start_game", "intent": "start game"}

        if "follow" in action_types:
            # Base heuristic (any player) + aggressive rule (sum_reasons >= 2.5)
            should, n_tt, s_reasons = self._should_follow_heuristic(hand, trump_suit)
            if should or s_reasons >= 2.5:
                self._whist_call_count += 1
                return {"action": "follow",
                        "intent": f"follow — heuristic: {n_tt} trump tricks, {s_reasons:.1f} reasons"}

            aces = self._count_aces(hand) if hand else 0
            est_whist_tricks = self._estimate_tricks_as_whister(hand, trump_suit) if hand else 0.0
            # Detect actual contract level from trump suit — _highest_bid_seen
            # only tracks levels Alice saw during HER bidding. When defending
            # against level 4-5 contracts she passed on, is_high_level was
            # incorrectly False, using loose whist rates against strong declarers.
            # Game 24 iter14: clubs level 5, Alice used 42% base rate instead of
            # 30%, whisted with 1A + junk → only 1 trick → -100.
            _contract_level = 0
            if contract_type == "sans":
                _contract_level = 7
            elif contract_type == "betl":
                _contract_level = 6
            elif trump_suit is not None:
                _contract_level = _SUIT_BID_VALUE.get(trump_suit, 2)
            is_high_level = _contract_level >= 4 or self._highest_bid_seen >= 3
            # Track repeat calls: G18 iter10 called twice → -100. Second call
            # means both defenders are in, doubling the risk. Halve effective rate.
            is_repeat_call = self._whist_call_count > 0

            # HARD PASS: Sans contracts — declarer has 3+ aces + high cards.
            if contract_type == "sans":
                if aces >= 3:
                    self._whist_call_count += 1
                    return {"action": "follow",
                            "intent": f"follow — sans whist with {aces} aces ({est_whist_tricks:.1f} tricks)"}
                if aces >= 2 and est_whist_tricks >= 1.5:
                    rate = 0.70
                    if is_repeat_call:
                        rate *= 0.5
                    if self.rng.random() < rate:
                        self._whist_call_count += 1
                        return {"action": "follow",
                                "intent": f"follow — sans whist 2A strong ({est_whist_tricks:.1f} tricks, {int(rate*100)}%)"}
                # 1 ace in sans: ace is guaranteed (no trumping possible).
                # Passing = -46.67 when declarer wins free. Even 1 trick as
                # whister is better EV. Game 26 iter12: 1A (A♣) + 2 queens,
                # passed sans → -46.67. With 1 guaranteed trick, whisting
                # would have saved ~15-20 points.
                if aces >= 1 and est_whist_tricks >= 0.7:
                    rate = 0.45
                    if is_repeat_call:
                        rate *= 0.5
                    if self.rng.random() < rate:
                        self._whist_call_count += 1
                        return {"action": "follow",
                                "intent": f"follow — sans whist 1A guaranteed ({est_whist_tricks:.1f} tricks, {int(rate*100)}%)"}
                return {"action": "pass",
                        "intent": f"pass — sans contract, need stronger hand ({aces}A, {est_whist_tricks:.1f} tricks)"}

            # HARD PASS: 4+ cards in declarer's trump suit = dead hand.
            # EXCEPTION: holding the trump ace guarantees 1 trick and weakens
            # the declarer's trump length. Game 10: Alice had A♣,Q♣,8♣,7♣
            # (4 clubs = declarer's trump) + A♥. Hard pass cost -33; with
            # trump ace she'd take 2-3 tricks easily.
            if hand and trump_suit is not None:
                trump_count = sum(1 for c in hand if c.suit == trump_suit)
                has_trump_ace_def = any(c.rank == 8 and c.suit == trump_suit for c in hand)
                if trump_count >= 4 and not has_trump_ace_def:
                    return {"action": "pass",
                            "intent": f"pass — {trump_count} cards in declarer's trump = dead hand ({est_whist_tricks:.1f} tricks)"}

            # SOFT GATE: 3+ unsupported kings = unreliable, but aggressive
            # Alice still takes a 15% speculative shot. Zero whist losses in
            # 4 iterations; G5/G9 iter4 both blocked by hard gate → -20/-26.
            if hand:
                groups = self._suit_groups(hand)
                unsup_kings = 0
                for suit, cards in groups.items():
                    has_a = any(c.rank == 8 for c in cards)
                    has_k = any(c.rank == 7 for c in cards)
                    if has_k and not has_a:
                        unsup_kings += 1
                if unsup_kings >= 3:
                    speculative_rate = 0.15
                    if is_repeat_call:
                        speculative_rate *= 0.5
                    if self.rng.random() >= speculative_rate:
                        return {"action": "pass",
                                "intent": f"pass — {unsup_kings} unsupported kings ({est_whist_tricks:.1f} tricks)"}

            # 2+ aces: whist most of the time, but check for flat/weak hands.
            # On repeat call (both defenders in), halve the rate — risk doubles.
            if aces >= 2:
                groups = self._suit_groups(hand) if hand else {}
                max_suit_len = max((len(cards) for cards in groups.values()), default=0)
                high = self._count_high_cards(hand) if hand else 0
                if aces >= 3:
                    self._whist_call_count += 1
                    return {"action": "follow",
                            "intent": f"follow — {aces} aces, {est_whist_tricks:.1f} est tricks"}

                # Queen-scatter penalty for 2-ace hands: G14 iter5
                # [[A,D,J,10,7],[K,9],[D,J],[A]] — 2A + 2 unsupported queens.
                # est ~2.0 but queens can't beat K/A as whister. Called twice → -100.
                # Iter68: Q with K in same suit is NOT unsupported — K protects Q.
                two_ace_q_penalty = False
                if hand:
                    unsup_q = 0
                    for suit, cards in groups.items():
                        if trump_suit is not None and suit == trump_suit:
                            continue
                        has_a = any(c.rank == 8 for c in cards)
                        has_k = any(c.rank == 7 for c in cards)
                        has_q = any(c.rank == 6 for c in cards)
                        if has_q and not has_a and not has_k:
                            unsup_q += 1
                    if unsup_q >= 2:
                        two_ace_q_penalty = True

                # Low-trump penalty: 0-1 cards in declarer's trump means aces
                # are vulnerable to being trumped. Declarer with 5-7 trumps
                # will be void in our ace suits.
                # Game 23 iter4: 2A, 1 trump (10♦), 0 tricks → -60.
                # Iter62: 2A, 0 trumps, est=2.10 → 0 tricks → -106.67.
                if hand and trump_suit is not None:
                    tc = sum(1 for c in hand if c.suit == trump_suit)
                    if tc == 0 and est_whist_tricks < 2.5:
                        zt_rate = 0.45  # tightened from 0.60
                        if is_repeat_call:
                            zt_rate *= 0.5
                        if self.rng.random() >= zt_rate:
                            return {"action": "pass",
                                    "intent": f"pass — 0 trumps, aces vulnerable to trumping ({est_whist_tricks:.1f} tricks)"}
                    # Game 36 iter5: tc=1, est~2.4, 2A → followed at 100% → 1
                    # trick → -93. Declarer void in ace suit → ace trumped.
                    # Old threshold <2.0 missed this. Raised to <2.5.
                    elif tc == 1 and est_whist_tricks < 2.5:
                        zt_rate = 0.55
                        if is_repeat_call:
                            zt_rate *= 0.5
                        if self.rng.random() >= zt_rate:
                            return {"action": "pass",
                                    "intent": f"pass — 1 trump, aces at risk ({est_whist_tricks:.1f} tricks)"}
                    # 2-trump penalty: with only 2 trumps, declarer has 6+
                    # trumps and is likely void in 1+ suit. Non-trump aces
                    # get trumped. Game 19: 2A, 2 trumps, est=1.55, followed
                    # at 100% → 1 trick → -74.67 from declarer overtricks.
                    # 2 trumps gives minimal protection against being trumped.
                    elif tc == 2 and est_whist_tricks < 1.7:
                        zt_rate = 0.75
                        if is_repeat_call:
                            zt_rate *= 0.5
                        if self.rng.random() >= zt_rate:
                            return {"action": "pass",
                                    "intent": f"pass — 2 trumps, low est ({est_whist_tricks:.1f} tricks)"}

                if high <= 2:
                    junk_rate = 0.76 if est_whist_tricks >= 1.5 else 0.60
                    if is_repeat_call:
                        junk_rate *= 0.5
                    if self.rng.random() < junk_rate:
                        self._whist_call_count += 1
                        return {"action": "follow",
                                "intent": f"follow — 2A junk hand {int(junk_rate*100)}% ({est_whist_tricks:.1f} tricks, high={high})"}
                    return {"action": "pass",
                            "intent": f"pass — 2A junk hand ({est_whist_tricks:.1f} tricks, high={high})"}
                # Gate max_suit_len on est >= 1.5: K-headed long suits are
                # junk as whister (only valued 0.30 in est) but inflated
                # max_suit_len, bypassing est check. Game 19: 5-card K♦
                # suit → max_suit_len=5 → 100% follow → 1 trick → -74.67.
                if est_whist_tricks >= 2.0 or (max_suit_len >= 4 and est_whist_tricks >= 1.5):
                    rate = 0.86 if two_ace_q_penalty else 1.0
                    if is_repeat_call:
                        rate *= 0.5
                    if self.rng.random() < rate:
                        self._whist_call_count += 1
                        return {"action": "follow",
                                "intent": f"follow — {aces} aces, {est_whist_tricks:.1f} est tricks ({int(rate*100)}%{', Q-penalty' if two_ace_q_penalty else ''})"}
                    return {"action": "pass",
                            "intent": f"pass — 2 aces dodged ({est_whist_tricks:.1f} tricks{', Q-penalty' if two_ace_q_penalty else ''})"}
                flat_rate = 0.68 if two_ace_q_penalty else 0.88
                if is_repeat_call:
                    flat_rate *= 0.5
                if self.rng.random() < flat_rate:
                    self._whist_call_count += 1
                    return {"action": "follow",
                            "intent": f"follow — {aces} aces flat ({est_whist_tricks:.1f} tricks, longest={max_suit_len}{', Q-penalty' if two_ace_q_penalty else ''})"}
                return {"action": "pass",
                        "intent": f"pass — 2 aces but flat shape ({est_whist_tricks:.1f} tricks, longest={max_suit_len}{', Q-penalty' if two_ace_q_penalty else ''})"}

            # 1 ace: whist based on trick potential — need 2+ tricks total
            if aces == 1:
                # Check for A-K combo in same non-trump suit: strong anchor.
                # G5 iter12: Alice had AK hearts = ~1.5 tricks from one suit alone.
                # EXCEPTION: AK in 5+ card suit is fragile — declarer trumps after
                # 2 rounds. Game 16 iter5: AK♥ in 5 hearts → whisted → 0 tricks → -66.
                has_ak_combo = False
                if hand:
                    groups = self._suit_groups(hand)
                    for suit, cards in groups.items():
                        if trump_suit is not None and suit == trump_suit:
                            continue
                        has_a = any(c.rank == 8 for c in cards)
                        has_k = any(c.rank == 7 for c in cards)
                        if has_a and has_k and len(cards) <= 4:
                            has_ak_combo = True
                            break

                # Queen-scatter penalty: G10 iter28 [[A,D,8],[K,D,8],[D,J,10],[7]]
                # — 3 queens scattered across suits + 1A. Queens inflate est but
                # can't beat K/A as whister. Called twice and lost -60.
                # Iter68: Q with K in same suit is NOT unsupported — K protects Q.
                queen_penalty = False
                if hand:
                    groups = self._suit_groups(hand)
                    unsup_queens = 0
                    for suit, cards in groups.items():
                        if trump_suit is not None and suit == trump_suit:
                            continue
                        has_a = any(c.rank == 8 for c in cards)
                        has_k = any(c.rank == 7 for c in cards)
                        has_q = any(c.rank == 6 for c in cards)
                        if has_q and not has_a and not has_k:
                            unsup_queens += 1
                    if unsup_queens >= 2:
                        queen_penalty = True

                if False:
                    # DISABLED: is_high_level blanket suppression caused many -33
                    # passive losses in iters 14-15 (avg -6.3). Hearts(4)/clubs(5)
                    # = 50% of suit contracts getting suppressed. The est-based
                    # rates + low-trump penalties already handle weak hands.
                    rate = 0.58 if est_whist_tricks >= 1.5 else 0.30
                else:
                    if est_whist_tricks >= 2.0:
                        rate = 1.0   # Very strong 1-ace hand — always whist
                    elif est_whist_tricks >= 1.5:
                        rate = 0.97  # Zero losses in iter4; 2% bump from 0.95
                    elif est_whist_tricks >= 1.0:
                        rate = 0.74  # Zero losses in iter4; 4% bump from 0.70
                    elif est_whist_tricks >= 0.7:
                        rate = 0.42  # Zero losses in iter4; 4% bump from 0.38
                    else:
                        rate = 0.12  # Zero losses in iter4; 2% bump from 0.10
                    if has_ak_combo and not queen_penalty:
                        rate = min(rate + 0.20, 1.0)
                    # Low-trump penalty: 0-1 cards in declarer's trump means our
                    # aces get trumped easily. Game 47 iter4: 0 diamonds vs diamonds
                    # declarer, 1A, est=1.5, rate=1.0 → whisted → -56. Declarer
                    # trumped the ace. Game 23 iter4: 1 trump, 2A → 0 tricks, -60.
                    if hand and trump_suit is not None:
                        trump_count = sum(1 for c in hand if c.suit == trump_suit)
                        if trump_count == 0:
                            rate *= 0.50  # aces very vulnerable to trumping
                        elif trump_count == 1:
                            rate *= 0.70  # still risky
                    if queen_penalty:
                        rate = max(rate - 0.25, 0.05)  # G19 iter9: 0.15 too mild, 1A+2Q called twice→-72. 0.25 drops est>=2.0 from 85%→75%.
                    # 2-trump penalty: with only 2 cards in declarer's trump,
                    # declarer has 6+ trumps and is likely void in 1+ suit.
                    # Non-trump aces get trumped. Game 47 iter2: 2 trumps,
                    # AK♥ in 4-card suit → ace trumped → 0 tricks → -106.
                    if hand and trump_suit is not None:
                        trump_count = sum(1 for c in hand if c.suit == trump_suit)
                        if trump_count <= 2 and est_whist_tricks < 2.0:
                            rate *= 0.60  # aces vulnerable with thin trump cover
                        # Long-suit lone ace penalty: ace in 4+ card non-trump
                        # suit without king is at high risk of being trumped
                        # when tc <= 2 (declarer has 6+ trumps, likely void).
                        # Game 30: A♣ in 4 clubs, tc=2, cashable floor forced
                        # 45% whist → led ace → declarer void → trumped → -60.
                        if trump_count <= 2:
                            _groups_lsa = self._suit_groups(hand)
                            _ace_in_long = any(
                                any(c.rank == 8 for c in cards)
                                and not any(c.rank == 7 for c in cards)
                                and len(cards) >= 4
                                for suit, cards in _groups_lsa.items()
                                if suit != trump_suit
                            )
                            if _ace_in_long:
                                rate *= 0.80
                # Cashable ace floor: when ace is in a 3+ card non-trump suit,
                # opponents are shorter → ace very likely to cash even with 0-2
                # trumps. Games 15/27: aces in 3-5 card suits got stacked
                # penalties (×0.50×0.60=13%) but the aces would have cashed.
                # Minimum rate prevents over-penalization of long-suit aces.
                if hand and trump_suit is not None:
                    _groups_ca = self._suit_groups(hand)
                    # Cashable ace: ace in 3-4 card non-trump suit is likely to
                    # cash (opponents hold 4-5 cards, unlikely void). But in 5+
                    # card suit, only 3 remain for opponents → high void risk →
                    # ace gets trumped. Game 31 iter6: A♦ in 5 diamonds →
                    # declarer void → trumped → 0 tricks.
                    _has_cashable = any(
                        any(c.rank == 8 for c in cards) and 3 <= len(cards) <= 4
                        for suit, cards in _groups_ca.items() if suit != trump_suit
                    )
                    if _has_cashable and trump_count >= 3:
                        rate = max(rate, 0.45)
                # Trump ace guarantee: when holding the ace of declarer's trump
                # suit, we have a guaranteed unbeatable trick. Whisting is always
                # positive EV. Game 4: A♣ in clubs trump, stacked penalties reduced
                # rate to 25% — but trump ace = guaranteed trick, should whist.
                # Trump ace guarantee: the ace of declarer's trump suit is
                # literally unbeatable — guaranteed 1 trick minimum. Whisting
                # is always +EV. Game 21: A♣ in clubs trump, rate was 0.31
                # after penalties, floor raised to 0.55, still rolled >55%
                # and passed → -33. Trump ace = guaranteed trick, raise floor
                # to 0.75 so Alice almost always whists with it.
                if hand and trump_suit is not None:
                    if any(c.rank == 8 and c.suit == trump_suit for c in hand):
                        rate = max(rate, 0.75)
                # G19 iter9: called twice at 85%*0.45=38% → -72. Tighter
                # multiplier 0.38: 75%*0.38=28.5% makes double-calls much rarer.
                if is_repeat_call:
                    rate *= 0.38
                # 1A minimum floor: stacked penalties (tc × queen × repeat) can
                # produce absurdly low rates (~5-10%) for hands with an ace.
                # Passing gives -33 when declarer wins free; even a ~20% whist
                # chance with 1 ace is better EV than guaranteed loss.
                # GATE: only apply floor when est >= 0.3. With est near 0,
                # the ace is virtually guaranteed to be trumped (e.g., 5-card
                # suit, 0 trump coverage). Game 20: est≈0.0, floor forced 18%
                # whist → 0 tricks → -100. Passing is -33 = far better EV.
                if est_whist_tricks >= 0.3:
                    rate = max(rate, 0.18)
                if self.rng.random() < rate:
                    self._whist_call_count += 1
                    return {"action": "follow",
                            "intent": f"follow — 1 ace, {int(rate*100)}% rate ({est_whist_tricks:.1f} tricks{', Q-penalty' if queen_penalty else ''})"}
                return {"action": "pass",
                        "intent": f"pass — 1 ace but rolled >{int(rate*100)}% ({est_whist_tricks:.1f} tricks{', Q-penalty' if queen_penalty else ''})"}

            # 0 aces: EV-aware whisting — account for ~50% solo risk.
            # Iter1 50-game: G2 whisted 0A [[K,D,9],[D,10,9],[K,7],[10,9]] est~0.45
            # → forced solo → -60. Solo needs ~4 tricks; 0A est<1.0 is clearly
            # negative EV. 0A est<1.5 is approximately break-even accounting for
            # solo probability. Rates reduced from iter46 to reflect EV math.
            if is_high_level:
                # Relaxed: was 0.40/0.0 which caused guaranteed passes against
                # hearts/clubs (50% of games). Even 0A hands benefit from some
                # whist chance vs guaranteed -33 passive loss.
                rate = 0.40 if est_whist_tricks >= 1.5 else 0.15
            else:
                if est_whist_tricks >= 1.5:
                    rate = 0.82  # Zero losses in iter4; 4% bump from 0.78
                elif est_whist_tricks >= 1.0:
                    rate = 0.65  # Zero losses in iter4; 5% bump from 0.60
                elif est_whist_tricks >= 0.5:
                    rate = 0.28  # Zero losses in iter4; 3% bump from 0.25
                else:
                    rate = 0.08  # Zero losses in iter4; 2% bump from 0.06
            # Low-trump penalty for 0A: no aces + no trump coverage = hopeless
            if hand and trump_suit is not None:
                trump_count = sum(1 for c in hand if c.suit == trump_suit)
                if trump_count <= 1:
                    rate *= 0.55
            if is_repeat_call:
                rate *= 0.5
            if rate > 0 and self.rng.random() < rate:
                self._whist_call_count += 1
                return {"action": "follow",
                        "intent": f"follow — 0 aces, {int(rate*100)}% rate ({est_whist_tricks:.1f} tricks)"}
            return {"action": "pass",
                    "intent": f"pass — 0 aces ({est_whist_tricks:.1f} tricks)"}

        if "pass" in action_types:
            return {"action": "pass", "intent": "pass — no follow option"}
        return {"action": action_types[0], "intent": f"fallback — {action_types[0]}"}

    # ------------------------------------------------------------------
    # Card play — strategic with declarer/whister awareness
    # ------------------------------------------------------------------

    # Personality params for shared strategy functions
    _PLAY_PARAMS = {'king_duck_tricks': 2, 'whister_trump_pref': 'highest'}

    def _score_all_cards(self, legal_cards):
        """Score all legal cards. Returns {card_id: float}."""
        contract_type = getattr(self, '_contract_type', None)
        ctx = getattr(self, '_ctx', None)
        rnd = getattr(self, '_rnd', None)

        if contract_type == "betl":
            trick = rnd.current_trick if rnd else None
            played = trick.cards if trick else []
            hand_size = self._total_hand_size - self._cards_played
            is_leading = len(played) == 0
            suits_in_legal = {c.suit for c in legal_cards}
            must_follow = len(suits_in_legal) == 1 and len(legal_cards) < hand_size
            declarer_id = rnd.declarer_id if rnd else None
            active = ctx.active_players if ctx else []
            return _score_betl_play(legal_cards, played, is_leading, must_follow,
                                    self._is_declarer, declarer_id, active)

        trick = rnd.current_trick if rnd else None
        played = ctx.trick_cards if ctx else (trick.cards if trick else [])
        hand_size = self._total_hand_size - self._cards_played
        is_leading = len(played) == 0
        suits_in_legal = {c.suit for c in legal_cards}
        must_follow = len(suits_in_legal) == 1 and len(legal_cards) < hand_size

        if is_leading:
            if self._is_declarer and contract_type == "sans":
                return _score_sans_declarer_lead(legal_cards, ctx)
            elif self._is_declarer and self._trump_suit_val is not None:
                scores = _score_declarer_lead(legal_cards, ctx,
                                              self._trump_suit_val, self._trump_leads)
                # Boost trump drawing when opponents still have trumps.
                # Game 37 iter2: Led A♠ with 2 opponent trumps out → P3 trumped it.
                # Drawing trumps first prevents opponents from ruffing side winners.
                # Game 48 iter5: A♣ then A♠ then K♥ → K♥ trumped by J♣.
                # With Q♣,9♣,7♣ (3 trumps) vs J♣,K♣ (2 out), should draw trumps
                # first. Old +12 boost wasn't enough to overcome side-suit ace score ~82.
                # New: when my_trumps >= trumps_out, strong priority to draw trumps.
                if ctx:
                    trumps_out = _ctx_trumps_remaining(ctx)
                    my_trumps = sum(1 for c in legal_cards if c.suit == self._trump_suit_val)
                    if trumps_out >= 2 and my_trumps >= trumps_out:
                        # We can exhaust opponent trumps — draw them first
                        for c in legal_cards:
                            if c.suit == self._trump_suit_val:
                                scores[c.id] += 25.0
                            else:
                                scores[c.id] -= 15.0
                    elif trumps_out >= 3:
                        for c in legal_cards:
                            if c.suit == self._trump_suit_val:
                                scores[c.id] += 20.0
                            else:
                                scores[c.id] -= 10.0
                    elif trumps_out >= 2:
                        for c in legal_cards:
                            if c.suit == self._trump_suit_val:
                                scores[c.id] += 12.0
                            elif c.rank < 8:  # non-ace side cards
                                scores[c.id] -= 5.0
                # Fix 1: Trump rank tiebreaker — when multiple trumps are all
                # "master" (score 95), they tie and iteration order picks the
                # winner. Game 5: Q♣ led before A♣ because both scored 95.
                # Add rank * 0.1 so ace(95.8) > king(95.7) > queen(95.6).
                for c in legal_cards:
                    if c.suit == self._trump_suit_val:
                        scores[c.id] += c.rank * 0.1
                # Fix 2: King-first in side suits — _score_declarer_lead gives
                # "forcing lead" low cards ~45-48, beating kings at 35-40.
                # Game 11: led 8♠ (score 48) instead of K♠ (35), giving away
                # a free trick. Ensure kings score above lower cards in suit.
                d_groups = self._suit_groups(legal_cards)
                for suit, cards in d_groups.items():
                    if suit == self._trump_suit_val:
                        continue
                    king = next((c for c in cards if c.rank == 7), None)
                    if king is None:
                        continue
                    lower_cards = [c for c in cards if c.rank < 7]
                    if not lower_cards:
                        continue
                    max_lower = max(scores[c.id] for c in lower_cards)
                    if scores[king.id] < max_lower + 2.0:
                        scores[king.id] = max_lower + 2.0
                # Fix 3: Ace-before-king in side suits — when AK are both
                # in the same side suit, master-card scoring can give K the
                # same score as A (both ~80). K led first gets trumped.
                # Game 48 iter5: K♥ and A♥ tied at 80 → K♥ led → trumped.
                # Ace is always safer to lead first (guaranteed winner).
                for suit, cards in d_groups.items():
                    if suit == self._trump_suit_val:
                        continue
                    ace = next((c for c in cards if c.rank == 8), None)
                    if ace is None:
                        continue
                    max_non_ace = max(
                        (scores[c.id] for c in cards if c.rank != 8),
                        default=0.0
                    )
                    if scores[ace.id] <= max_non_ace:
                        scores[ace.id] = max_non_ace + 2.0
                return scores
            else:
                if ctx:
                    scores = _score_whister_lead(legal_cards, ctx, self._trump_suit_val)
                else:
                    # No ctx fallback — score based on simple heuristic
                    scores = {}
                    for c in legal_cards:
                        if c.rank == 8:
                            scores[c.id] = 80.0
                        elif c.rank == 7:
                            scores[c.id] = 50.0
                        else:
                            scores[c.id] = 20.0 + c.rank
                # Whister long-suit penalties: apply BEFORE ace-ordering fix
                # so that ace-ordering is the final constraint.
                if self._trump_suit_val is not None:
                    w_groups = self._suit_groups(legal_cards)
                    w_trumps_out = _ctx_trumps_remaining(ctx) if ctx else 0

                    # Ace suit-length preference: prefer aces from shorter
                    # non-trump suits. Shorter suit = more cards with opponents
                    # = declarer less likely void = ace safer to lead.
                    # Game 1 iter12: A♠(3 cards) and A♥(3 cards) tied at ~80.
                    # If A♥ was in 2-card suit, it should be preferred.
                    for c in legal_cards:
                        if c.rank == 8 and c.suit != self._trump_suit_val:
                            suit_len = len(w_groups.get(c.suit, []))
                            if suit_len <= 2:
                                scores[c.id] += 5.0
                            elif suit_len == 3:
                                scores[c.id] += 2.0

                    # Penalty 1: Ace penalty for long non-trump suits.
                    # When holding 4+ cards, declarer may be void → ace trumped.
                    # Game 25: A♣ from 4 clubs ruffed by void declarer → -66.
                    # Game 6: A♦ from 4 diamonds ruffed trick 4 → lost 3rd ace.
                    # Increased 4-card penalty: -30/-35 (was -15/-25).
                    for c in legal_cards:
                        if c.rank == 8 and c.suit != self._trump_suit_val:
                            suit_len = len(w_groups.get(c.suit, []))
                            has_king_too = any(
                                x.rank == 7 for x in w_groups.get(c.suit, [])
                            )
                            if has_king_too and suit_len <= 3:
                                pass  # AK in short suit — don't penalize ace
                            elif has_king_too and suit_len >= 4:
                                scores[c.id] -= 10.0
                            elif suit_len >= 5:
                                scores[c.id] -= 35.0
                            elif suit_len >= 4:
                                extra = 15.0 if w_trumps_out >= 3 else (10.0 if w_trumps_out >= 2 else 0.0)
                                scores[c.id] -= 20.0 + extra
                            elif suit_len >= 3 and w_trumps_out >= 3:
                                scores[c.id] -= 8.0

                    # Penalty 2: Non-ace master cards from 4+ card non-trump
                    # suits when many trumps out. _master_bonus gives sequential
                    # winners (AKQ♦) scores of 86+, but declarer with 5+ trumps
                    # is likely void and will trump. Game 42: Q♦ scored 86 from
                    # sequential bonus, led before A♠(77) → got trumped → lost
                    # a guaranteed trick. Cap non-ace masters from long suits.
                    for suit, cards in w_groups.items():
                        if suit == self._trump_suit_val:
                            continue
                        if len(cards) >= 4 and w_trumps_out >= 3:
                            for c in cards:
                                if c.rank != 8 and scores[c.id] > 50.0:
                                    scores[c.id] = 50.0

                    # Penalty 3: Kings from 4+ card non-trump suits when many
                    # trumps remain. Declarer is often void in long suits and
                    # will trump the king. Game 35: K♣ from 4-card clubs led
                    # after winning A♥ → declarer void, trumped with J♦ →
                    # lost remaining 8 tricks. Prefer leading from short suits.
                    for suit, cards in w_groups.items():
                        if suit == self._trump_suit_val:
                            continue
                        if len(cards) >= 4 and w_trumps_out >= 3:
                            for c in cards:
                                if c.rank == 7:  # King
                                    scores[c.id] -= 15.0

                    # Short-suit lead preference: when we have a 4+ card ace
                    # suit AND a 1-2 card non-trump non-ace suit, boost the
                    # short-suit leads. Leading from short suits is safer —
                    # declarer is less likely void, forces declarer to use
                    # trumps on our junk rather than ruffing our aces.
                    # Game 25: 8♠ from 2-card spades was safer than A♣ from 4 clubs.
                    # Game 6: 9♣ from 1-card clubs was safer than A♦ from 4 diamonds.
                    _has_long_ace_suit = any(
                        any(c.rank == 8 for c in cards) and len(cards) >= 4
                        for suit, cards in w_groups.items()
                        if suit != self._trump_suit_val
                    )
                    if _has_long_ace_suit and w_trumps_out >= 2:
                        for suit, cards in w_groups.items():
                            if suit == self._trump_suit_val:
                                continue
                            if len(cards) <= 2 and not any(c.rank == 8 for c in cards):
                                for c in cards:
                                    scores[c.id] += 25.0

                    # Ace-before-non-ace ordering: MUST be last step.
                    # Ensures ace always scores >= max non-ace in same suit,
                    # even after all penalties. Previously ran before penalties,
                    # so penalty could push ace below non-ace masters again.
                    for suit, cards in w_groups.items():
                        if suit == self._trump_suit_val:
                            continue
                        ace = next((c for c in cards if c.rank == 8), None)
                        if ace is None:
                            continue
                        max_non_ace = max(
                            (scores[c.id] for c in cards if c.rank != 8),
                            default=0.0
                        )
                        if scores[ace.id] < max_non_ace:
                            scores[ace.id] = max_non_ace + 2.0
                return scores
        elif must_follow:
            return _score_must_follow(legal_cards, ctx, played,
                                      self._is_declarer, self._trump_suit_val,
                                      self._PLAY_PARAMS)
        else:
            return _score_cant_follow(legal_cards, ctx, self._is_declarer,
                                      self._trump_suit_val, self._PLAY_PARAMS)

    def choose_card(self, legal_cards):
        """Strategic card play — scores all cards and picks the best."""
        if len(legal_cards) == 1:
            self._ranked_cards = [(legal_cards[0].id, 100.0)]
            self._cards_played += 1
            return legal_cards[0].id

        card_scores = self._score_all_cards(legal_cards)
        self._ranked_cards = sorted(card_scores.items(), key=lambda x: -x[1])

        # Update trump_leads counter if declarer led a trump
        best_id = self._ranked_cards[0][0]
        ctx = getattr(self, '_ctx', None)
        rnd = getattr(self, '_rnd', None)
        trick = rnd.current_trick if rnd else None
        played = ctx.trick_cards if ctx else (trick.cards if trick else [])
        if len(played) == 0 and self._is_declarer and self._trump_suit_val is not None:
            best_card = next(c for c in legal_cards if c.id == best_id)
            if best_card.suit == self._trump_suit_val:
                self._trump_leads += 1

        self._cards_played += 1
        return best_id

    def _sans_declarer_lead(self, legal_cards, ctx):
        """Sans declarer leading: cash winners, then run longest suit.

        Iter13: Alice used _whister_lead for sans declaring, which leads
        'highest from shortest suit' after aces/kings — wrong for sans
        declarer. Led 7♠ (loser) before Q♥ and 9♣ (both winners).
        Sans needs: cash aces→kings→run longest suit (length = tricks).
        """
        groups = self._suit_groups(legal_cards)

        # Phase 1: Cash aces (shortest suit first — preserve long suit)
        aces = [c for c in legal_cards if c.rank == 8]
        if aces:
            aces.sort(key=lambda c: len(groups.get(c.suit, [])))
            return aces[0]

        # Phase 2: Cash kings (likely promoted after aces played)
        kings = [c for c in legal_cards if c.rank == 7]
        if kings:
            # Prefer kings from shorter suits (cash before switching to long)
            kings.sort(key=lambda c: len(groups.get(c.suit, [])))
            return kings[0]

        # Phase 3: Cash queens from short suits (may be promoted)
        queens = [c for c in legal_cards if c.rank == 6 and len(groups.get(c.suit, [])) <= 2]
        if queens:
            return queens[0]

        # Phase 4: Run longest suit — in sans, length = tricks after
        # opponents exhaust their cards in that suit
        longest_suit = max(groups.keys(), key=lambda s: len(groups[s]))
        return groups[longest_suit][0]  # highest card in longest suit

    def _betl_choose_card(self, legal_cards):
        """Betl card play using trick context.

        Declarer: play the highest card that still loses the trick.
        Defender: if before declarer play lowest; if after declarer play
                  highest card lower than declarer's, or 1 above other defender.
        """
        rnd = getattr(self, '_rnd', None)
        trick = rnd.current_trick if rnd else None
        my_id = getattr(self, '_player_id', None)
        declarer_id = rnd.declarer_id if rnd else None
        # Cards already played in this trick: [(player_id, Card), ...]
        played = trick.cards if trick else []

        hand_size = self._total_hand_size - self._cards_played
        is_leading = len(played) == 0
        suits_in_legal = {c.suit for c in legal_cards}
        must_follow = len(suits_in_legal) == 1 and len(legal_cards) < hand_size

        if self._is_declarer:
            return self._betl_declarer_play(legal_cards, played, is_leading, must_follow)
        else:
            return self._betl_defender_play(legal_cards, played, is_leading,
                                            must_follow, declarer_id)

    def _betl_declarer_play(self, legal_cards, played, is_leading, must_follow):
        """Declarer in betl: play the highest card that still LOSES the trick."""
        if is_leading:
            # Leading: play highest card that opponents can beat (gap > 0)
            # Prefer longest suit to burn safe cards
            groups = self._suit_groups(legal_cards)
            best_card = None
            best_score = -1
            for suit, cards in groups.items():
                for c in cards:
                    gap = 8 - c.rank  # how many opponent cards rank above this
                    if gap > 0:
                        # Prefer: higher rank (burn more), longer suit, larger gap
                        score = c.rank * 100 + len(cards) * 10 + gap
                        if score > best_score:
                            best_score = score
                            best_card = c
            if best_card:
                return best_card
            # All cards are unbeatable (aces etc) — play lowest to minimize damage
            return min(legal_cards, key=lambda c: c.rank)

        elif must_follow:
            # Must follow suit: play highest card that is LOWER than the
            # current highest card in the trick (to lose)
            suit_led = played[0][1].suit if played else None
            # Find the highest card of the led suit already played
            max_played = 0
            for pid, card in played:
                if card.suit == suit_led:
                    if card.rank > max_played:
                        max_played = card.rank
            # Play highest card below max_played
            below = [c for c in legal_cards if c.rank < max_played]
            if below:
                return max(below, key=lambda c: c.rank)
            # All our cards are >= max_played — we'll win. Play lowest to
            # minimize the "height" of the winning card.
            return min(legal_cards, key=lambda c: c.rank)

        else:
            # Can't follow suit: discard highest/most dangerous card
            return max(legal_cards, key=lambda c: c.rank)

    def _betl_defender_play(self, legal_cards, played, is_leading,
                            must_follow, declarer_id):
        """Defender in betl: force declarer to win tricks."""
        if is_leading:
            # Betl Strategy 1: Lead from shortest suit
            hand = getattr(self, '_hand', legal_cards)
            return _shared_betl_defender_lead(legal_cards, hand)

        elif must_follow:
            suit_led = played[0][1].suit if played else None

            # Check if declarer has already played in this trick
            declarer_card = None
            other_defender_card = None
            my_id = getattr(self, '_player_id', None)
            for pid, card in played:
                if card.suit == suit_led:
                    if pid == declarer_id:
                        declarer_card = card
                    elif pid != my_id:
                        other_defender_card = card

            if declarer_card:
                # Declarer already played: play highest card LOWER than
                # declarer's card (duck under so declarer wins the trick)
                below = [c for c in legal_cards if c.rank < declarer_card.rank]
                if below:
                    return max(below, key=lambda c: c.rank)
                # All our cards beat declarer — play lowest (we win but
                # save high cards for future tricks)
                return min(legal_cards, key=lambda c: c.rank)
            else:
                # Declarer hasn't played yet (we're before declarer)
                if other_defender_card:
                    # Other defender already played: play just 1 above their
                    # card to coordinate (don't waste high cards)
                    above = sorted([c for c in legal_cards
                                    if c.rank > other_defender_card.rank],
                                   key=lambda c: c.rank)
                    if above:
                        return above[0]  # smallest card above other defender
                # Play lowest — save high cards, force declarer to play high
                return min(legal_cards, key=lambda c: c.rank)

        else:
            # Can't follow suit: discard lowest (save high cards for later)
            return min(legal_cards, key=lambda c: c.rank)

    def _declarer_lead(self, legal_cards):
        """Declarer leading: draw high trumps, cash side aces, probe side
        suits with length while keeping trumps in reserve."""
        groups = self._suit_groups(legal_cards)
        trump = self._trump_suit_val

        # Phase 1: Lead high trumps to draw out opponent trumps
        if trump in groups:
            trump_cards = groups[trump]
            # Lead ace of trump first
            for c in trump_cards:
                if c.rank == 8:
                    return c
            # With 3+ trumps, keep drawing trumps before cashing side aces.
            # This strips opponents of trumps so they can't ruff our winners.
            if len(trump_cards) >= 3 and trump_cards[0].rank >= 6:
                return trump_cards[0]
            # Lead high trump (King, Queen) to draw out opponents
            if trump_cards[0].rank >= 6:
                return trump_cards[0]

        # Phase 2: Cash side-suit aces (shortest suit first to preserve long suits)
        aces = [c for c in legal_cards if c.rank == 8 and c.suit != trump]
        if aces:
            aces.sort(key=lambda c: len(groups.get(c.suit, [])))
            return aces[0]

        # Phase 2.5: Cash promoted kings from short non-trump suits.
        # After aces are cashed, a lone K in a 1-2 card suit is likely
        # promoted (our ace cleared the way). Cash before leading from
        # long suits where opponents may still ruff.
        short_kings = [c for c in legal_cards if c.rank == 7 and c.suit != trump
                       and len(groups.get(c.suit, [])) <= 2]
        if short_kings:
            short_kings.sort(key=lambda c: len(groups.get(c.suit, [])))
            return short_kings[0]

        # Phase 3: Probe side suits with length before dumping trumps.
        # Leading from a 2+ card side suit can establish extra tricks if
        # opponents are short in that suit. Keep trumps in reserve to
        # regain the lead after opponents win a side-suit trick.
        non_trump = {s: cards for s, cards in groups.items() if s != trump}
        non_trump_with_length = {s: cards for s, cards in non_trump.items()
                                 if len(cards) >= 2}
        if non_trump_with_length and trump in groups:
            longest = max(non_trump_with_length.keys(),
                          key=lambda s: len(non_trump_with_length[s]))
            return non_trump_with_length[longest][0]

        # Phase 4: Lead remaining trumps (no more side suits to probe)
        if trump in groups:
            return groups[trump][0]

        # Phase 5: Lead highest from longest off-suit (length winners)
        if non_trump:
            longest = max(non_trump.keys(), key=lambda s: len(non_trump[s]))
            return non_trump[longest][0]

        return max(legal_cards, key=lambda c: c.rank)

    def _whister_lead(self, legal_cards):
        """Whister leading: lead aces from A-K suits first, then shortest suit aces."""
        groups = self._suit_groups(legal_cards)

        # Lead aces first — guaranteed trick winners
        # Priority: trump ace (removes declarer's ruffing power) > A-K combo > shortest.
        aces = [c for c in legal_cards if c.rank == 8]
        if aces:
            def ace_priority(c):
                suit_cards = groups.get(c.suit, [])
                has_king = any(x.rank == 7 for x in suit_cards)
                is_trump = (self._trump_suit_val is not None
                            and c.suit == self._trump_suit_val)
                # Lower score = higher priority
                priority = 0
                if is_trump:
                    priority -= 200  # Trump ace first: draws declarer's trumps
                if has_king:
                    priority -= 100  # AK combo: ace cashes, king promoted
                return priority + len(suit_cards)
            aces.sort(key=ace_priority)
            return aces[0]

        # Lead kings — prefer from suits where ace has likely been played
        # (longer suits more likely ace already cashed), or A-K promoted suits.
        kings = [c for c in legal_cards if c.rank == 7]
        if kings:
            kings.sort(key=lambda c: len(groups.get(c.suit, [])), reverse=True)
            return kings[0]

        # Lead highest from shortest suit to void it for future ruffing
        shortest_suit = min(groups.keys(), key=lambda s: len(groups[s]))
        return groups[shortest_suit][0]


class PlayerBob(WeightedRandomPlayer):
    """Bob: CAUTIOUS Preferans player — minimize negative scores.

    Key strategies (iteration 49):
    - Iter5 results (50-game): Bob=-280 across 29 games. 4 declaring wins
      (+213), ZERO declaring losses. TWO solo whist losses: G38(-56, 1A
      est ~2.0, solo rate 14%), G48(-37, 2A est ~2.35, solo rate 40%).
    - FIX: decide_to_call solo rates slashed across the board. Solo whist
      needs ~4 tricks; est 2.0-2.35 only provides ~2 → huge negative EV.
      1A solo: 14→6%. 2A solo est>=2.0: 40→22%. 2A solo est>=3.0: 30→18%.
      3A solo: 55→42%. Expected to save ~50 pts per 50-game iteration.
    - Paired whist is profitable (G19 +25) — no changes to following_decision.
    """

    def __init__(self, seed: int | None = None):
        super().__init__("Bob", seed=seed,
                         w_pass=80, w_game=15, w_in_hand=3, w_betl=1, w_sans=1)
        self._cards_played = 0
        self._total_hand_size = 10
        self._is_declarer = False
        self._trump_suit_val = None   # suit enum value when we're declarer
        self._highest_bid_seen = 0    # track auction escalation for whisting
        self._betl_intent = False     # True when bidding in_hand with betl in mind
        self._i_bid_in_auction = False  # True if Bob bid (not just passed) in auction
        self._trump_leads = 0         # track trump leads as declarer for smart management
        self._ctx = None              # CardPlayContext set before choose_card

    # ------------------------------------------------------------------
    # Hand evaluation helpers
    # ------------------------------------------------------------------

    def _suit_groups(self, hand):
        """Group cards by suit. Returns {suit_value: [Card, ...]} sorted high-to-low."""
        groups = {}
        for c in hand:
            groups.setdefault(c.suit, []).append(c)
        for s in groups:
            groups[s].sort(key=lambda c: c.rank, reverse=True)
        return groups

    def _count_aces(self, hand):
        return sum(1 for c in hand if c.rank == 8)

    def _count_high_cards(self, hand):
        """Count cards rank >= Queen (6)."""
        return sum(1 for c in hand if c.rank >= 6)

    def _best_trump_suit(self, hand):
        """Find best suit for trump: longest suit, break ties by total rank,
        with a cost penalty for expensive suits."""
        groups = self._suit_groups(hand)
        # Suit cost: spades=2, diamonds=3, hearts=4, clubs=5
        suit_cost = {Suit.SPADES: 2, Suit.DIAMONDS: 3, Suit.HEARTS: 4, Suit.CLUBS: 5}
        best_suit = None
        best_score = -1
        for suit, cards in groups.items():
            score = len(cards) * 100 + sum(c.rank for c in cards)
            # Penalize expensive suits: subtract cost * 8 so cheaper suits win on ties
            score -= suit_cost.get(suit, 2) * 8
            if score > best_score:
                best_score = score
                best_suit = suit
        return best_suit, best_score

    def _hand_strength(self, hand):
        """Estimate trick-taking potential for declaring game 2.

        Returns estimated tricks with best trump suit.
        Game 2 needs 6 tricks. Talon adds ~1.5 tricks on average,
        so we need ~4.5 estimated tricks pre-exchange to be comfortable.
        """
        best_suit, _ = self._best_trump_suit(hand)
        if best_suit is None:
            return 0.0
        return self._hand_strength_for_suit(hand, best_suit)

    def _hand_strength_for_suit(self, hand, trump_suit):
        """Estimate tricks with a specific trump suit (cautious coefficients)."""
        groups = self._suit_groups(hand)
        tricks = 0.0
        trump_cards = groups.get(trump_suit, [])
        has_trump_ace = any(c.rank == 8 for c in trump_cards)
        has_trump_king = any(c.rank == 7 for c in trump_cards)

        # Trump tricks
        for c in trump_cards:
            if c.rank == 8:  # Ace
                tricks += 1.0
            elif c.rank == 7:  # King
                if has_trump_ace:
                    tricks += 0.95  # A draws opponents, K nearly guaranteed
                else:
                    tricks += 0.8 if len(trump_cards) >= 3 else 0.45
            elif c.rank >= 5:  # J/Q
                if len(trump_cards) >= 4 and has_trump_ace and has_trump_king:
                    tricks += 0.70  # AK draw opponents' honors first
                elif len(trump_cards) >= 4:
                    tricks += 0.45
            elif len(trump_cards) >= 5:  # low trump with 5+ length
                tricks += 0.3

        # 4th+ trump with Ace control: distribution value after Ace draws
        if has_trump_ace and len(trump_cards) >= 4:
            tricks += 0.45

        # Long trump bonus (ruffing potential)
        if len(trump_cards) >= 5:
            tricks += (len(trump_cards) - 4) * 0.7
        elif len(trump_cards) >= 4:
            tricks += 0.3

        # Side suits
        for suit, cards in groups.items():
            if suit == trump_suit:
                continue
            has_ace = any(c.rank == 8 for c in cards)
            has_king = any(c.rank == 7 for c in cards)
            for c in cards:
                if c.rank == 8:
                    tricks += 0.9
                elif c.rank == 7:  # King
                    if has_ace:
                        tricks += 0.90  # A cashes first, K is master
                    elif len(cards) >= 2:
                        tricks += 0.75  # Guarded K, declarer controls tempo

            # Side-suit length bonus: long suits generate length winners.
            if len(cards) >= 4:
                if has_ace:
                    tricks += (len(cards) - 3) * 0.5
                elif has_king:
                    tricks += (len(cards) - 3) * 0.35
                else:
                    tricks += (len(cards) - 3) * 0.2

        # Void/short suits = ruffing potential
        num_suits = len(groups)
        if num_suits <= 2 and len(trump_cards) >= 4:
            tricks += 1.5
        elif num_suits <= 3 and len(trump_cards) >= 3:
            tricks += 0.5

        return tricks

    def _is_good_betl_hand(self, hand):
        """CAUTIOUS betl: zero danger, 3+ safe suits, no uncovered high cards.

        Allow 1 "soft danger" — solo 8 (rank 2) or solo 9 (rank 3) counts
        as safe enough since 6+ opponent cards sit above them.
        """
        a = betl_hand_analysis(hand)
        if a["danger_count"] == 0 and a["safe_suits"] >= 3:
            return True
        # Allow 1 soft danger: a solo low card (rank <= 3 i.e. 7/8/9) in a
        # 1-card suit — 5+ opponent cards above it = very likely covered
        if a["danger_count"] == 1 and a["safe_suits"] >= 3:
            d_suit, d_rank = a["danger_list"][0]
            suit_detail = None
            for sv, det in a["details"].items():
                suit_name_map = {1: "clubs", 2: "diamonds", 3: "hearts", 4: "spades"}
                if suit_name_map.get(sv) == d_suit:
                    suit_detail = det
                    break
            if suit_detail and suit_detail["num_cards"] == 1 and d_rank <= 3:
                return True
        return False

    def _is_good_betl_hand_in_hand(self, hand):
        """In-hand betl (no talon): zero danger, all 4 suits safe."""
        a = betl_hand_analysis(hand)
        return a["danger_count"] == 0 and a["safe_suits"] == 4

    def _is_good_sans_hand(self, hand):
        """Check if hand has enough aces and high cards for sans (need 6 tricks)."""
        aces = self._count_aces(hand)
        high = self._count_high_cards(hand)
        return aces >= 4 and high >= 7

    # ------------------------------------------------------------------
    # Bidding — hand-strength aware, track auction level
    # ------------------------------------------------------------------

    def bid_intent(self, hand, legal_bids):
        bid_types = {b["bid_type"] for b in legal_bids}

        if bid_types == {"pass"}:
            return {"bid": legal_bids[0], "intent": "forced pass (no other options)"}

        # Reset per-round state on first bid call.
        # Detect new game: if _cards_played > 0, cards were played in the
        # previous game, so this is a fresh auction — reset _i_bid_in_auction.
        # Within the same auction (subsequent bid_intent calls), _cards_played
        # is 0 (just reset below), so we don't re-reset the flag.
        if self._cards_played > 0:
            self._i_bid_in_auction = False
        self._cards_played = 0
        self._is_declarer = False
        self._highest_bid_seen = 0
        self._betl_intent = False
        self._trump_leads = 0

        # Track auction escalation for whisting decisions later
        game_bids = [b for b in legal_bids if b["bid_type"] == "game"]
        if game_bids:
            min_val = min(b.get("value", 2) for b in game_bids)
            self._highest_bid_seen = max(self._highest_bid_seen, min_val - 1)
        if any(b["bid_type"] in ("sans", "betl", "in_hand") for b in legal_bids):
            self._highest_bid_seen = max(self._highest_bid_seen, 5)

        aces = self._count_aces(hand) if hand else 0
        high = self._count_high_cards(hand) if hand else 0
        strength = self._hand_strength(hand) if hand else 0.0

        # Check if "game" bid is available
        if game_bids:
            game_val = game_bids[0].get("value", 2)
            if game_val <= 2:
                # Shape check: longest suit length
                groups = self._suit_groups(hand) if hand else {}
                max_suit_len = max((len(cards) for cards in groups.values()), default=0)

                # Game 2: bid with strong hands (cautious but not passive)
                # Auto-bid with 3+ aces IF shape concentrated (longest >= 4), or
                # strength >= 4.0, or 2+ aces with concentrated shape AND strength >= 3.8.
                # Flat 3-ace (longest < 4): 60% — G5 iter11 lost -80 with 3 aces
                # across 3+3+2+2 shape, aces spread = no ruffing, no long suit.
                # G5 iter13: lost -40 with 2A+str3.5+longest4 — trump AJ109 lacked K.
                # Raised 2-ace threshold from 3.5 to 3.8 to require stronger trump.
                # 2-ace with all 4 suits (no void): need higher strength.
                # G18 iter27: 2A + 4+2+2+2 shape, str 4.05, no voids → -60.
                # No void = no ruffing, flat shape = risky. Require 4.2.
                # G3 iter29: 1A + 5-card K-high trump (KJXXX), str 4.3 → -120.
                # K-high trump without ace loses control. With ≤1 ace, require
                # ace in best trump suit for str >= 4.0 auto-bid, else 4.5.
                num_suits_held = len(groups)
                two_ace_threshold = 4.2 if (aces == 2 and num_suits_held >= 4) else 3.8
                # Trump-ace gate: with ≤1 ace, trump ace is critical for control
                best_suit_obj, _ = self._best_trump_suit(hand)
                has_trump_ace = best_suit_obj and any(
                    c.rank == 8 and c.suit == best_suit_obj for c in hand)
                str_threshold = 4.0 if (has_trump_ace or aces >= 2) else 4.2
                if (aces >= 3 and max_suit_len >= 4) or strength >= str_threshold or (aces >= 2 and strength >= two_ace_threshold and max_suit_len >= 4):
                    self._i_bid_in_auction = True
                    return {"bid": game_bids[0],
                            "intent": f"game 2 — strong (strength {strength:.1f}, aces={aces}, longest={max_suit_len}, trump_ace={has_trump_ace})"}
                elif aces >= 3 and max_suit_len < 4 and self.rng.random() < 0.60:
                    self._i_bid_in_auction = True
                    return {"bid": game_bids[0],
                            "intent": f"game 2 — flat 3-ace 60% (strength {strength:.1f}, longest={max_suit_len})"}
                # Flat 2-ace hands (max suit < 4): 30% borderline — G6 iter7
                # lost -60 with 2 aces but 3+3+2+2 shape
                elif aces >= 2 and strength >= 3.0 and self.rng.random() < 0.47:
                    self._i_bid_in_auction = True
                    return {"bid": game_bids[0],
                            "intent": f"game 2 — flat 2-ace borderline 47% (strength {strength:.1f}, longest={max_suit_len})"}
                # Borderline hands (2.8+): bid 45% with 1+ ace — zero borderline
                # declaring losses. G5 iter10 missed AKQ8 spades+side K (str 2.95).
                elif strength >= 2.8 and aces >= 1 and self.rng.random() < 0.61:
                    self._i_bid_in_auction = True
                    return {"bid": game_bids[0],
                            "intent": f"game 2 — borderline strength {strength:.1f}, 61% roll (aces={aces}, longest={max_suit_len})"}
                else:
                    intent = f"pass — strength {strength:.1f} below 2.8 for cautious game 2 (aces={aces}, longest={max_suit_len})"
            else:
                # Game 3+: only with very strong hands + trump ace
                # G2 iter14: 6-card A-high suit + void (str=5.25) missed at 20%.
                # Neural declared and won +66 while Bob got -33. Zero declaring
                # losses across all iterations proves massive room. Rate 20→50%.
                # New tier: str >= 4.5 at 25% for hands forced to game 3.
                best_suit_obj, _ = self._best_trump_suit(hand)
                has_trump_ace = best_suit_obj and any(
                    c.rank == 8 and c.suit == best_suit_obj for c in hand)
                if game_val <= 3 and strength >= 5.0 and has_trump_ace and self.rng.random() < 0.50:
                    self._i_bid_in_auction = True
                    return {"bid": game_bids[0],
                            "intent": f"game {game_val} — very strong 50% (strength {strength:.1f}, aces={aces}, trump_ace)"}
                if game_val <= 3 and strength >= 4.5 and has_trump_ace and self.rng.random() < 0.25:
                    self._i_bid_in_auction = True
                    return {"bid": game_bids[0],
                            "intent": f"game {game_val} — strong 25% (strength {strength:.1f}, aces={aces}, trump_ace)"}
                intent = f"pass — game {game_val}+ too risky for cautious style (aces={aces}, str={strength:.1f})"
            pass_bid = next((b for b in legal_bids if b["bid_type"] == "pass"), None)
            if pass_bid:
                return {"bid": pass_bid, "intent": intent}

        # Check for betl/in_hand betl — auction betl = in-hand (no exchange)
        if hand:
            betl_bids = [b for b in legal_bids if b["bid_type"] == "betl"]
            if betl_bids and self._is_good_betl_hand_in_hand(hand):
                self._i_bid_in_auction = True
                a = betl_hand_analysis(hand)
                return {"bid": betl_bids[0],
                        "intent": f"betl — cautious zero danger (safe_suits={a['safe_suits']})"}
            in_hand_bids = [b for b in legal_bids if b["bid_type"] == "in_hand"]
            if in_hand_bids and self._is_good_betl_hand_in_hand(hand):
                self._i_bid_in_auction = True
                self._betl_intent = True
                a = betl_hand_analysis(hand)
                return {"bid": in_hand_bids[0],
                        "intent": f"in_hand (betl intent) — zero danger, safe_suits={a['safe_suits']}"}

        pass_bid = next((b for b in legal_bids if b["bid_type"] == "pass"), None)
        if pass_bid:
            return {"bid": pass_bid,
                    "intent": f"pass — no suitable bids (aces={aces}, high={high})"}

        # Fallback
        return super().bid_intent(hand, legal_bids)

    # ------------------------------------------------------------------
    # Exchange — keep strongest suit + aces, discard weakest, create voids
    # ------------------------------------------------------------------

    def discard_decision(self, hand_card_ids, talon_card_ids):
        """Keep best trump suit cards and aces; discard weakest. Try to create voids.
        If betl looks promising, discard highest/most dangerous cards instead."""
        all_ids = hand_card_ids + talon_card_ids
        rank_order = {"7": 1, "8": 2, "9": 3, "10": 4, "J": 5, "Q": 6, "K": 7, "A": 8}

        def card_rank(cid):
            return rank_order.get(cid.split("_")[0], 0)

        def card_suit(cid):
            return cid.split("_")[1]

        # Try betl-optimized discard first
        betl_discard = self._try_betl_discard(all_ids, card_rank, card_suit)
        if betl_discard:
            return betl_discard

        suit_counts = {}
        suit_cards = {}
        for cid in all_ids:
            s = card_suit(cid)
            suit_counts[s] = suit_counts.get(s, 0) + 1
            suit_cards.setdefault(s, []).append(cid)

        # Among tied-length suits, prefer lower-cost ones
        suit_cost = {"spades": 2, "diamonds": 3, "hearts": 4, "clubs": 5}
        best_suit = max(suit_counts,
                        key=lambda s: (suit_counts[s], -suit_cost.get(s, 2)))

        # Void suits where both cards are below King (2-card suits)
        voidable = []
        for s in sorted(suit_cards.keys()):
            if s == best_suit:
                continue
            cards = suit_cards[s]
            if len(cards) == 2 and all(card_rank(c) < 7 for c in cards):
                total_rank = sum(card_rank(c) for c in cards)
                voidable.append((total_rank, cards))
        if voidable:
            voidable.sort()
            return {"discard": voidable[0][1],
                    "intent": f"void weakest off-suit below K (trump={best_suit})"}

        # Check for two singleton off-suits below King — discard both to create voids
        singleton_discards = []
        for s in sorted(suit_cards.keys()):
            if s == best_suit:
                continue
            cards = suit_cards[s]
            if len(cards) == 1 and card_rank(cards[0]) < 7:
                singleton_discards.append(cards[0])
        if len(singleton_discards) >= 2:
            # Discard the two weakest singletons
            singleton_discards.sort(key=card_rank)
            return {"discard": [singleton_discards[0], singleton_discards[1]],
                    "intent": f"void two singleton off-suits (trump={best_suit})"}

        def keep_score(cid):
            score = card_rank(cid) * 10
            s = card_suit(cid)
            if s == best_suit:
                score += 100
            if card_rank(cid) == 8:
                score += 50
            if card_rank(cid) == 7:
                score += 25
            if s != best_suit and suit_counts[s] <= 2:
                score -= 40
            return score

        sorted_cards = sorted(all_ids, key=keep_score)
        return {"discard": sorted_cards[:2],
                "intent": f"discard weakest cards (trump={best_suit})"}

    _try_betl_discard = PlayerAlice._try_betl_discard

    def choose_discard(self, hand_card_ids, talon_card_ids):
        self._is_declarer = True
        winner_bid = getattr(self, '_winner_bid', None)
        if winner_bid:
            result = self._evaluate_12_card_contracts(hand_card_ids, talon_card_ids, winner_bid)
            self._pre_chosen_contract = result["contract"]
            return result["discard"]
        decision = self.discard_decision(hand_card_ids, talon_card_ids)
        return decision["discard"]

    # ------------------------------------------------------------------
    # 12-card evaluation — unified discard + contract selection
    # ------------------------------------------------------------------

    def _score_hand_for_contract(self, hand, contract_type, trump_suit=None):
        """Score a 10-card hand for a specific contract (cautious style)."""
        if contract_type == "betl":
            a = betl_hand_analysis(hand)
            # CRITICAL: Aces are guaranteed trick-winners in betl = instant loss.
            # G18(-120): 2A chose betl. G19(-120): A+K+Q+J chose betl. -240 total.
            if a["has_ace"]:
                return -200
            # 3+ high cards (K/Q/A) make betl very risky even without aces
            if a["high_card_count"] >= 3:
                return -150
            # Bob is cautious: require zero danger + safe suits
            score = 100 - a["danger_count"] * 50
            if a["safe_suits"] >= 3:
                score += 15
            elif a["safe_suits"] >= 2 and a["max_suit_len"] <= 3:
                score += 5
            score -= a["max_rank"] * 3
            # Very low cards bonus (cautious threshold)
            if a["max_rank"] <= 4 and a["safe_suits"] >= 2:
                score += 15
            return score
        elif contract_type == "sans":
            if not self._is_good_sans_hand(hand):
                return -100
            aces = self._count_aces(hand)
            high = self._count_high_cards(hand)
            return 80 + aces * 15 + high * 5
        else:
            strength = self._hand_strength_for_suit(hand, trump_suit)
            groups = self._suit_groups(hand)
            trump_len = len(groups.get(trump_suit, []))
            cost_penalty = (_SUIT_BID_VALUE.get(trump_suit, 2) - 2) * 2
            return strength * 15 + trump_len * 3 - cost_penalty

    _evaluate_12_card_contracts = PlayerAlice._evaluate_12_card_contracts

    # ------------------------------------------------------------------
    # Contract — use pre-evaluated choice or fall back to heuristic
    # ------------------------------------------------------------------

    def bid_decision(self, hand, legal_levels, winner_bid):
        """Pick safest contract based on hand evaluation."""
        # Use pre-evaluated contract from 12-card analysis if available
        pre = getattr(self, '_pre_chosen_contract', None)
        if pre:
            self._pre_chosen_contract = None
            ctype, trump, level = pre
            if ctype == "suit" and trump:
                self._trump_suit_val = {v: k for k, v in SUIT_NAMES.items()}.get(trump)
            return {"contract_type": ctype, "trump": trump, "level": level,
                    "intent": f"{ctype} — 12-card evaluation"}

        # In-hand betl: if we bid in_hand with betl intent, choose betl
        if self._betl_intent and 6 in legal_levels:
            return {"contract_type": "betl", "trump": None, "level": 6,
                    "intent": "betl — in-hand betl intent"}
        # Post-exchange betl (cautious): zero danger preferred, very conservative extras
        if 6 in legal_levels:
            a = betl_hand_analysis(hand)
            # Zero danger + 3 safe suits — guaranteed safe
            if a["danger_count"] == 0 and a["safe_suits"] >= 3:
                return {"contract_type": "betl", "trump": None, "level": 6,
                        "intent": f"betl — zero danger post-exchange (safe_suits={a['safe_suits']})"}
            # All very low cards: max rank ≤ 10 (rank 4), no aces — every card
            # has 4+ opponent cards above it; even without safe_suits it's hard to win a trick
            if not a["has_ace"] and a["max_rank"] <= 4 and a["safe_suits"] >= 2:
                return {"contract_type": "betl", "trump": None, "level": 6,
                        "intent": f"betl — very low cards (max_rank={a['max_rank']}, cautious)"}
            # Zero danger but only 2 safe suits — still safe if spread thin
            if a["danger_count"] == 0 and a["safe_suits"] >= 2 and a["max_suit_len"] <= 3:
                return {"contract_type": "betl", "trump": None, "level": 6,
                        "intent": f"betl — zero danger spread (safe={a['safe_suits']}, longest={a['max_suit_len']})"}

        if 7 in legal_levels and self._is_good_sans_hand(hand):
            return {"contract_type": "sans", "trump": None, "level": 7,
                    "intent": "sans — dominant high cards"}

        min_bid = winner_bid.effective_value if winner_bid else 0
        suit_bid = {Suit.SPADES: 2, Suit.DIAMONDS: 3, Suit.HEARTS: 4, Suit.CLUBS: 5}
        best_suit, _ = self._best_trump_suit(hand)
        if best_suit and suit_bid.get(best_suit, 0) < min_bid:
            groups = self._suit_groups(hand)
            best_suit = None
            best_score = -1
            for suit, cards in groups.items():
                if suit_bid.get(suit, 0) < min_bid:
                    continue
                score = len(cards) * 100 + sum(c.rank for c in cards)
                score -= suit_bid.get(suit, 2) * 8
                if score > best_score:
                    best_score = score
                    best_suit = suit
            if best_suit is None:
                best_suit = min((s for s, v in suit_bid.items() if v >= min_bid), key=lambda s: suit_bid[s])
        trump_name = SUIT_NAMES[best_suit] if best_suit else "spades"
        self._trump_suit_val = best_suit

        suit_levels = [l for l in legal_levels if l not in (6, 7)]
        if suit_levels:
            level = min(suit_levels)
        elif legal_levels:
            level = min(legal_levels)
        else:
            level = 2
        return {"contract_type": "suit", "trump": trump_name, "level": level,
                "intent": f"suit {trump_name} level {level} — strongest valid suit"}

    def choose_contract(self, legal_levels, hand, winner_bid):
        decision = self.bid_decision(hand, legal_levels, winner_bid)
        return decision["contract_type"], decision["trump"], decision["level"]

    # ------------------------------------------------------------------
    # Whisting — hand-strength-aware, cautious: only whist with 2+ aces
    # ------------------------------------------------------------------

    def _estimate_whist_tricks(self, hand, trump_suit=None):
        """Estimate tricks as whister, accounting for declarer's trump suit.

        When trump_suit is known:
        - Cards in declarer's trump suit are worth less (declarer has length)
        - Non-trump aces remain high value (guaranteed tricks when led)
        - Unsupported kings (no ace in same suit) devalued — ace may sit over us
        - Queens without ace in suit contribute very little
        """
        if not hand:
            return 0.0
        tricks = 0.0
        groups = self._suit_groups(hand)
        unsupported_kings = 0
        unsupported_queens = 0
        for suit, cards in groups.items():
            has_ace = any(c.rank == 8 for c in cards)
            if suit == trump_suit:
                # In declarer's trump suit: only high trumps matter
                for c in cards:
                    if c.rank == 8:    # Ace of trump — still strong but declarer has length
                        tricks += 0.85
                    elif c.rank == 7:  # King of trump — risky, declarer likely has ace
                        tricks += 0.30
                    # Low trumps worthless as whister — declarer extracts them
            else:
                # Non-trump suits
                for c in cards:
                    if c.rank == 8:  # Ace — almost guaranteed trick
                        tricks += 0.95
                    elif c.rank == 7:  # King
                        if trump_suit and len(cards) <= 1:
                            tricks += 0.15  # singleton king vulnerable to trumping
                        elif has_ace:
                            # Supported king (ace in same suit) — reliable
                            tricks += 0.55 if len(cards) >= 3 else 0.40
                        else:
                            # Unsupported king — opponent may have ace over us
                            unsupported_kings += 1
                            tricks += 0.35 if len(cards) >= 3 else 0.20
                    elif c.rank == 6 and len(cards) >= 3:  # Queen with support
                        if not has_ace:
                            unsupported_queens += 1
                        tricks += 0.20
        # Penalty for multiple unsupported kings — unreliable
        if unsupported_kings >= 3:
            tricks -= 0.4
        elif unsupported_kings >= 2:
            tricks -= 0.2
        # Penalty for multiple unsupported queens — G4 iter14: 3 queens (D) scattered
        # across suits without aces contributed nothing, inflated est
        if unsupported_queens >= 3:
            tricks -= 0.25
        elif unsupported_queens >= 2:
            tricks -= 0.15

        # A-K combo bonus: ace + king in same non-trump suit = ~1.5 guaranteed tricks
        # More reliable than scattered high cards across suits
        for suit, cards in groups.items():
            if suit == trump_suit:
                continue
            has_ace = any(c.rank == 8 for c in cards)
            has_king = any(c.rank == 7 for c in cards)
            if has_ace and has_king:
                tricks += 0.20

        # Void-suit bonus: having a void in a non-trump suit = ruffing potential
        # G1 iter15: Bob had void in suit 4 but est didn't reflect ruffing value
        if trump_suit:
            all_suits = {1, 2, 3, 4}
            suits_held = set(groups.keys())
            void_suits = all_suits - suits_held - {trump_suit}
            if void_suits:
                tricks += 0.25

        # Long non-trump suit penalty: 5+ cards in a single non-trump suit = dead weight
        # G5 iter16: Bob had 6 spades (KQJT98) vs diamond trump. Those 6 cards
        # consumed 60% of the hand but contributed almost nothing — declarer ruffs them.
        if trump_suit:
            for suit, cards in groups.items():
                if suit != trump_suit and len(cards) >= 5:
                    tricks -= 0.30
                    break  # Only penalize once

        # Low trump count penalty: 0-1 cards in declarer's trump suit = no trump power.
        # G3 iter19: Bob had 0 trump cards (void in trump). Couldn't ruff, couldn't
        # defend trump leads. Declarer draws zero trumps from you — your high cards
        # in side suits are vulnerable to being ruffed.
        # G8 iter8: 0 trumps, est 1.15, whisted → -90. Penalty -0.35→-0.45.
        # Iter15: G15 2A hand penalized from 2.10→1.65 → missed at 90%. Side aces
        # still win regardless of trump count. Reduce -0.45→-0.38.
        if trump_suit:
            trump_count = sum(1 for c in hand if c.suit == trump_suit)
            if trump_count <= 1:
                tricks -= 0.38

        # Flat shape penalty: when no non-trump suit has 4+ cards, high cards are
        # spread thin and can't develop length winners. G9 iter21: Bob had 4+4+1+1
        # shape (AKQ9 + A1098) but both suits only 4 cards — declarer's 5-card
        # trump dominated. Flat whist hands overestimate trick potential.
        if trump_suit:
            max_non_trump_len = max(
                (len(cards) for suit, cards in groups.items() if suit != trump_suit),
                default=0
            )
            if max_non_trump_len <= 3:
                tricks -= 0.20

        return tricks

    def decide_to_call(self, hand, contract_type, trump_suit, legal_actions):
        """Decide whether to call when the other defender passed — CAUTIOUS.

        Solo whisting requires ~4 tricks alone (vs ~2 each paired). Very high bar.
        G3 iter5: 2A est ~1.2, fell to following_decision (96% paired rate) → -80.
        G11 iter5: 2A est ~2.4, same pattern → -40. Both catastrophic.
        G14 iter5: 2A est ~2.1, solo → -10 (passive was -34, so correct).
        Fix: NO fallback to following_decision. Explicit solo thresholds only.
        """
        aces = self._count_aces(hand) if hand else 0
        est = self._estimate_whist_tricks(hand, trump_suit) if hand else 0.0
        outbid_penalty = 0.20 if self._i_bid_in_auction else 0.0

        # Sans: only call alone with 4+ aces — solo sans needs near-perfect hand
        if contract_type == "sans":
            if aces >= 4:
                return {"action": "call",
                        "intent": f"call sans — {aces} aces ({est:.1f} tricks)"}
            return {"action": "pass",
                    "intent": f"pass — solo sans too risky ({aces}A, {est:.1f} tricks)"}

        # Suit: explicit solo whist thresholds — NO following_decision fallback
        # Iter5: G38(-56, 1A 14% solo) + G48(-37, 2A 40% solo) = -93.
        # Solo needs ~4 tricks; est 2.0-2.35 provides ~2 → huge negative EV.
        # Slash all solo rates for cautious play.
        if aces >= 3 and est >= 3.5:
            rate = max(0.42 - outbid_penalty, 0.20)
            if self.rng.random() < rate:
                return {"action": "call",
                        "intent": f"call solo — {aces}A + {est:.1f} tricks {int(rate*100)}%"}
        if aces >= 2 and est >= 3.0:
            rate = max(0.18 - outbid_penalty, 0.06)
            if self.rng.random() < rate:
                return {"action": "call",
                        "intent": f"call solo — {aces}A strong {int(rate*100)}% ({est:.1f} tricks)"}
        # 2A + decent est: G48(-37): 2A est ~2.35 rolled within 40% → solo → -37.
        # Solo with est ~2.35 means ~2 tricks vs needed ~4. Slashed 40→22%.
        if aces >= 2 and est >= 2.0:
            rate = max(0.22 - outbid_penalty, 0.06)
            if self.rng.random() < rate:
                return {"action": "call",
                        "intent": f"call solo — {aces}A decent {int(rate*100)}% ({est:.1f} tricks)"}
        # 1A + strong est: G38(-56): 1A est ~2.0 rolled within 14% → solo → -56.
        # Even 14% is too high — est 2.0 means ~2 tricks, solo needs ~4.
        # Slashed 14→6%. Outbid hands get 0% (safe).
        if aces >= 1 and est >= 2.0:
            rate = max(0.06 - outbid_penalty, 0.0)
            if rate > 0 and self.rng.random() < rate:
                return {"action": "call",
                        "intent": f"call solo — {aces}A speculative {int(rate*100)}% ({est:.1f} tricks)"}

        # Not strong enough for solo whist — always pass (cautious default)
        return {"action": "pass",
                "intent": f"pass — solo whist too risky ({aces}A, {est:.1f} tricks)"}

    def decide_to_counter(self, hand, contract_type, trump_suit, legal_actions):
        """Decide whether to counter (double stakes) — CAUTIOUS.

        Almost never counters. Only with extreme confidence.
        """
        action_types = [a["action"] for a in legal_actions]
        aces = self._count_aces(hand) if hand else 0
        est = self._estimate_whist_tricks(hand, trump_suit) if hand else 0.0

        # Declarer responding to a counter
        if "double_counter" in action_types:
            if est >= 4.5 and aces >= 3:
                return {"action": "double_counter",
                        "intent": f"double counter — very confident ({aces}A, {est:.1f} tricks)"}
            return {"action": "start_game",
                    "intent": f"start game — accept counter cautiously ({aces}A, {est:.1f} tricks)"}

        # Defender: almost never counter
        if aces >= 4 and est >= 5.0:
            return {"action": "counter",
                    "intent": f"counter — overwhelming ({aces}A, {est:.1f} tricks)"}

        # Not strong enough to counter
        if "start_game" in action_types:
            return {"action": "start_game",
                    "intent": f"start game — cautious ({aces}A, {est:.1f} tricks)"}
        if "call" in action_types:
            return self.decide_to_call(hand, contract_type, trump_suit, legal_actions)
        # G43 fix: do NOT fall through to following_decision which uses aggressive
        # paired-whist rates (100% for 1A). In counter context, prefer pass for
        # cautious play. G43: 1A est ~1.67 fell through → "follow" at 100% →
        # counter escalation → -70.
        if "pass" in action_types:
            return {"action": "pass",
                    "intent": f"pass — cautious counter fallback ({aces}A, {est:.1f} tricks)"}
        return self.following_decision(hand, contract_type, trump_suit, legal_actions)

    def following_decision(self, hand, contract_type, trump_suit, legal_actions):
        """Hand-strength-aware whisting — CAUTIOUS style with trump awareness.

        Iter5: ALL 3 negatives (-36,-72,-40) came from 2-ace whist calls.
        Slashed rates across the board: 2A strong 92→78, weak 85→60,
        high-level 82→62/70→45. Outbid penalty 15→25%. 1A rates reduced,
        0A rate 24→12%.

        Scoring math (single whister, game_value=v):
          2+ tricks -> positive.  1 trick -> -(v*9).  0 tricks -> -(v*10).
          Higher game_value = bigger loss. Pass = 0 (safe).
        """
        action_types = [a["action"] for a in legal_actions]

        if "start_game" in action_types:
            return {"action": "start_game", "intent": "start game"}

        if "follow" in action_types:
            # Base heuristic (any player)
            should, n_tt, s_reasons = self._should_follow_heuristic(hand, trump_suit)
            if should:
                return {"action": "follow",
                        "intent": f"follow — heuristic: {n_tt} trump tricks, {s_reasons:.1f} reasons"}

            aces = self._count_aces(hand) if hand else 0
            est_tricks = self._estimate_whist_tricks(hand, trump_suit) if hand else 0.0
            is_high_level = self._highest_bid_seen >= 3

            # Sans gate: sans declarers have 3-4 aces and control all suits.
            if contract_type == "sans":
                if aces >= 3:
                    rate = 0.65
                    if self.rng.random() < rate:
                        return {"action": "follow",
                                "intent": f"follow — sans whist, {aces}A {int(rate*100)}% ({est_tricks:.1f} tricks)"}
                    return {"action": "pass",
                            "intent": f"pass — sans whist, {aces}A cautious dodge ({est_tricks:.1f} tricks)"}
                if aces >= 2 and est_tricks >= 2.0:
                    rate = 0.40
                    if self.rng.random() < rate:
                        return {"action": "follow",
                                "intent": f"follow — sans whist, 2A+strong {int(rate*100)}% ({est_tricks:.1f} tricks)"}
                    return {"action": "pass",
                            "intent": f"pass — sans whist, 2A cautious ({est_tricks:.1f} tricks)"}
                return {"action": "pass",
                        "intent": f"pass — sans contract, need 2+ aces ({aces}A, {est_tricks:.1f} tricks)"}

            # Hard gate: 4+ cards in declarer's trump suit = always pass
            if trump_suit and hand:
                trump_count = sum(1 for c in hand if c.suit == trump_suit)
                has_trump_ace = any(c.suit == trump_suit and c.rank == 8 for c in hand)
                if trump_count >= 4 and not has_trump_ace and aces < 2:
                    return {"action": "pass",
                            "intent": f"pass — {trump_count} cards in declarer's trump, dead weight"}

            # Hard gate: 3+ unsupported kings = always pass.
            if hand:
                groups_w = self._suit_groups(hand)
                n_unsup_kings = 0
                for suit_w, cards_w in groups_w.items():
                    if suit_w == trump_suit:
                        continue
                    has_ace_w = any(c.rank == 8 for c in cards_w)
                    has_king_w = any(c.rank == 7 for c in cards_w)
                    if has_king_w and not has_ace_w:
                        n_unsup_kings += 1
                if n_unsup_kings >= 3:
                    return {"action": "pass",
                            "intent": f"pass — {n_unsup_kings} unsupported kings, unreliable"}

            # 2+ aces: whist when est is good, hedge when weak/flat
            # G4 iter14: 2A + flat (3+3+2+2) + 3 scattered queens, est ~1.8 → -90.
            # "Always whist" was too aggressive for weak 2-ace hands.
            # G9 iter28: Bob bid, Neural outbid, Bob whisted → -72. When you bid
            # and get outbid, declarer is proven strong → reduce rates.
            # Iter9: 0.25→0.22, solo gate protects against solo disasters.
            outbid_penalty = 0.22 if self._i_bid_in_auction else 0.0
            if aces >= 2:
                if is_high_level and est_tricks < 2.5:
                    # High-level + weak support: 65% hedge (was 62%, zero losses iters 1-10)
                    rate = max(0.65 - outbid_penalty, 0.20)
                    if self.rng.random() < rate:
                        return {"action": "follow",
                                "intent": f"follow — {aces} aces, high-level {int(rate*100)}% hedge ({est_tricks:.1f} est tricks)"}
                    return {"action": "pass",
                            "intent": f"pass — {aces} aces, high-level cautious hedge ({est_tricks:.1f} est tricks)"}
                if est_tricks >= 2.0:
                    # Bumped: 95→97 / 100→100. Zero 2A whist losses iters 1-10.
                    base_strong_2a = 0.97 if is_high_level else 1.00
                    rate = max(base_strong_2a - outbid_penalty, 0.62)
                    if self.rng.random() < rate:
                        return {"action": "follow",
                                "intent": f"follow — {aces} aces, strong est {int(rate*100)}% whist ({est_tricks:.1f} est tricks)"}
                    return {"action": "pass",
                            "intent": f"pass — {aces} aces, strong est safety dodge ({est_tricks:.1f} est tricks)"}
                # Weak 2-ace (est < 2.0): bumped 89→92, 99→100.
                # Zero 2A whist losses iters 1-10. Near-automatic.
                base_weak_2a = 0.92 if is_high_level else 1.00
                rate = max(base_weak_2a - outbid_penalty, 0.46)
                if self.rng.random() < rate:
                    return {"action": "follow",
                            "intent": f"follow — {aces} aces, weak est {int(rate*100)}% ({est_tricks:.1f} est tricks)"}
                return {"action": "pass",
                        "intent": f"pass — {aces} aces, weak est cautious ({est_tricks:.1f} est tricks)"}

            # 1 ace: bumped rates. Zero 1A whist losses iters 1-9.
            # Iter9: zero whist calls, all passive. Bump to capture more income.
            outbid_1a = 0.13 if self._i_bid_in_auction else 0.0
            if aces == 1:
                if is_high_level:
                    # Bumped: 55→58% (est>=2.0), 31→34% (est>=1.5).
                    # Zero 1A high-level losses iters 1-10. Solo gate protects.
                    if est_tricks >= 2.0:
                        rate = max(0.58 - outbid_1a, 0.0)
                    elif est_tricks >= 1.5:
                        rate = max(0.34 - outbid_1a, 0.0)
                    else:
                        rate = 0.0
                elif est_tricks >= 2.0:
                    # Keep 100%: automatic with 1A est 2.0+.
                    rate = max(1.00 - outbid_1a, 0.62)
                elif est_tricks >= 1.5:
                    # Keep 100%: zero 1A whist losses iters 1-10.
                    rate = max(1.00 - outbid_1a, 0.54)
                elif est_tricks >= 1.0:
                    # Bumped 89→92%: zero 1A whist losses across ALL 10
                    # iterations. 8% miss is still safe. Solo gate protects.
                    rate = max(0.92 - outbid_1a, 0.40)
                else:
                    rate = 0.34   # Weak 1A floor bumped 31→34% — solo gate protects
                # A-K combo boost: ace + king in same non-trump side suit = concentrated
                # strength, more reliable than scattered cards. Add 0.15 to rate.
                if rate > 0 and hand and trump_suit:
                    groups = self._suit_groups(hand)
                    for suit, cards in groups.items():
                        if suit == trump_suit:
                            continue
                        has_a = any(c.rank == 8 for c in cards)
                        has_k = any(c.rank == 7 for c in cards)
                        if has_a and has_k:
                            rate = max(rate, min(rate + 0.15, 0.85))
                            break
                # Void-suit boost: having a void = ruffing potential. Add 0.12.
                # G4 iter22: 1A + void [[A,J,9,7],[J,10,9,8],[D,10],[]] missed at ~37%.
                # Void hands are consistently profitable — bump 0.10 → 0.12.
                if rate > 0 and hand and trump_suit:
                    all_suits = {1, 2, 3, 4}
                    suits_held = {c.suit for c in hand}
                    void_suits = all_suits - suits_held - {trump_suit}
                    if void_suits:
                        rate = max(rate, min(rate + 0.12, 0.85))
                if rate > 0 and self.rng.random() < rate:
                    return {"action": "follow",
                            "intent": f"follow — 1 ace, {int(rate*100)}% rate ({est_tricks:.1f} tricks)"}
                return {"action": "pass",
                        "intent": f"pass — 1 ace, cautious ({est_tricks:.1f} tricks)"}

            # 0 aces: chance with strong kings. Bumped: 70→74, 54→58, 30→34.
            # Zero 0A whist losses iters 1-10. More income from king-based hands.
            if est_tricks >= 2.0:
                rate_0a = 0.40 if is_high_level else 0.74
                if self.rng.random() < rate_0a:
                    return {"action": "follow",
                            "intent": f"follow — 0 aces, {int(rate_0a*100)}% rate on strong kings ({est_tricks:.1f} tricks)"}
            elif est_tricks >= 1.5 and not is_high_level:
                if self.rng.random() < 0.58:
                    return {"action": "follow",
                            "intent": f"follow — 0 aces, 58% rate on decent kings ({est_tricks:.1f} tricks)"}
            elif est_tricks >= 1.0 and not is_high_level:
                if self.rng.random() < 0.34:
                    return {"action": "follow",
                            "intent": f"follow — 0 aces, 34% speculative ({est_tricks:.1f} tricks)"}
            elif est_tricks >= 0.5 and not is_high_level:
                # Speculative floor bumped 12→16%: zero 0A losses in 10 iterations.
                if self.rng.random() < 0.16:
                    return {"action": "follow",
                            "intent": f"follow — 0 aces, 16% speculative floor ({est_tricks:.1f} tricks)"}
            return {"action": "pass",
                    "intent": f"pass — 0 aces, cautious ({est_tricks:.1f} tricks)"}

        if "pass" in action_types:
            return {"action": "pass", "intent": "pass — no follow option"}
        return {"action": action_types[0], "intent": f"fallback — {action_types[0]}"}

    # ------------------------------------------------------------------
    # Card play — strategic with declarer/whister awareness
    # ------------------------------------------------------------------

    # Personality params: Bob ducks king for 3 tricks, uses highest trump
    _PLAY_PARAMS = {'king_duck_tricks': 3, 'whister_trump_pref': 'highest'}

    _score_all_cards = PlayerAlice._score_all_cards
    choose_card = PlayerAlice.choose_card

    _betl_choose_card = PlayerAlice._betl_choose_card
    _betl_declarer_play = PlayerAlice._betl_declarer_play
    _betl_defender_play = PlayerAlice._betl_defender_play

    def _whister_lead(self, legal_cards):
        """Fallback whister lead when no context available."""
        groups = self._suit_groups(legal_cards)
        aces = [c for c in legal_cards if c.rank == 8]
        if aces:
            ak_aces = [a for a in aces if any(c.rank == 7 and c.suit == a.suit for c in legal_cards)]
            if ak_aces:
                return ak_aces[0]
            aces.sort(key=lambda c: len(groups.get(c.suit, [])))
            return aces[0]
        kings = [c for c in legal_cards if c.rank == 7]
        if kings:
            kings.sort(key=lambda c: len(groups.get(c.suit, [])), reverse=True)
            return kings[0]
        shortest_suit = min(groups.keys(), key=lambda s: len(groups[s]))
        return groups[shortest_suit][0]


class PlayerCarol(WeightedRandomPlayer):
    """Carol: PRAGMATIC Preferans player — calculated risks for best EV.

    Key strategies (iteration 45):
    - Iter3 (50-game) results: +117 across 31 games. Declaring: 12/13 (+525).
      Whisting: G39(-100) and G47(-42) from decide_to_counter bug.
    - CRITICAL FIX: decide_to_counter unconditionally called with weak hands.
      G39: 1A est ~1.6, counter phase returned "call" (open play) → -100.
      Fix: strong hands (2A+est>=3.0) call, weak hands start_game (closed).
    - Small rate bumps: 1A est>=2.0 88→90%, 2A est>=2.0 96/82→97/83%.
      Zero whist losses in initial following_decision across iter3.
    """

    def __init__(self, seed: int | None = None):
        super().__init__("Carol", seed=seed,
                         w_pass=85, w_game=12, w_in_hand=2, w_betl=1, w_sans=1)
        self._cards_played = 0
        self._total_hand_size = 10
        self._is_declarer = False
        self._trump_suit = None       # trump suit name for card play
        self._trump_suit_val = None   # trump suit enum value for card play
        self._highest_bid_seen = 0    # track auction escalation for whisting
        self._betl_intent = False     # True when bidding in_hand with betl in mind
        self._whist_call_count = 0    # track repeat whist calls in same round
        self._trump_leads = 0         # track trump leads as declarer for smart management
        self._ctx = None              # CardPlayContext set before choose_card

    # ------------------------------------------------------------------
    # Hand evaluation helpers
    # ------------------------------------------------------------------

    def _is_good_betl_hand(self, hand):
        """PRAGMATIC betl: ≤1 danger + safe suits or voids."""
        a = betl_hand_analysis(hand)
        if a["danger_count"] == 0:
            return True
        if a["danger_count"] <= 1 and a["safe_suits"] >= 2 and a["void_count"] >= 1:
            return True
        return False

    def _is_good_betl_hand_in_hand(self, hand):
        """In-hand betl (no talon): zero danger + 2+ voids."""
        a = betl_hand_analysis(hand)
        return a["danger_count"] == 0 and a["void_count"] >= 2

    def _suit_groups(self, hand):
        """Group cards by suit → {suit_value: [Card, ...]} sorted high→low."""
        groups = {}
        for c in hand:
            groups.setdefault(c.suit, []).append(c)
        for s in groups:
            groups[s].sort(key=lambda c: c.rank, reverse=True)
        return groups

    def _count_aces(self, hand):
        return sum(1 for c in hand if c.rank == 8)

    def _count_high(self, hand):
        """Cards rank >= Queen (6)."""
        return sum(1 for c in hand if c.rank >= 6)

    def _best_trump(self, hand):
        """Find best trump suit: longest suit, break ties by total rank."""
        groups = self._suit_groups(hand)
        best_suit = None
        best_score = -1
        for suit, cards in groups.items():
            score = len(cards) * 100 + sum(c.rank for c in cards)
            if score > best_score:
                best_score = score
                best_suit = suit
        return best_suit

    def _hand_strength(self, hand):
        """Estimate trick-taking potential of the hand for declaring game 2.

        Returns estimated number of tricks with best trump suit.
        Game 2 needs 6 tricks out of 10. After exchange we get 2 more cards
        from talon, so we estimate conservatively on the 10-card pre-exchange hand.
        """
        groups = self._suit_groups(hand)
        best_suit = self._best_trump(hand)
        if best_suit is None:
            return 0.0

        tricks = 0.0
        trump_cards = groups.get(best_suit, [])
        has_trump_ace = any(c.rank == 8 for c in trump_cards)
        has_trump_king = any(c.rank == 7 for c in trump_cards)

        # Trump tricks
        for c in trump_cards:
            if c.rank == 8:  # Ace
                tricks += 1.0
            elif c.rank == 7:  # King
                if has_trump_ace:
                    tricks += 0.95  # A draws opponents, K nearly guaranteed
                else:
                    tricks += 0.7 if len(trump_cards) >= 3 else 0.4
            elif c.rank >= 5:  # J/Q
                if len(trump_cards) >= 4 and has_trump_ace and has_trump_king:
                    tricks += 0.70  # AK draw opponents' honors first
                elif len(trump_cards) >= 4:
                    tricks += 0.4
            elif len(trump_cards) >= 5:  # low trump with 5+ length
                tricks += 0.3

        # 4th+ trump with Ace control: distribution value after Ace draws
        if has_trump_ace and len(trump_cards) >= 4:
            tricks += 0.45

        # Long trump bonus (extra trumps = ruffing potential)
        if len(trump_cards) >= 5:
            tricks += (len(trump_cards) - 4) * 0.6
        elif len(trump_cards) >= 4:
            tricks += 0.3

        # Side suits
        for suit, cards in groups.items():
            if suit == best_suit:
                continue
            has_ace = any(c.rank == 8 for c in cards)
            for c in cards:
                if c.rank == 8:
                    tricks += 0.9
                elif c.rank == 7:  # King
                    if has_ace:
                        tricks += 0.90  # A cashes first, K is master
                    elif len(cards) >= 2:
                        tricks += 0.75  # Guarded K, declarer controls tempo

        # Void/short suits = ruffing potential with trumps
        num_suits = len(groups)
        if num_suits <= 2 and len(trump_cards) >= 4:
            tricks += 1.2
        elif num_suits <= 3 and len(trump_cards) >= 3:
            tricks += 0.4

        return tricks

    def _estimate_whist_tricks(self, hand, declarer_trump=None):
        """Estimate tricks we can take as a whisting defender.

        Trump-aware: cards in declarer's trump suit are worth less (declarer
        controls that suit). Singleton non-trump kings are easily trumped.
        Ace-King combos in same suit are very reliable (2 tricks almost guaranteed).
        Void suits penalized: as whister, having fewer suits means declarer
        can ruff our side-suit winners more easily.
        Singleton aces penalized: declarer can duck the ace, then trump the
        suit next time we lead it. Aces need 2+ cards for reliable tricks.
        Unsupported kings devalued (iter 12): G3 iter12 lost -72 with 2
        unsupported kings (K in 3-card suits). Kings without aces rarely
        convert to tricks against strong declarers. Multi-king penalty added.
        """
        tricks = 0.0
        groups = self._suit_groups(hand)
        unsupported_kings = 0
        unsupported_queens = 0
        for suit, cards in groups.items():
            is_trump = (suit == declarer_trump) if declarer_trump else False
            has_ace = any(c.rank == 8 for c in cards)
            has_ten = any(c.rank == 4 for c in cards)
            for c in cards:
                if c.rank == 8:  # Ace
                    if is_trump:
                        tricks += 0.88  # ace of trump: near-guaranteed trick as whister
                    elif len(cards) >= 3 and has_ten:
                        tricks += 0.95  # A+10 with length: 10 promotes after ace
                    elif len(cards) >= 2:
                        tricks += 0.90  # guarded ace — very reliable
                    else:
                        tricks += 0.78  # singleton ace — more reliable than 0.75
                elif c.rank == 7:  # King
                    if is_trump:
                        tricks += 0.20 if len(cards) >= 2 else 0.05
                    elif has_ace:
                        # A-K in same suit: king is very reliable after ace cashes
                        tricks += 0.65
                    elif len(cards) >= 3:
                        # Unsupported king in 3-card suit — reduced from 0.50
                        # G3 iter12: 2 unsupported kings at 0.50 each inflated
                        # est to 2.1, triggered 80% whist rate, lost -72.
                        tricks += 0.40
                        unsupported_kings += 1
                    elif len(cards) >= 2:
                        tricks += 0.20  # reduced from 0.30
                        unsupported_kings += 1
                    else:
                        tricks += 0.05  # singleton king easily trumped
                        unsupported_kings += 1
                elif c.rank == 6 and len(cards) >= 3:  # Queen with length
                    if not is_trump:
                        if not has_ace:
                            unsupported_queens += 1
                        tricks += 0.15  # reduced from 0.2
                elif c.rank == 5 and len(cards) >= 4:  # Jack with 4+ length
                    if not is_trump:
                        tricks += 0.1

        # Penalty for multiple unsupported kings — they can't all convert.
        # Declarer only needs to hold aces in 1-2 suits to neutralize multiple kings.
        # G8 iter15: 3 unsupported kings inflated est → lost -36. Scale penalty.
        if unsupported_kings >= 3:
            tricks -= 0.40
        elif unsupported_kings >= 2:
            tricks -= 0.25
        # Penalty for multiple unsupported queens — scattered queens without
        # aces contribute very little as whister. G9 iter14: Carol's scattered
        # D's (queens) inflated hand strength estimate.
        # G10 iter17: 2A + 3 queens (1 unsupported), est ~2.2, called twice,
        # lost -100. Queens inflate est but can't beat K/A as whister.
        # Increased penalty: 3+ unsupported queens → -0.30, 2+ → -0.20.
        if unsupported_queens >= 3:
            tricks -= 0.30
        elif unsupported_queens >= 2:
            tricks -= 0.20
        # Penalty for scattered jacks — jacks in multiple suits without aces
        # contribute almost nothing as whister. G6+G8 iter22: Carol had 2A +
        # scattered jacks (3 jacks across different suits), both lost -36.
        # Jacks inflate est via length/queen bonuses but can't convert tricks.
        scattered_jacks = 0
        for suit, cards in groups.items():
            is_trump = (suit == declarer_trump) if declarer_trump else False
            if is_trump:
                continue
            has_ace = any(c.rank == 8 for c in cards)
            has_jack = any(c.rank == 5 for c in cards)
            if has_jack and not has_ace:
                scattered_jacks += 1
        if scattered_jacks >= 3:
            tricks -= 0.15

        # Bonus for A-K in same suit: ace cashes guaranteed,
        # king promoted next trick. ~1.5 reliable tricks from one suit.
        # G3 iter13: Carol had AK spades but passed whist — missed income.
        # G16 iter8: AK in declarer's trump is very strong defensive holding
        # (controls trump suit) — bonus applies to trump too (iter30 NEW).
        for suit, cards in groups.items():
            is_trump = (suit == declarer_trump) if declarer_trump else False
            has_ace = any(c.rank == 8 for c in cards)
            has_king = any(c.rank == 7 for c in cards)
            if has_ace and has_king:
                if is_trump:
                    tricks += 0.15  # AK in trump: strong defensive control
                else:
                    tricks += 0.20  # Extra bonus on top of individual A/K values

        # Long non-trump suit penalty: 5+ cards in one non-trump suit is dead
        # weight — declarer ruffs them easily. Only the top 1-2 cards matter.
        for suit, cards in groups.items():
            is_trump = (suit == declarer_trump) if declarer_trump else False
            if not is_trump and len(cards) >= 5:
                tricks -= 0.25

        # Void-suit bonus: void in a non-trump suit = ruffing potential as whister.
        # Bob/Alice already have this. Pushes hands with voids above rate thresholds.
        if declarer_trump:
            for s_val in [1, 2, 3, 4]:
                if s_val != declarer_trump and s_val not in groups:
                    tricks += 0.25
                    break  # only one void bonus

        # Penalize hands with void suits or many short suits — as whister,
        # we DON'T have trump control; declarer ruffs our winners in short suits.
        # G3 iter6: void in 4th suit inflated estimate, lost -36.
        suits_held = len(groups)
        if suits_held <= 2:
            tricks -= 0.5  # Very concentrated — declarer ruffs other suits
        elif suits_held <= 3:
            # Check for singletons without aces (easy for declarer to ruff)
            weak_shorts = sum(
                1 for s, cards in groups.items()
                if len(cards) <= 1 and not any(c.rank == 8 for c in cards)
            )
            if weak_shorts >= 2:
                tricks -= 0.3
            elif weak_shorts >= 1:
                tricks -= 0.2
        return max(tricks, 0.0)

    # ------------------------------------------------------------------
    # Bidding — hand-strength aware, pragmatic risk assessment
    # ------------------------------------------------------------------

    def bid_intent(self, hand, legal_bids):
        bid_types = {b["bid_type"] for b in legal_bids}

        if bid_types == {"pass"}:
            return {"bid": legal_bids[0], "intent": "forced pass (no other options)"}

        # Reset state at start of each round to prevent stale values
        self._cards_played = 0
        self._is_declarer = False
        self._trump_suit = None
        self._trump_suit_val = None
        self._betl_intent = False
        self._whist_call_count = 0
        self._trump_leads = 0

        # Track auction escalation for whisting decisions later.
        game_bids = [b for b in legal_bids if b["bid_type"] == "game"]
        if game_bids:
            min_val = min(b.get("value", 2) for b in game_bids)
            self._highest_bid_seen = max(self._highest_bid_seen, min_val - 1)

        if any(b["bid_type"] in ("sans", "betl", "in_hand") for b in legal_bids):
            self._highest_bid_seen = max(self._highest_bid_seen, 5)

        aces = self._count_aces(hand) if hand else 0
        high = self._count_high(hand) if hand else 0
        est_tricks = self._hand_strength(hand) if hand else 0.0

        # Bid game 2 based on hand strength (pragmatic: bid when odds favor us)
        if game_bids:
            game_val = game_bids[0].get("value", 2)
            if game_val <= 2:
                # 3+ aces: always bid regardless of shape. G9 iter9: Carol had
                # 3 aces + 2 kings (3+3+2+2 flat) but passed due to 40% flat-shape
                # check. 3 aces = 3 guaranteed tricks + talon = almost certain win.
                if aces >= 3:
                    return {"bid": game_bids[0],
                            "intent": f"game 2 — 3+ aces always bid (aces={aces}, tricks={est_tricks:.1f})"}
                # 2 aces: bid if suit concentration is decent.
                # Flat 2-ace hands (max suit < 4) at 50% (up from 40%, since
                # 2 aces + talon is usually enough even with flat shape).
                # G3 iter19: 2A + 6-card suit but J-high (no K in trump) +
                # singleton jacks lost -80. Require king or ace in best suit
                # for auto-bid; otherwise fall through to rate-based.
                if aces == 2:
                    groups = self._suit_groups(hand) if hand else {}
                    max_suit_len = max((len(cards) for cards in groups.values()), default=0)
                    if max_suit_len >= 4:
                        best_s = self._best_trump(hand)
                        best_cards = groups.get(best_s, [])
                        has_top = any(c.rank >= 7 for c in best_cards)  # K or A in trump
                        if has_top:
                            return {"bid": game_bids[0],
                                    "intent": f"game 2 — 2 aces + concentrated + top trump (len={max_suit_len}, tricks={est_tricks:.1f})"}
                        # No top card in trump: fall through to est-based or 50% rate
                        if est_tricks >= 3.5:
                            return {"bid": game_bids[0],
                                    "intent": f"game 2 — 2 aces + concentrated, no top but strong est (len={max_suit_len}, tricks={est_tricks:.1f})"}
                    # Flat 2-ace but strong est (3.0+): always bid.
                    # G7 iter11: AK7+AJ7+KJ+D7 = 2A+2K, flat 3+3+2+2,
                    # est ~3.5. Passed at 50% rate, missed +80. With est >= 3.0,
                    # 2 aces + strong support = reliable 6 tricks post-talon.
                    if est_tricks >= 3.0:
                        return {"bid": game_bids[0],
                                "intent": f"game 2 — 2 aces flat but strong est always bid (len={max_suit_len}, tricks={est_tricks:.1f})"}
                    # G1 iter37: 2A flat est ~1.8, bid at 68%, declared → -83.
                    # Flat 2A with est < 2.0 is too risky (need 6 tricks, talon adds ~2).
                    # Gate flat rate on est to prevent weak flat declarations.
                    if est_tricks >= 2.5:
                        flat_rate = 0.68
                    elif est_tricks >= 2.0:
                        flat_rate = 0.50
                    else:
                        flat_rate = 0.30
                    if self.rng.random() < flat_rate:
                        return {"bid": game_bids[0],
                                "intent": f"game 2 — 2 aces but flat shape {int(flat_rate*100)}% (len={max_suit_len}, tricks={est_tricks:.1f})"}
                    intent = f"pass — 2 aces but flat shape rolled >{int(flat_rate*100)}% (len={max_suit_len}, tricks={est_tricks:.1f})"
                    pass_bid = next((b for b in legal_bids if b["bid_type"] == "pass"), None)
                    if pass_bid:
                        return {"bid": pass_bid, "intent": intent}
                # Strong hands: always bid (3.0+ estimated tricks)
                if est_tricks >= 3.0:
                    return {"bid": game_bids[0],
                            "intent": f"game 2 — strong hand (tricks={est_tricks:.1f}, aces={aces}, high={high})"}
                # 1 ace + 5-card suit: strong declaring shape. G2 iter13:
                # Carol had [[A,D,10,9,7],[K,10,9],[K],[8]] — 5 spades with
                # ace, est ~2.9 but only 45% marginal rate → missed all-pass.
                # 5-card ace suit + talon = reliable 6 tricks.
                groups = self._suit_groups(hand) if hand else {}
                max_suit_len = max((len(cards) for cards in groups.values()), default=0)
                num_suits = len(groups)
                if aces >= 1 and max_suit_len >= 5:
                    return {"bid": game_bids[0],
                            "intent": f"game 2 — ace + 5-card suit (tricks={est_tricks:.1f}, longest={max_suit_len})"}
                # 1 ace + 4-card suit + void: void = guaranteed ruffing.
                # G1 iter19: 1A + void + A-Q-10-9 trump (no King) lost -100.
                # Queens without king in trump are unreliable. Require king in
                # best suit OR est >= 2.5 for auto-bid; else 50% rate.
                if aces >= 1 and max_suit_len >= 4 and num_suits <= 3:
                    best_s = self._best_trump(hand)
                    best_cards = groups.get(best_s, [])
                    has_king_in_trump = any(c.rank == 7 for c in best_cards)
                    if has_king_in_trump or est_tricks >= 2.5:
                        return {"bid": game_bids[0],
                                "intent": f"game 2 — ace + 4-card suit + void + solid trump (tricks={est_tricks:.1f}, suits={num_suits})"}
                    if self.rng.random() < 0.50:
                        return {"bid": game_bids[0],
                                "intent": f"game 2 — ace + 4-card suit + void, no K in trump 50% (tricks={est_tricks:.1f}, suits={num_suits})"}
                # Marginal hands (2.0-3.0 tricks): require 1+ ace.
                # G15 iter3: [[D,J,8,7],[A,10],[K,J],[10,7]] — 1A + Q-J-8-7
                # trump (no K/A in trump), est ~2.35, 50% rate fired → lost -120.
                # Queen-high trump without K/A is unreliable. Require top card
                # in trump for higher rate; without it, reduce to 35%.
                if est_tricks >= 2.0 and aces >= 1:
                    groups_m = self._suit_groups(hand) if hand else {}
                    max_len = max((len(cards) for cards in groups_m.values()), default=0)
                    best_s_m = self._best_trump(hand)
                    best_cards_m = groups_m.get(best_s_m, [])
                    has_top_m = any(c.rank >= 7 for c in best_cards_m)  # K or A in trump
                    if max_len >= 4 and est_tricks >= 2.5 and has_top_m:
                        m_rate = 0.65
                    elif has_top_m:
                        m_rate = 0.50
                    else:
                        m_rate = 0.35  # No top card in trump — risky
                    if self.rng.random() < m_rate:
                        return {"bid": game_bids[0],
                                "intent": f"game 2 — marginal (tricks={est_tricks:.1f}, aces={aces}, longest={max_len}, rate={int(m_rate*100)}%)"}
                    intent = f"pass — marginal hand rolled >{int(m_rate*100)}% (tricks={est_tricks:.1f}, aces={aces})"
                # 1 ace with high-card density: 25% speculative
                # Bumped from 20% — high-card density hands with talon
                # frequently reach 6 tricks.
                elif aces >= 1 and high >= 4:
                    if self.rng.random() < 0.38:
                        return {"bid": game_bids[0],
                                "intent": f"game 2 — 1 ace + dense high cards (tricks={est_tricks:.1f}, high={high})"}
                    intent = f"pass — 1 ace dense but rolled >38% (tricks={est_tricks:.1f}, high={high})"
                # 0-ace speculative bids removed — they are net negative
                else:
                    intent = f"pass — weak hand (tricks={est_tricks:.1f}, aces={aces}, high={high})"
            elif game_val == 3:
                # Game 3: only with strong hands (4.0+ tricks or 3+ aces)
                if est_tricks >= 4.0 or aces >= 3:
                    return {"bid": game_bids[0],
                            "intent": f"game 3 — strong hand (tricks={est_tricks:.1f}, aces={aces})"}
                # G19 iter4: bidding war at game 3 cost -72 despite strong hand.
                # But G5/G9 iter5: 2A hands lost bidding wars at game 2 → passive.
                # 2A + est >= 3.0 at game 3 is safer than 1A. Tiered: 2A→28%, 1A→18%.
                game3_rate = 0.28 if aces >= 2 else 0.18
                if est_tricks >= 3.0 and aces >= 1 and self.rng.random() < game3_rate:
                    return {"bid": game_bids[0],
                            "intent": f"game 3 — calculated gamble (tricks={est_tricks:.1f}, aces={aces})"}
                intent = f"pass — too weak for game 3 (tricks={est_tricks:.1f}, aces={aces})"
            else:
                intent = f"pass — game {game_val}+ too risky (tricks={est_tricks:.1f}, aces={aces})"
            pass_bid = next((b for b in legal_bids if b["bid_type"] == "pass"), None)
            if pass_bid:
                return {"bid": pass_bid, "intent": intent}

        # Check for betl/in_hand betl — auction betl = in-hand (no exchange)
        if hand:
            betl_bids = [b for b in legal_bids if b["bid_type"] == "betl"]
            if betl_bids and self._is_good_betl_hand_in_hand(hand):
                a = betl_hand_analysis(hand)
                return {"bid": betl_bids[0],
                        "intent": f"betl — pragmatic zero danger (safe_suits={a['safe_suits']}, voids={a['void_count']})"}
            in_hand_bids = [b for b in legal_bids if b["bid_type"] == "in_hand"]
            if in_hand_bids and self._is_good_betl_hand_in_hand(hand):
                self._betl_intent = True
                a = betl_hand_analysis(hand)
                return {"bid": in_hand_bids[0],
                        "intent": f"in_hand (betl intent) — zero danger, voids={a['void_count']}"}

        pass_bid = next((b for b in legal_bids if b["bid_type"] == "pass"), None)
        if pass_bid:
            return {"bid": pass_bid,
                    "intent": f"pass — no suitable bids (aces={aces}, high={high})"}

        # Fallback
        return super().bid_intent(hand, legal_bids)

    # ------------------------------------------------------------------
    # Exchange — keep strongest suit + aces, void short suits
    # ------------------------------------------------------------------

    def discard_decision(self, hand_card_ids, talon_card_ids):
        """Keep trump-suit cards and aces; discard weakest. Try to create voids.
        If betl looks promising, discard highest/most dangerous cards instead."""
        all_ids = hand_card_ids + talon_card_ids
        rank_order = {"7": 1, "8": 2, "9": 3, "10": 4, "J": 5, "Q": 6, "K": 7, "A": 8}

        def card_rank(cid):
            return rank_order.get(cid.split("_")[0], 0)

        def card_suit(cid):
            return cid.split("_")[1]

        # Try betl-optimized discard first
        betl_discard = self._try_betl_discard(all_ids, card_rank, card_suit)
        if betl_discard:
            return betl_discard

        suit_counts = {}
        suit_cards = {}
        for cid in all_ids:
            s = card_suit(cid)
            suit_counts[s] = suit_counts.get(s, 0) + 1
            suit_cards.setdefault(s, []).append(cid)

        best_suit = max(suit_counts, key=suit_counts.get)

        # Void suits without aces — check 2-card suits and singleton pairs
        voidable_pairs = []
        for s in sorted(suit_cards.keys()):
            if s == best_suit:
                continue
            cards = suit_cards[s]
            if len(cards) == 2 and all(card_rank(c) < 8 for c in cards):
                total_rank = sum(card_rank(c) for c in cards)
                voidable_pairs.append((total_rank, cards))
        if voidable_pairs:
            voidable_pairs.sort()
            return {"discard": voidable_pairs[0][1],
                    "intent": f"void weakest off-suit (trump={best_suit})"}

        # Two singleton off-suits below King — discard both to create 2 voids
        singletons = []
        for s in sorted(suit_cards.keys()):
            if s == best_suit:
                continue
            cards = suit_cards[s]
            if len(cards) == 1 and card_rank(cards[0]) < 7:  # below King
                singletons.append((card_rank(cards[0]), cards[0]))
        if len(singletons) >= 2:
            singletons.sort()
            return {"discard": [singletons[0][1], singletons[1][1]],
                    "intent": f"void two singleton off-suits (trump={best_suit})"}

        def keep_score(cid):
            score = card_rank(cid) * 10
            s = card_suit(cid)
            if s == best_suit:
                score += 100
            if card_rank(cid) == 8:
                score += 50
            if s != best_suit and suit_counts[s] <= 2:
                score -= 40
            return score

        sorted_cards = sorted(all_ids, key=keep_score)
        return {"discard": sorted_cards[:2],
                "intent": f"discard weakest cards (trump={best_suit})"}

    _try_betl_discard = PlayerAlice._try_betl_discard

    def choose_discard(self, hand_card_ids, talon_card_ids):
        self._is_declarer = True
        winner_bid = getattr(self, '_winner_bid', None)
        if winner_bid:
            result = self._evaluate_12_card_contracts(hand_card_ids, talon_card_ids, winner_bid)
            self._pre_chosen_contract = result["contract"]
            return result["discard"]
        decision = self.discard_decision(hand_card_ids, talon_card_ids)
        return decision["discard"]

    # ------------------------------------------------------------------
    # 12-card evaluation — unified discard + contract selection
    # ------------------------------------------------------------------

    def _hand_strength_for_suit(self, hand, trump_suit):
        """Estimate tricks with a specific trump suit (pragmatic coefficients)."""
        groups = self._suit_groups(hand)
        tricks = 0.0
        trump_cards = groups.get(trump_suit, [])
        has_trump_ace = any(c.rank == 8 for c in trump_cards)
        has_trump_king = any(c.rank == 7 for c in trump_cards)

        for c in trump_cards:
            if c.rank == 8:
                tricks += 1.0
            elif c.rank == 7:
                if has_trump_ace:
                    tricks += 0.95  # A draws opponents, K nearly guaranteed
                else:
                    tricks += 0.7 if len(trump_cards) >= 3 else 0.4
            elif c.rank >= 5:  # J/Q
                if len(trump_cards) >= 4 and has_trump_ace and has_trump_king:
                    tricks += 0.70  # AK draw opponents' honors first
                elif len(trump_cards) >= 4:
                    tricks += 0.4
            elif len(trump_cards) >= 5:
                tricks += 0.3

        # 4th+ trump with Ace control
        if has_trump_ace and len(trump_cards) >= 4:
            tricks += 0.45

        if len(trump_cards) >= 5:
            tricks += (len(trump_cards) - 4) * 0.6
        elif len(trump_cards) >= 4:
            tricks += 0.3

        for suit, cards in groups.items():
            if suit == trump_suit:
                continue
            has_ace = any(c.rank == 8 for c in cards)
            has_king = any(c.rank == 7 for c in cards)
            for c in cards:
                if c.rank == 8:
                    tricks += 0.9
                elif c.rank == 7:
                    if has_ace:
                        tricks += 0.90  # A cashes first, K is master
                    elif len(cards) >= 2:
                        tricks += 0.75  # Guarded K, declarer controls tempo

            # Side-suit length bonus: 8 cards per suit total; with 4 cards
            # opponents hold 4, after ~3 rounds one defender is out → 4th card
            # can win via ruff or becomes master. Even without ace, K-headed
            # 4-card suits generate length winners.
            if len(cards) >= 4:
                if has_ace:
                    tricks += (len(cards) - 3) * 0.5
                elif has_king:
                    tricks += (len(cards) - 3) * 0.35
                else:
                    tricks += (len(cards) - 3) * 0.2

        num_suits = len(groups)
        if num_suits <= 2 and len(trump_cards) >= 4:
            tricks += 1.2
        elif num_suits <= 3 and len(trump_cards) >= 3:
            tricks += 0.4

        return tricks

    def _score_hand_for_contract(self, hand, contract_type, trump_suit=None):
        """Score a 10-card hand for a specific contract."""
        if contract_type == "betl":
            a = betl_hand_analysis(hand)
            # CRITICAL: aces are guaranteed trick-winners in betl = instant loss.
            if a["has_ace"]:
                return -200
            # Kings are nearly as bad — they win tricks in betl most of the time.
            # G15 iter2: 12-card eval discarded ace but king remained, -120.
            king_count = sum(1 for c in hand if c.rank == 7)
            if king_count >= 1:
                return -100
            # 3+ high cards (Q/K/A) make betl very risky
            if a["high_card_count"] >= 3:
                return -150
            # Pragmatic betl: zero danger good, low cards + void good
            score = 100 - a["danger_count"] * 45
            score += a["safe_suits"] * 5
            score += a["void_count"] * 8
            score -= a["max_rank"] * 3
            if a["max_rank"] <= 5 and a["void_count"] >= 1:
                score += 15
            return score
        elif contract_type == "sans":
            # Sans: only for monster hands (3+ aces + many high cards)
            aces = sum(1 for c in hand if c.rank == 8)
            high = sum(1 for c in hand if c.rank >= 6)
            if aces >= 3 and high >= 6:
                return 150  # Very strong sans hand
            return -200  # Not strong enough
        else:
            strength = self._hand_strength_for_suit(hand, trump_suit)
            groups = self._suit_groups(hand)
            trump_len = len(groups.get(trump_suit, []))
            cost_penalty = (_SUIT_BID_VALUE.get(trump_suit, 2) - 2) * 2
            # Boosted multiplier (18 vs 15) and trump length bonus (5 vs 3 for 5+)
            # G15 iter2: 6-card AKQ suit scored lower than betl due to low multiplier
            trump_bonus = 5 if trump_len >= 5 else 3
            return strength * 18 + trump_len * trump_bonus - cost_penalty

    def _evaluate_12_card_contracts(self, hand_card_ids, talon_card_ids, winner_bid):
        """Evaluate all 66 discard combos × all legal contracts.
        Also stores self._ranked_discards: list of (discard, contract, score) sorted best-first."""
        from itertools import combinations
        import heapq

        all_ids = hand_card_ids + talon_card_ids
        min_bid = winner_bid.effective_value if winner_bid else 0

        pool_aces = sum(1 for cid in all_ids if cid.startswith("A_"))
        pool_kings = sum(1 for cid in all_ids if cid.startswith("K_"))
        pool_high = pool_aces + pool_kings
        skip_betl = pool_aces >= 1 or pool_high >= 3

        top_n = []
        TOP_K = 10

        def _push(score, discard, contract):
            entry = (score, discard, contract)
            if len(top_n) < TOP_K:
                heapq.heappush(top_n, entry)
            elif score > top_n[0][0]:
                heapq.heapreplace(top_n, entry)

        for pair in combinations(range(len(all_ids)), 2):
            discard = [all_ids[i] for i in pair]
            remaining_ids = [cid for cid in all_ids if cid not in discard]
            hand = _ids_to_cards(remaining_ids)

            if not skip_betl:
                betl_sc = self._score_hand_for_contract(hand, "betl")
                _push(betl_sc, discard, ("betl", None, 6))

            sans_sc = self._score_hand_for_contract(hand, "sans")
            _push(sans_sc, discard, ("sans", None, 7))

            for suit, suit_level in _SUIT_BID_VALUE.items():
                if suit_level < min_bid:
                    continue
                level = max(suit_level, min_bid)
                sc = self._score_hand_for_contract(hand, "suit", trump_suit=suit)
                _push(sc, discard, ("suit", SUIT_NAMES[suit], level))

        ranked = sorted(top_n, key=lambda x: -x[0])
        self._ranked_discards = [(d, c, s) for s, d, c in ranked]

        best = ranked[0] if ranked else None
        if best:
            return {"discard": best[1], "contract": best[2]}
        return {"discard": None, "contract": None}

    # ------------------------------------------------------------------
    # Contract — use pre-evaluated choice or fall back to heuristic
    # ------------------------------------------------------------------

    def bid_decision(self, hand, legal_levels, winner_bid):
        """Pick contract. Prefer suit, but allow betl when intent is set."""
        # Use pre-evaluated contract from 12-card analysis if available
        pre = getattr(self, '_pre_chosen_contract', None)
        if pre:
            self._pre_chosen_contract = None
            ctype, trump, level = pre
            if ctype == "suit" and trump:
                self._trump_suit = trump
                self._trump_suit_val = {v: k for k, v in SUIT_NAMES.items()}.get(trump)
            return {"contract_type": ctype, "trump": trump, "level": level,
                    "intent": f"{ctype} — 12-card evaluation"}

        # In-hand betl: if we bid in_hand with betl intent, choose betl
        if self._betl_intent and 6 in legal_levels:
            return {"contract_type": "betl", "trump": None, "level": 6,
                    "intent": "betl — in-hand betl intent"}
        # Sans for monster hands (fallback when 12-card eval not used)
        if 7 in legal_levels and hand:
            aces = self._count_aces(hand)
            high = self._count_high(hand)
            if aces >= 3 and high >= 6:
                return {"contract_type": "sans", "trump": None, "level": 7,
                        "intent": f"sans — monster hand (aces={aces}, high={high})"}
        # Post-exchange betl (pragmatic): zero danger + no kings/aces
        if 6 in legal_levels:
            a = betl_hand_analysis(hand)
            has_king = any(c.rank == 7 for c in hand) if hand else False
            # Zero danger and no kings — always take it
            if a["danger_count"] == 0 and not has_king and not a["has_ace"]:
                return {"contract_type": "betl", "trump": None, "level": 6,
                        "intent": f"betl — zero danger post-exchange (safe_suits={a['safe_suits']})"}
            # All low cards: max rank ≤ Jack (5), no aces/kings + at least 1 void
            if not a["has_ace"] and not has_king and a["max_rank"] <= 5 and a["void_count"] >= 1:
                return {"contract_type": "betl", "trump": None, "level": 6,
                        "intent": f"betl — low cards + void (max_rank={a['max_rank']}, voids={a['void_count']})"}

        min_bid = winner_bid.effective_value if winner_bid else 0
        suit_bid = {Suit.SPADES: 2, Suit.DIAMONDS: 3, Suit.HEARTS: 4, Suit.CLUBS: 5}
        groups = self._suit_groups(hand)

        # Pick strongest valid suit, with cost penalty for expensive suits
        best_suit = None
        best_score = -1
        for suit, cards in groups.items():
            if suit_bid.get(suit, 0) < min_bid:
                continue
            score = len(cards) * 100 + sum(c.rank for c in cards)
            # Penalize expensive suits: lower game_value = smaller loss if failed
            cost_penalty = (suit_bid[suit] - 2) * 10
            score -= cost_penalty
            if score > best_score:
                best_score = score
                best_suit = suit
        if best_suit is None:
            best_suit = min((s for s, v in suit_bid.items() if v >= min_bid), key=lambda s: suit_bid[s])
        trump_name = SUIT_NAMES[best_suit] if best_suit else "spades"
        self._trump_suit = trump_name
        self._trump_suit_val = best_suit

        suit_levels = [l for l in legal_levels if l not in (6, 7)]
        if suit_levels:
            level = min(suit_levels)
        elif legal_levels:
            level = min(legal_levels)
        else:
            level = 2
        return {"contract_type": "suit", "trump": trump_name, "level": level,
                "intent": f"suit {trump_name} level {level} — never sans/betl"}

    def choose_contract(self, legal_levels, hand, winner_bid):
        decision = self.bid_decision(hand, legal_levels, winner_bid)
        return decision["contract_type"], decision["trump"], decision["level"]

    # ------------------------------------------------------------------
    # Whisting — hand-strength-aware, pragmatic risk assessment
    # ------------------------------------------------------------------

    def decide_to_call(self, hand, contract_type, trump_suit, legal_actions):
        """Decide whether to call when the other defender passed — PRAGMATIC.

        Moderate bar: willing to call with good hands but not recklessly.
        """
        # Store declarer's trump suit for card play ruffing
        if not self._is_declarer and trump_suit is not None:
            self._trump_suit_val = trump_suit
        aces = self._count_aces(hand) if hand else 0
        declarer_trump = trump_suit if trump_suit else None
        est = self._estimate_whist_tricks(hand, declarer_trump) if hand else 0.0

        # Sans: call only with exceptional hands
        if contract_type == "sans":
            if aces >= 4:
                return {"action": "call",
                        "intent": f"call sans — {aces} aces ({est:.1f} tricks)"}
            if aces >= 3 and est >= 3.5:
                rate = 0.45
                if self.rng.random() < rate:
                    return {"action": "call",
                            "intent": f"call sans — {aces}A {int(rate*100)}% ({est:.1f} tricks)"}
            return self.following_decision(hand, contract_type, trump_suit, legal_actions)

        # 1A/0A solo protection: calling alone needs ~4 tricks. est < 2.5
        # is hopeless solo. G7 iter2: 1A est 1.4, solo whisted → -100.
        if aces <= 1 and est < 2.5:
            action_types_solo = [a["action"] for a in legal_actions]
            if "pass" in action_types_solo:
                return {"action": "pass",
                        "intent": f"pass — solo gate: {aces}A est {est:.1f} < 2.5"}

        # Suit: moderate bar
        if aces >= 3 and est >= 3.5:
            rate = 0.65
            if self.rng.random() < rate:
                return {"action": "call",
                        "intent": f"call — {aces}A + {est:.1f} tricks {int(rate*100)}%"}
        if aces >= 2 and est >= 4.0:
            # Check for AK combo — concentrated strength justifies call
            has_ak = False
            if hand:
                groups = self._suit_groups(hand)
                for suit, cards in groups.items():
                    if declarer_trump is not None and suit == declarer_trump:
                        continue
                    has_a = any(c.rank == 8 for c in cards)
                    has_k = any(c.rank == 7 for c in cards)
                    if has_a and has_k:
                        has_ak = True
                        break
            rate = 0.50 if has_ak else 0.30
            if self.rng.random() < rate:
                return {"action": "call",
                        "intent": f"call — {aces}A {int(rate*100)}% ({est:.1f} tricks{', AK' if has_ak else ''})"}

        # Not strong enough for explicit call criteria — pass.
        # Calling alone requires ~4 tricks; following_decision uses ~2-trick thresholds.
        # G4 iter1: falling through to following_decision caused -60 loss with 1A.
        action_types_call = [a["action"] for a in legal_actions]
        if "pass" in action_types_call:
            return {"action": "pass",
                    "intent": f"pass — below call threshold ({aces}A, {est:.1f} tricks)"}
        return self.following_decision(hand, contract_type, trump_suit, legal_actions)

    def decide_to_counter(self, hand, contract_type, trump_suit, legal_actions):
        """Decide whether to counter (double stakes) — PRAGMATIC.

        Rare: only with very strong hands. Pragmatic means no needless risks.
        """
        action_types = [a["action"] for a in legal_actions]
        aces = self._count_aces(hand) if hand else 0
        declarer_trump = trump_suit if trump_suit else None
        est = self._estimate_whist_tricks(hand, declarer_trump) if hand else 0.0

        # Declarer responding to a counter
        if "double_counter" in action_types:
            if est >= 4.5 and aces >= 2:
                return {"action": "double_counter",
                        "intent": f"double counter — strong declarer ({aces}A, {est:.1f} tricks)"}
            return {"action": "start_game",
                    "intent": f"start game — accept counter ({aces}A, {est:.1f} tricks)"}

        # Defender: counter only with overwhelming strength
        if aces >= 3 and est >= 4.5:
            rate = 0.40
            if self.rng.random() < rate:
                return {"action": "counter",
                        "intent": f"counter — {aces}A + {est:.1f} tricks {int(rate*100)}%"}
        if aces >= 4:
            return {"action": "counter",
                    "intent": f"counter — {aces} aces ({est:.1f} tricks)"}

        # Not strong enough to counter.
        # When "call" is available (other defender passed), choose based on
        # hand strength. "call" = open play (more info but higher penalty
        # on failure). "start_game" = closed play (less penalty on failure).
        # G39 iter3: 1A est ~1.6, unconditionally called → -100 solo disaster.
        # G47 iter3: 1A est ~1.75, called → -42. Weak hands must NOT call.
        if "call" in action_types:
            # Strong hands: call (open play) for trick advantage
            if aces >= 2 and est >= 3.0:
                return {"action": "call",
                        "intent": f"call — strong open play ({aces}A, {est:.1f} tricks)"}
            # Weak hands: start_game (closed play, less penalty on failure)
            return {"action": "start_game",
                    "intent": f"start game — closed play, safer ({aces}A, {est:.1f} tricks)"}
        if "start_game" in action_types:
            return {"action": "start_game",
                    "intent": f"start game — pragmatic ({aces}A, {est:.1f} tricks)"}
        return self.following_decision(hand, contract_type, trump_suit, legal_actions)

    def following_decision(self, hand, contract_type, trump_suit, legal_actions):
        """Hand-strength-aware whisting — PRAGMATIC style.

        Key insight: we HAVE access to our hand. Use ace count + trick estimate.
        Scoring math for single whister (game level 2, game_value=4):
          2 tricks -> +8, 3 tricks -> +12, 4 tricks -> +16
          1 trick  -> -36, 0 tricks -> -40
          pass     ->  0 (but declarer gets +40/+80 for free)

        Iter 37: Fixed critical ruffing bug — _trump_suit_val now set for
        defenders. Reduced 2A rates to account for ~40% solo risk.
        """
        # Set declarer's trump suit for card play ruffing (CRITICAL fix iter37)
        if not self._is_declarer and trump_suit is not None:
            self._trump_suit_val = trump_suit

        action_types = [a["action"] for a in legal_actions]

        if "start_game" in action_types:
            return {"action": "start_game", "intent": "start game"}

        if "follow" in action_types:
            # Base heuristic (any player)
            should, n_tt, s_reasons = self._should_follow_heuristic(hand, trump_suit)
            if should:
                self._whist_call_count += 1
                return {"action": "follow",
                        "intent": f"follow — heuristic: {n_tt} trump tricks, {s_reasons:.1f} reasons"}

            aces = self._count_aces(hand) if hand else 0
            declarer_trump = trump_suit if trump_suit else None
            est_tricks = self._estimate_whist_tricks(hand, declarer_trump) if hand else 0.0
            is_high_level = self._highest_bid_seen >= 3

            # Hard pass gate: 4+ cards in declarer's trump suit = dead weight.
            if declarer_trump and hand:
                groups = self._suit_groups(hand)
                trump_count = len(groups.get(declarer_trump, []))
                if trump_count >= 4:
                    return {"action": "pass",
                            "intent": f"pass — hard gate: {trump_count} cards in declarer's trump"}

            # Hard pass gate: 3+ unsupported kings → always pass.
            if hand:
                groups_k = self._suit_groups(hand)
                unsup_k = 0
                for suit, cards in groups_k.items():
                    has_a = any(c.rank == 8 for c in cards)
                    has_k = any(c.rank == 7 for c in cards)
                    if has_k and not has_a:
                        unsup_k += 1
                if unsup_k >= 3:
                    return {"action": "pass",
                            "intent": f"pass — hard gate: {unsup_k} unsupported kings"}

            # Hard pass gate: 3+ scattered jacks without aces → always pass.
            if hand and declarer_trump:
                groups_j = self._suit_groups(hand)
                scattered_j = 0
                for suit, cards in groups_j.items():
                    if suit == declarer_trump:
                        continue
                    has_a = any(c.rank == 8 for c in cards)
                    has_j = any(c.rank == 5 for c in cards)
                    if has_j and not has_a:
                        scattered_j += 1
                if scattered_j >= 3:
                    return {"action": "pass",
                            "intent": f"pass — hard gate: {scattered_j} scattered jacks without aces"}

            # Hard pass gate: singleton ace + 2+ unsupported kings → always pass.
            if hand and aces == 1:
                groups_sak = self._suit_groups(hand)
                has_singleton_ace = False
                unsup_k_sak = 0
                for suit, cards in groups_sak.items():
                    if len(cards) == 1 and cards[0].rank == 8:
                        has_singleton_ace = True
                    has_a = any(c.rank == 8 for c in cards)
                    has_k = any(c.rank == 7 for c in cards)
                    if has_k and not has_a:
                        unsup_k_sak += 1
                if has_singleton_ace and unsup_k_sak >= 2:
                    return {"action": "pass",
                            "intent": f"pass — hard gate: singleton ace + {unsup_k_sak} unsupported kings"}

            # Sans gate: sans declarers have 3-4 aces, dominate all suits.
            if contract_type == "sans":
                if aces >= 3:
                    rate = 0.65
                elif aces >= 2 and est_tricks >= 2.0:
                    rate = 0.40
                else:
                    return {"action": "pass",
                            "intent": f"pass — sans gate: {aces}A ({est_tricks:.1f} tricks)"}
                if self._whist_call_count > 0:
                    rate *= 0.5
                self._whist_call_count += 1
                if self.rng.random() < rate:
                    return {"action": "follow",
                            "intent": f"follow — sans {aces}A {int(rate*100)}% ({est_tricks:.1f} tricks)"}
                return {"action": "pass",
                        "intent": f"pass — sans {aces}A rolled >{int(rate*100)}% ({est_tricks:.1f} tricks)"}

            # Repeat-call protection: on 2nd+ whist entry, only whist with strong hands.
            # G4 iter1: Carol called twice with 1A est ~1.0 → -60.
            # G15 iter34: 2A est 3.0, repeat-call gate allowed (est >= 2.0),
            # solo whisted Alice's monster → -93. Solo needs ~4 tricks.
            # Raised threshold: est < 3.5 (was 2.0) to block solo whisting
            # with moderate hands. Solo calling is far riskier than paired.
            if self._whist_call_count > 0:
                if aces < 2 or est_tricks < 3.5:
                    return {"action": "pass",
                            "intent": f"pass — repeat call gate ({aces}A, {est_tricks:.1f} tricks)"}
            self._whist_call_count += 1

            # 2+ aces: whist when estimated tricks support it.
            # Zero 2-ace whist losses across iters 8-9 proves room for bumps.
            if aces >= 2:
                # Check for AK combo and high card quality
                has_ak_combo_2a = False
                high_count_2a = self._count_high(hand) if hand else 0
                if hand:
                    groups_2a = self._suit_groups(hand)
                    for suit, cards in groups_2a.items():
                        if declarer_trump is not None and suit == declarer_trump:
                            continue
                        has_a = any(c.rank == 8 for c in cards)
                        has_k = any(c.rank == 7 for c in cards)
                        if has_a and has_k:
                            has_ak_combo_2a = True
                            break
                # Junk check: 2 aces but only aces are high (high_count <= 2,
                # no AK combo). Remaining 8 cards are junk.
                # Was <= 3 but G5 iter7: 2A+1Q (high=3) misclassified as junk,
                # rate capped from 90% to 70% → missed whist opportunity.
                # 2A + queen provides support; only bare 2A (high=2) is true junk.
                is_junk_2a = (high_count_2a <= 2 and not has_ak_combo_2a)
                # Singleton ace + no AK combo → junk: aces isolated, scattered honors
                # G10 iter1: 2A [[K,10,9,8],[A,D,9],[D,10],[A]] — singleton A, no AK → -56
                if not is_junk_2a and not has_ak_combo_2a:
                    for suit_2a, cards_2a in groups_2a.items():
                        if len(cards_2a) == 1 and cards_2a[0].rank == 8:
                            is_junk_2a = True
                            break
                # Iter37: Reduced rates to account for ~40% solo risk.
                # Solo whisting needs ~2 tricks for break-even; catastrophic at 0-1.
                # G4,G6 iter36: 2A est ~2.2 at 80-98% → solo → -80 each.
                if est_tricks >= 2.5:
                    if is_junk_2a:
                        rate = 0.87  # was 0.84; zero junk 2A losses across 9 iters
                    else:
                        rate = 1.00  # was 0.99; zero non-junk 2A losses — auto-follow
                    if self.rng.random() < rate:
                        return {"action": "follow",
                                "intent": f"follow — {aces} aces, {int(rate*100)}% rate ({est_tricks:.1f} tricks)"}
                    return {"action": "pass",
                            "intent": f"pass — {aces} aces est >= 2.5 rolled >{int(rate*100)}% ({est_tricks:.1f} tricks)"}
                if est_tricks >= 2.0:
                    rate = 0.83 if is_high_level else 0.97  # was 0.82/0.96; zero 2A losses iter3
                    if is_junk_2a:
                        rate = min(rate, 0.78)  # was 0.77; zero junk 2A losses
                    if self.rng.random() < rate:
                        return {"action": "follow",
                                "intent": f"follow — {aces} aces, {int(rate*100)}% rate ({est_tricks:.1f} tricks)"}
                    return {"action": "pass",
                            "intent": f"pass — {aces} aces est 2.0-2.5 rolled >{int(rate*100)}% ({est_tricks:.1f} tricks)"}
                if is_high_level:
                    rate = 0.82  # was 0.79; zero 2A losses
                else:
                    rate = 0.92  # was 0.89; zero 2A losses
                if is_junk_2a:
                    rate = min(rate, 0.77)  # was 0.74; zero junk 2A losses
                if self.rng.random() < rate:
                    return {"action": "follow",
                            "intent": f"follow — {aces} aces, {int(rate*100)}% rate ({est_tricks:.1f} tricks)"}
                return {"action": "pass",
                        "intent": f"pass — {aces} aces but weak est, rolled >{int(rate*100)}% ({est_tricks:.1f} tricks)"}

            # 1 ace: tiered by est_tricks. Reduced rates to account for
            # observed ~65% solo rate (4/4 solo in iter5). Solo with
            # 1A est<2.5 is catastrophic: G1(-60), G2(-93).
            # Rates: est>=2.0 95→78%, est>=1.5 88→68%, est>=1.0 58→44%, <1.0 40→30%.
            if aces == 1:
                # Check for A-K combo in same non-trump suit
                has_ak_combo = False
                has_void = False
                if hand:
                    groups = self._suit_groups(hand)
                    for suit, cards in groups.items():
                        if declarer_trump is not None and suit == declarer_trump:
                            continue
                        has_a = any(c.rank == 8 for c in cards)
                        has_k = any(c.rank == 7 for c in cards)
                        if has_a and has_k:
                            has_ak_combo = True
                            break
                    # Check for void in non-trump suit (ruffing potential)
                    if declarer_trump:
                        for s_val in [1, 2, 3, 4]:
                            if s_val != declarer_trump and s_val not in groups:
                                has_void = True
                                break

                if is_high_level:
                    rate = 0.54 if est_tricks >= 1.5 else 0.38  # was 0.58/0.42; 3 whist losses in iter10
                else:
                    if est_tricks >= 2.0:
                        rate = 0.90  # was 0.88; zero 1A losses iter3, G28 +40 profitable
                    elif est_tricks >= 1.5:
                        rate = 0.76  # was 0.82; solo risk makes marginal 1A -EV
                    elif est_tricks >= 1.0:
                        rate = 0.52  # was 0.59; G9 iter10 lost -36 at this tier
                    else:
                        rate = 0.37  # was 0.42; more conservative for weak 1A
                    # A-K combo in side suit bumps rate: reliable trick anchor
                    if has_ak_combo:
                        rate = min(rate + 0.15, 0.95)
                    # Void-suit bonus: ruffing potential (reduced cap for solo safety)
                    if has_void:
                        rate = min(rate + 0.08, 0.88)
                if self.rng.random() < rate:
                    return {"action": "follow",
                            "intent": f"follow — 1 ace, {int(rate*100)}% rate ({est_tricks:.1f} tricks)"}
                return {"action": "pass",
                        "intent": f"pass — 1 ace but rolled >{int(rate*100)}% ({est_tricks:.1f} tricks)"}

            # 0 aces: conservative — pullback from aggressive bumps.
            # Iter10 whist losses justify more caution across all tiers.
            if is_high_level:
                rate = 0.33 if est_tricks >= 1.0 else 0.0  # was 0.36; more cautious
            else:
                rate = 0.48 if est_tricks >= 1.0 else 0.35  # was 0.53/0.39; more cautious
            if rate > 0 and self.rng.random() < rate:
                return {"action": "follow",
                        "intent": f"follow — 0 aces, {int(rate*100)}% speculative ({est_tricks:.1f} tricks)"}
            return {"action": "pass",
                    "intent": f"pass — 0 aces ({est_tricks:.1f} tricks)"}

        if "pass" in action_types:
            return {"action": "pass", "intent": "pass — no follow option"}
        return {"action": action_types[0], "intent": f"fallback — {action_types[0]}"}

    # ------------------------------------------------------------------
    # Card play — strategic with declarer/whister awareness
    # ------------------------------------------------------------------

    # Personality params: Carol ducks king for 3 tricks, uses lowest trump
    _PLAY_PARAMS = {'king_duck_tricks': 3, 'whister_trump_pref': 'lowest'}

    _score_all_cards = PlayerAlice._score_all_cards
    choose_card = PlayerAlice.choose_card

    _betl_choose_card = PlayerAlice._betl_choose_card
    _betl_declarer_play = PlayerAlice._betl_declarer_play
    _betl_defender_play = PlayerAlice._betl_defender_play

    def _whister_lead(self, legal_cards):
        """Fallback whister lead when no context available."""
        groups = self._suit_groups(legal_cards)
        aces = [c for c in legal_cards if c.rank == 8]
        if aces:
            def ace_priority(c):
                suit_cards = groups.get(c.suit, [])
                has_king = any(x.rank == 7 for x in suit_cards)
                return (-100 if has_king else 0) + len(suit_cards)
            aces.sort(key=ace_priority)
            return aces[0]
        kings = [c for c in legal_cards if c.rank == 7]
        if kings:
            kings.sort(key=lambda c: len(groups.get(c.suit, [])), reverse=True)
            return kings[0]
        non_ace_suits = {s: cards for s, cards in groups.items()
                         if not any(c.rank == 8 for c in cards)}
        if non_ace_suits:
            shortest = min(non_ace_suits.keys(), key=lambda s: len(non_ace_suits[s]))
            return non_ace_suits[shortest][0]
        shortest_suit = min(groups.keys(), key=lambda s: len(groups[s]))
        return groups[shortest_suit][0]


# ---------------------------------------------------------------------------
# Sim3000: Monte Carlo simulation player
# ---------------------------------------------------------------------------

def _sim_determine_winner(trick_cards, trump_suit):
    """Determine trick winner from [(player_id, Card), ...] list."""
    if not trick_cards:
        return None
    winner_pid, winner_card = trick_cards[0]
    led_suit = trick_cards[0][1].suit
    for pid, card in trick_cards[1:]:
        beats = False
        if trump_suit:
            if card.suit == trump_suit and winner_card.suit != trump_suit:
                beats = True
            elif card.suit != trump_suit and winner_card.suit == trump_suit:
                beats = False
            elif card.suit == winner_card.suit and card.rank > winner_card.rank:
                beats = True
        else:
            if card.suit == winner_card.suit and card.rank > winner_card.rank:
                beats = True
            elif card.suit == led_suit and winner_card.suit != led_suit:
                beats = True
        if beats:
            winner_pid, winner_card = pid, card
    return winner_pid


def _sim_get_legal_cards(hand, trick_cards, trump_suit):
    """Get legal cards for a player given current trick state."""
    if not trick_cards:
        return list(hand)
    led_suit = trick_cards[0][1].suit
    suit_cards = [c for c in hand if c.suit == led_suit]
    if suit_cards:
        return suit_cards
    if trump_suit:
        trump_cards = [c for c in hand if c.suit == trump_suit]
        if trump_cards:
            return trump_cards
    return list(hand)


def _sim_playout(hands, active_ids, trick_order_fn, trump_suit, contract_type,
                 declarer_id, tricks_won, current_trick_cards, helper_cls,
                 rng, max_tricks=10, next_lead=None, prior_played_cards=None):
    """Play out the rest of a game from given state. Returns tricks_won per player."""
    # Deep copy hands so we don't mutate originals
    sim_hands = {pid: list(h) for pid, h in hands.items()}
    sim_tricks = dict(tricks_won)  # {pid: int}
    trick_cards = list(current_trick_cards)  # [(pid, Card), ...]
    tricks_played = sum(sim_tricks.values())

    # Build helper strategy instances for each player
    helpers = {}
    for pid in active_ids:
        h = helper_cls(f"sim_{pid}")
        h._contract_type = contract_type
        h._trump_suit = trump_suit
        h._is_declarer = (pid == declarer_id)
        h._total_hand_size = len(sim_hands.get(pid, []))
        h._cards_played = 0
        helpers[pid] = h

    # Determine who plays next in current trick
    if trick_cards:
        played_pids = {pid for pid, _ in trick_cards}
        lead_pid = trick_cards[0][0]
    else:
        lead_pid = next_lead or (active_ids[0] if active_ids else None)
        played_pids = set()

    # Get remaining players in trick order
    def remaining_in_trick(lead, played_set):
        order = trick_order_fn(None, lead)
        return [pid for pid in order if pid not in played_set]

    # Track all cards played during simulation for context
    sim_played_cards = list(prior_played_cards) if prior_played_cards else []

    # Play out
    while tricks_played < max_tricks:
        # Play remaining cards in current trick
        to_play = remaining_in_trick(lead_pid, played_pids)
        for pid in to_play:
            hand = sim_hands.get(pid, [])
            if not hand:
                continue
            legal = _sim_get_legal_cards(hand, trick_cards, trump_suit)
            if not legal:
                continue

            # Use helper logic to pick card
            h = helpers[pid]
            h._hand = hand
            h._total_hand_size = len(hand) + h._cards_played

            # Build a minimal CardPlayContext
            ctx = CardPlayContext.__new__(CardPlayContext)
            ctx.trick_cards = trick_cards
            ctx.declarer_id = declarer_id
            ctx.my_id = pid
            ctx.active_players = trick_order_fn(None, lead_pid)
            ctx.played_cards = sim_played_cards
            ctx.trump_suit = trump_suit
            ctx.contract_type = contract_type
            ctx.is_declarer = (pid == declarer_id)
            ctx.tricks_played = tricks_played
            ctx.my_hand = hand
            h._ctx = ctx
            h._rnd = None
            h._player_id = pid

            card_id = h.choose_card(legal)
            card_obj = next(c for c in legal if c.id == card_id)
            sim_hands[pid].remove(card_obj)
            h._cards_played += 1
            trick_cards.append((pid, card_obj))
            played_pids.add(pid)

        # Trick complete
        winner = _sim_determine_winner(trick_cards, trump_suit)
        if winner is None:
            break
        sim_tricks[winner] = sim_tricks.get(winner, 0) + 1
        tricks_played += 1

        # Add completed trick cards to played history
        for _, card_obj in trick_cards:
            sim_played_cards.append(card_obj)

        # Early termination: betl declarer wins a trick
        if contract_type == 'betl' and winner == declarer_id:
            break
        # Non-betl: followers got 5 tricks
        if contract_type != 'betl':
            follower_tricks = sum(v for k, v in sim_tricks.items() if k != declarer_id)
            if follower_tricks >= 5:
                break

        # No cards left
        if all(len(h) == 0 for h in sim_hands.values()):
            break

        # New trick — winner leads
        lead_pid = winner
        trick_cards = []
        played_pids = set()

    return sim_tricks


class Sim3000(WeightedRandomPlayer):
    """Monte Carlo simulation player.

    Delegates bidding, discarding, following to a helper player class.
    For card play, simulates N random games and picks the move that
    maximizes score.
    """

    def __init__(self, name: str, num_simulations: int = 10,
                 helper_cls=None, seed: int | None = None,
                 adaptive: bool = False):
        super().__init__(name, seed=seed)
        self.num_simulations = num_simulations
        self.adaptive = adaptive
        self.helper_cls = helper_cls or PlayerAlice
        # Create a helper instance for non-simulation decisions
        self._helper = self.helper_cls(name + "_helper")
        self._sim_rng = random.Random(seed)

    # Delegate all non-card-play decisions to helper via choose_* methods
    # (works with any helper class, including NeuralPlayer which doesn't
    # implement the lower-level decision routines)

    def choose_bid(self, legal_bids):
        self._helper._hand = getattr(self, '_hand', [])
        result = self._helper.choose_bid(legal_bids)
        self.last_bid_intent = self._helper.last_bid_intent
        return result

    def choose_discard(self, hand_card_ids, talon_card_ids):
        self._helper._is_declarer = True
        self._helper._winner_bid = getattr(self, '_winner_bid', None)
        result = self._helper.choose_discard(hand_card_ids, talon_card_ids)
        self._pre_chosen_contract = getattr(self._helper, '_pre_chosen_contract', None)
        return result

    def choose_contract(self, legal_levels, hand, winner_bid):
        pre = getattr(self, '_pre_chosen_contract', None)
        if pre:
            self._pre_chosen_contract = None
            return pre
        return self._helper.choose_contract(legal_levels, hand, winner_bid)

    def choose_whist_action(self, legal_actions):
        self._helper._hand = getattr(self, '_hand', [])
        self._helper._contract_type = getattr(self, '_contract_type', None)
        self._helper._trump_suit = getattr(self, '_trump_suit', None)
        return self._helper.choose_whist_action(legal_actions)

    @staticmethod
    def _all_connected(cards):
        """Check if all cards are same suit with consecutive ranks."""
        if len(cards) <= 1:
            return True
        suit = cards[0].suit
        if any(c.suit != suit for c in cards):
            return False
        ranks = sorted(c.rank_value for c in cards)
        return ranks[-1] - ranks[0] == len(ranks) - 1

    def choose_card(self, legal_cards):
        if len(legal_cards) == 1:
            return legal_cards[0].id

        # All connected cards are equivalent — skip simulation
        if self._all_connected(legal_cards):
            return legal_cards[0].id

        ctx = getattr(self, '_ctx', None)
        rnd = getattr(self, '_rnd', None)
        pid = getattr(self, '_player_id', None)

        if not ctx or not rnd:
            # Fallback to helper
            self._helper._ctx = ctx
            self._helper._rnd = rnd
            self._helper._player_id = pid
            self._helper._hand = getattr(self, '_hand', [])
            self._helper._contract_type = getattr(self, '_contract_type', None)
            self._helper._trump_suit = getattr(self, '_trump_suit', None)
            return self._helper.choose_card(legal_cards)

        contract_type = ctx.contract_type
        trump_suit = ctx.trump_suit
        declarer_id = ctx.declarer_id
        my_id = ctx.my_id
        active_players = ctx.active_players
        trick_cards = ctx.trick_cards  # [(pid, Card), ...]

        # Known cards: my hand + played cards + current trick
        my_hand = list(ctx.my_hand)
        played_cards = list(ctx.played_cards)
        trick_card_objs = [card for _, card in trick_cards]

        known_cards = set()
        for c in my_hand:
            known_cards.add(c.id)
        for c in played_cards:
            known_cards.add(c.id)
        for c in trick_card_objs:
            known_cards.add(c.id)

        # Build full deck
        all_card_ids = []
        from models import Rank as RankEnum
        for suit in Suit:
            for rank in RankEnum:
                all_card_ids.append(Card(rank=rank, suit=suit).id)

        # Unknown cards = full deck - known
        unknown_ids = [cid for cid in all_card_ids if cid not in known_cards]
        unknown_cards = [Card.from_id(cid) for cid in unknown_ids]

        # Other active players who still have cards
        others = [p for p in active_players if p != my_id]

        # Estimate hand sizes for others
        played_in_trick = {pid for pid, _ in trick_cards}
        other_hand_sizes = {}
        my_remaining = len(my_hand)
        for o in others:
            if o in played_in_trick:
                # Already played a card this trick, so one fewer than me
                other_hand_sizes[o] = my_remaining - 1
            else:
                other_hand_sizes[o] = my_remaining

        # Current tricks won per player
        tricks_won = {p: 0 for p in active_players}
        # First try _tricks_per_player (set by web server or externally)
        tpp = getattr(self, '_tricks_per_player', None)
        if tpp:
            for p in active_players:
                tricks_won[p] = tpp.get(p, 0)
        # Fall back to reading from rnd.tricks (benchmark / play_game context)
        elif rnd and hasattr(rnd, 'tricks'):
            for t in rnd.tricks:
                if t.winner_id is not None and t.winner_id in tricks_won:
                    tricks_won[t.winner_id] += 1

        # Trick order function
        def trick_order_fn(tricks_played_n, lead=None):
            """Return play order starting from lead."""
            if lead is None:
                lead = active_players[0] if active_players else my_id
            # Find positions for active players
            # Use the order from active_players but rotated to start with lead
            order = list(active_players)
            if lead in order:
                idx = order.index(lead)
                order = order[idx:] + order[:idx]
            return order

        # Determine number of simulations (adaptive reduces as cards are played)
        n_sims = self.num_simulations
        if self.adaptive and len(unknown_ids) < 22:
            n_sims = int((self.num_simulations - 5) * len(unknown_ids) / 22 + 5)
            n_sims = max(n_sims, 1)

        # Simulate each legal card
        card_scores = {}
        for candidate in legal_cards:
            total_score = 0.0
            for _ in range(n_sims):
                # Randomly assign unknown cards to opponents
                shuffled = list(unknown_cards)
                self._sim_rng.shuffle(shuffled)

                sim_hands = {my_id: [c for c in my_hand if c.id != candidate.id]}
                idx = 0
                for o in others:
                    sz = min(other_hand_sizes.get(o, 0), len(shuffled) - idx)
                    sim_hands[o] = shuffled[idx:idx + sz]
                    idx += sz

                # Set up current trick with our candidate card played
                sim_trick = list(trick_cards) + [(my_id, candidate)]

                sim_tricks_won = dict(tricks_won)

                # Check if trick is complete after our play
                if len(sim_trick) == len(active_players):
                    winner = _sim_determine_winner(sim_trick, trump_suit)
                    sim_tricks_won[winner] = sim_tricks_won.get(winner, 0) + 1
                    tricks_played = sum(sim_tricks_won.values())

                    # Early termination check
                    done = False
                    if contract_type == 'betl' and winner == declarer_id:
                        done = True
                    if contract_type != 'betl':
                        ftricks = sum(v for k, v in sim_tricks_won.items() if k != declarer_id)
                        if ftricks >= 5:
                            done = True

                    if not done and tricks_played < 10:
                        # Include real played cards + the just-completed trick
                        prior = played_cards + [card for _, card in sim_trick]
                        result = _sim_playout(
                            sim_hands, active_players, trick_order_fn,
                            trump_suit, contract_type, declarer_id,
                            sim_tricks_won, [], self.helper_cls,
                            self._sim_rng, max_tricks=10,
                            next_lead=winner,
                            prior_played_cards=prior,
                        )
                        sim_tricks_won = result
                else:
                    # Trick not complete yet — playout from here
                    result = _sim_playout(
                        sim_hands, active_players, trick_order_fn,
                        trump_suit, contract_type, declarer_id,
                        tricks_won, sim_trick, self.helper_cls,
                        self._sim_rng, max_tricks=10,
                        prior_played_cards=played_cards,
                    )
                    sim_tricks_won = result

                # Score: from our perspective
                my_tricks = sim_tricks_won.get(my_id, 0)
                if my_id == declarer_id:
                    # Declarer wants to maximize own tricks
                    if contract_type == 'betl':
                        total_score += (10.0 if my_tricks == 0 else -10.0)
                    else:
                        total_score += my_tricks
                else:
                    # Defender wants to maximize combined defender tricks
                    defender_tricks = sum(v for k, v in sim_tricks_won.items() if k != declarer_id)
                    if contract_type == 'betl':
                        # Want declarer to win tricks
                        decl_tricks = sim_tricks_won.get(declarer_id, 0)
                        total_score += (10.0 if decl_tricks > 0 else -10.0)
                    else:
                        total_score += defender_tricks

            card_scores[candidate.id] = total_score / n_sims

        # Pick best card
        best_id = max(card_scores, key=card_scores.get)
        return best_id


def make_simsim_cls(num_simulations: int = 10, helper_cls=None,
                    adaptive: bool = False):
    """Create a SimSim class: a Sim3000 whose playout helper is itself a Sim3000."""
    _inner_n = num_simulations
    _inner_helper = helper_cls or PlayerAlice
    _inner_adaptive = adaptive

    class _SimSimHelper(Sim3000):
        def __init__(self, name, seed=None):
            super().__init__(name, num_simulations=_inner_n,
                             helper_cls=_inner_helper, seed=seed,
                             adaptive=_inner_adaptive)

    return _SimSimHelper


class NoisyPlayer(WeightedRandomPlayer):
    """Player that adds random noise to a helper's card scores.

    Uses the helper's heuristic scoring (_score_all_cards) and multiplies
    each card's score by a random factor in (1-p, 1+p), then picks the best.
    This means close-scoring cards can swap order, while clear winners stay.
    Bidding, discarding, and whisting are delegated to the helper directly.
    """

    def __init__(self, name: str, noise: float = 0.1,
                 helper_cls=None, seed: int | None = None):
        super().__init__(name, seed=seed)
        self.noise = noise
        self.helper_cls = helper_cls or PlayerAlice
        self._helper = self.helper_cls(name + "_helper")
        self._noise_rng = random.Random(seed)

    def choose_bid(self, legal_bids):
        self._helper._hand = getattr(self, '_hand', [])
        result = self._helper.choose_bid(legal_bids)
        self.last_bid_intent = self._helper.last_bid_intent
        return result

    def choose_discard(self, hand_card_ids, talon_card_ids):
        self._helper._is_declarer = True
        self._helper._winner_bid = getattr(self, '_winner_bid', None)
        result = self._helper.choose_discard(hand_card_ids, talon_card_ids)
        self._pre_chosen_contract = getattr(self._helper, '_pre_chosen_contract', None)
        self._ranked_discards = getattr(self._helper, '_ranked_discards', None)
        return result

    def choose_contract(self, legal_levels, hand, winner_bid):
        pre = getattr(self, '_pre_chosen_contract', None)
        if pre:
            self._pre_chosen_contract = None
            return pre
        return self._helper.choose_contract(legal_levels, hand, winner_bid)

    def choose_whist_action(self, legal_actions):
        self._helper._hand = getattr(self, '_hand', [])
        self._helper._contract_type = getattr(self, '_contract_type', None)
        self._helper._trump_suit = getattr(self, '_trump_suit', None)
        return self._helper.choose_whist_action(legal_actions)

    def choose_card(self, legal_cards):
        if len(legal_cards) == 1:
            self._ranked_cards = [(legal_cards[0].id, 100.0)]
            self._cards_played += 1
            return legal_cards[0].id

        # Copy state to helper so _score_all_cards works
        self._helper._contract_type = getattr(self, '_contract_type', None)
        self._helper._trump_suit = getattr(self, '_trump_suit', None)
        # _trump_suit_val may not be set (e.g. in sim playout); fall back to _trump_suit
        self._helper._trump_suit_val = getattr(self, '_trump_suit_val',
                                                getattr(self, '_trump_suit', None))
        self._helper._is_declarer = getattr(self, '_is_declarer', False)
        self._helper._total_hand_size = getattr(self, '_total_hand_size', 10)
        self._helper._cards_played = getattr(self, '_cards_played', 0)
        self._helper._trump_leads = getattr(self, '_trump_leads', 0)
        self._helper._ctx = getattr(self, '_ctx', None)
        self._helper._rnd = getattr(self, '_rnd', None)
        self._helper._player_id = getattr(self, '_player_id', None)
        self._helper._hand = getattr(self, '_hand', [])
        self._helper._PLAY_PARAMS = getattr(self, '_PLAY_PARAMS',
                                             getattr(self._helper, '_PLAY_PARAMS',
                                                     {'king_duck_tricks': 2, 'whister_trump_pref': 'highest'}))

        # Get base scores from helper's heuristic
        card_scores = self._helper._score_all_cards(legal_cards)

        # Apply multiplicative noise: score * uniform(1-p, 1+p)
        p = self.noise
        noisy_scores = {}
        for cid, score in card_scores.items():
            factor = self._noise_rng.uniform(1.0 - p, 1.0 + p)
            noisy_scores[cid] = score * factor

        self._ranked_cards = sorted(noisy_scores.items(), key=lambda x: -x[1])

        # Update trump_leads counter if declarer led a trump
        best_id = self._ranked_cards[0][0]
        ctx = getattr(self, '_ctx', None)
        rnd = getattr(self, '_rnd', None)
        trick = rnd.current_trick if rnd else None
        played = ctx.trick_cards if ctx else (trick.cards if trick else [])
        trump_val = getattr(self, '_trump_suit_val', None)
        if len(played) == 0 and getattr(self, '_is_declarer', False) and trump_val is not None:
            best_card = next(c for c in legal_cards if c.id == best_id)
            if best_card.suit == trump_val:
                self._trump_leads = getattr(self, '_trump_leads', 0) + 1

        self._cards_played += 1
        return best_id


class NeuralPlayer(BasePlayer):
    """ML-based Preferans player. Uses a trained PrefNet model for decisions.

    Falls back to random moves if the model file is not found.
    """

    # Bid type ↔ index mapping
    BID_TYPES = ["pass", "game", "in_hand", "betl", "sans"]
    FOLLOWING_ACTIONS = ["pass", "follow", "call", "counter", "start_game", "double_counter"]

    def __init__(self, name: str, seed: int | None = None,
                 model_path: str = "neural/models/pref_net.pt",
                 temperature: float = 0.0,
                 aggressiveness: float = 0.5):
        super().__init__(name)
        self.rng = random.Random(seed)
        self.temperature = temperature
        self.aggressiveness = aggressiveness
        self._observed_cards = []
        self._cards_played = 0
        self._is_declarer = False
        self.model = None

        # Try to load model
        try:
            import torch
            from neural.model import PrefNet
            self._torch = torch
            self._features = __import__("neural.features", fromlist=["features"])
            net = PrefNet()
            if os.path.exists(model_path):
                net.load_state_dict(torch.load(model_path, map_location="cpu",
                                               weights_only=True))
                net.eval()
                self.model = net
        except ImportError:
            pass

    def _aggr_tensor(self):
        """Return aggressiveness as (1, 1) tensor."""
        return self._torch.tensor([[self.aggressiveness]], dtype=self._torch.float32)

    def _sample_or_argmax(self, logits):
        """Given 1D logits tensor, return index via argmax or temperature sampling."""
        torch = self._torch
        if self.temperature <= 0:
            return logits.argmax().item()
        probs = torch.softmax(logits / self.temperature, dim=-1)
        return torch.multinomial(probs, 1).item()

    # ------------------------------------------------------------------
    # Bidding
    # ------------------------------------------------------------------

    def choose_bid(self, legal_bids):
        hand = getattr(self, '_hand', [])

        if self.model is None:
            bid = self.rng.choice(legal_bids)
            self.last_bid_intent = "neural (random fallback)"
            return bid

        torch = self._torch
        feat = self._features

        hand_feat = torch.from_numpy(feat.encode_hand(hand)).unsqueeze(0)
        mask = torch.zeros(1, 5)
        bid_type_map = {}
        for b in legal_bids:
            bt = b.get("bid_type")
            if bt in self.BID_TYPES:
                idx = self.BID_TYPES.index(bt)
                mask[0, idx] = 1.0
                bid_type_map[idx] = b

        with torch.no_grad():
            logits = self.model.forward_bid(hand_feat, self._aggr_tensor(), mask)[0]

        chosen_idx = self._sample_or_argmax(logits)
        if chosen_idx in bid_type_map:
            chosen = bid_type_map[chosen_idx]
        else:
            chosen = self.rng.choice(legal_bids)

        self.last_bid_intent = f"neural (bid={self.BID_TYPES[chosen_idx]})"
        return chosen

    # ------------------------------------------------------------------
    # Discarding
    # ------------------------------------------------------------------

    def choose_discard(self, hand_card_ids, talon_card_ids):
        self._is_declarer = True

        if self.model is None:
            all_ids = list(hand_card_ids) + list(talon_card_ids)
            return self.rng.sample(all_ids, 2)

        torch = self._torch
        feat = self._features
        from models import Card

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
            __import__("numpy").array(card_feats, dtype=__import__("numpy").float32)
        ).unsqueeze(0)

        with torch.no_grad():
            scores = self.model.forward_discard(hand_feat, self._aggr_tensor(), card_feats_t)[0]

        # Pick top-2 scoring cards to discard
        top2 = scores.topk(2).indices.tolist()
        return [all_ids[i] for i in top2]

    # ------------------------------------------------------------------
    # Contract declaration
    # ------------------------------------------------------------------

    def choose_contract(self, legal_levels, hand, winner_bid):
        if self.model is None:
            suits = {}
            for c in hand:
                suits.setdefault(c.suit, []).append(c)
            best_suit = max(suits, key=lambda s: len(suits[s]))
            trump_name = SUIT_NAMES[best_suit]
            level = min(legal_levels) if legal_levels else 2
            return "suit", trump_name, level

        torch = self._torch
        feat = self._features

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

        with torch.no_grad():
            type_logits, trump_logits = self.model.forward_contract(hand_feat, self._aggr_tensor(), context)

        type_idx = type_logits[0].argmax().item()
        contract_types = ["suit", "betl", "sans"]
        ctype = contract_types[type_idx]

        if ctype == "suit":
            # Suit inherent levels: spades=2, diamonds=3, hearts=4, clubs=5
            suit_levels = {"clubs": 5, "diamonds": 3, "hearts": 4, "spades": 2}
            suit_names = ["clubs", "diamonds", "hearts", "spades"]
            # Sort by model preference, pick first valid suit
            trump_probs = trump_logits[0]
            sorted_idx = trump_probs.argsort(descending=True).tolist()
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
    # Whisting / following — routes to correct head
    # ------------------------------------------------------------------

    CALLING_ACTIONS = ["pass", "follow", "call", "counter"]
    COUNTERING_ACTIONS = ["start_game", "counter", "double_counter"]

    def choose_whist_action(self, legal_actions):
        hand = getattr(self, '_hand', [])
        contract_type = getattr(self, '_contract_type', None)
        trump_suit = getattr(self, '_trump_suit', None)

        if self.model is None:
            action = self.rng.choice(legal_actions)
            return action.get("action") if isinstance(action, dict) else action

        # Determine which actions are legal to route to correct head
        action_strs = set()
        for a in legal_actions:
            act = a.get("action") if isinstance(a, dict) else a
            action_strs.add(act)

        if "call" in action_strs:
            return self._choose_calling(hand, contract_type, trump_suit, legal_actions, action_strs)
        elif "start_game" in action_strs and ("counter" in action_strs or "double_counter" in action_strs):
            return self._choose_countering(hand, contract_type, trump_suit, legal_actions, action_strs)
        else:
            return self._choose_following(hand, contract_type, trump_suit, legal_actions)

    def _choose_following(self, hand, contract_type, trump_suit, legal_actions):
        torch = self._torch
        feat = self._features

        hand_feat = torch.from_numpy(feat.encode_hand(hand)).unsqueeze(0)
        context = torch.from_numpy(
            feat.encode_following_context(contract_type, trump_suit, hand)
        ).unsqueeze(0)

        mask = torch.zeros(1, 2)
        action_map = {}
        following_actions = ["pass", "follow"]
        for a in legal_actions:
            act = a.get("action") if isinstance(a, dict) else a
            if act in following_actions:
                idx = following_actions.index(act)
                mask[0, idx] = 1.0
                action_map[idx] = act

        with torch.no_grad():
            logits = self.model.forward_following(hand_feat, self._aggr_tensor(), context, mask)[0]

        chosen_idx = self._sample_or_argmax(logits)
        if chosen_idx in action_map:
            return action_map[chosen_idx]
        a = self.rng.choice(legal_actions)
        return a.get("action") if isinstance(a, dict) else a

    def _choose_calling(self, hand, contract_type, trump_suit, legal_actions, action_strs):
        torch = self._torch
        feat = self._features

        hand_feat = torch.from_numpy(feat.encode_hand(hand)).unsqueeze(0)
        other_defender_passed = "follow" not in action_strs and "pass" in action_strs
        is_counter_subphase = "counter" in action_strs
        context = torch.from_numpy(
            feat.encode_calling_context(
                contract_type, trump_suit, hand,
                other_defender_passed, is_counter_subphase, 2,
            )
        ).unsqueeze(0)

        mask = torch.zeros(1, 4)
        action_map = {}
        for a in legal_actions:
            act = a.get("action") if isinstance(a, dict) else a
            if act in self.CALLING_ACTIONS:
                idx = self.CALLING_ACTIONS.index(act)
                mask[0, idx] = 1.0
                action_map[idx] = act

        with torch.no_grad():
            logits = self.model.forward_calling(hand_feat, self._aggr_tensor(), context, mask)[0]

        chosen_idx = self._sample_or_argmax(logits)
        if chosen_idx in action_map:
            return action_map[chosen_idx]
        a = self.rng.choice(legal_actions)
        return a.get("action") if isinstance(a, dict) else a

    def _choose_countering(self, hand, contract_type, trump_suit, legal_actions, action_strs):
        torch = self._torch
        feat = self._features

        hand_feat = torch.from_numpy(feat.encode_hand(hand)).unsqueeze(0)
        is_declarer_responding = "double_counter" in action_strs
        context = torch.from_numpy(
            feat.encode_countering_context(
                contract_type, trump_suit, hand,
                is_declarer_responding, 2, 1,
            )
        ).unsqueeze(0)

        mask = torch.zeros(1, 3)
        action_map = {}
        for a in legal_actions:
            act = a.get("action") if isinstance(a, dict) else a
            if act in self.COUNTERING_ACTIONS:
                idx = self.COUNTERING_ACTIONS.index(act)
                mask[0, idx] = 1.0
                action_map[idx] = act

        with torch.no_grad():
            logits = self.model.forward_countering(hand_feat, self._aggr_tensor(), context, mask)[0]

        chosen_idx = self._sample_or_argmax(logits)
        if chosen_idx in action_map:
            return action_map[chosen_idx]
        a = self.rng.choice(legal_actions)
        return a.get("action") if isinstance(a, dict) else a

    # ------------------------------------------------------------------
    # Card play
    # ------------------------------------------------------------------

    def choose_card(self, legal_cards):
        if self.model is None:
            return self.rng.choice(legal_cards).id

        torch = self._torch
        feat = self._features
        np = __import__("numpy")

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
                # trick.cards is list of (player_id, Card) tuples
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

        with torch.no_grad():
            scores = self.model.forward_card_play(
                hand_feat, self._aggr_tensor(), play_ctx, played_vec, card_feats_t)[0]

        n = len(legal_cards)
        chosen_idx = self._sample_or_argmax(scores[:n])
        chosen_card = legal_cards[chosen_idx]

        # Track played cards
        self._observed_cards.append(chosen_card)
        self._cards_played += 1

        return chosen_card.id


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------

def card_str(card):
    suit_sym = {"spades": "♠", "diamonds": "♦", "clubs": "♣", "hearts": "♥"}
    return f"{RANK_NAMES[card.rank]}{suit_sym[SUIT_NAMES[card.suit]]}"


def hand_str(cards):
    return " ".join(card_str(c) for c in cards)


# ---------------------------------------------------------------------------
# Compact log helpers
# ---------------------------------------------------------------------------

COMPACT_RANK = {8: 'A', 7: 'K', 6: 'D', 5: 'J', 4: '10', 3: '9', 2: '8', 1: '7'}


def _seq_strength_cmp(ranks_a, ranks_b):
    """Compare two equal-length rank sequences (sorted descending).

    Returns >0 if a is stronger, <0 if b is stronger, 0 if equal.
    Applies KDJ exceptions: connected K-D-J beats certain A-led sequences.
    """
    K, D, J, TEN = 7, 6, 5, 4
    A = 8
    n = len(ranks_a)

    def kdj_wins(ra, rb):
        if len(ra) < 3 or ra[:3] != [K, D, J]:
            return False
        if rb[0] != A:
            return False
        if n == 3:
            # Rule 1: KDJ > ADx for x <= 10
            if rb[1] == D and rb[2] <= TEN:
                return True
            # Rule 2: KDJ > AJx
            if rb[1] == J:
                return True
        if n >= 4:
            # Rule 3: KDJx > AJxy
            if rb[1] == J:
                return True
            # Rule 4: KDJx > AD10x
            if rb[1] == D and rb[2] == TEN:
                return True
        return False

    if kdj_wins(ranks_a, ranks_b):
        return 1
    if kdj_wins(ranks_b, ranks_a):
        return -1

    for ra, rb in zip(ranks_a, ranks_b):
        if ra != rb:
            return ra - rb
    return 0


def _get_sorted_suits(hand):
    """Group hand by suit, sort by strength (strongest first).

    Returns [(suit_val, [Card...]), ...] with all 4 suits (empty ones at end).
    """
    groups = {}
    for c in hand:
        groups.setdefault(c.suit, []).append(c)
    for s in groups:
        groups[s].sort(key=lambda c: c.rank, reverse=True)

    non_empty = list(groups.items())

    def cmp_suits(a, b):
        sa, ca = a
        sb, cb = b
        # 1. More cards = stronger
        if len(ca) != len(cb):
            return len(ca) - len(cb)
        # 2. Sequence strength
        ra = [c.rank for c in ca]
        rb = [c.rank for c in cb]
        seq = _seq_strength_cmp(ra, rb)
        if seq != 0:
            return seq
        # 3. Lexicographically larger suit name wins (higher enum = larger name)
        return sa - sb

    non_empty.sort(key=cmp_to_key(cmp_suits), reverse=True)

    present = {s for s, _ in non_empty}
    missing = sorted([s for s in [1, 2, 3, 4] if s not in present], reverse=True)
    return non_empty + [(s, []) for s in missing]


def compact_hand_fmt(hand):
    """Format hand as [[A, K, D], [J, 10], ...] sorted by suit strength."""
    sorted_suits = _get_sorted_suits(hand)
    parts = []
    for _, cards in sorted_suits:
        parts.append('[' + ', '.join(COMPACT_RANK[c.rank] for c in cards) + ']')
    return '[' + ', '.join(parts) + ']'


def compact_suit_index(suit_val, hand):
    """Get 0-based index of a suit in the strength-sorted ordering."""
    sorted_suits = _get_sorted_suits(hand)
    for i, (s, _) in enumerate(sorted_suits):
        if s == suit_val:
            return i
    return -1


# ---------------------------------------------------------------------------
# Game runner
# ---------------------------------------------------------------------------

def play_game(strategies: dict[int, BasePlayer], seed: int = 42) -> tuple[list[str], list[str], dict]:
    """Play one full game (single round) and return (log_lines, compact_lines, timing)."""
    log = []
    compact = []
    # Per-player timing: {player_name: [list of choose_card durations in seconds]}
    timing = {strategies[pid].name: [] for pid in strategies}
    # Compact log data
    compact_bids = []      # (name, hand_snapshot, bid_label)
    compact_whists = []     # (name, hand_snapshot, contract_label, action)

    def emit(msg):
        log.append(msg)

    # Create game via GameSession — use names from strategy objects
    player_names = [strategies[pid].name for pid in sorted(strategies.keys())]
    if len(player_names) != 3:
        player_names = ["Alice", "Bob", "Carol"]

    # Seed global random so shuffle_and_deal produces the same deal for this seed
    random.seed(seed)

    session = GameSession(player_names)
    engine = session.engine
    game = engine.game

    rnd = game.current_round
    emit(f"=== Preferans Single Game ===")
    emit(f"Seed: {seed}")
    emit(f"")

    # Show positions
    for p in sorted(game.players, key=lambda p: p.position):
        pos_name = {1: "Forehand", 2: "Middlehand", 3: "Dealer"}[p.position]
        strat = strategies[p.id]
        emit(f"P{p.position} ({pos_name}): {p.name} [{strat.__class__.__name__}]")
    emit(f"")

    # Show dealt hands
    emit("--- Dealt Hands ---")
    for p in sorted(game.players, key=lambda p: p.position):
        emit(f"  P{p.position} {p.name}: {hand_str(p.hand)}")
    emit(f"  Talon: {' '.join(card_str(c) for c in rnd.talon)}")
    emit(f"")

    # ------------------------------------------------------------------
    # AUCTION (direct engine calls)
    # ------------------------------------------------------------------
    emit("--- Auction ---")

    max_steps = 30
    while rnd.phase == RoundPhase.AUCTION and max_steps > 0:
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

        bid_label = chosen.get("label", chosen["bid_type"])
        intent = getattr(strat, 'last_bid_intent', '')
        if intent:
            emit(f"  P{player.position} {player.name}: {bid_label}  [{intent}]")
        else:
            emit(f"  P{player.position} {player.name}: {bid_label}")

        # Compact log: record bid intent
        bt = chosen["bid_type"]
        if bt == "pass":
            compact_bid_label = "pass"
        elif bt in ("sans", "betl"):
            compact_bid_label = bt
        else:
            compact_bid_label = "0"
        compact_bids.append((player.name, list(player.hand), compact_bid_label))

        engine.place_bid(bidder_id, chosen["bid_type"], chosen.get("value", 0))

    # ------------------------------------------------------------------
    # EXCHANGING (regular game, declarer picks up talon)
    # ------------------------------------------------------------------
    rnd = game.current_round
    if rnd.phase == RoundPhase.EXCHANGING:
        declarer_id = rnd.declarer_id
        declarer = game.get_player(declarer_id)
        winner_bid = rnd.auction.get_winner_bid()

        emit(f"")
        emit(f"Auction winner: P{declarer.position} {declarer.name} "
             f"(bid: {winner_bid.bid_type.value} {winner_bid.value})")
        emit(f"")
        emit("--- Exchange ---")
        emit(f"  Talon: {' '.join(card_str(c) for c in rnd.talon)}")

        strat_d = strategies[declarer_id]
        strat_d._winner_bid = winner_bid
        hand_ids = [c.id for c in declarer.hand]
        talon_ids = [c.id for c in rnd.talon]
        discard_ids = strat_d.choose_discard(hand_ids, talon_ids)

        engine.complete_exchange(declarer_id, discard_ids)

        emit(f"  {declarer.name} discards: {', '.join(discard_ids)}")
        emit(f"  Hand after exchange: {hand_str(declarer.hand)}")
        emit(f"")

        # Announce contract
        legal_levels = engine.get_legal_contract_levels(declarer_id)
        ctype, trump, level = strat_d.choose_contract(
            legal_levels, declarer.hand, winner_bid)
        engine.announce_contract(declarer_id, ctype, trump_suit=trump, level=level)

        trump_display = f" ({trump})" if trump else ""
        emit(f"Contract: {ctype}{trump_display} level {rnd.contract.bid_value}")
        emit(f"")

    # ------------------------------------------------------------------
    # IN-HAND CONTRACT DECLARATION (playing phase, no contract yet)
    # ------------------------------------------------------------------
    elif rnd.phase == RoundPhase.PLAYING and rnd.contract is None:
        declarer_id = rnd.declarer_id
        declarer = game.get_player(declarer_id)
        winner_bid = rnd.auction.get_winner_bid()

        emit(f"")
        emit(f"Auction winner: P{declarer.position} {declarer.name} "
             f"(bid: {winner_bid.bid_type.value} {winner_bid.value})")
        emit(f"")
        emit("--- In-Hand Contract Declaration ---")

        legal_levels = engine.get_legal_contract_levels(declarer_id)
        strat_d = strategies[declarer_id]
        ctype, trump, level = strat_d.choose_contract(
            legal_levels, declarer.hand, winner_bid)
        engine.announce_contract(declarer_id, ctype,
                                 trump_suit=trump, level=level)

        trump_display = f" ({trump})" if trump else ""
        emit(f"Contract: {ctype}{trump_display} "
             f"level {rnd.contract.bid_value} (in-hand)")
        emit(f"")

    # Handle redeal (all passed)
    if rnd.phase == RoundPhase.REDEAL:
        emit("  => All passed — redeal (game over for this round)")
        emit("")
        emit("--- Result: REDEAL ---")
        for name, hand, blabel in compact_bids:
            compact.append(f"{name} bid: {compact_hand_fmt(hand)} -> {blabel}")
        compact.append("")
        for p in sorted(game.players, key=lambda p: p.position):
            compact.append(f"{p.name} score: {p.score}")
        return log, compact, timing

    # ------------------------------------------------------------------
    # WHISTING
    # ------------------------------------------------------------------
    rnd = game.current_round
    whist_emitted = False
    while rnd.phase == RoundPhase.WHISTING:
        defender_id = rnd.whist_current_id
        if defender_id is None:
            break

        actions = engine.get_legal_whist_actions(defender_id)
        if not actions:
            break

        if not whist_emitted:
            # Emit auction winner if not already done for in-hand case
            if rnd.declarer_id is not None and rnd.contract is not None:
                if rnd.contract.is_in_hand:
                    declarer = game.get_player(rnd.declarer_id)
                    winner_bid = rnd.auction.get_winner_bid()
                    if not any("Auction winner" in line for line in log):
                        emit(f"")
                        emit(f"Auction winner: P{declarer.position} {declarer.name} "
                             f"(bid: {winner_bid.bid_type.value} {winner_bid.value})")
                        emit(f"")
                        trump_display = f" ({rnd.contract.trump_suit})" if rnd.contract.trump_suit else ""
                        emit(f"Contract: {rnd.contract.type.value}{trump_display} "
                             f"level {rnd.contract.bid_value} (in-hand)")
                        emit(f"")
            emit("--- Whisting ---")
            whist_emitted = True

        player = game.get_player(defender_id)
        strat = strategies[defender_id]

        strat._hand = player.hand
        strat._contract_type = rnd.contract.type.value if rnd.contract else None
        strat._trump_suit = rnd.contract.trump_suit if rnd.contract else None
        action = strat.choose_whist_action(actions)

        emit(f"  P{player.position} {player.name}: {action}")

        # Compact log: record whisting decision
        contract = rnd.contract
        if contract.type == ContractType.SANS:
            c_label = "sans"
        elif contract.type == ContractType.BETL:
            c_label = "betl"
        else:
            c_label = str(compact_suit_index(contract.trump_suit, player.hand))
        compact_action = "call" if action not in ("pass",) else "pass"
        compact_whists.append((player.name, list(player.hand), c_label, compact_action))

        # Call engine to mutate game state
        if rnd.whist_declaring_done:
            engine.declare_counter_action(defender_id, action)
        else:
            engine.declare_whist(defender_id, action)

    # Handle no followers
    if rnd.phase == RoundPhase.SCORING:
        if rnd.contract and not rnd.whist_followers:
            emit("  => No followers — declarer wins automatically")
            emit(f"")

    # ------------------------------------------------------------------
    # PLAYING
    # ------------------------------------------------------------------
    if rnd.phase == RoundPhase.PLAYING:
        contract = rnd.contract
        trump_display = ""
        if contract.trump_suit:
            trump_display = f" trump={SUIT_NAMES[contract.trump_suit]}"
        emit(f"--- Playing (contract: {contract.type.value}{trump_display}, "
             f"level={contract.bid_value}, in_hand={contract.is_in_hand}) ---")

        # Show hands before play
        emit(f"Hands before play:")
        for p in sorted(game.players, key=lambda p: p.position):
            emit(f"  P{p.position} {p.name}: {hand_str(p.hand)}")
        emit(f"")

        trick_num = 0
        played_cards_history = []  # track all cards from completed tricks
        tricks_completed = 0
        # Compute active player IDs (not dropped out)
        active_ids = [p.id for p in sorted(game.players, key=lambda p: p.position)
                      if not p.has_dropped_out]
        # Determine trump suit and contract type for context
        ctx_trump = contract.trump_suit if contract.type == ContractType.SUIT else None
        ctx_contract_type = contract.type.value  # "suit", "betl", "sans"

        while rnd.phase == RoundPhase.PLAYING:
            trick = rnd.current_trick
            if trick is None:
                break

            if trick.number != trick_num:
                trick_num = trick.number
                emit(f"  Trick {trick_num}:")

            next_id = engine._get_next_player_in_trick(trick)
            player = game.get_player(next_id)
            legal_cards = engine.get_legal_cards(next_id)
            if not legal_cards:
                break

            # Build active_players in trick order starting from leader
            lead_id = trick.lead_player_id
            ccw = [1, 3, 2, 1, 3, 2]
            lead_pos = game.get_player(lead_id).position
            start = ccw.index(lead_pos)
            trick_order = []
            for p in ccw[start:start + 3]:
                pid = next((pl.id for pl in game.players if pl.position == p), None)
                if pid and pid in active_ids:
                    trick_order.append(pid)

            # Build CardPlayContext
            ctx = CardPlayContext(
                trick_cards=list(trick.cards),
                declarer_id=rnd.declarer_id,
                my_id=next_id,
                active_players=trick_order,
                played_cards=list(played_cards_history),
                trump_suit=ctx_trump,
                contract_type=ctx_contract_type,
                is_declarer=(next_id == rnd.declarer_id),
                tricks_played=tricks_completed,
                my_hand=list(player.hand),
            )

            strat = strategies[next_id]
            strat._rnd = rnd
            strat._player_id = next_id
            strat._ctx = ctx
            t0 = time.perf_counter()
            card_id = strat.choose_card(legal_cards)
            timing[player.name].append(time.perf_counter() - t0)
            card_obj = next(c for c in legal_cards if c.id == card_id)

            result = engine.play_card(next_id, card_id)

            emit(f"    P{player.position} {player.name} plays {card_str(card_obj)}")

            if result.get("trick_complete"):
                winner = game.get_player(result["trick_winner_id"])
                emit(f"    => Winner: P{winner.position} {winner.name}")
                # Track played cards from completed trick
                for _pid, _card in trick.cards:
                    played_cards_history.append(_card)
                tricks_completed += 1

        emit(f"")

    # ------------------------------------------------------------------
    # SCORING
    # ------------------------------------------------------------------
    emit("--- Scoring ---")
    for p in sorted(game.players, key=lambda p: p.position):
        emit(f"  P{p.position} {p.name}: tricks={p.tricks_won}, score={p.score}")
    if rnd.contract:
        emit(f"  Contract: {rnd.contract.type.value}, level={rnd.contract.bid_value}")
        declarer = game.get_player(rnd.declarer_id)
        required = rnd.contract.tricks_required
        # Auto-win: if no defenders followed, declarer wins automatically (rule 2.1)
        auto_win = rnd.contract.type != ContractType.BETL and not rnd.whist_followers
        if auto_win:
            won = True
        elif rnd.contract.type == ContractType.BETL:
            won = declarer.tricks_won == 0
        else:
            won = declarer.tricks_won >= required
        emit(f"  Declarer {declarer.name}: {declarer.tricks_won} tricks "
             f"(needed {'0' if rnd.contract.type == ContractType.BETL else str(required)}) "
             f"→ {'WON' if won else 'LOST'}")
    emit(f"")

    # ------------------------------------------------------------------
    # COMPACT LOG
    # ------------------------------------------------------------------
    for name, hand, blabel in compact_bids:
        compact.append(f"{name} bid: {compact_hand_fmt(hand)} -> {blabel}")
    compact.append("")
    for name, hand, clabel, action in compact_whists:
        compact.append(f"{name} declaration: {compact_hand_fmt(hand)}, {clabel} -> {action}")
    compact.append("")
    for p in sorted(game.players, key=lambda p: p.position):
        compact.append(f"{p.name} score: {p.score}")

    return log, compact, timing


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("seed", nargs="?", type=int, default=None)
    parser.add_argument("--players", type=str, default=None,
                        help="Comma-separated player names: alice,bob,carol,neural (pick 3)")
    args = parser.parse_args()

    seed = args.seed if args.seed is not None else random.randint(1, 999999)

    # Build player pool
    player_makers = {
        "alice": lambda s: PlayerAlice(seed=s),
        "bob": lambda s: PlayerBob(seed=s + 1),
        "carol": lambda s: PlayerCarol(seed=s + 2),
        "neural": lambda s: NeuralPlayer("Neural", seed=s + 3),
        "neural-aggressive": lambda s: NeuralPlayer("N-aggressive", seed=s + 4, aggressiveness=1.0),
        "neural-pragmatic": lambda s: NeuralPlayer("N-pragmatic", seed=s + 5, aggressiveness=0.5),
    }

    if args.players:
        names = [n.strip().lower() for n in args.players.split(",")]
    else:
        names = ["alice", "bob", "carol"]

    if len(names) != 3:
        print(f"Error: need exactly 3 players, got {len(names)}: {names}")
        sys.exit(1)
    for n in names:
        if n not in player_makers:
            print(f"Error: unknown player '{n}'. Choose from: {list(player_makers.keys())}")
            sys.exit(1)

    players = [player_makers[n](seed) for n in names]
    strategies = {i + 1: p for i, p in enumerate(players)}

    # Show weights
    print(f"Seed: {seed}")
    for p in players:
        w = p.weights_str() if hasattr(p, 'weights_str') else "(neural)"
        print(f"  {p.name} weights: {w}")
    print()

    log_lines, compact_lines, _ = play_game(strategies, seed=seed)

    # Print to stdout
    for line in log_lines:
        print(line)

    # Write to files
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = os.path.dirname(os.path.abspath(__file__))

    filename = f"game_output_{timestamp}.txt"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w") as f:
        for line in log_lines:
            f.write(line + "\n")

    compact_filename = f"game_compact_{timestamp}.txt"
    compact_filepath = os.path.join(output_dir, compact_filename)
    with open(compact_filepath, "w") as f:
        for line in compact_lines:
            f.write(line + "\n")

    print(f"\nLog saved to: {filepath}")
    print(f"Compact saved to: {compact_filepath}")


if __name__ == "__main__":
    main()
