"""Play a single Preferans game with three RandomMove players and log every move."""

import os
import sys
import random
import datetime
from functools import cmp_to_key

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "server"))

from models import (
    Game, Player, PlayerType, RoundPhase, ContractType, Suit,
    SUIT_NAMES, RANK_NAMES,
)
from engine import GameEngine


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


class PlayerAlice(WeightedRandomPlayer):
    """Alice: AGGRESSIVE Preferans player aiming for HIGH scores.

    Key strategies (iteration 24):
    - Declaring: G9(+60) — 3-ace auto-bid, clean win. Only 1 declaration in
      10 games; Carol dominated unopposed (+520). Bidding thresholds fine but
      few strong hands dealt. Keep current declaring logic.
    - Whisting: G3(-100) + G7(-80) = -180 from whisting! Both were 1-ace
      hands with est ~0.85-1.2 where the 40% rate at est 1.0-1.5 fired.
      The iter23 bump 30%→40% was too aggressive — immediately catastrophic.
      G3: [[D,10,9,7],[K,D,J],[10,7],[A]] — 1A scattered, called vs Carol's
      strong 3-suit hand. G7: [[A,J,9],[9,8,7],[K,7],[10,7]] — 1A, called
      TWICE vs Carol's [[A,K,J,8],[K,D,10],[J,8],[K]]. Junk around the ace.
    - Fix: 1-ace est 1.0-1.5 rate reduced 40% → 25%. Still above iter21's
      20% for aggression, but much less exposed. -180 in one iteration proves
      40% fires too often on scattered 1-ace hands.
    - Fix: Add scattered-cards penalty: when 1 ace is the only high card and
      remaining cards are below Queen across 3+ suits, subtract 0.15 est.
      G3/G7 both had ace surrounded by J/10/9/7 junk that inflated est.
    - 0-ace rates unchanged (no 0-ace whist losses this iter).
    - Flat 2-ace whist rate unchanged at 55% (no 2-ace data this iter).
    """

    def __init__(self, seed: int | None = None):
        super().__init__("Alice", seed=seed,
                         w_pass=45, w_game=45, w_in_hand=5, w_betl=1, w_sans=1)
        self._cards_played = 0
        self._total_hand_size = 10
        self._is_declarer = False
        self._trump_suit_val = None   # suit enum value when we're declarer
        self._highest_bid_seen = 0    # track auction escalation

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
        total_jacks = sum(1 for c in hand if c.rank == 5)
        if total_jacks >= 3:
            tricks -= 0.15

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
        if declarer_trump is not None:
            for suit, cards in groups.items():
                if suit != declarer_trump and len(cards) >= 5:
                    tricks -= 0.30
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
        """Check if hand is suitable for betl (need very low cards, no gaps)."""
        max_rank = max(c.rank for c in hand)
        if max_rank >= 7:  # King or Ace — too risky
            return False
        groups = self._suit_groups(hand)
        for suit, cards in groups.items():
            if len(cards) == 1 and cards[0].rank >= 5:  # Lone J+ is dangerous
                return False
        return max_rank <= 4  # Only 10 or below is safe

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
                    # Flat 2-ace hand — bumped 70% → 75%: iter20 had 0 losses,
                    # scored 0 in 8/10 games. Need more declaration opportunities.
                    if self.rng.random() < 0.75:
                        return {"bid": game_bids[0],
                                "intent": f"game 2 — 2 aces but flat (tricks={est_tricks:.1f}, longest={max_suit_len})"}
                # Marginal hands: bumped 85% → 90%. Iter 20: 2/2 declaring
                # wins (+180). Alice rarely loses when declaring — maximize
                # declaration frequency.
                if est_tricks >= 1.5 and aces >= 1:
                    if self.rng.random() < 0.90:
                        return {"bid": game_bids[0],
                                "intent": f"game 2 — marginal aggressive (tricks={est_tricks:.1f}, aces={aces})"}
                # High-card-dense hands without aces: bid 45% if lots of high cards
                # Lowered from high >= 5 to >= 4 — G2 iter13 had 4 high cards, missed bid
                if est_tricks >= 1.5 and high >= 4:
                    if self.rng.random() < 0.45:
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
                if aces >= 2 and max_suit_len >= 4 and est_tricks >= 3.5:
                    return {"bid": game_bids[0],
                            "intent": f"game 3 — 2+ aces with shape (tricks={est_tricks:.1f}, longest={max_suit_len})"}
                if est_tricks >= 3.5 and aces >= 1 and self.rng.random() < 0.30:
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

        # Check for sans/betl with hand evaluation
        if hand:
            sans_bids = [b for b in legal_bids if b["bid_type"] == "sans"]
            if sans_bids and self._is_good_sans_hand(hand):
                return {"bid": sans_bids[0], "intent": f"sans — dominant high cards (aces={aces})"}
            betl_bids = [b for b in legal_bids if b["bid_type"] == "betl"]
            if betl_bids and self._is_good_betl_hand(hand):
                return {"bid": betl_bids[0], "intent": "betl — very low cards"}

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
        """Keep best trump suit cards and aces; discard weakest. Try to create voids."""
        all_ids = hand_card_ids + talon_card_ids
        rank_order = {"7": 1, "8": 2, "9": 3, "10": 4, "J": 5, "Q": 6, "K": 7, "A": 8}

        def card_rank(cid):
            return rank_order.get(cid.split("_")[0], 0)

        def card_suit(cid):
            return cid.split("_")[1]

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

    def choose_discard(self, hand_card_ids, talon_card_ids):
        self._is_declarer = True
        decision = self.discard_decision(hand_card_ids, talon_card_ids)
        return decision["discard"]

    # ------------------------------------------------------------------
    # Contract — pick best trump, minimum level, prefer cheaper suits
    # ------------------------------------------------------------------

    def bid_decision(self, hand, legal_levels, winner_bid):
        """Pick safest contract. Prefer cheaper suits (lower game_value) when tied."""
        if 6 in legal_levels and self._is_good_betl_hand(hand):
            return {"contract_type": "betl", "trump": None, "level": 6,
                    "intent": "betl — hand has only low cards"}

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
            # G6 iter17: [[A,J,10,8],[A,10,9],[9,8],[8]] — 2A, flat 4+3+2+1, lost -40.
            # G7 iter17: [[A,9,8,7],[A,D],[D,8],[10,8]] — 2A, weak support, lost -80.
            # Both had est ~1.5-1.7 and failed: scattered J/10/9 can't take tricks.
            if aces >= 2:
                groups = self._suit_groups(hand) if hand else {}
                max_suit_len = max((len(cards) for cards in groups.values()), default=0)
                if aces >= 3 or est_whist_tricks >= 2.0 or max_suit_len >= 4:
                    return {"action": "follow",
                            "intent": f"follow — {aces} aces, {est_whist_tricks:.1f} est tricks"}
                # Flat 2-ace hand: bumped 45% → 55%. Iter 22: STILL 0 whist
                # attempts after 5 iterations of bumps. Aggressive style needs
                # whist income — 2 aces alone usually take 2 tricks = +8.
                if self.rng.random() < 0.55:
                    return {"action": "follow",
                            "intent": f"follow — {aces} aces flat ({est_whist_tricks:.1f} tricks, longest={max_suit_len})"}
                return {"action": "pass",
                        "intent": f"pass — 2 aces but flat shape ({est_whist_tricks:.1f} tricks, longest={max_suit_len})"}

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

                if is_high_level:
                    # High-level: G10 iter8 lost -80 calling twice on escalated game.
                    rate = 0.45 if est_whist_tricks >= 1.5 else 0.20
                else:
                    # Game 2: AGGRESSIVE — iter22 had 0 whist attempts for 5th
                    # straight iteration. Problem isn't rates, it's thresholds:
                    # most hands land at est 0.8-1.3, below the 1.5 high-rate tier.
                    # Lowering boundaries so more hands reach actionable rates.
                    if est_whist_tricks >= 2.0:
                        rate = 1.0   # Very strong 1-ace hand — always whist
                    elif est_whist_tricks >= 1.5:
                        rate = 0.90  # Strong 1-ace: near-certain whist
                    elif est_whist_tricks >= 1.0:
                        # Reduced 40% → 25%: iter23 G3(-100) + G7(-80) = -180.
                        # 40% fired on scattered 1-ace hands (est ~1.0-1.2)
                        # where only the ace contributed. 25% still above
                        # iter21's 20% for aggression but much safer.
                        rate = 0.25
                    elif est_whist_tricks >= 0.7:
                        # NEW tier: hands with est 0.7-1.0 were falling into
                        # the 5% catch-all. With 1 ace that's ~0.85 tricks from
                        # ace alone — 15% gives some action on decent support.
                        rate = 0.15
                    else:
                        rate = 0.05  # Very weak — near-pure gamble
                    # A-K combo in side suit bumps rate: guaranteed ~1.5 tricks
                    if has_ak_combo:
                        rate = min(rate + 0.20, 1.0)
                    # Void in declarer's trump = ruffing potential. New iter 22:
                    # When we hold 0 cards in trump, we can ruff declarer's
                    # side-suit leads — pushes borderline hands above thresholds.
                    if hand and trump_suit is not None:
                        trump_count = sum(1 for c in hand if c.suit == trump_suit)
                        if trump_count == 0:
                            rate = min(rate + 0.10, 1.0)
                if self.rng.random() < rate:
                    return {"action": "follow",
                            "intent": f"follow — 1 ace, {int(rate*100)}% rate ({est_whist_tricks:.1f} tricks)"}
                return {"action": "pass",
                        "intent": f"pass — 1 ace but rolled >{int(rate*100)}% ({est_whist_tricks:.1f} tricks)"}

            # 0 aces: whist based on estimated trick potential — controlled aggression
            # Tightened: 0-ace whists produced net negative in iter 8 analysis.
            if is_high_level:
                if est_whist_tricks >= 1.5 and self.rng.random() < 0.15:
                    return {"action": "follow",
                            "intent": f"follow — 0 aces, high-level speculative ({est_whist_tricks:.1f} tricks)"}
            else:
                # Iter 22: ~50% of non-declaring hands had 0 aces (G3,G6,G10).
                # These represent a huge missed income pool. Bumping rates and
                # adding lower tier to capture more hands for aggressive style.
                if est_whist_tricks >= 1.5:
                    rate = 0.30  # Strong 0-ace hand (multiple K/Q combos)
                elif est_whist_tricks >= 1.0:
                    rate = 0.18  # Decent kings/queens
                elif est_whist_tricks >= 0.5:
                    rate = 0.08  # NEW tier: some kings = marginal action
                else:
                    rate = 0.03  # Very weak — rarely profitable
                if self.rng.random() < rate:
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

    Key strategies (iteration 26):
    - Declaring: 4/4 wins iter22 (G3+40, G6+40, G8+40, G10+140 sans!).
      +260 declaring income. Bidding calibration excellent — keep thresholds.
    - Whisting: G5(+24) correct 2-ace whist. Net whist: +24. Zero negatives!
      G4 missed: 1A + void [[A,J,9,7],[J,10,9,8],[D,10],[]] at ~37% rate.
      Void-suit hands are profitable — bump void boost +0.10 → +0.12.
    - Total: +284 (1st place). Zero negatives across multiple iterations.
    - Shape-aware bidding: 3-ace auto-bid requires longest suit >= 4.
      Flat 3-ace hands (3+3+2+2) downgraded to 60%.
      Flat 2-ace hands (3+3+2+2) downgraded to 30% borderline.
    - 2-ace auto-bid tightened: requires strength >= 3.8 (was 3.5).
    - Borderline bidding requires 1+ ace at 30%, threshold 2.8.
    - Trump AK combo bonus +0.2 in hand strength.
    - Never game 3+.
    - Whisting: Hard pass gate: 4+ cards in declarer's trump → always pass.
      Hard pass gate: 3+ unsupported kings → always pass.
    - 2-ace whisting: est >= 2.0 at 92%; est < 2.0 at 85% game 2, 30% high-level.
    - Queen penalty in whist estimation: 3+ queens without ace → -0.25.
    - 1-ace whisting: A-K combo boost +0.15 to rate. Rates 78/62/29.
      Void-suit bonus +0.12 to rate (ruffing potential, was +0.10).
    - 0-ace whisting: 22% when est >= 2.0 on game 2 (was 20%).
    - Whist estimation: A-K combo bonus (+0.20) for ace+king in same side suit.
      Void-suit bonus (+0.25) for ruffing potential.
      Long non-trump suit penalty (-0.30) for 5+ cards in single non-trump suit.
      Low trump count penalty (-0.35) for 0-1 cards in declarer's trump.
      Flat shape penalty (-0.20) for no 4+ non-trump suit as whister.
    - Smart exchange: void short off-suits, singleton pairs below King.
    - Card play: whister King/Queen ducking early, declarer draws trumps then
      cashes aces, Phase 4 from longest off-suit.
    - Whister lead: prefer aces from A-K combo suits first (cash ace, promote king).
    """

    def __init__(self, seed: int | None = None):
        super().__init__("Bob", seed=seed,
                         w_pass=80, w_game=15, w_in_hand=3, w_betl=1, w_sans=1)
        self._cards_played = 0
        self._total_hand_size = 10
        self._is_declarer = False
        self._trump_suit_val = None   # suit enum value when we're declarer
        self._highest_bid_seen = 0    # track auction escalation for whisting

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
        groups = self._suit_groups(hand)
        best_suit, _ = self._best_trump_suit(hand)
        if best_suit is None:
            return 0.0

        tricks = 0.0
        trump_cards = groups.get(best_suit, [])

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
            if suit == best_suit:
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
        """Check if hand is suitable for betl (need very low cards, no gaps)."""
        max_rank = max(c.rank for c in hand)
        if max_rank >= 7:  # King or Ace — too risky
            return False
        groups = self._suit_groups(hand)
        for suit, cards in groups.items():
            if len(cards) == 1 and cards[0].rank >= 5:  # Lone J+ is dangerous
                return False
        return max_rank <= 4  # Only 10 or below is safe

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
                if (aces >= 3 and max_suit_len >= 4) or strength >= 4.0 or (aces >= 2 and strength >= 3.8 and max_suit_len >= 4):
                    return {"bid": game_bids[0],
                            "intent": f"game 2 — strong (strength {strength:.1f}, aces={aces}, longest={max_suit_len})"}
                elif aces >= 3 and max_suit_len < 4 and self.rng.random() < 0.60:
                    return {"bid": game_bids[0],
                            "intent": f"game 2 — flat 3-ace 60% (strength {strength:.1f}, longest={max_suit_len})"}
                # Flat 2-ace hands (max suit < 4): 30% borderline — G6 iter7
                # lost -60 with 2 aces but 3+3+2+2 shape
                elif aces >= 2 and strength >= 3.0 and self.rng.random() < 0.30:
                    return {"bid": game_bids[0],
                            "intent": f"game 2 — flat 2-ace borderline (strength {strength:.1f}, longest={max_suit_len})"}
                # Borderline hands (2.8+): bid 30% with 1+ ace — G5 iter10 missed
                # AKQ8 spades+side K (strength 2.95) that should have been bid
                elif strength >= 2.8 and aces >= 1 and self.rng.random() < 0.30:
                    return {"bid": game_bids[0],
                            "intent": f"game 2 — borderline strength {strength:.1f}, 30% roll (aces={aces}, longest={max_suit_len})"}
                else:
                    intent = f"pass — strength {strength:.1f} below 2.8 for cautious game 2 (aces={aces}, longest={max_suit_len})"
            else:
                # Game 3+: never bid — too risky for cautious Bob
                intent = f"pass — game {game_val}+ too risky for cautious style (aces={aces})"
            pass_bid = next((b for b in legal_bids if b["bid_type"] == "pass"), None)
            if pass_bid:
                return {"bid": pass_bid, "intent": intent}

        # No game bids — pass (avoid sans/betl/in_hand)
        pass_bid = next((b for b in legal_bids if b["bid_type"] == "pass"), None)
        if pass_bid:
            return {"bid": pass_bid,
                    "intent": f"pass — no game bids, avoiding sans/betl/in_hand (aces={aces}, high={high})"}

        # Fallback
        return super().bid_intent(hand, legal_bids)

    # ------------------------------------------------------------------
    # Exchange — keep strongest suit + aces, discard weakest, create voids
    # ------------------------------------------------------------------

    def discard_decision(self, hand_card_ids, talon_card_ids):
        """Keep best trump suit cards and aces; discard weakest. Try to create voids."""
        all_ids = hand_card_ids + talon_card_ids
        rank_order = {"7": 1, "8": 2, "9": 3, "10": 4, "J": 5, "Q": 6, "K": 7, "A": 8}

        def card_rank(cid):
            return rank_order.get(cid.split("_")[0], 0)

        def card_suit(cid):
            return cid.split("_")[1]

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

    def choose_discard(self, hand_card_ids, talon_card_ids):
        self._is_declarer = True
        decision = self.discard_decision(hand_card_ids, talon_card_ids)
        return decision["discard"]

    # ------------------------------------------------------------------
    # Contract — pick best trump, minimum level, prefer cheap suits
    # ------------------------------------------------------------------

    def bid_decision(self, hand, legal_levels, winner_bid):
        """Pick safest contract based on hand evaluation."""
        if 6 in legal_levels and self._is_good_betl_hand(hand):
            return {"contract_type": "betl", "trump": None, "level": 6,
                    "intent": "betl — hand has only low cards"}

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
        # in side suits are vulnerable to being ruffed. -0.35 penalty.
        if trump_suit:
            trump_count = sum(1 for c in hand if c.suit == trump_suit)
            if trump_count <= 1:
                tricks -= 0.35

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

        Key insights (iteration 25):
        - Iter 21: Bob scored -4 (3rd). Declared 2 games: G6(+40), G8(+40) —
          both wins (+80). Whisted 2 games: G5(+16), G9(-100). Net whist: -84.
          G9's -100 with 2A + AKQ9 + A1098 devastated score.
        - G9 root cause: 2A + est >= 2.0 triggered always-whist (100% gate).
          Carol had dominant 5+4 shape. No escape valve on the "always" gate.
          Fix: reduce 2A+est>=2.0 from 100% to 92%. Add flat shape penalty
          (-0.20) when no non-trump suit has 4+ cards — spreads tricks thin.

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
            if aces >= 2:
                if is_high_level and est_tricks < 2.5:
                    # High-level + weak support: 30% hedge (was 35%)
                    if self.rng.random() < 0.30:
                        return {"action": "follow",
                                "intent": f"follow — {aces} aces, high-level 30% hedge ({est_tricks:.1f} est tricks)"}
                    return {"action": "pass",
                            "intent": f"pass — {aces} aces, high-level cautious hedge ({est_tricks:.1f} est tricks)"}
                if est_tricks >= 2.0:
                    # G9 iter21: 2A + est >= 2.0 always-whisted → -100 against Carol's
                    # dominant 5+4 hand. 100% rate too aggressive — no escape valve.
                    # Reduce to 92% to give 8% dodge chance on catastrophic matchups.
                    if self.rng.random() < 0.92:
                        return {"action": "follow",
                                "intent": f"follow — {aces} aces, strong est 92% whist ({est_tricks:.1f} est tricks)"}
                    return {"action": "pass",
                            "intent": f"pass — {aces} aces, strong est 8% safety dodge ({est_tricks:.1f} est tricks)"}
                # Weak 2-ace (est < 2.0): 85% on game 2 (was 80%) — iter20 had
                # zero whist negatives again, room for more income
                if self.rng.random() < 0.85:
                    return {"action": "follow",
                            "intent": f"follow — {aces} aces, weak est 75% ({est_tricks:.1f} est tricks)"}
                return {"action": "pass",
                        "intent": f"pass — {aces} aces, weak est cautious ({est_tricks:.1f} est tricks)"}

            # 1 ace: Zero negatives iter22. 4/4 declaring wins, 1/1 whist win (+24).
            # G4 missed whist with 1A + void (est ~1.3, rate ~37%) — just variance.
            # Small 3% bump across tiers: 78/62/29 (was 75/60/27). Proven safe.
            if aces == 1:
                if is_high_level:
                    rate = 0.0   # Never whist game 3+ with 1 ace — losses too big
                elif est_tricks >= 2.0:
                    rate = 0.78  # Bumped from 0.75 — zero negatives, safe tier
                elif est_tricks >= 1.5:
                    rate = 0.62  # Bumped from 0.60 — zero negatives, room for income
                elif est_tricks >= 1.0:
                    rate = 0.29  # Bumped from 0.27 — G4 iter22 missed at ~37%
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

            # 0 aces: small chance on game 2 with very strong kings
            # Bumped 20% → 22%: iter22 had zero negatives from any whisting tier.
            # Strong-king hands (est >= 2.0) can take 2 tricks on game 2.
            if not is_high_level and est_tricks >= 2.0 and self.rng.random() < 0.22:
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
            # If forced to trump (all legal cards are trump), play lowest trump
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

    Key strategies (iteration 22):
    - Bidding: 3+ aces always bid. 2 aces + concentrated (longest >= 4) auto-bid
      BUT require king or ace in trump for concentrated 2A.
      Flat 2-ace: always bid if est >= 3.0, else 60% (was 55%). Carol declared
      only 1 game in iter22 (+60) — too passive. Need more declarations.
      Strong hands (3.0+) always bid.
      1-ace + 5-card suit always bids. 1-ace + 4-card suit + void: require est >= 2.5
      or king in trump.
      Marginal (2.0-3.0) at 65%/50% (was 60/45). Marginal 1-ace without 4+ trump at 50%.
      1 ace + dense high cards: 30% (was 25%).
    - Whisting: Hard pass gate: 4+ cards in declarer's trump → always pass.
      Hard pass gate: 3+ unsupported kings → always pass.
      NEW hard pass gate: 3+ scattered jacks without aces → always pass.
      2 aces: est >= 2.5 always whist; est 2.0-2.5 at 80%/50% (was 85/55).
      Below 2.0: 70%/50% (was 80/60). G6+G8 iter22 both lost -36 with 2A —
      both had scattered jacks inflating est. Reduce 2-ace rates.
      1-ace rates: unchanged. 0-ace at 8%/3%.
    - Whist estimation: A-K side-suit bonus (+0.20). Unsupported kings devalued.
      Queen-heavy penalty: 2+ unsupported queens → -0.20, 3+ → -0.30.
      NEW scattered jacks penalty: 3+ jacks in different suits → -0.15.
      Scaled king penalty: 3+ → -0.40.
      Void-suit bonus (+0.25) for ruffing potential.
    - NEVER declare sans or betl — catastrophic downside (-140/-120)
    - Smart exchange: void short off-suits, keep trump + aces; cost-aware suit
    - Declarer card play: draw ALL trumps then cash aces; lead from longest
      off-suit in Phase 4 to develop length winners.
    - Whister card play: lead aces from A-K combo suits first; kings from longest;
      improved following discipline; second-hand-low conservation.
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

    # ------------------------------------------------------------------
    # Hand evaluation helpers
    # ------------------------------------------------------------------

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
            for c in cards:
                if c.rank == 8:  # Ace
                    if is_trump:
                        tricks += 0.75  # ace of trump less valuable as whister
                    elif len(cards) >= 2:
                        tricks += 0.90  # guarded ace — very reliable
                    else:
                        tricks += 0.70  # singleton ace — declarer can duck/plan around it
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

        # Bonus for A-K in same non-trump side suit: ace cashes guaranteed,
        # king promoted next trick. ~1.5 reliable tricks from one suit.
        # G3 iter13: Carol had AK spades but passed whist — missed income.
        for suit, cards in groups.items():
            is_trump = (suit == declarer_trump) if declarer_trump else False
            if is_trump:
                continue
            has_ace = any(c.rank == 8 for c in cards)
            has_king = any(c.rank == 7 for c in cards)
            if has_ace and has_king:
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
                    if self.rng.random() < 0.60:
                        return {"bid": game_bids[0],
                                "intent": f"game 2 — 2 aces but flat shape 60% (len={max_suit_len}, tricks={est_tricks:.1f})"}
                    intent = f"pass — 2 aces but flat shape rolled >60% (len={max_suit_len}, tricks={est_tricks:.1f})"
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
                # G9 iter14: [[A,D,8,7],[K,D,10],[9,7],[9]] — 1A + scattered
                # queens, est ~2.05, lost -40. Queens without K in trump are
                # unreliable. Tighten: 55% for 4+ trump with strong est (2.5+),
                # 40% for weaker shapes or lower est.
                if est_tricks >= 2.0 and aces >= 1:
                    groups_m = self._suit_groups(hand) if hand else {}
                    max_len = max((len(cards) for cards in groups_m.values()), default=0)
                    m_rate = 0.65 if (max_len >= 4 and est_tricks >= 2.5) else 0.50
                    if self.rng.random() < m_rate:
                        return {"bid": game_bids[0],
                                "intent": f"game 2 — marginal (tricks={est_tricks:.1f}, aces={aces}, longest={max_len}, rate={int(m_rate*100)}%)"}
                    intent = f"pass — marginal hand rolled >{int(m_rate*100)}% (tricks={est_tricks:.1f}, aces={aces})"
                # 1 ace with high-card density: 25% speculative
                # Bumped from 20% — high-card density hands with talon
                # frequently reach 6 tricks.
                elif aces >= 1 and high >= 4:
                    if self.rng.random() < 0.30:
                        return {"bid": game_bids[0],
                                "intent": f"game 2 — 1 ace + dense high cards (tricks={est_tricks:.1f}, high={high})"}
                    intent = f"pass — 1 ace dense but rolled >30% (tricks={est_tricks:.1f}, high={high})"
                # 0-ace speculative bids removed — they are net negative
                else:
                    intent = f"pass — weak hand (tricks={est_tricks:.1f}, aces={aces}, high={high})"
            elif game_val == 3:
                # Game 3: only with strong hands (4.0+ tricks or 3+ aces)
                if est_tricks >= 4.0 or aces >= 3:
                    return {"bid": game_bids[0],
                            "intent": f"game 3 — strong hand (tricks={est_tricks:.1f}, aces={aces})"}
                if est_tricks >= 3.0 and aces >= 1 and self.rng.random() < 0.25:
                    return {"bid": game_bids[0],
                            "intent": f"game 3 — calculated gamble (tricks={est_tricks:.1f}, aces={aces})"}
                intent = f"pass — too weak for game 3 (tricks={est_tricks:.1f}, aces={aces})"
            else:
                intent = f"pass — game {game_val}+ too risky (tricks={est_tricks:.1f}, aces={aces})"
            pass_bid = next((b for b in legal_bids if b["bid_type"] == "pass"), None)
            if pass_bid:
                return {"bid": pass_bid, "intent": intent}

        # Pass everything else (never sans/betl/in_hand)
        pass_bid = next((b for b in legal_bids if b["bid_type"] == "pass"), None)
        if pass_bid:
            return {"bid": pass_bid,
                    "intent": f"pass — no game bids, avoiding sans/betl/in_hand (aces={aces}, high={high})"}

        # Fallback
        return super().bid_intent(hand, legal_bids)

    # ------------------------------------------------------------------
    # Exchange — keep strongest suit + aces, void short suits
    # ------------------------------------------------------------------

    def discard_decision(self, hand_card_ids, talon_card_ids):
        """Keep trump-suit cards and aces; discard weakest. Try to create voids."""
        all_ids = hand_card_ids + talon_card_ids
        rank_order = {"7": 1, "8": 2, "9": 3, "10": 4, "J": 5, "Q": 6, "K": 7, "A": 8}

        def card_rank(cid):
            return rank_order.get(cid.split("_")[0], 0)

        def card_suit(cid):
            return cid.split("_")[1]

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

    def choose_discard(self, hand_card_ids, talon_card_ids):
        self._is_declarer = True
        decision = self.discard_decision(hand_card_ids, talon_card_ids)
        return decision["discard"]

    # ------------------------------------------------------------------
    # Contract — ALWAYS suit at minimum level, NEVER sans/betl
    # ------------------------------------------------------------------

    def bid_decision(self, hand, legal_levels, winner_bid):
        """Always pick suit contract at minimum level. Prefer cheaper suits.

        NEVER choose sans or betl — catastrophic downside.
        """
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
            # G6+G8 iter22: both lost -36 with 2A + scattered jacks. Reduce
            # 2-ace rates: est 2.0-2.5 at 80%/50% (was 85/55).
            # Below 2.0: 70%/50% (was 80/60). Losses (-36 each) prove 2-ace
            # hands with inflated est from jacks are unreliable.
            if aces >= 2:
                if est_tricks >= 2.5:
                    return {"action": "follow",
                            "intent": f"follow — {aces} aces, strong est ({est_tricks:.1f} tricks)"}
                if est_tricks >= 2.0:
                    rate = 0.50 if is_high_level else 0.80
                    if self.rng.random() < rate:
                        return {"action": "follow",
                                "intent": f"follow — {aces} aces, {int(rate*100)}% rate ({est_tricks:.1f} tricks)"}
                    return {"action": "pass",
                            "intent": f"pass — {aces} aces est 2.0-2.5 rolled >{int(rate*100)}% ({est_tricks:.1f} tricks)"}
                if is_high_level:
                    rate = 0.50
                else:
                    rate = 0.70
                if self.rng.random() < rate:
                    return {"action": "follow",
                            "intent": f"follow — {aces} aces, {int(rate*100)}% rate ({est_tricks:.1f} tricks)"}
                return {"action": "pass",
                        "intent": f"pass — {aces} aces but weak est, rolled >{int(rate*100)}% ({est_tricks:.1f} tricks)"}

            # 1 ace: tiered by est_tricks. Check for A-K combo in side suit:
            # guaranteed ~1.5 tricks from one suit = strong anchor for whisting.
            # G3 iter13: Carol had AK spades (1 ace) but passed — missed +20 income.
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
                    rate = 0.20 if est_tricks >= 1.5 else 0.08
                else:
                    if est_tricks >= 2.0:
                        rate = 0.85  # Strong 1-ace hand (was 0.80)
                    elif est_tricks >= 1.5:
                        rate = 0.60  # Decent support (was 0.55)
                    elif est_tricks >= 1.0:
                        rate = 0.22  # Marginal (was 0.20)
                        # G7 iter20: 1A + 2 unsup kings, est ~1.3, called at 25%,
                        # lost -80. Losses are asymmetric (-80) vs gains (+8-16).
                    else:
                        rate = 0.08  # Weak 1-ace hand
                    # A-K combo in side suit bumps rate: reliable trick anchor
                    if has_ak_combo:
                        rate = min(rate + 0.15, 1.0)
                    # Void-suit bonus: ruffing potential pushes rate up
                    if has_void:
                        rate = min(rate + 0.10, 0.85)
                if self.rng.random() < rate:
                    return {"action": "follow",
                            "intent": f"follow — 1 ace, {int(rate*100)}% rate ({est_tricks:.1f} tricks)"}
                return {"action": "pass",
                        "intent": f"pass — 1 ace but rolled >{int(rate*100)}% ({est_tricks:.1f} tricks)"}

            # 0 aces: tightened from 10%/3% → 8%/3%.
            # 0-ace whists are almost always losing.
            if not is_high_level:
                rate = 0.08 if est_tricks >= 1.0 else 0.03
                if self.rng.random() < rate:
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
        """Whister leading: lead aces from A-K suits first, then shortest suit."""
        groups = self._suit_groups(legal_cards)

        # Lead aces first — guaranteed trick winners
        # Prefer aces from A-K combo suits: cash the ace, king promoted next trick.
        # G8 iter13: 2 aces lost -80 — need to extract max value from ace leads.
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

        # Lead highest from shortest suit to try to void it
        shortest_suit = min(groups.keys(), key=lambda s: len(groups[s]))
        return groups[shortest_suit][0]


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

    # Create game and players
    game = Game(id="test-game")
    for name in ["Alice", "Bob", "Carol"]:
        p = Player(id=0, name=name, player_type=PlayerType.HUMAN)
        game.add_player(p)

    engine = GameEngine(game)
    engine.start_game()

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
    # AUCTION
    # ------------------------------------------------------------------
    emit("--- Auction ---")
    max_auction_steps = 30
    while rnd.phase == RoundPhase.AUCTION and max_auction_steps > 0:
        max_auction_steps -= 1
        auction = rnd.auction
        bidder_id = auction.current_bidder_id
        if bidder_id is None:
            break
        bidder = game.get_player(bidder_id)
        legal_bids = engine.get_legal_bids(bidder_id)
        if not legal_bids:
            break

        strat = strategies[bidder_id]
        strat._hand = bidder.hand
        chosen = strat.choose_bid(legal_bids)

        bid_label = chosen.get("label", chosen["bid_type"])
        intent = getattr(strat, 'last_bid_intent', '')
        if intent:
            emit(f"  P{bidder.position} {bidder.name}: {bid_label}  [{intent}]")
        else:
            emit(f"  P{bidder.position} {bidder.name}: {bid_label}")

        # Compact log: record bid intent
        bt = chosen["bid_type"]
        if bt == "pass":
            compact_bid_label = "pass"
        elif bt in ("sans", "betl"):
            compact_bid_label = bt
        else:
            # game or in_hand: map intended suit to index 0 (strongest)
            compact_bid_label = "0"
        compact_bids.append((bidder.name, list(bidder.hand), compact_bid_label))

        engine.place_bid(bidder_id, chosen["bid_type"], chosen.get("value", 0))

    if rnd.phase == RoundPhase.REDEAL:
        emit("  => All passed — redeal (game over for this round)")
        emit("")
        emit("--- Result: REDEAL ---")
        # Compact log for redeal
        for name, hand, blabel in compact_bids:
            compact.append(f"{name} bid: {compact_hand_fmt(hand)} -> {blabel}")
        compact.append("")
        for p in sorted(game.players, key=lambda p: p.position):
            compact.append(f"{p.name} score: {p.score}")
        return log, compact

    emit(f"")
    declarer = game.get_player(rnd.declarer_id)
    winner_bid = rnd.auction.get_winner_bid()
    emit(f"Auction winner: P{declarer.position} {declarer.name} "
         f"(bid: {winner_bid.bid_type.value} {winner_bid.value})")
    emit(f"")

    # ------------------------------------------------------------------
    # EXCHANGE (if not in-hand)
    # ------------------------------------------------------------------
    if rnd.phase == RoundPhase.EXCHANGING:
        emit("--- Exchange ---")
        emit(f"  Talon: {' '.join(card_str(c) for c in rnd.talon)}")

        strat = strategies[declarer.id]
        hand_ids = [c.id for c in declarer.hand]
        talon_ids = [c.id for c in rnd.talon]
        discard_ids = strat.choose_discard(hand_ids, talon_ids)

        engine.complete_exchange(declarer.id, discard_ids)

        emit(f"  {declarer.name} discards: {', '.join(discard_ids)}")
        emit(f"  Hand after exchange: {hand_str(declarer.hand)}")
        emit(f"")

        # Announce contract
        legal_levels = engine.get_legal_contract_levels(declarer.id)
        ctype, trump, level = strat.choose_contract(legal_levels, declarer.hand, winner_bid)
        engine.announce_contract(declarer.id, ctype, trump_suit=trump, level=level)

        trump_display = f" ({trump})" if trump else ""
        emit(f"Contract: {ctype}{trump_display} level {rnd.contract.bid_value}")
        emit(f"")

    elif rnd.phase == RoundPhase.PLAYING and rnd.contract is None:
        # In-hand with undeclared value — declarer must announce
        emit("--- In-Hand Contract Declaration ---")
        strat = strategies[declarer.id]
        legal_levels = engine.get_legal_contract_levels(declarer.id)
        ctype, trump, level = strat.choose_contract(legal_levels, declarer.hand, winner_bid)
        engine.announce_contract(declarer.id, ctype, trump_suit=trump, level=level)

        trump_display = f" ({trump})" if trump else ""
        emit(f"Contract: {ctype}{trump_display} level {rnd.contract.bid_value} (in-hand)")
        emit(f"")

    # ------------------------------------------------------------------
    # WHISTING
    # ------------------------------------------------------------------
    if rnd.phase == RoundPhase.WHISTING:
        emit("--- Whisting ---")
        max_whist_steps = 20
        while rnd.phase == RoundPhase.WHISTING and max_whist_steps > 0:
            max_whist_steps -= 1
            wid = rnd.whist_current_id
            if wid is None:
                break
            wp = game.get_player(wid)
            legal_actions = engine.get_legal_whist_actions(wid)
            if not legal_actions:
                break

            strat = strategies[wid]
            strat._hand = wp.hand
            strat._contract_type = rnd.contract.type.value if rnd.contract else None
            strat._trump_suit = rnd.contract.trump_suit if rnd.contract else None
            action = strat.choose_whist_action(legal_actions)

            emit(f"  P{wp.position} {wp.name}: {action}")

            # Compact log: record whisting decision
            contract = rnd.contract
            if contract.type == ContractType.SANS:
                c_label = "sans"
            elif contract.type == ContractType.BETL:
                c_label = "betl"
            else:
                c_label = str(compact_suit_index(contract.trump_suit, wp.hand))
            compact_action = "call" if action not in ("pass",) else "pass"
            compact_whists.append((wp.name, list(wp.hand), c_label, compact_action))

            if rnd.whist_declaring_done:
                engine.declare_counter_action(wid, action)
            else:
                engine.declare_whist(wid, action)

        if rnd.phase == RoundPhase.SCORING:
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
    seed = random.randint(1, 999999)
    if len(sys.argv) > 1:
        seed = int(sys.argv[1])

    # Create strategies — each player gets a unique seed for weights & moves
    alice = PlayerAlice(seed=seed)
    bob = PlayerBob(seed=seed + 1)
    carol = PlayerCarol(seed=seed + 2)

    strategies = {1: alice, 2: bob, 3: carol}

    # Show weights
    print(f"Seed: {seed}")
    for name, s in [("Alice", alice), ("Bob", bob), ("Carol", carol)]:
        print(f"  {name} weights: {s.weights_str()}")
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
