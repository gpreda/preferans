"""Game engine for Preferans - handles all game logic."""
from typing import Optional
from models import (
    Game, Player, Card, Round, Trick, Bid, Contract, Auction,
    Suit, Rank, ContractType, GameStatus, RoundPhase, PlayerType,
    SUIT_NAMES, NAME_TO_SUIT
)


class GameError(Exception):
    """Base exception for game errors."""
    pass


class InvalidMoveError(GameError):
    """Raised when a player makes an invalid move."""
    pass


class InvalidPhaseError(GameError):
    """Raised when an action is attempted in the wrong phase."""
    pass


class GameEngine:
    """Manages game state and enforces rules for Preferans."""

    def __init__(self, game: Game):
        self.game = game

    # === Game Setup ===

    def start_game(self):
        """Start the game with the current players."""
        if len(self.game.players) != 3:
            raise GameError("Game requires exactly 3 players")

        self.game.status = GameStatus.PLAYING
        self.start_new_round()

    def start_new_round(self):
        """Start a new round: shuffle, deal, and begin auction."""
        self.game.assign_positions()
        self.game.shuffle_and_deal()

        # Set first bidder to forehand (position 1)
        forehand = self._get_player_by_position(1)
        self.game.current_round.auction.current_bidder_id = forehand.id

    # === Bidding Phase ===

    def place_bid(self, player_id: int, value: int, suit: Optional[str] = None, is_hand: bool = False) -> Bid:
        """Place a bid during the auction phase."""
        self._validate_phase(RoundPhase.AUCTION)
        player = self._get_player(player_id)
        auction = self.game.current_round.auction

        # Validate it's this player's turn
        if auction.current_bidder_id != player_id:
            raise InvalidMoveError(f"Not player {player_id}'s turn to bid")

        # Validate player hasn't already passed
        if player_id in auction.passed_players:
            raise InvalidMoveError("Player has already passed")

        # Create bid
        suit_enum = NAME_TO_SUIT.get(suit) if suit else None
        bid = Bid(player_id=player_id, value=value, suit=suit_enum, is_hand=is_hand)

        # Validate bid value
        if not bid.is_pass():
            if value < 2 or value > 7:
                raise InvalidMoveError("Bid value must be between 2 and 7")
            if auction.highest_bid and not bid.beats(auction.highest_bid):
                raise InvalidMoveError("Bid must be higher than current highest bid")

        # Record bid
        auction.add_bid(bid)

        # Move to next bidder or end auction
        self._advance_auction()

        return bid

    def _advance_auction(self):
        """Advance to the next bidder or end the auction."""
        auction = self.game.current_round.auction
        round = self.game.current_round

        # Check if auction is complete (2 players passed)
        active_players = [p for p in self.game.players if p.id not in auction.passed_players]

        if len(active_players) == 1:
            # Auction complete - one player remaining
            winner = active_players[0]
            winner.is_declarer = True
            round.declarer_id = winner.id
            round.phase = RoundPhase.EXCHANGING
            return

        if len(active_players) == 0:
            # Everyone passed - special case: dealer must play or pay penalty
            # For simplicity, we'll force a redeal
            self.start_new_round()
            return

        # Find next bidder (rotate through positions)
        current_player = self._get_player(auction.current_bidder_id)
        next_position = (current_player.position % 3) + 1

        for _ in range(3):
            next_player = self._get_player_by_position(next_position)
            if next_player.id not in auction.passed_players:
                auction.current_bidder_id = next_player.id
                return
            next_position = (next_position % 3) + 1

    # === Exchange Phase ===

    def pick_up_talon(self, player_id: int) -> list[Card]:
        """Declarer picks up the talon cards."""
        self._validate_phase(RoundPhase.EXCHANGING)
        round = self.game.current_round

        if round.declarer_id != player_id:
            raise InvalidMoveError("Only declarer can pick up talon")

        player = self._get_player(player_id)

        # Add talon cards to player's hand
        for card in round.talon:
            player.add_card(card)

        player.sort_hand()
        return round.talon

    def discard_cards(self, player_id: int, card_ids: list[str]) -> list[Card]:
        """Declarer discards two cards after picking up talon."""
        self._validate_phase(RoundPhase.EXCHANGING)
        round = self.game.current_round

        if round.declarer_id != player_id:
            raise InvalidMoveError("Only declarer can discard")

        if len(card_ids) != 2:
            raise InvalidMoveError("Must discard exactly 2 cards")

        player = self._get_player(player_id)

        # Validate player has 12 cards (10 + 2 from talon)
        if len(player.hand) != 12:
            raise InvalidMoveError("Must pick up talon before discarding")

        # Find and remove cards
        discarded = []
        for card_id in card_ids:
            card = self._find_card_in_hand(player, card_id)
            if not card:
                raise InvalidMoveError(f"Card {card_id} not in hand")
            player.remove_card(card)
            discarded.append(card)

        round.discarded = discarded
        player.sort_hand()
        return discarded

    def announce_contract(self, player_id: int, contract_type: str, trump_suit: Optional[str] = None):
        """Declarer announces the contract after discarding."""
        self._validate_phase(RoundPhase.EXCHANGING)
        round = self.game.current_round

        if round.declarer_id != player_id:
            raise InvalidMoveError("Only declarer can announce contract")

        player = self._get_player(player_id)
        if len(player.hand) != 10:
            raise InvalidMoveError("Must discard before announcing contract")

        # Parse contract type
        try:
            ctype = ContractType(contract_type)
        except ValueError:
            raise InvalidMoveError(f"Invalid contract type: {contract_type}")

        # Validate trump suit for suit contracts
        trump = None
        if ctype == ContractType.SUIT:
            if not trump_suit:
                raise InvalidMoveError("Suit contract requires trump suit")
            trump = NAME_TO_SUIT.get(trump_suit)
            if not trump:
                raise InvalidMoveError(f"Invalid trump suit: {trump_suit}")
        elif trump_suit:
            raise InvalidMoveError(f"{contract_type} contract cannot have trump suit")

        # Get bid value from auction
        bid_value = round.auction.highest_bid.value if round.auction.highest_bid else 2

        # Create contract
        round.contract = Contract(
            type=ctype,
            trump_suit=trump,
            bid_value=bid_value,
            is_hand=round.auction.highest_bid.is_hand if round.auction.highest_bid else False
        )

        # Move to playing phase
        round.phase = RoundPhase.PLAYING

        # Start first trick - declarer leads
        round.start_new_trick(lead_player_id=player_id)

    # === Playing Phase ===

    def play_card(self, player_id: int, card_id: str) -> dict:
        """Play a card to the current trick."""
        self._validate_phase(RoundPhase.PLAYING)
        round = self.game.current_round
        trick = round.current_trick

        if not trick:
            raise GameError("No active trick")

        # Validate it's this player's turn
        expected_player_id = self._get_next_player_in_trick(trick)
        if player_id != expected_player_id:
            raise InvalidMoveError(f"Not player {player_id}'s turn")

        player = self._get_player(player_id)

        # Find card in hand
        card = self._find_card_in_hand(player, card_id)
        if not card:
            raise InvalidMoveError(f"Card {card_id} not in hand")

        # Validate card is legal to play
        self._validate_card_play(player, card, trick, round.contract)

        # Play the card
        player.remove_card(card)
        trick.add_card(player_id, card)

        result = {"card": card.to_dict(), "trick_complete": False}

        # Check if trick is complete (3 cards played)
        if len(trick.cards) == 3:
            trump = round.contract.trump_suit if round.contract.type == ContractType.SUIT else None
            winner_id = trick.determine_winner(trump_suit=trump)
            winner = self._get_player(winner_id)
            winner.tricks_won += 1

            result["trick_complete"] = True
            result["trick_winner_id"] = winner_id

            # Check if round is complete (10 tricks)
            if len(round.tricks) == 10:
                self._end_round()
                result["round_complete"] = True
            else:
                # Start new trick - winner leads
                round.start_new_trick(lead_player_id=winner_id)

        return result

    def _validate_card_play(self, player: Player, card: Card, trick: Trick, contract: Contract):
        """Validate that playing this card is legal."""
        if not trick.cards:
            # Leading - any card is legal
            return

        led_suit = trick.suit_led
        trump_suit = contract.trump_suit if contract.type == ContractType.SUIT else None

        # Must follow suit if possible
        if player.has_suit(led_suit):
            if card.suit != led_suit:
                raise InvalidMoveError(f"Must follow suit ({SUIT_NAMES[led_suit]})")
            return

        # Can't follow suit - must trump if possible (only in suit contracts)
        if contract.type == ContractType.SUIT and trump_suit and player.has_suit(trump_suit):
            if card.suit != trump_suit:
                raise InvalidMoveError(f"Must play trump ({SUIT_NAMES[trump_suit]})")
            return

        # Can play any card

    def _get_next_player_in_trick(self, trick: Trick) -> int:
        """Get the next player to play in the current trick."""
        if not trick.cards:
            return trick.lead_player_id

        # Find who has played
        played_ids = [pid for pid, _ in trick.cards]

        # Get lead player position and rotate
        lead_player = self._get_player(trick.lead_player_id)
        next_position = lead_player.position

        for _ in range(3):
            next_position = (next_position % 3) + 1
            next_player = self._get_player_by_position(next_position)
            if next_player.id not in played_ids:
                return next_player.id

        raise GameError("Could not determine next player")

    # === Scoring Phase ===

    def _end_round(self):
        """End the current round and calculate scores."""
        round = self.game.current_round
        round.phase = RoundPhase.SCORING

        declarer = self._get_player(round.declarer_id)
        defenders = [p for p in self.game.players if p.id != round.declarer_id]
        contract = round.contract

        # Calculate results
        declarer_tricks = declarer.tricks_won
        required_tricks = contract.tricks_required

        results = {
            "declarer_id": declarer.id,
            "declarer_tricks": declarer_tricks,
            "required_tricks": required_tricks,
            "contract_type": contract.type.value,
            "bid_value": contract.bid_value,
        }

        if contract.type == ContractType.BETL:
            # Betl: declarer must take 0 tricks
            declarer_won = declarer_tricks == 0
            results["declarer_won"] = declarer_won
            # Scoring: bid_value * 5 bulls
            score_change = contract.bid_value * 5
            if declarer_won:
                declarer.score += score_change
                for d in defenders:
                    d.score -= score_change // 2
            else:
                declarer.score -= score_change
                for d in defenders:
                    d.score += score_change // 2

        elif contract.type == ContractType.SANS:
            # Sans: declarer must take 6+ tricks (no trumps)
            declarer_won = declarer_tricks >= required_tricks
            results["declarer_won"] = declarer_won
            score_change = contract.bid_value * 10
            if declarer_won:
                declarer.score += score_change
                for d in defenders:
                    d.score -= score_change // 2
            else:
                declarer.score -= score_change
                for d in defenders:
                    d.score += score_change // 2

        else:  # SUIT contract
            # Suit: declarer needs 6+, each defender needs 2+
            declarer_won = declarer_tricks >= required_tricks
            results["declarer_won"] = declarer_won
            results["defender_results"] = []

            base_value = contract.bid_value
            trump_multiplier = contract.trump_suit.value if contract.trump_suit else 1

            # Declarer scoring
            if declarer_won:
                declarer.score += base_value * trump_multiplier
            else:
                declarer.score -= base_value * trump_multiplier * 2

            # Defender scoring
            for d in defenders:
                defender_won = d.tricks_won >= 2
                results["defender_results"].append({
                    "player_id": d.id,
                    "tricks": d.tricks_won,
                    "won": defender_won
                })
                if defender_won:
                    d.score += d.tricks_won
                else:
                    d.score -= (2 - d.tricks_won) * base_value

        results["scores"] = {p.id: p.score for p in self.game.players}
        round.phase = RoundPhase.SCORING

        return results

    def start_next_round(self):
        """Start the next round after scoring."""
        self.game.rotate_dealer()
        self.start_new_round()

    # === Helper Methods ===

    def _validate_phase(self, expected_phase: RoundPhase):
        """Validate the game is in the expected phase."""
        if not self.game.current_round:
            raise InvalidPhaseError("No active round")
        if self.game.current_round.phase != expected_phase:
            raise InvalidPhaseError(
                f"Expected phase {expected_phase.value}, "
                f"but in {self.game.current_round.phase.value}"
            )

    def _get_player(self, player_id: int) -> Player:
        """Get a player by ID."""
        player = self.game.get_player(player_id)
        if not player:
            raise GameError(f"Player {player_id} not found")
        return player

    def _get_player_by_position(self, position: int) -> Player:
        """Get a player by position (1-3)."""
        for p in self.game.players:
            if p.position == position:
                return p
        raise GameError(f"No player at position {position}")

    def _find_card_in_hand(self, player: Player, card_id: str) -> Optional[Card]:
        """Find a card in player's hand by ID."""
        for card in player.hand:
            if card.id == card_id:
                return card
        return None

    # === Game State Queries ===

    def get_legal_bids(self, player_id: int) -> list[dict]:
        """Get all legal bids for a player."""
        if self.game.current_round.phase != RoundPhase.AUCTION:
            return []

        auction = self.game.current_round.auction
        if auction.current_bidder_id != player_id:
            return []

        legal_bids = [{"value": 0, "label": "Pass"}]

        min_bid = 2
        if auction.highest_bid and not auction.highest_bid.is_pass():
            min_bid = auction.highest_bid.value + 1

        for value in range(min_bid, 8):
            legal_bids.append({"value": value, "label": f"Bid {value}"})

        return legal_bids

    def get_legal_cards(self, player_id: int) -> list[Card]:
        """Get all legal cards a player can play."""
        if self.game.current_round.phase != RoundPhase.PLAYING:
            return []

        trick = self.game.current_round.current_trick
        if not trick or self._get_next_player_in_trick(trick) != player_id:
            return []

        player = self._get_player(player_id)
        contract = self.game.current_round.contract

        # If leading, all cards are legal
        if not trick.cards:
            return player.hand.copy()

        led_suit = trick.suit_led
        trump_suit = contract.trump_suit if contract.type == ContractType.SUIT else None

        # Must follow suit if possible
        suit_cards = player.get_cards_of_suit(led_suit)
        if suit_cards:
            return suit_cards

        # Must trump if possible (suit contracts only)
        if trump_suit:
            trump_cards = player.get_cards_of_suit(trump_suit)
            if trump_cards:
                return trump_cards

        # Can play any card
        return player.hand.copy()

    def get_game_state(self, viewer_id: Optional[int] = None) -> dict:
        """Get the current game state, optionally from a player's perspective."""
        state = self.game.to_dict(viewer_id=viewer_id)

        if self.game.current_round:
            round = self.game.current_round

            # Add context based on phase
            if round.phase == RoundPhase.AUCTION:
                state["current_bidder_id"] = round.auction.current_bidder_id
                if viewer_id:
                    state["legal_bids"] = self.get_legal_bids(viewer_id)

            elif round.phase == RoundPhase.PLAYING:
                trick = round.current_trick
                if trick:
                    state["current_player_id"] = self._get_next_player_in_trick(trick)
                    if viewer_id:
                        state["legal_cards"] = [c.to_dict() for c in self.get_legal_cards(viewer_id)]

        return state
