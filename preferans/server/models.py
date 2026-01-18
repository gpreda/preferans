"""Game models for Preferans."""
from enum import IntEnum, Enum
from dataclasses import dataclass, field
from typing import Optional
import random


# === Enums ===

class Suit(IntEnum):
    CLUBS = 1
    DIAMONDS = 2
    HEARTS = 3
    SPADES = 4


class Rank(IntEnum):
    SEVEN = 1
    EIGHT = 2
    NINE = 3
    TEN = 4
    JACK = 5
    QUEEN = 6
    KING = 7
    ACE = 8


class ContractType(Enum):
    SUIT = "suit"
    BETL = "betl"
    SANS = "sans"


class GameStatus(Enum):
    WAITING = "waiting"
    BIDDING = "bidding"
    PLAYING = "playing"
    SCORING = "scoring"
    FINISHED = "finished"


class RoundPhase(Enum):
    DEALING = "dealing"
    AUCTION = "auction"
    EXCHANGING = "exchanging"
    PLAYING = "playing"
    SCORING = "scoring"


class PlayerType(Enum):
    HUMAN = "human"
    AI = "ai"


class BidType(Enum):
    PASS = "pass"
    GAME = "game"           # Regular game bid (2-5)
    IN_HAND = "in_hand"     # Intent to play in_hand (undeclared value)
    BETL = "betl"           # Bid 6 = betl
    SANS = "sans"           # Bid 7 = sans


class AuctionPhase(Enum):
    INITIAL = "initial"                 # First bids: pass, game 2, or in_hand
    GAME_BIDDING = "game_bidding"       # Normal game bidding (2-5, then 6=betl, 7=sans)
    IN_HAND_DECIDING = "in_hand_deciding"  # Other players decide pass or in_hand
    IN_HAND_DECLARING = "in_hand_declaring"  # In_hand bidders declare their values
    COMPLETE = "complete"


# === Mappings ===

SUIT_NAMES = {
    Suit.CLUBS: "clubs",
    Suit.DIAMONDS: "diamonds",
    Suit.HEARTS: "hearts",
    Suit.SPADES: "spades",
}

RANK_NAMES = {
    Rank.SEVEN: "7",
    Rank.EIGHT: "8",
    Rank.NINE: "9",
    Rank.TEN: "10",
    Rank.JACK: "J",
    Rank.QUEEN: "Q",
    Rank.KING: "K",
    Rank.ACE: "A",
}

NAME_TO_SUIT = {v: k for k, v in SUIT_NAMES.items()}
NAME_TO_RANK = {v: k for k, v in RANK_NAMES.items()}

# Sort order for hand display (spades > diamonds > clubs > hearts, 7 > 8 > ... > A)
SUIT_SORT_ORDER = {
    Suit.SPADES: 4,
    Suit.DIAMONDS: 3,
    Suit.CLUBS: 2,
    Suit.HEARTS: 1,
}

RANK_SORT_ORDER = {
    Rank.SEVEN: 8,
    Rank.EIGHT: 7,
    Rank.NINE: 6,
    Rank.TEN: 5,
    Rank.JACK: 4,
    Rank.QUEEN: 3,
    Rank.KING: 2,
    Rank.ACE: 1,
}


# === Models ===

@dataclass
class Card:
    rank: Rank
    suit: Suit

    @property
    def id(self) -> str:
        return f"{RANK_NAMES[self.rank]}_{SUIT_NAMES[self.suit]}"

    @property
    def rank_value(self) -> int:
        return self.rank.value

    @property
    def suit_value(self) -> int:
        return self.suit.value

    def beats(self, other: "Card", trump_suit: Optional[Suit] = None, led_suit: Optional[Suit] = None) -> bool:
        """Check if this card beats another card."""
        print(f"[beats] {self.id} vs {other.id}, trump={trump_suit}, led={led_suit}")

        # Trump beats non-trump
        if trump_suit:
            if self.suit == trump_suit and other.suit != trump_suit:
                print(f"[beats] {self.id} is trump, {other.id} is not -> True")
                return True
            if self.suit != trump_suit and other.suit == trump_suit:
                print(f"[beats] {self.id} is not trump, {other.id} is trump -> False")
                return False

        # Same suit: higher rank wins
        if self.suit == other.suit:
            result = self.rank_value > other.rank_value
            print(f"[beats] Same suit, rank {self.rank_value} vs {other.rank_value} -> {result}")
            return result

        # Different suits (no trump involved): led suit wins
        if led_suit:
            if self.suit == led_suit and other.suit != led_suit:
                print(f"[beats] {self.id} is led suit, {other.id} is not -> True")
                return True
            if self.suit != led_suit and other.suit == led_suit:
                print(f"[beats] {self.id} is not led suit, {other.id} is -> False")
                return False

        # Different suits, neither is led: first card wins (shouldn't happen in valid play)
        print(f"[beats] Different suits, neither trump nor led -> False")
        return False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "rank": RANK_NAMES[self.rank],
            "suit": SUIT_NAMES[self.suit],
            "rank_value": self.rank_value,
            "suit_value": self.suit_value,
        }

    @classmethod
    def from_id(cls, card_id: str) -> "Card":
        rank_str, suit_str = card_id.split("_")
        return cls(rank=NAME_TO_RANK[rank_str], suit=NAME_TO_SUIT[suit_str])


@dataclass
class Player:
    id: int
    name: str
    player_type: PlayerType = PlayerType.HUMAN
    position: int = 0  # 1=forehand, 2=middlehand, 3=dealer
    hand: list[Card] = field(default_factory=list)
    tricks_won: int = 0
    score: int = 0
    is_declarer: bool = False
    has_dropped_out: bool = False

    @property
    def is_human(self) -> bool:
        return self.player_type == PlayerType.HUMAN

    @property
    def is_ai(self) -> bool:
        return self.player_type == PlayerType.AI

    def add_card(self, card: Card):
        self.hand.append(card)

    def remove_card(self, card: Card):
        self.hand.remove(card)

    def has_suit(self, suit: Suit) -> bool:
        return any(c.suit == suit for c in self.hand)

    def get_cards_of_suit(self, suit: Suit) -> list[Card]:
        return [c for c in self.hand if c.suit == suit]

    def sort_hand(self):
        """Sort hand by suit (spades > diamonds > clubs > hearts) then rank (7 > 8 > ... > A)."""
        self.hand.sort(key=lambda c: (SUIT_SORT_ORDER[c.suit], RANK_SORT_ORDER[c.rank]), reverse=True)

    def reset_for_round(self):
        self.hand = []
        self.tricks_won = 0
        self.is_declarer = False
        self.has_dropped_out = False

    def to_dict(self, hide_hand: bool = False) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "player_type": self.player_type.value,
            "is_human": self.is_human,
            "is_ai": self.is_ai,
            "position": self.position,
            "hand": [] if hide_hand else [c.to_dict() for c in self.hand],
            "hand_count": len(self.hand),
            "tricks_won": self.tricks_won,
            "score": self.score,
            "is_declarer": self.is_declarer,
            "has_dropped_out": self.has_dropped_out,
        }


@dataclass
class Bid:
    player_id: int
    bid_type: BidType
    value: int = 0  # 0 for pass/in_hand intent, 2-5 for game, 6 for betl, 7 for sans
    suit: Optional[Suit] = None

    def is_pass(self) -> bool:
        return self.bid_type == BidType.PASS

    def is_in_hand(self) -> bool:
        return self.bid_type == BidType.IN_HAND

    def is_game(self) -> bool:
        return self.bid_type == BidType.GAME

    def is_betl(self) -> bool:
        return self.bid_type == BidType.BETL

    def is_sans(self) -> bool:
        return self.bid_type == BidType.SANS

    @property
    def effective_value(self) -> int:
        """Get the effective bid value for comparison."""
        if self.bid_type == BidType.PASS:
            return 0
        if self.bid_type == BidType.IN_HAND:
            return self.value if self.value > 0 else 0
        if self.bid_type == BidType.GAME:
            return self.value
        if self.bid_type == BidType.BETL:
            return 6
        if self.bid_type == BidType.SANS:
            return 7
        return 0

    def to_dict(self) -> dict:
        return {
            "player_id": self.player_id,
            "bid_type": self.bid_type.value,
            "value": self.value,
            "suit": SUIT_NAMES[self.suit] if self.suit else None,
            "is_pass": self.is_pass(),
            "is_in_hand": self.is_in_hand(),
            "effective_value": self.effective_value,
        }


@dataclass
class Contract:
    type: ContractType
    trump_suit: Optional[Suit] = None  # None for betl/sans
    bid_value: int = 2  # 2-5 for game, 6 for betl, 7 for sans
    is_in_hand: bool = False  # True if played without picking up talon

    @property
    def tricks_required(self) -> int:
        if self.type == ContractType.BETL:
            return 0
        return 6  # suit and sans

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "trump_suit": SUIT_NAMES[self.trump_suit] if self.trump_suit else None,
            "bid_value": self.bid_value,
            "is_in_hand": self.is_in_hand,
            "tricks_required": self.tricks_required,
        }


@dataclass
class Trick:
    number: int
    lead_player_id: int
    cards: list[tuple[int, Card]] = field(default_factory=list)  # [(player_id, card), ...]
    winner_id: Optional[int] = None

    @property
    def suit_led(self) -> Optional[Suit]:
        if self.cards:
            return self.cards[0][1].suit
        return None

    def add_card(self, player_id: int, card: Card):
        self.cards.append((player_id, card))

    def determine_winner(self, trump_suit: Optional[Suit] = None) -> int:
        """Determine the winner of the trick."""
        if not self.cards:
            raise ValueError("No cards in trick")

        winning_player_id, winning_card = self.cards[0]
        led_suit = winning_card.suit

        for player_id, card in self.cards[1:]:
            if card.beats(winning_card, trump_suit=trump_suit, led_suit=led_suit):
                winning_player_id = player_id
                winning_card = card

        self.winner_id = winning_player_id
        return winning_player_id

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "lead_player_id": self.lead_player_id,
            "cards": [{"player_id": pid, "card": c.to_dict()} for pid, c in self.cards],
            "winner_id": self.winner_id,
            "suit_led": SUIT_NAMES[self.suit_led] if self.suit_led else None,
        }


@dataclass
class Auction:
    bids: list[Bid] = field(default_factory=list)
    current_bidder_id: Optional[int] = None
    highest_game_bid: Optional[Bid] = None  # Highest game bid (2-7)
    passed_players: list[int] = field(default_factory=list)
    phase: AuctionPhase = AuctionPhase.INITIAL
    # For game bidding: track the original highest bidder (who can "hold")
    first_game_bidder_id: Optional[int] = None
    # For in_hand: track players who declared in_hand intent
    in_hand_players: list[int] = field(default_factory=list)
    # For in_hand declaring: track the highest in_hand declaration
    highest_in_hand_bid: Optional[Bid] = None
    # Track who has already bid in current phase
    players_bid_this_phase: list[int] = field(default_factory=list)

    def add_bid(self, bid: Bid):
        self.bids.append(bid)
        if bid.is_pass():
            if bid.player_id not in self.passed_players:
                self.passed_players.append(bid.player_id)
        elif bid.is_in_hand():
            if bid.player_id not in self.in_hand_players:
                self.in_hand_players.append(bid.player_id)
            # If in_hand has a value (declaration phase)
            if bid.value > 0:
                if self.highest_in_hand_bid is None or bid.value > self.highest_in_hand_bid.value:
                    self.highest_in_hand_bid = bid
        elif bid.is_game() or bid.is_betl() or bid.is_sans():
            # First game bidder can "hold" (match) - their bid becomes highest
            is_hold = (bid.player_id == self.first_game_bidder_id and
                      self.highest_game_bid and
                      bid.effective_value == self.highest_game_bid.effective_value)

            if self.highest_game_bid is None or bid.effective_value > self.highest_game_bid.effective_value or is_hold:
                self.highest_game_bid = bid
                if self.first_game_bidder_id is None:
                    self.first_game_bidder_id = bid.player_id

    def get_winner_bid(self) -> Optional[Bid]:
        """Get the winning bid."""
        if self.highest_in_hand_bid:
            return self.highest_in_hand_bid
        return self.highest_game_bid

    def get_winner_player_id(self) -> Optional[int]:
        """Get the ID of the auction winner."""
        winner_bid = self.get_winner_bid()
        return winner_bid.player_id if winner_bid else None

    def is_complete(self) -> bool:
        return self.phase == AuctionPhase.COMPLETE

    def to_dict(self) -> dict:
        return {
            "bids": [b.to_dict() for b in self.bids],
            "current_bidder_id": self.current_bidder_id,
            "highest_game_bid": self.highest_game_bid.to_dict() if self.highest_game_bid else None,
            "highest_in_hand_bid": self.highest_in_hand_bid.to_dict() if self.highest_in_hand_bid else None,
            "passed_players": self.passed_players,
            "phase": self.phase.value,
            "in_hand_players": self.in_hand_players,
            "first_game_bidder_id": self.first_game_bidder_id,
        }


@dataclass
class Round:
    id: int
    talon: list[Card] = field(default_factory=list)
    discarded: list[Card] = field(default_factory=list)
    declarer_id: Optional[int] = None
    contract: Optional[Contract] = None
    tricks: list[Trick] = field(default_factory=list)
    auction: Auction = field(default_factory=Auction)
    phase: RoundPhase = RoundPhase.DEALING

    @property
    def current_trick(self) -> Optional[Trick]:
        if self.tricks:
            return self.tricks[-1]
        return None

    def start_new_trick(self, lead_player_id: int) -> Trick:
        trick = Trick(number=len(self.tricks) + 1, lead_player_id=lead_player_id)
        self.tricks.append(trick)
        return trick

    def to_dict(self, hide_talon: bool = True) -> dict:
        return {
            "id": self.id,
            "talon": [] if hide_talon else [c.to_dict() for c in self.talon],
            "talon_count": len(self.talon),
            "discarded": [c.to_dict() for c in self.discarded],
            "declarer_id": self.declarer_id,
            "contract": self.contract.to_dict() if self.contract else None,
            "tricks": [t.to_dict() for t in self.tricks],
            "auction": self.auction.to_dict(),
            "phase": self.phase.value,
        }


@dataclass
class Game:
    id: str
    players: list[Player] = field(default_factory=list)
    bulls: int = 0
    dealer_index: int = 0
    current_round: Optional[Round] = None
    round_number: int = 0
    status: GameStatus = GameStatus.WAITING

    def add_player(self, player: Player) -> bool:
        if len(self.players) >= 3:
            return False
        player.id = len(self.players) + 1
        self.players.append(player)
        return True

    def add_human_player(self, name: str) -> Optional[Player]:
        """Add a human player to the game."""
        if len(self.players) >= 3:
            return None
        player = Player(id=0, name=name, player_type=PlayerType.HUMAN)
        self.add_player(player)
        return player

    def add_ai_player(self, name: str = None) -> Optional[Player]:
        """Add an AI player to the game."""
        if len(self.players) >= 3:
            return None
        ai_number = sum(1 for p in self.players if p.is_ai) + 1
        if name is None:
            name = f"AI Player {ai_number}"
        player = Player(id=0, name=name, player_type=PlayerType.AI)
        self.add_player(player)
        return player

    def fill_with_ai(self):
        """Fill remaining slots with AI players."""
        while len(self.players) < 3:
            self.add_ai_player()

    def get_human_players(self) -> list[Player]:
        """Get all human players."""
        return [p for p in self.players if p.is_human]

    def get_ai_players(self) -> list[Player]:
        """Get all AI players."""
        return [p for p in self.players if p.is_ai]

    def get_player(self, player_id: int) -> Optional[Player]:
        for p in self.players:
            if p.id == player_id:
                return p
        return None

    def rotate_dealer(self):
        self.dealer_index = (self.dealer_index + 1) % 3

    def assign_positions(self):
        """Assign positions based on dealer.

        Positions: 1=forehand (left of dealer), 2=middlehand, 3=dealer
        """
        for i, player in enumerate(self.players):
            offset = (i - self.dealer_index) % 3
            if offset == 0:
                player.position = 3  # dealer
            elif offset == 1:
                player.position = 1  # forehand (plays first)
            else:
                player.position = 2  # middlehand

    def create_deck(self) -> list[Card]:
        """Create a standard 32-card deck."""
        deck = []
        for suit in Suit:
            for rank in Rank:
                deck.append(Card(rank=rank, suit=suit))
        return deck

    def shuffle_and_deal(self):
        """Shuffle deck and deal cards: 3 - talon(2) - 4 - 3."""
        # Reset players
        for player in self.players:
            player.reset_for_round()

        # Create and shuffle deck
        deck = self.create_deck()
        random.shuffle(deck)

        # Create new round
        self.round_number += 1
        self.current_round = Round(id=self.round_number)

        # Deal: 3 - talon - 4 - 3
        idx = 0
        player_order = sorted(self.players, key=lambda p: p.position)

        # First 3 cards to each player
        for player in player_order:
            for _ in range(3):
                player.add_card(deck[idx])
                idx += 1

        # 2 cards to talon
        self.current_round.talon = [deck[idx], deck[idx + 1]]
        idx += 2

        # Next 4 cards to each player
        for player in player_order:
            for _ in range(4):
                player.add_card(deck[idx])
                idx += 1

        # Final 3 cards to each player
        for player in player_order:
            for _ in range(3):
                player.add_card(deck[idx])
                idx += 1

        # Sort hands
        for player in self.players:
            player.sort_hand()

        self.current_round.phase = RoundPhase.AUCTION
        self.status = GameStatus.BIDDING

    def to_dict(self, viewer_id: Optional[int] = None) -> dict:
        """Convert to dict, optionally hiding other players' hands."""
        return {
            "id": self.id,
            "players": [
                p.to_dict(hide_hand=(viewer_id is not None and p.id != viewer_id))
                for p in self.players
            ],
            "bulls": self.bulls,
            "dealer_index": self.dealer_index,
            "current_round": self.current_round.to_dict() if self.current_round else None,
            "round_number": self.round_number,
            "status": self.status.value,
        }
