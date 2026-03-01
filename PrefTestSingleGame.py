"""Play a single Preferans game with three RandomMove players and log every move."""

import os
import sys
import random
import datetime
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

    def following_decision(self, hand, contract_type, trump_suit, legal_actions) -> dict:
        """Decide whether to follow, pass, call or counter a declaration.

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
    # Action routines — these call the decision routines above
    # ------------------------------------------------------------------

    def choose_bid(self, legal_bids: list[dict]) -> dict:
        raise NotImplementedError

    def choose_discard(self, hand_card_ids: list[str], talon_card_ids: list[str]) -> list[str]:
        raise NotImplementedError

    def choose_contract(self, legal_levels: list[int], hand, winner_bid) -> tuple[str, str | None, int]:
        """Return (contract_type, trump_suit_name_or_None, level)."""
        raise NotImplementedError

    def choose_whist_action(self, legal_actions: list[dict]) -> str:
        raise NotImplementedError

    def choose_card(self, legal_cards) -> str:
        raise NotImplementedError


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


class PlayerAlice(WeightedRandomPlayer):
    """Alice: AGGRESSIVE Preferans player aiming for HIGH scores.

    Key strategies (iteration 34):
    - Iter33 (iter10) results: +372 across 18 games (20.7/game avg, 3rd).
      Carol dominated at 672/17 (39.5 avg). Declaring excellent: G10(+120
      betl), G11(+100), G15(+60), G19(+80), G20(+100) = +460. One whist
      loss: G18(-100) called twice vs Neural's game. 12/18 games at 0 —
      too passive.
    - G18(-100): 1A [[D,J,10,7],[A,10,8],[J,8,7],[]] vs Neural's game 3.
      Called twice (both defenders called). 3 jacks, est ~0.6-0.8. Even
      if rate fires once, calling a second time doubles risk. NEW: track
      whist call count; reduce rate by 50% on second call.
    - 12/18 at 0: Too many passes. Need bolder bidding. (1) 0-ace hands
      with 5-card K-headed suit + 5+ high cards should bid. G1 hand
      [[K,D,10,9,8],[K,D,J],[8],[7]] = 5 high cards, strong shape. (2)
      Bump 0-ace high-card dense rate 48%→55%. (3) Bump marginal shaped
      rate 90%→95%.
    - Whisting rates: only 1 loss (G18) vs 12 games at 0. Bump 1-ace
      rates: est>=1.0: 40→45%, est>=0.7: 18→22%. Bump 0-ace rates:
      est>=1.5: 48→55%, est>=1.0: 35→40%.
    """

    def __init__(self, seed: int | None = None):
        super().__init__("Alice", seed=seed,
                         w_pass=45, w_game=45, w_in_hand=5, w_betl=1, w_sans=1)
        self._cards_played = 0
        self._total_hand_size = 10
        self._is_declarer = False
        self._trump_suit_val = None   # suit enum value when we're declarer
        self._highest_bid_seen = 0    # track auction escalation
        self._betl_intent = False     # True when bidding in_hand with betl in mind
        self._whist_call_count = 0    # how many times we called whist this round

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

        # Count trump tricks
        for c in trump_cards:
            if c.rank == 8:  # Ace
                tricks += 1.0
            elif c.rank == 7:  # King
                if len(trump_cards) >= 3:
                    tricks += 0.85
                elif len(trump_cards) >= 2:
                    tricks += 0.55
                else:
                    tricks += 0.2
            elif c.rank >= 5 and len(trump_cards) >= 4:  # J/Q with 4+ trumps
                tricks += 0.5
            elif c.rank >= 5 and len(trump_cards) >= 3:  # J/Q with 3 trumps (e.g. AQJ)
                tricks += 0.25
            elif c.rank >= 3 and len(trump_cards) >= 5:  # low trump with 5+ length
                tricks += 0.35

        # Long trump suit bonus (extra trumps can ruff)
        if len(trump_cards) >= 5:
            tricks += (len(trump_cards) - 4) * 0.7
        elif len(trump_cards) >= 4:
            tricks += 0.3

        # Side suit aces
        for suit, cards in groups.items():
            if suit == trump_suit:
                continue
            for c in cards:
                if c.rank == 8:  # Ace
                    tricks += 0.9
                elif c.rank == 7 and len(cards) >= 2:  # King with guard
                    tricks += 0.4

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
            for c in cards:
                if c.rank == 8:  # Ace
                    # Ace in trump still good but slightly less reliable
                    tricks += 0.60 if in_trump else 0.85
                elif c.rank == 7:  # King
                    if in_trump:
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

        # Penalty for multiple unsupported queens — G5 iter19: 3 queens scattered
        # across suits without aces contributed nothing, inflated est ~1.0-1.2.
        # Queens can't beat K/A as whister; same penalty as Bob/Carol.
        unsupported_queens = 0
        for suit, cards in groups.items():
            in_trump = (declarer_trump is not None and suit == declarer_trump)
            if in_trump:
                continue
            has_ace = any(c.rank == 8 for c in cards)
            has_queen = any(c.rank == 6 for c in cards)
            if has_queen and not has_ace:
                unsupported_queens += 1
        if unsupported_queens >= 3:
            tricks -= 0.25
        elif unsupported_queens >= 2:
            tricks -= 0.15

        # Bonus for A-K in same non-trump suit: guaranteed ~1.5 tricks together.
        # G5 iter12: Alice had AK hearts but passed whist; AK combo is very strong.
        for suit, cards in groups.items():
            in_trump = (declarer_trump is not None and suit == declarer_trump)
            if in_trump:
                continue
            has_ace = any(c.rank == 8 for c in cards)
            has_king = any(c.rank == 7 for c in cards)
            if has_ace and has_king:
                tricks += 0.25  # Extra bonus on top of individual A/K values

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
                        pass  # AK anchor — no penalty
                    elif has_ace and not has_king:
                        tricks -= 0.40  # Ace-only long suit: ace takes 1 trick but 4+ cards wasted
                        break
                    else:
                        # G12 iter9: [[D,J,10,9,8],[A,J,8],[K,7],[]] — 5-card
                        # suit without A/K, only 1 ace in hand. Junk suit crowds
                        # out useful cards and gets trumped after 0-1 tricks.
                        # Increased penalty when hand has only 1 ace.
                        tricks -= 0.40 if total_aces_check <= 1 else 0.30
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

    # ------------------------------------------------------------------
    # Bidding — hand-strength aware, conservative on high bids
    # ------------------------------------------------------------------

    def bid_intent(self, hand, legal_bids):
        bid_types = {b["bid_type"] for b in legal_bids}

        if bid_types == {"pass"}:
            return {"bid": legal_bids[0], "intent": "forced pass (no other options)"}

        # Reset per-round state on first bid call
        self._cards_played = 0
        self._is_declarer = False
        self._highest_bid_seen = 0
        self._trump_suit_val = None
        self._betl_intent = False
        self._whist_call_count = 0

        # Track auction escalation for whisting decisions
        game_bids = [b for b in legal_bids if b["bid_type"] == "game"]
        if game_bids:
            min_val = min(b.get("value", 2) for b in game_bids)
            self._highest_bid_seen = max(self._highest_bid_seen, min_val - 1)
        if any(b["bid_type"] in ("sans", "betl", "in_hand") for b in legal_bids):
            self._highest_bid_seen = max(self._highest_bid_seen, 5)

        aces = self._count_aces(hand) if hand else 0
        high = self._count_high_cards(hand) if hand else 0

        # Estimate hand strength using best trump suit
        best_suit = None
        est_tricks = 0.0
        if hand:
            best_suit, _ = self._best_trump_suit(hand)
            if best_suit:
                est_tricks = self._hand_strength_for_suit(hand, best_suit)

        if game_bids:
            game_val = game_bids[0].get("value", 2)
            if game_val <= 2:
                # AGGRESSIVE: lower thresholds to declare more often.
                # Talon adds ~1.5 tricks, need 6 to win → need ~4.5 post-exchange.
                groups = self._suit_groups(hand) if hand else {}
                max_suit_len = max((len(cards) for cards in groups.values()), default=0)
                # 3+ aces: always bid — 3 guaranteed tricks + talon is enough
                if aces >= 3:
                    return {"bid": game_bids[0],
                            "intent": f"game 2 — 3+ aces auto-bid (tricks={est_tricks:.1f}, aces={aces})"}
                if est_tricks >= 3.0:
                    return {"bid": game_bids[0],
                            "intent": f"game 2 — strong hand (tricks={est_tricks:.1f}, aces={aces}, high={high})"}
                if aces >= 2 and max_suit_len >= 4:
                    return {"bid": game_bids[0],
                            "intent": f"game 2 — 2+ aces with shape (tricks={est_tricks:.1f}, longest={max_suit_len})"}
                # 1 ace + 5-card suit: strong declaring shape. G2 iter11: Alice
                # had [[A,D,J,9,7],[J,10],[J,9],[7]] — 5 spades with ace, passed.
                # 5-card ace suit + talon = reliable 6 tricks.
                if aces >= 1 and max_suit_len >= 5:
                    return {"bid": game_bids[0],
                            "intent": f"game 2 — ace + 5-card suit (tricks={est_tricks:.1f}, longest={max_suit_len})"}
                # 1 ace + 4-card suit + void: strong shape compensates for fewer trumps.
                # G5 iter16: [[A,D,10,8],[J,10,8,7],[K,10],[]] — 1A + 4 spades + void
                # in clubs, passed. Void = guaranteed ruffing, 4-card suit with ace is solid.
                num_suits = len(groups)
                if aces >= 1 and max_suit_len >= 4 and num_suits <= 3:
                    return {"bid": game_bids[0],
                            "intent": f"game 2 — ace + 4-card suit + void (tricks={est_tricks:.1f}, suits={num_suits})"}
                # 1 ace + 4-card suit + est >= 2.0: strong enough to declare.
                if aces >= 1 and max_suit_len >= 4 and est_tricks >= 2.0:
                    if self.rng.random() < 0.85:
                        return {"bid": game_bids[0],
                                "intent": f"game 2 — ace + 4-card suit strong (tricks={est_tricks:.1f}, longest={max_suit_len})"}
                if aces >= 2 and max_suit_len < 4:
                    # Flat 2-ace hand — bumped 75% → 80%: declaring excellent in
                    # iter10, too many 0-score games.
                    if self.rng.random() < 0.80:
                        return {"bid": game_bids[0],
                                "intent": f"game 2 — 2 aces but flat (tricks={est_tricks:.1f}, longest={max_suit_len})"}
                # Marginal hands: 95% for shaped, 65% for flat.
                # Bumped shaped 90%→95%: declaring wins are excellent (460 from 5 wins iter10).
                # Bumped flat 60%→65%: 12/18 at 0 too passive, need more declaration.
                if est_tricks >= 1.5 and aces >= 1:
                    doubletons = sum(1 for cards in groups.values() if len(cards) == 2)
                    is_flat = max_suit_len <= 4 and doubletons >= 2
                    marginal_rate = 0.65 if is_flat else 0.95
                    if self.rng.random() < marginal_rate:
                        return {"bid": game_bids[0],
                                "intent": f"game 2 — marginal {'flat ' if is_flat else ''}aggressive (tricks={est_tricks:.1f}, aces={aces})"}
                # 0-ace hands with 5-card K-headed suit + many high cards: strong shape.
                # G1 iter10: [[K,D,10,9,8],[K,D,J],[8],[7]] — 0A, 5-card suit with
                # K,D,10,9,8 + 3 hearts KDJ = 5 high cards. Talon adds ~1.5 tricks,
                # and K becomes near-ace when opponents draw aces early.
                if aces == 0 and max_suit_len >= 5 and high >= 5:
                    # Check if longest suit has K
                    best_suit_cards = groups.get(best_suit, [])
                    has_king_in_best = any(c.rank == 7 for c in best_suit_cards) if hand else False
                    if has_king_in_best:
                        if self.rng.random() < 0.65:
                            return {"bid": game_bids[0],
                                    "intent": f"game 2 — 0A K-headed 5+suit (tricks={est_tricks:.1f}, high={high})"}
                # High-card-dense hands without aces: bid with lots of high cards
                # Bumped from 48%→55%: declaring win rate strong, 12/18 at 0 too passive
                if est_tricks >= 1.5 and high >= 4:
                    if self.rng.random() < 0.55:
                        return {"bid": game_bids[0],
                                "intent": f"game 2 — high-card dense (tricks={est_tricks:.1f}, high={high})"}
                intent = f"pass — weak hand for game 2 (tricks={est_tricks:.1f}, aces={aces}, high={high})"
            elif game_val == 3:
                # Game 3: competitive auction — opponents who passed on game 2 are
                # strong enough to bid, so they'll whist aggressively. G4/G6 iter9:
                # Alice outbid Bob then Bob called twice → -60 each.
                # Require est >= 4.5 or (2+ aces AND shape). Plain 2-ace no longer auto-bids.
                groups = self._suit_groups(hand) if hand else {}
                max_suit_len = max((len(cards) for cards in groups.values()), default=0)
                if est_tricks >= 4.5:
                    return {"bid": game_bids[0],
                            "intent": f"game 3 — very strong (tricks={est_tricks:.1f}, aces={aces})"}
                if aces >= 2 and max_suit_len >= 4 and est_tricks >= 4.0:
                    return {"bid": game_bids[0],
                            "intent": f"game 3 — 2+ aces with shape (tricks={est_tricks:.1f}, longest={max_suit_len})"}
                if est_tricks >= 3.5 and aces >= 1 and self.rng.random() < 0.20:
                    return {"bid": game_bids[0],
                            "intent": f"game 3 — aggressive gamble (tricks={est_tricks:.1f}, aces={aces})"}
                intent = f"pass — too weak for game 3 (tricks={est_tricks:.1f}, aces={aces}, high={high})"
            elif game_val == 4:
                # Game 4: only with very strong hands
                if est_tricks >= 5.0 or aces >= 3:
                    return {"bid": game_bids[0],
                            "intent": f"game 4 — very strong (tricks={est_tricks:.1f}, aces={aces})"}
                intent = f"pass — game 4 too risky (tricks={est_tricks:.1f}, aces={aces})"
            else:
                intent = f"pass — game {game_val}+ too risky (tricks={est_tricks:.1f}, aces={aces})"
            pass_bid = next((b for b in legal_bids if b["bid_type"] == "pass"), None)
            if pass_bid:
                return {"bid": pass_bid, "intent": intent}

        # Check for sans/betl/in_hand with hand evaluation
        if hand:
            sans_bids = [b for b in legal_bids if b["bid_type"] == "sans"]
            if sans_bids and self._is_good_sans_hand(hand):
                return {"bid": sans_bids[0], "intent": f"sans — dominant high cards (aces={aces})"}
            # Betl in auction = in-hand betl (no exchange), so use strict check
            betl_bids = [b for b in legal_bids if b["bid_type"] == "betl"]
            if betl_bids and self._is_good_betl_hand_in_hand(hand):
                a = betl_hand_analysis(hand)
                return {"bid": betl_bids[0],
                        "intent": f"betl — zero danger in-hand (safe_suits={a['safe_suits']})"}
            # In-hand bid with betl intent
            in_hand_bids = [b for b in legal_bids if b["bid_type"] == "in_hand"]
            if in_hand_bids and self._is_good_betl_hand_in_hand(hand):
                self._betl_intent = True
                a = betl_hand_analysis(hand)
                return {"bid": in_hand_bids[0],
                        "intent": f"in_hand (betl intent) — zero danger, safe_suits={a['safe_suits']}"}

        pass_bid = next((b for b in legal_bids if b["bid_type"] == "pass"), None)
        if pass_bid:
            return {"bid": pass_bid,
                    "intent": f"pass — no good options (tricks={est_tricks:.1f}, aces={aces}, high={high})"}

        # Fallback
        return super().bid_intent(hand, legal_bids)

    # ------------------------------------------------------------------
    # Exchange — smart discard: keep trump suit + aces, create voids
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

        # Try betl-optimized discard: discard 2 highest-ranked cards,
        # check if resulting 10-card hand is good for betl
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

        # Try to void an entire short off-suit (don't discard Kings or Aces)
        voidable = []
        for s in suit_cards:
            if s == best_suit:
                continue
            cards = suit_cards[s]
            if len(cards) == 2 and all(card_rank(c) < 7 for c in cards):
                total_rank = sum(card_rank(c) for c in cards)
                voidable.append((total_rank, cards))
        if voidable:
            voidable.sort()
            return {"discard": voidable[0][1],
                    "intent": f"void weakest off-suit (trump={best_suit})"}

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
            # Betl score: zero danger is great, fewer dangers = better
            # Base: 100 if zero danger, penalize each danger heavily
            score = 100 - a["danger_count"] * 40
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
            return 80 + aces * 15 + high * 5
        else:
            # Suit contract
            strength = self._hand_strength_for_suit(hand, trump_suit)
            groups = self._suit_groups(hand)
            trump_len = len(groups.get(trump_suit, []))
            # Scale: 6 tricks needed. strength * 15 gives a good range.
            # Bonus for trump length, penalty for suit cost
            cost_penalty = (_SUIT_BID_VALUE.get(trump_suit, 2) - 2) * 2
            return strength * 15 + trump_len * 3 - cost_penalty

    def _evaluate_12_card_contracts(self, hand_card_ids, talon_card_ids, winner_bid):
        """Evaluate all 66 discard combos × all legal contracts.
        Returns {"discard": [id, id], "contract": (type, trump, level)}."""
        from itertools import combinations

        all_ids = hand_card_ids + talon_card_ids
        min_bid = winner_bid.effective_value if winner_bid else 0

        # Pre-check: skip betl evaluation if pool has 2+ aces.
        # Can only discard 2 cards, so at least 1 ace remains → betl is suicide.
        # G2/G12/G13 iter25: -360 from betl with ace-heavy hands.
        pool_aces = sum(1 for cid in all_ids if cid.startswith("A_"))
        skip_betl = pool_aces >= 2

        best_score = -999
        best_discard = None
        best_contract = None

        for pair in combinations(range(len(all_ids)), 2):
            discard = [all_ids[i] for i in pair]
            remaining_ids = [cid for cid in all_ids if cid not in discard]
            hand = _ids_to_cards(remaining_ids)

            # Evaluate betl (skip if pool has too many aces)
            if not skip_betl:
                betl_sc = self._score_hand_for_contract(hand, "betl")
                if betl_sc > best_score:
                    best_score = betl_sc
                    best_discard = discard
                    best_contract = ("betl", None, 6)

            # Evaluate sans (level 7, needs min_bid <= 7)
            if min_bid <= 7:
                sans_sc = self._score_hand_for_contract(hand, "sans")
                if sans_sc > best_score:
                    best_score = sans_sc
                    best_discard = discard
                    best_contract = ("sans", None, 7)

            # Evaluate each suit at minimum legal level
            for suit, suit_level in _SUIT_BID_VALUE.items():
                if suit_level < min_bid:
                    continue
                level = max(suit_level, min_bid)
                sc = self._score_hand_for_contract(hand, "suit", trump_suit=suit)
                if sc > best_score:
                    best_score = sc
                    best_discard = discard
                    best_contract = ("suit", SUIT_NAMES[suit], level)

        return {"discard": best_discard, "contract": best_contract}

    # ------------------------------------------------------------------
    # Contract — use pre-evaluated choice or fall back to heuristic
    # ------------------------------------------------------------------

    def bid_decision(self, hand, legal_levels, winner_bid):
        """Pick safest contract. Prefer cheaper suits (lower game_value) when tied."""
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
        # Post-exchange betl (aggressive): zero danger + extra low-card heuristics
        if 6 in legal_levels:
            a = betl_hand_analysis(hand)
            # Zero danger — always bid betl
            if a["danger_count"] == 0:
                return {"contract_type": "betl", "trump": None, "level": 6,
                        "intent": f"betl — zero danger (safe_suits={a['safe_suits']})"}
            # All low cards: max rank ≤ Jack (5), no Aces → every card has 3+
            # opponent cards above it. Very safe for betl.
            if not a["has_ace"] and a["max_rank"] <= 5:
                return {"contract_type": "betl", "trump": None, "level": 6,
                        "intent": f"betl — all low (max_rank={a['max_rank']}, no aces)"}
            # Mostly low + spread: max rank ≤ Queen (6), no Aces, no long suits
            # (≤3). Spread shape reduces chance of being stuck in any one suit.
            if not a["has_ace"] and a["max_rank"] <= 6 and a["max_suit_len"] <= 3:
                return {"contract_type": "betl", "trump": None, "level": 6,
                        "intent": f"betl — low+spread (max_rank={a['max_rank']}, longest={a['max_suit_len']})"}

        if 7 in legal_levels and self._is_good_sans_hand(hand):
            return {"contract_type": "sans", "trump": None, "level": 7,
                    "intent": "sans — dominant high cards"}

        min_bid = winner_bid.effective_value if winner_bid else 0
        suit_bid = {Suit.SPADES: 2, Suit.DIAMONDS: 3, Suit.HEARTS: 4, Suit.CLUBS: 5}
        groups = self._suit_groups(hand)

        best_suit = None
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
    # Whisting — hand-strength aware whisting
    # ------------------------------------------------------------------

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
        action_types = [a["action"] for a in legal_actions]

        if "start_game" in action_types:
            return {"action": "start_game", "intent": "start game"}

        if "follow" in action_types:
            aces = self._count_aces(hand) if hand else 0
            est_whist_tricks = self._estimate_tricks_as_whister(hand, trump_suit) if hand else 0.0
            is_high_level = self._highest_bid_seen >= 3
            # Track repeat calls: G18 iter10 called twice → -100. Second call
            # means both defenders are in, doubling the risk. Halve effective rate.
            is_repeat_call = self._whist_call_count > 0

            # HARD PASS: Sans contracts — declarer has 3+ aces + high cards.
            # G4 iter7: Alice called sans with [[A,K,8,7],[D,10,9],[9,8],[9]] —
            # only AK in one suit, lost -40. Sans declarers dominate all suits.
            # Require 2+ aces minimum; even then only whist with strong est.
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
                return {"action": "pass",
                        "intent": f"pass — sans contract, need 2+ aces ({aces}A, {est_whist_tricks:.1f} tricks)"}

            # HARD PASS: 4+ cards in declarer's trump suit = dead hand.
            # G10 iter12: [[J,9,8,7],[A,9,8],[J,10,9],[]] — 4 spades in
            # declarer's trump, 1 ace couldn't save it, lost -100.
            if hand and trump_suit is not None:
                trump_count = sum(1 for c in hand if c.suit == trump_suit)
                if trump_count >= 4:
                    return {"action": "pass",
                            "intent": f"pass — {trump_count} cards in declarer's trump = dead hand ({est_whist_tricks:.1f} tricks)"}

            # HARD PASS: 3+ unsupported kings = unreliable hand.
            # Kings without aces in the same suit rarely convert against
            # declarer's trump. Bob/Carol both have this gate.
            if hand:
                groups = self._suit_groups(hand)
                unsup_kings = 0
                for suit, cards in groups.items():
                    has_a = any(c.rank == 8 for c in cards)
                    has_k = any(c.rank == 7 for c in cards)
                    if has_k and not has_a:
                        unsup_kings += 1
                if unsup_kings >= 3:
                    return {"action": "pass",
                            "intent": f"pass — {unsup_kings} unsupported kings = unreliable ({est_whist_tricks:.1f} tricks)"}

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
                two_ace_q_penalty = False
                if hand:
                    unsup_q = 0
                    for suit, cards in groups.items():
                        if trump_suit is not None and suit == trump_suit:
                            continue
                        has_a = any(c.rank == 8 for c in cards)
                        has_q = any(c.rank == 6 for c in cards)
                        if has_q and not has_a:
                            unsup_q += 1
                    if unsup_q >= 2:
                        two_ace_q_penalty = True

                if high <= 3:
                    junk_rate = 0.45 if est_whist_tricks >= 1.5 else 0.30
                    if is_repeat_call:
                        junk_rate *= 0.5
                    if self.rng.random() < junk_rate:
                        self._whist_call_count += 1
                        return {"action": "follow",
                                "intent": f"follow — 2A junk hand {int(junk_rate*100)}% ({est_whist_tricks:.1f} tricks, high={high})"}
                    return {"action": "pass",
                            "intent": f"pass — 2A junk hand ({est_whist_tricks:.1f} tricks, high={high})"}
                if est_whist_tricks >= 2.0 or max_suit_len >= 4:
                    rate = 0.79 if two_ace_q_penalty else 0.94
                    if is_repeat_call:
                        rate *= 0.5
                    if self.rng.random() < rate:
                        self._whist_call_count += 1
                        return {"action": "follow",
                                "intent": f"follow — {aces} aces, {est_whist_tricks:.1f} est tricks ({int(rate*100)}%{', Q-penalty' if two_ace_q_penalty else ''})"}
                    return {"action": "pass",
                            "intent": f"pass — 2 aces dodged ({est_whist_tricks:.1f} tricks{', Q-penalty' if two_ace_q_penalty else ''})"}
                flat_rate = 0.52 if two_ace_q_penalty else 0.68
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
                has_ak_combo = False
                if hand:
                    groups = self._suit_groups(hand)
                    for suit, cards in groups.items():
                        if trump_suit is not None and suit == trump_suit:
                            continue
                        has_a = any(c.rank == 8 for c in cards)
                        has_k = any(c.rank == 7 for c in cards)
                        if has_a and has_k:
                            has_ak_combo = True
                            break

                # Queen-scatter penalty: G10 iter28 [[A,D,8],[K,D,8],[D,J,10],[7]]
                # — 3 queens scattered across suits + 1A. Queens inflate est but
                # can't beat K/A as whister. Called twice and lost -60.
                queen_penalty = False
                if hand:
                    groups = self._suit_groups(hand)
                    unsup_queens = 0
                    for suit, cards in groups.items():
                        if trump_suit is not None and suit == trump_suit:
                            continue
                        has_a = any(c.rank == 8 for c in cards)
                        has_q = any(c.rank == 6 for c in cards)
                        if has_q and not has_a:
                            unsup_queens += 1
                    if unsup_queens >= 2:
                        queen_penalty = True

                if is_high_level:
                    rate = 0.45 if est_whist_tricks >= 1.5 else 0.20
                else:
                    if est_whist_tricks >= 2.0:
                        rate = 1.0   # Very strong 1-ace hand — always whist
                    elif est_whist_tricks >= 1.5:
                        rate = 0.97
                    elif est_whist_tricks >= 1.0:
                        rate = 0.45  # Bumped from 40%: only 1 whist loss iter10, 12/18 at 0
                    elif est_whist_tricks >= 0.7:
                        rate = 0.22  # Bumped from 18%: more income from marginal hands
                    else:
                        rate = 0.08  # Bumped from 6%: more speculative whisting
                    if has_ak_combo and not queen_penalty:
                        rate = min(rate + 0.20, 1.0)
                    if hand and trump_suit is not None:
                        trump_count = sum(1 for c in hand if c.suit == trump_suit)
                        if trump_count == 0:
                            rate = min(rate + 0.10, 1.0)
                    if queen_penalty:
                        rate = max(rate - 0.15, 0.05)
                # G18 iter10: called twice → -100. Second call doubles risk.
                if is_repeat_call:
                    rate *= 0.5
                if self.rng.random() < rate:
                    self._whist_call_count += 1
                    return {"action": "follow",
                            "intent": f"follow — 1 ace, {int(rate*100)}% rate ({est_whist_tricks:.1f} tricks{', Q-penalty' if queen_penalty else ''})"}
                return {"action": "pass",
                        "intent": f"pass — 1 ace but rolled >{int(rate*100)}% ({est_whist_tricks:.1f} tricks{', Q-penalty' if queen_penalty else ''})"}

            # 0 aces: whist based on estimated trick potential — controlled aggression
            # Iter10: zero 0-ace whist losses, 12/18 at 0. Bump rates for more income.
            if is_high_level:
                rate = 0.28 if est_whist_tricks >= 1.5 else 0.0
            else:
                if est_whist_tricks >= 1.5:
                    rate = 0.55  # Bumped from 48%: strong 0-ace hand
                elif est_whist_tricks >= 1.0:
                    rate = 0.40  # Bumped from 35%: decent kings/queens
                elif est_whist_tricks >= 0.5:
                    rate = 0.18  # Bumped from 15%
                else:
                    rate = 0.06  # Bumped from 5%
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

    def choose_card(self, legal_cards):
        """Strategic card play with different logic for declarer vs whister."""
        if len(legal_cards) == 1:
            self._cards_played += 1
            return legal_cards[0].id

        contract_type = getattr(self, '_contract_type', None)

        # Betl-specific card play
        if contract_type == "betl":
            card = self._betl_choose_card(legal_cards)
            self._cards_played += 1
            return card.id

        hand_size = self._total_hand_size - self._cards_played
        is_leading = len(legal_cards) == hand_size
        suits_in_legal = {c.suit for c in legal_cards}
        must_follow = len(suits_in_legal) == 1 and len(legal_cards) < hand_size

        if is_leading:
            if self._is_declarer and self._trump_suit_val is not None:
                card = self._declarer_lead(legal_cards)
            else:
                card = self._whister_lead(legal_cards)
            self._cards_played += 1
            return card.id

        elif must_follow:
            by_rank_desc = sorted(legal_cards, key=lambda c: c.rank, reverse=True)
            # If we have the ace, play it (guaranteed winner)
            if by_rank_desc[0].rank == 8:
                self._cards_played += 1
                return by_rank_desc[0].id
            # If best card is below Queen, unlikely to win — play lowest to save
            if by_rank_desc[0].rank < 6:
                self._cards_played += 1
                return min(legal_cards, key=lambda c: c.rank).id
            # As whister: duck with K/Q early (first 3 tricks) — wait for aces
            # to be played, then our K/Q becomes the winner in later tricks.
            if not self._is_declarer and self._cards_played < 3 and by_rank_desc[0].rank == 7:
                # Hold the King — play lowest instead
                self._cards_played += 1
                return min(legal_cards, key=lambda c: c.rank).id
            # Play highest to try to win
            self._cards_played += 1
            return by_rank_desc[0].id

        else:
            # Can't follow suit
            if self._is_declarer and self._trump_suit_val is not None:
                # As declarer: ruff with lowest trump if possible
                trumps = [c for c in legal_cards if c.suit == self._trump_suit_val]
                if trumps:
                    card = min(trumps, key=lambda c: c.rank)
                    self._cards_played += 1
                    return card.id
            # As whister: try to ruff with lowest trump when can't follow suit.
            # Even as non-declarer, trumping wins tricks against side-suit leads.
            if not self._is_declarer and self._trump_suit_val is not None:
                trumps = [c for c in legal_cards if c.suit == self._trump_suit_val]
                if trumps:
                    card = min(trumps, key=lambda c: c.rank)
                    self._cards_played += 1
                    return card.id
            # If forced to trump (all legal cards are one suit = trumps), play lowest
            if len(suits_in_legal) == 1:
                card = min(legal_cards, key=lambda c: c.rank)
                self._cards_played += 1
                return card.id
            # Discard lowest from longest off-suit to preserve short suits
            groups = self._suit_groups(legal_cards)
            longest_suit = max(groups.keys(), key=lambda s: len(groups[s]))
            worst_card = groups[longest_suit][-1]
            self._cards_played += 1
            return worst_card.id

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
            # Lead highest card — force declarer to play over it or duck
            return max(legal_cards, key=lambda c: c.rank)

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
        """Declarer leading: draw trumps first, then cash side aces."""
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

        # Phase 3: Lead remaining trumps
        if trump in groups:
            return groups[trump][0]

        # Phase 4: Lead highest from longest off-suit (length winners)
        non_trump = {s: cards for s, cards in groups.items() if s != trump}
        if non_trump:
            longest = max(non_trump.keys(), key=lambda s: len(non_trump[s]))
            return non_trump[longest][0]

        return max(legal_cards, key=lambda c: c.rank)

    def _whister_lead(self, legal_cards):
        """Whister leading: lead aces from A-K suits first, then shortest suit aces."""
        groups = self._suit_groups(legal_cards)

        # Lead aces first — guaranteed trick winners
        # Prefer aces from A-K combo suits: cash the ace, then king is promoted.
        # G6 iter13: 2 aces but lost -36 — need to cash aces from strongest suits first.
        aces = [c for c in legal_cards if c.rank == 8]
        if aces:
            # Priority: A-K combo suit (ace cashes, king promoted next) > shortest suit
            def ace_priority(c):
                suit_cards = groups.get(c.suit, [])
                has_king = any(x.rank == 7 for x in suit_cards)
                # Lower score = higher priority: A-K combo gets -100 bonus
                return (-100 if has_king else 0) + len(suit_cards)
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

    Key strategies (iteration 40):
    - Iter 10 results: Bob=+208 across 11 games. ZERO negatives (perfect
      cautious goal again). 3 declaration wins: G3(+60), G13(+100), G16(+40).
      1 whist win: G2(+8). But 7/11 games at 0 — still too passive on whisting.
    - G7(0, missed whist): 1A [[A,10,9,7],[10,9,8],[10,8],[K]], Carol scored
      100. 4-card A-high suit, est ~1.2. Missed at 32% rate for est>=1.0.
    - G14(0, missed whist): 1A [[A,D,10],[K,J,8],[D,8],[J,8]], Carol scored
      60. est ~1.2-1.4. Missed at 32% rate. Two consecutive iterations of
      zero whist losses proves 1A est>=1.0 rate too conservative.
    - Bump 1A rates: est>=2.0 82→86%, est>=1.5 62→68%, est>=1.0 32→40%.
    - Bump 2A rates: strong 92→94%, weak 78→82%. High-level hedge 32→36%.
    - Bump 0A rate 22→26%. 1A high-level 16→20%.
    - Whist card play: as whister when forced to trump, play highest trump
      to maximize trick-winning chance (was playing lowest).
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

        # Trump tricks
        for c in trump_cards:
            if c.rank == 8:  # Ace
                tricks += 1.0
            elif c.rank == 7:  # King
                tricks += 0.8 if len(trump_cards) >= 3 else 0.45
            elif c.rank >= 5 and len(trump_cards) >= 4:  # J/Q with 4+ trumps
                tricks += 0.45
            elif len(trump_cards) >= 5:  # low trump with 5+ length
                tricks += 0.3

        # Trump AK combo bonus: King is nearly guaranteed when you hold Ace
        has_trump_ace = any(c.rank == 8 for c in trump_cards)
        has_trump_king = any(c.rank == 7 for c in trump_cards)
        if has_trump_ace and has_trump_king:
            tricks += 0.2

        # Long trump bonus (ruffing potential)
        if len(trump_cards) >= 5:
            tricks += (len(trump_cards) - 4) * 0.7
        elif len(trump_cards) >= 4:
            tricks += 0.3

        # Side suit aces
        for suit, cards in groups.items():
            if suit == trump_suit:
                continue
            for c in cards:
                if c.rank == 8:
                    tricks += 0.9
                elif c.rank == 7 and len(cards) >= 2:  # guarded King
                    tricks += 0.4

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

        # Reset per-round state on first bid call
        self._cards_played = 0
        self._is_declarer = False
        self._highest_bid_seen = 0
        self._betl_intent = False
        self._i_bid_in_auction = False

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
                str_threshold = 4.0 if (aces >= 2 or has_trump_ace) else 4.2
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
                elif aces >= 2 and strength >= 3.0 and self.rng.random() < 0.28:
                    self._i_bid_in_auction = True
                    return {"bid": game_bids[0],
                            "intent": f"game 2 — flat 2-ace borderline (strength {strength:.1f}, longest={max_suit_len})"}
                # Borderline hands (2.8+): bid 30% with 1+ ace — G5 iter10 missed
                # AKQ8 spades+side K (strength 2.95) that should have been bid
                elif strength >= 2.8 and aces >= 1 and self.rng.random() < 0.40:
                    self._i_bid_in_auction = True
                    return {"bid": game_bids[0],
                            "intent": f"game 2 — borderline strength {strength:.1f}, 40% roll (aces={aces}, longest={max_suit_len})"}
                else:
                    intent = f"pass — strength {strength:.1f} below 2.8 for cautious game 2 (aces={aces}, longest={max_suit_len})"
            else:
                # Game 3+: never bid — too risky for cautious Bob
                intent = f"pass — game {game_val}+ too risky for cautious style (aces={aces})"
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

    def _evaluate_12_card_contracts(self, hand_card_ids, talon_card_ids, winner_bid):
        """Evaluate all 66 discard combos × all legal contracts."""
        from itertools import combinations

        all_ids = hand_card_ids + talon_card_ids
        min_bid = winner_bid.effective_value if winner_bid else 0

        # Skip betl evaluation entirely if pool has 2+ aces — can only discard 2,
        # so at least 1 ace remains → betl is suicide. G18/G19: -240 from betl
        # with ace-heavy hands.
        pool_aces = sum(1 for cid in all_ids if cid.startswith("A_"))
        skip_betl = pool_aces >= 2

        best_score = -999
        best_discard = None
        best_contract = None

        for pair in combinations(range(len(all_ids)), 2):
            discard = [all_ids[i] for i in pair]
            remaining_ids = [cid for cid in all_ids if cid not in discard]
            hand = _ids_to_cards(remaining_ids)

            if not skip_betl:
                betl_sc = self._score_hand_for_contract(hand, "betl")
                if betl_sc > best_score:
                    best_score = betl_sc
                    best_discard = discard
                    best_contract = ("betl", None, 6)

            if min_bid <= 7:
                sans_sc = self._score_hand_for_contract(hand, "sans")
                if sans_sc > best_score:
                    best_score = sans_sc
                    best_discard = discard
                    best_contract = ("sans", None, 7)

            for suit, suit_level in _SUIT_BID_VALUE.items():
                if suit_level < min_bid:
                    continue
                level = max(suit_level, min_bid)
                sc = self._score_hand_for_contract(hand, "suit", trump_suit=suit)
                if sc > best_score:
                    best_score = sc
                    best_discard = discard
                    best_contract = ("suit", SUIT_NAMES[suit], level)

        return {"discard": best_discard, "contract": best_contract}

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
        # G8 iter8: 0 trumps, est 1.15, whisted → -90. Penalty increased -0.35→-0.45.
        if trump_suit:
            trump_count = sum(1 for c in hand if c.suit == trump_suit)
            if trump_count <= 1:
                tricks -= 0.45

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
            aces = self._count_aces(hand) if hand else 0
            est_tricks = self._estimate_whist_tricks(hand, trump_suit) if hand else 0.0
            is_high_level = self._highest_bid_seen >= 3

            # Hard gate: 4+ cards in declarer's trump suit = always pass.
            # These hands are dead weight — declarer has trump length and
            # will extract all your trumps. Alice G10 iter12 lost -100 this way.
            if trump_suit and hand:
                trump_count = sum(1 for c in hand if c.suit == trump_suit)
                if trump_count >= 4:
                    return {"action": "pass",
                            "intent": f"pass — {trump_count} cards in declarer's trump, dead weight"}

            # Hard gate: 3+ unsupported kings = always pass.
            # Singleton kings are near-worthless (0.15 each), easily trumped.
            # G7 iter17: 1A + K(4-card) + K(singleton) + K(singleton) = 3
            # unsupported kings. Even with 1 ace, these hands can't take 2 tricks.
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
            # and get outbid, declarer is proven strong → reduce rates by 15%.
            outbid_penalty = 0.25 if self._i_bid_in_auction else 0.0
            if aces >= 2:
                if is_high_level and est_tricks < 2.5:
                    # High-level + weak support: 36% hedge (was 32%, zero losses iters 1-10)
                    rate = max(0.36 - outbid_penalty, 0.12)
                    if self.rng.random() < rate:
                        return {"action": "follow",
                                "intent": f"follow — {aces} aces, high-level {int(rate*100)}% hedge ({est_tricks:.1f} est tricks)"}
                    return {"action": "pass",
                            "intent": f"pass — {aces} aces, high-level cautious hedge ({est_tricks:.1f} est tricks)"}
                if est_tricks >= 2.0:
                    # Bumped: 92→94 / 78→82. Zero 2A whist losses iters 1-10.
                    base_strong_2a = 0.82 if is_high_level else 0.94
                    rate = max(base_strong_2a - outbid_penalty, 0.55)
                    if self.rng.random() < rate:
                        return {"action": "follow",
                                "intent": f"follow — {aces} aces, strong est {int(rate*100)}% whist ({est_tricks:.1f} est tricks)"}
                    return {"action": "pass",
                            "intent": f"pass — {aces} aces, strong est safety dodge ({est_tricks:.1f} est tricks)"}
                # Weak 2-ace (est < 2.0): bumped 78→82, 64→68.
                # Zero 2A whist losses iters 1-10.
                base_weak_2a = 0.68 if is_high_level else 0.82
                rate = max(base_weak_2a - outbid_penalty, 0.35)
                if self.rng.random() < rate:
                    return {"action": "follow",
                            "intent": f"follow — {aces} aces, weak est {int(rate*100)}% ({est_tricks:.1f} est tricks)"}
                return {"action": "pass",
                        "intent": f"pass — {aces} aces, weak est cautious ({est_tricks:.1f} est tricks)"}

            # 1 ace: bumped rates. Zero 1A whist losses iters 8-10.
            # Rates: est>=2.0 82→86, est>=1.5 62→68, est>=1.0 32→40.
            outbid_1a = 0.18 if self._i_bid_in_auction else 0.0
            if aces == 1:
                if is_high_level:
                    # Allow strong 1-ace hands to whist game 3+ at 20% (was 16%)
                    rate = max(0.20 - outbid_1a, 0.0) if est_tricks >= 2.0 else 0.0
                elif est_tricks >= 2.0:
                    rate = max(0.86 - outbid_1a, 0.48)
                elif est_tricks >= 1.5:
                    rate = max(0.68 - outbid_1a, 0.35)
                elif est_tricks >= 1.0:
                    rate = max(0.40 - outbid_1a, 0.18)
                else:
                    rate = 0.0   # Too weak, pass
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
                            rate = min(rate + 0.15, 0.85)
                            break
                # Void-suit boost: having a void = ruffing potential. Add 0.12.
                # G4 iter22: 1A + void [[A,J,9,7],[J,10,9,8],[D,10],[]] missed at ~37%.
                # Void hands are consistently profitable — bump 0.10 → 0.12.
                if rate > 0 and hand and trump_suit:
                    all_suits = {1, 2, 3, 4}
                    suits_held = {c.suit for c in hand}
                    void_suits = all_suits - suits_held - {trump_suit}
                    if void_suits:
                        rate = min(rate + 0.12, 0.85)
                if rate > 0 and self.rng.random() < rate:
                    return {"action": "follow",
                            "intent": f"follow — 1 ace, {int(rate*100)}% rate ({est_tricks:.1f} tricks)"}
                return {"action": "pass",
                        "intent": f"pass — 1 ace, cautious ({est_tricks:.1f} tricks)"}

            # 0 aces: small chance on game 2 with very strong kings.
            # Bumped 22% → 26%: zero 0A whist losses iters 8-10.
            if not is_high_level and est_tricks >= 2.0 and self.rng.random() < 0.26:
                return {"action": "follow",
                        "intent": f"follow — 0 aces, 15% rate on strong kings ({est_tricks:.1f} tricks)"}
            return {"action": "pass",
                    "intent": f"pass — 0 aces, cautious ({est_tricks:.1f} tricks)"}

        if "pass" in action_types:
            return {"action": "pass", "intent": "pass — no follow option"}
        return {"action": action_types[0], "intent": f"fallback — {action_types[0]}"}

    # ------------------------------------------------------------------
    # Card play — strategic with declarer/whister awareness
    # ------------------------------------------------------------------

    def choose_card(self, legal_cards):
        """Strategic card play with different logic for declarer vs whister."""
        if len(legal_cards) == 1:
            self._cards_played += 1
            return legal_cards[0].id

        contract_type = getattr(self, '_contract_type', None)

        # Betl-specific card play
        if contract_type == "betl":
            card = self._betl_choose_card(legal_cards)
            self._cards_played += 1
            return card.id

        hand_size = self._total_hand_size - self._cards_played
        is_leading = len(legal_cards) == hand_size
        suits_in_legal = {c.suit for c in legal_cards}
        must_follow = len(suits_in_legal) == 1 and len(legal_cards) < hand_size

        if is_leading:
            if self._is_declarer and self._trump_suit_val is not None:
                card = self._declarer_lead(legal_cards)
            else:
                card = self._whister_lead(legal_cards)
            self._cards_played += 1
            return card.id

        elif must_follow:
            by_rank_desc = sorted(legal_cards, key=lambda c: c.rank, reverse=True)
            # If we have the ace, play it (guaranteed winner in suit)
            if by_rank_desc[0].rank == 8:
                self._cards_played += 1
                return by_rank_desc[0].id
            # King: as whister, duck in early tricks (first 3) to let ace drop first
            # Then play King later when it becomes a winner
            if by_rank_desc[0].rank == 7:
                if not self._is_declarer and self._cards_played < 3 and len(legal_cards) >= 2:
                    # Duck with lowest — save King for after aces are played
                    self._cards_played += 1
                    return min(legal_cards, key=lambda c: c.rank).id
                if len(legal_cards) >= 2:
                    self._cards_played += 1
                    return by_rank_desc[0].id
                # Singleton king — must play
                self._cards_played += 1
                return by_rank_desc[0].id
            # If best card is below Queen, unlikely to win — play lowest to save
            if by_rank_desc[0].rank < 6:
                self._cards_played += 1
                return min(legal_cards, key=lambda c: c.rank).id
            # Queen: play if 2+ cards and late game; duck early as whister
            if by_rank_desc[0].rank == 6:
                if not self._is_declarer and self._cards_played < 3 and len(legal_cards) >= 2:
                    self._cards_played += 1
                    return min(legal_cards, key=lambda c: c.rank).id
                if len(legal_cards) >= 2:
                    self._cards_played += 1
                    return by_rank_desc[0].id
            # Default: play lowest to conserve
            self._cards_played += 1
            return min(legal_cards, key=lambda c: c.rank).id

        else:
            # Can't follow suit — engine enforces: must trump if possible
            if self._is_declarer and self._trump_suit_val is not None:
                # As declarer: ruff with lowest trump if possible
                trumps = [c for c in legal_cards if c.suit == self._trump_suit_val]
                if trumps:
                    card = min(trumps, key=lambda c: c.rank)
                    self._cards_played += 1
                    return card.id
            # As whister or if no trump: discard lowest card overall
            # (engine already filtered to only legal cards — trump or discard)
            # If forced to trump (all legal cards are trump), play highest trump
            # to maximize trick-winning chance (G10: whisted betl but got 0)
            if len(suits_in_legal) == 1:
                card = max(legal_cards, key=lambda c: c.rank) if not self._is_declarer else min(legal_cards, key=lambda c: c.rank)
                self._cards_played += 1
                return card.id
            # Discard lowest from longest off-suit to preserve short suits
            groups = self._suit_groups(legal_cards)
            longest_suit = max(groups.keys(), key=lambda s: len(groups[s]))
            worst_card = groups[longest_suit][-1]
            self._cards_played += 1
            return worst_card.id

    _betl_choose_card = PlayerAlice._betl_choose_card
    _betl_declarer_play = PlayerAlice._betl_declarer_play
    _betl_defender_play = PlayerAlice._betl_defender_play

    def _declarer_lead(self, legal_cards):
        """Declarer leading: draw trumps first, then cash side aces."""
        groups = self._suit_groups(legal_cards)
        trump = self._trump_suit_val

        # Phase 1: Lead high trumps to draw out opponent trumps
        if trump in groups:
            trump_cards = groups[trump]
            for c in trump_cards:
                if c.rank == 8:  # Ace of trump first
                    return c
            if trump_cards[0].rank >= 6:  # King/Queen to draw opponents
                return trump_cards[0]

        # Phase 2: Cash side-suit aces (shortest suit first to preserve long suits)
        aces = [c for c in legal_cards if c.rank == 8 and c.suit != trump]
        if aces:
            aces.sort(key=lambda c: len(groups.get(c.suit, [])))
            return aces[0]

        # Phase 3: Lead remaining trumps
        if trump in groups:
            return groups[trump][0]

        # Phase 4: Lead from longest off-suit to develop length winners
        non_trump = {s: cards for s, cards in groups.items() if s != trump}
        if non_trump:
            # Prefer longest suit; break ties by highest top card
            longest = max(non_trump.keys(),
                          key=lambda s: (len(non_trump[s]), non_trump[s][0].rank))
            return non_trump[longest][0]

        return max(legal_cards, key=lambda c: c.rank)

    def _whister_lead(self, legal_cards):
        """Whister leading: lead aces from A-K combo suits first, then shortest."""
        groups = self._suit_groups(legal_cards)

        # Lead aces first — guaranteed trick winners
        # Prefer aces from A-K combo suits (cash ace, king promoted immediately)
        # Then prefer aces from shortest suit to void it quickly
        aces = [c for c in legal_cards if c.rank == 8]
        if aces:
            ak_aces = [a for a in aces if any(c.rank == 7 and c.suit == a.suit for c in legal_cards)]
            if ak_aces:
                return ak_aces[0]
            aces.sort(key=lambda c: len(groups.get(c.suit, [])))
            return aces[0]

        # Lead kings from longest suit — more supporting cards means
        # higher chance the ace has already been played or will be ducked
        kings = [c for c in legal_cards if c.rank == 7]
        if kings:
            kings.sort(key=lambda c: len(groups.get(c.suit, [])), reverse=True)
            return kings[0]

        # Lead highest from shortest suit to try to void it
        shortest_suit = min(groups.keys(), key=lambda s: len(groups[s]))
        return groups[shortest_suit][0]


class PlayerCarol(WeightedRandomPlayer):
    """Carol: PRAGMATIC Preferans player — calculated risks for best EV.

    Key strategies (iteration 32):
    - Iter31 (iter10) results: +672 across 17 games (1st place! 39.5/game avg).
      Declaring: 9/9 wins (+640): G2(+40), G5(+40), G6(+140 sans), G7(+100),
      G8(+60), G9(+60), G12(+100), G14(+60), G17(+40). ZERO declaring losses.
      Whist: G1(+12), G18(+20) = +32. 8/17 games at 0. Zero whist losses.
    - Declaring is flawless — no changes needed.
    - G11(0, missed whist): [[A,D,10,8],[D,J],[J,8],[9,8]] — 1A, est ~0.8.
      30% rate rolled high. 2 scattered queens + 2 jacks. Correctly marginal.
    - G13(0, missed whist): [[K,D,J,10],[A,J,8],[9,7],[8]] — 1A, est ~1.1.
      55% rate rolled high (variance). Reasonable.
    - G16(0, missed whist): [[D,10,9,8],[K,10,8],[A,10],[7]] — 1A, est ~0.9.
      55% rate. A10 in suit3 worth more than singleton ace — 10 promotes
      after ace. Bumped singleton ace from 0.70→0.75 and 10-support bonus.
    - Zero whist losses for 4 consecutive iters proves room for rate bumps.
    - 1-ace rates bumped: est>=1.0: 55→62%, est<1.0: 30→36%.
    - 0-ace rates bumped: est>=1.0: 35→42%, est<1.0: 22→28%.
    - Whister card play: third-position play high when holding top 2 cards.
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

        # Trump tricks
        for c in trump_cards:
            if c.rank == 8:  # Ace
                tricks += 1.0
            elif c.rank == 7:  # King
                tricks += 0.7 if len(trump_cards) >= 3 else 0.4
            elif c.rank >= 5 and len(trump_cards) >= 4:  # J/Q with 4+ trumps
                tricks += 0.4
            elif len(trump_cards) >= 5:  # low trump with 5+ length
                tricks += 0.3

        # Long trump bonus (extra trumps = ruffing potential)
        if len(trump_cards) >= 5:
            tricks += (len(trump_cards) - 4) * 0.6
        elif len(trump_cards) >= 4:
            tricks += 0.3

        # Side suit aces (very reliable tricks)
        for suit, cards in groups.items():
            if suit == best_suit:
                continue
            for c in cards:
                if c.rank == 8:
                    tricks += 0.9
                elif c.rank == 7 and len(cards) >= 2:  # guarded King
                    tricks += 0.35

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
                        tricks += 0.85  # ace of trump: near-guaranteed trick as whister
                    elif len(cards) >= 3 and has_ten:
                        tricks += 0.95  # A+10 with length: 10 promotes after ace
                    elif len(cards) >= 2:
                        tricks += 0.90  # guarded ace — very reliable
                    else:
                        tricks += 0.75  # singleton ace — more reliable than 0.70
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
                    if self.rng.random() < 0.68:
                        return {"bid": game_bids[0],
                                "intent": f"game 2 — 2 aces but flat shape 68% (len={max_suit_len}, tricks={est_tricks:.1f})"}
                    intent = f"pass — 2 aces but flat shape rolled >68% (len={max_suit_len}, tricks={est_tricks:.1f})"
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
                # Reduced from 25% to 20% — high-level contracts have asymmetric
                # risk (losses are 2-3x larger than wins at game 3+).
                if est_tricks >= 3.0 and aces >= 1 and self.rng.random() < 0.20:
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

        for c in trump_cards:
            if c.rank == 8:
                tricks += 1.0
            elif c.rank == 7:
                tricks += 0.7 if len(trump_cards) >= 3 else 0.4
            elif c.rank >= 5 and len(trump_cards) >= 4:
                tricks += 0.4
            elif len(trump_cards) >= 5:
                tricks += 0.3

        if len(trump_cards) >= 5:
            tricks += (len(trump_cards) - 4) * 0.6
        elif len(trump_cards) >= 4:
            tricks += 0.3

        for suit, cards in groups.items():
            if suit == trump_suit:
                continue
            for c in cards:
                if c.rank == 8:
                    tricks += 0.9
                elif c.rank == 7 and len(cards) >= 2:
                    tricks += 0.35

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
        """Evaluate all 66 discard combos × all legal contracts."""
        from itertools import combinations

        all_ids = hand_card_ids + talon_card_ids
        min_bid = winner_bid.effective_value if winner_bid else 0

        # Pre-check: skip betl eval entirely if pool has ANY ace OR 3+ high cards.
        # G3 iter6: Carol had 1 ace in pool, eval discarded ace to play betl,
        # both opponents called → -120. Discarding an ace to play betl is almost
        # always losing because opponents saw your strong pre-talon hand.
        # G15 iter2: 6-card AKQ suit, eval discarded A but K remained → -120.
        pool_aces = sum(1 for cid in all_ids if cid.startswith("A_"))
        pool_kings = sum(1 for cid in all_ids if cid.startswith("K_"))
        pool_high = pool_aces + pool_kings
        skip_betl = pool_aces >= 1 or pool_high >= 3

        best_score = -999
        best_discard = None
        best_contract = None

        for pair in combinations(range(len(all_ids)), 2):
            discard = [all_ids[i] for i in pair]
            remaining_ids = [cid for cid in all_ids if cid not in discard]
            hand = _ids_to_cards(remaining_ids)

            if not skip_betl:
                betl_sc = self._score_hand_for_contract(hand, "betl")
                if betl_sc > best_score:
                    best_score = betl_sc
                    best_discard = discard
                    best_contract = ("betl", None, 6)

            # Sans for monster hands (3+ aces + 6+ high cards)
            sans_sc = self._score_hand_for_contract(hand, "sans")
            if sans_sc > best_score:
                best_score = sans_sc
                best_discard = discard
                best_contract = ("sans", None, 7)

            for suit, suit_level in _SUIT_BID_VALUE.items():
                if suit_level < min_bid:
                    continue
                level = max(suit_level, min_bid)
                sc = self._score_hand_for_contract(hand, "suit", trump_suit=suit)
                if sc > best_score:
                    best_score = sc
                    best_discard = discard
                    best_contract = ("suit", SUIT_NAMES[suit], level)

        return {"discard": best_discard, "contract": best_contract}

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

    def following_decision(self, hand, contract_type, trump_suit, legal_actions):
        """Hand-strength-aware whisting — PRAGMATIC style.

        Key insight: we HAVE access to our hand. Use ace count + trick estimate.
        Scoring math for single whister (game level 2, game_value=4):
          2 tricks -> +8, 3 tricks -> +12, 4 tricks -> +16
          1 trick  -> -36, 0 tricks -> -40
          pass     ->  0 (but declarer gets +40/+80 for free)

        Iter 12: Tightened across all tiers. G3 iter12 lost -72 with 1 ace +
        2 unsupported kings at 80% rate. Reduced 1-ace top tier 80%→75%.
        Added hard pass gate for 4+ cards in declarer's trump.
        """
        action_types = [a["action"] for a in legal_actions]

        if "start_game" in action_types:
            return {"action": "start_game", "intent": "start game"}

        if "follow" in action_types:
            aces = self._count_aces(hand) if hand else 0
            # Pass declarer's trump suit for more accurate estimation
            declarer_trump = trump_suit if trump_suit else None
            est_tricks = self._estimate_whist_tricks(hand, declarer_trump) if hand else 0.0
            is_high_level = self._highest_bid_seen >= 3

            # Hard pass gate: 4+ cards in declarer's trump suit = dead weight.
            # Declarer extracts them easily, hand is effectively 6 cards.
            # Bob iter16 added this gate; prevents catastrophic losses.
            if declarer_trump and hand:
                groups = self._suit_groups(hand)
                trump_count = len(groups.get(declarer_trump, []))
                if trump_count >= 4:
                    return {"action": "pass",
                            "intent": f"pass — hard gate: {trump_count} cards in declarer's trump"}

            # Hard pass gate: 3+ unsupported kings → always pass.
            # G8 iter15: Carol had 1A + 3 unsupported kings [[A,9,7],[K,D,7],[K,8,7],[K]].
            # Kings without ace in suit rarely convert vs strong declarer. Lost -36.
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

            # Hard pass gate: 3+ scattered jacks (in different non-trump suits
            # without aces) → always pass. G6+G8 iter22: Carol had 2A but jacks
            # scattered across 3 suits inflated est, lost -36 each time. Jacks
            # without aces can't convert tricks against strong declarers.
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
                # Junk check: 2 aces but only aces are high (high_count <= 4,
                # no AK combo). Remaining 8 cards are junk.
                is_junk_2a = (high_count_2a <= 4 and not has_ak_combo_2a)
                if est_tricks >= 2.5:
                    if is_junk_2a:
                        rate = 0.82
                    else:
                        rate = 0.99
                    if self.rng.random() < rate:
                        return {"action": "follow",
                                "intent": f"follow — {aces} aces, {int(rate*100)}% rate ({est_tricks:.1f} tricks)"}
                    return {"action": "pass",
                            "intent": f"pass — {aces} aces est >= 2.5 rolled >{int(rate*100)}% ({est_tricks:.1f} tricks)"}
                if est_tricks >= 2.0:
                    rate = 0.76 if is_high_level else 0.97
                    if is_junk_2a:
                        rate = min(rate, 0.76)
                    if self.rng.random() < rate:
                        return {"action": "follow",
                                "intent": f"follow — {aces} aces, {int(rate*100)}% rate ({est_tricks:.1f} tricks)"}
                    return {"action": "pass",
                            "intent": f"pass — {aces} aces est 2.0-2.5 rolled >{int(rate*100)}% ({est_tricks:.1f} tricks)"}
                if is_high_level:
                    rate = 0.76
                else:
                    rate = 0.92
                if is_junk_2a:
                    rate = min(rate, 0.72)
                if self.rng.random() < rate:
                    return {"action": "follow",
                            "intent": f"follow — {aces} aces, {int(rate*100)}% rate ({est_tricks:.1f} tricks)"}
                return {"action": "pass",
                        "intent": f"pass — {aces} aces but weak est, rolled >{int(rate*100)}% ({est_tricks:.1f} tricks)"}

            # 1 ace: tiered by est_tricks. Zero 1-ace whist losses across iters 5-10.
            # Bumped rates: est>=1.0 55→62%, est<1.0 30→36%.
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
                    rate = 0.52 if est_tricks >= 1.5 else 0.32
                else:
                    if est_tricks >= 2.0:
                        rate = 0.99  # Strong 1-ace hand
                    elif est_tricks >= 1.5:
                        rate = 0.92  # Decent support (was 0.88)
                    elif est_tricks >= 1.0:
                        rate = 0.62  # Marginal (was 0.55) — zero losses proves room
                    else:
                        rate = 0.36  # Weak 1-ace hand (was 0.30)
                    # A-K combo in side suit bumps rate: reliable trick anchor
                    if has_ak_combo:
                        rate = min(rate + 0.15, 1.0)
                    # Void-suit bonus: ruffing potential pushes rate up
                    if has_void:
                        rate = min(rate + 0.10, 0.92)
                if self.rng.random() < rate:
                    return {"action": "follow",
                            "intent": f"follow — 1 ace, {int(rate*100)}% rate ({est_tricks:.1f} tricks)"}
                return {"action": "pass",
                        "intent": f"pass — 1 ace but rolled >{int(rate*100)}% ({est_tricks:.1f} tricks)"}

            # 0 aces: bumped 35%/22% → 42%/28%. Zero 0-ace whist losses
            # across iters 5-10. More speculative income.
            if is_high_level:
                rate = 0.22 if est_tricks >= 1.0 else 0.0
            else:
                rate = 0.42 if est_tricks >= 1.0 else 0.28
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

    def choose_card(self, legal_cards):
        """Strategic card play with different logic for declarer vs whister."""
        if len(legal_cards) == 1:
            self._cards_played += 1
            return legal_cards[0].id

        contract_type = getattr(self, '_contract_type', None)

        # Betl-specific card play
        if contract_type == "betl":
            card = self._betl_choose_card(legal_cards)
            self._cards_played += 1
            return card.id

        hand_size = self._total_hand_size - self._cards_played
        is_leading = len(legal_cards) == hand_size
        suits_in_legal = {c.suit for c in legal_cards}
        must_follow = len(suits_in_legal) == 1 and len(legal_cards) < hand_size

        if is_leading:
            if self._is_declarer and self._trump_suit_val is not None:
                card = self._declarer_lead(legal_cards)
            else:
                card = self._whister_lead(legal_cards)
            self._cards_played += 1
            return card.id

        elif must_follow:
            by_rank_desc = sorted(legal_cards, key=lambda c: c.rank, reverse=True)
            # If we have the ace, play it (guaranteed winner in suit)
            if by_rank_desc[0].rank == 8:
                self._cards_played += 1
                return by_rank_desc[0].id
            # As whister: second-hand-low — play low to conserve high cards
            # unless we have a near-certain winner (King when ace likely played)
            if not self._is_declarer:
                # Early in the hand (first 3 tricks), duck with King — wait
                # for aces to be played first, then King becomes a winner
                if self._cards_played < 3 and by_rank_desc[0].rank == 7:
                    self._cards_played += 1
                    return min(legal_cards, key=lambda c: c.rank).id
                # With only low cards, play lowest
                if by_rank_desc[0].rank < 6:
                    self._cards_played += 1
                    return min(legal_cards, key=lambda c: c.rank).id
            # Play highest to try to win
            self._cards_played += 1
            return by_rank_desc[0].id

        else:
            # Can't follow suit
            if self._is_declarer and self._trump_suit_val is not None:
                # As declarer: ruff with lowest trump if possible
                trumps = [c for c in legal_cards if c.suit == self._trump_suit_val]
                if trumps:
                    card = min(trumps, key=lambda c: c.rank)
                    self._cards_played += 1
                    return card.id
            # As whister: try to trump with lowest trump to win the trick
            if not self._is_declarer and self._trump_suit_val is not None:
                trumps = [c for c in legal_cards if c.suit == self._trump_suit_val]
                if trumps:
                    card = min(trumps, key=lambda c: c.rank)
                    self._cards_played += 1
                    return card.id
            # If forced to trump (all legal cards are one suit = trumps), play lowest
            if len(suits_in_legal) == 1:
                card = min(legal_cards, key=lambda c: c.rank)
                self._cards_played += 1
                return card.id
            # Discard lowest from longest off-suit to preserve short suits
            groups = self._suit_groups(legal_cards)
            longest_suit = max(groups.keys(), key=lambda s: len(groups[s]))
            worst_card = groups[longest_suit][-1]
            self._cards_played += 1
            return worst_card.id

    _betl_choose_card = PlayerAlice._betl_choose_card
    _betl_declarer_play = PlayerAlice._betl_declarer_play
    _betl_defender_play = PlayerAlice._betl_defender_play

    def _declarer_lead(self, legal_cards):
        """Declarer leading: draw ALL trumps, then cash side aces.

        Iter 3 improvement: with 5-6 trumps, keep leading trumps even after
        ace/king to ensure opponents are stripped of all trumps before we
        cash side-suit winners (prevents ruffing of our side aces).
        """
        groups = self._suit_groups(legal_cards)
        trump = self._trump_suit_val

        # Phase 1: Lead ALL trumps to strip opponents completely
        # With long trump suits (5-6), keep drawing even mid-rank trumps
        if trump in groups:
            trump_cards = groups[trump]
            # Lead ace of trump first
            for c in trump_cards:
                if c.rank == 8:
                    return c
            # Keep leading trumps if we have 3+ remaining (opponents likely
            # still have trumps that could ruff our side aces)
            if len(trump_cards) >= 3:
                return trump_cards[0]  # lead highest remaining
            # With 1-2 trumps left, lead high ones (K/Q) to draw
            if trump_cards[0].rank >= 6:
                return trump_cards[0]

        # Phase 2: Cash side-suit aces (shortest suit first)
        aces = [c for c in legal_cards if c.rank == 8 and c.suit != trump]
        if aces:
            aces.sort(key=lambda c: len(groups.get(c.suit, [])))
            return aces[0]

        # Phase 3: Lead remaining trumps
        if trump in groups:
            return groups[trump][0]

        # Phase 4: Lead highest from strongest off-suit
        # Prefer suits with high top card + length to develop winners
        non_trump = {s: cards for s, cards in groups.items() if s != trump}
        if non_trump:
            def suit_quality(s):
                cards = non_trump[s]
                return cards[0].rank * 10 + len(cards)
            best = max(non_trump.keys(), key=suit_quality)
            return non_trump[best][0]

        return max(legal_cards, key=lambda c: c.rank)

    def _whister_lead(self, legal_cards):
        """Whister leading: lead aces from A-K suits, then create voids.

        Iter31: improved void creation — lead from shortest non-ace suit
        to void it quickly for ruffing potential later.
        """
        groups = self._suit_groups(legal_cards)

        # Lead aces first — guaranteed trick winners
        # Prefer aces from A-K combo suits: cash the ace, king promoted next trick.
        aces = [c for c in legal_cards if c.rank == 8]
        if aces:
            def ace_priority(c):
                suit_cards = groups.get(c.suit, [])
                has_king = any(x.rank == 7 for x in suit_cards)
                # Lower score = higher priority: A-K combo gets -100 bonus
                return (-100 if has_king else 0) + len(suit_cards)
            aces.sort(key=ace_priority)
            return aces[0]

        # Lead kings from longest suit — more supporting cards means
        # higher chance the ace has already been played
        kings = [c for c in legal_cards if c.rank == 7]
        if kings:
            kings.sort(key=lambda c: len(groups.get(c.suit, [])), reverse=True)
            return kings[0]

        # Lead from shortest suit (without aces) to create voids for ruffing.
        # Prefer suits with 1-2 cards — voiding these creates ruffing lanes.
        non_ace_suits = {s: cards for s, cards in groups.items()
                         if not any(c.rank == 8 for c in cards)}
        if non_ace_suits:
            shortest = min(non_ace_suits.keys(), key=lambda s: len(non_ace_suits[s]))
            return non_ace_suits[shortest][0]  # lead highest from shortest

        # Fallback: lead highest from shortest suit
        shortest_suit = min(groups.keys(), key=lambda s: len(groups[s]))
        return groups[shortest_suit][0]


class NeuralPlayer(BasePlayer):
    """ML-based Preferans player. Uses a trained PrefNet model for decisions.

    Falls back to random moves if the model file is not found.
    """

    # Bid type ↔ index mapping
    BID_TYPES = ["pass", "game", "in_hand", "betl", "sans"]
    FOLLOWING_ACTIONS = ["pass", "follow", "call", "counter", "start_game", "double_counter"]

    def __init__(self, name: str, seed: int | None = None,
                 model_path: str = "neural/models/pref_net.pt",
                 temperature: float = 0.0):
        super().__init__(name)
        self.rng = random.Random(seed)
        self.temperature = temperature
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
            logits = self.model.forward_bid(hand_feat, mask)[0]

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
            scores = self.model.forward_discard(hand_feat, card_feats_t)[0]

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
            type_logits, trump_logits = self.model.forward_contract(hand_feat, context)

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
    # Whisting / following
    # ------------------------------------------------------------------

    def choose_whist_action(self, legal_actions):
        hand = getattr(self, '_hand', [])
        contract_type = getattr(self, '_contract_type', None)
        trump_suit = getattr(self, '_trump_suit', None)

        if self.model is None:
            action = self.rng.choice(legal_actions)
            return action.get("action") if isinstance(action, dict) else action

        torch = self._torch
        feat = self._features

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

        with torch.no_grad():
            logits = self.model.forward_following(hand_feat, context, mask)[0]

        chosen_idx = self._sample_or_argmax(logits)
        if chosen_idx in action_map:
            return action_map[chosen_idx]

        # Fallback
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
                hand_feat, play_ctx, played_vec, card_feats_t)[0]

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

def play_game(strategies: dict[int, BasePlayer], seed: int = 42) -> tuple[list[str], list[str]]:
    """Play one full game (single round) and return (log_lines, compact_lines)."""
    log = []
    compact = []
    # Compact log data
    compact_bids = []      # (name, hand_snapshot, bid_label)
    compact_whists = []     # (name, hand_snapshot, contract_label, action)

    def emit(msg):
        log.append(msg)

    # Create game via GameSession — use names from strategy objects
    player_names = [strategies[pid].name for pid in sorted(strategies.keys())]
    if len(player_names) != 3:
        player_names = ["Alice", "Bob", "Carol"]

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
        return log, compact

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

            strat = strategies[next_id]
            strat._rnd = rnd
            strat._player_id = next_id
            card_id = strat.choose_card(legal_cards)
            card_obj = next(c for c in legal_cards if c.id == card_id)

            result = engine.play_card(next_id, card_id)

            emit(f"    P{player.position} {player.name} plays {card_str(card_obj)}")

            if result.get("trick_complete"):
                winner = game.get_player(result["trick_winner_id"])
                emit(f"    => Winner: P{winner.position} {winner.name}")

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

    return log, compact


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

    log_lines, compact_lines = play_game(strategies, seed=seed)

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
