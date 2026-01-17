"""Game engine for Preferans - handles all game logic."""
from typing import Optional
from models import (
    Game, Player, Card, Round, Trick, Bid, Contract, Auction,
    Suit, Rank, ContractType, GameStatus, RoundPhase, PlayerType,
    BidType, AuctionPhase,
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

    def place_bid(self, player_id: int, bid_type: str, value: int = 0) -> Bid:
        """Place a bid during the auction phase.

        Args:
            player_id: The player making the bid
            bid_type: One of "pass", "game", "in_hand", "betl", "sans"
            value: For game bids (2-5), or in_hand declarations (2-5)
        """
        self._validate_phase(RoundPhase.AUCTION)
        player = self._get_player(player_id)
        auction = self.game.current_round.auction

        # Validate it's this player's turn
        if auction.current_bidder_id != player_id:
            raise InvalidMoveError(f"Not player {player_id}'s turn to bid")

        # Validate player hasn't already passed
        if player_id in auction.passed_players:
            raise InvalidMoveError("Player has already passed")

        # Parse bid type
        try:
            btype = BidType(bid_type)
        except ValueError:
            raise InvalidMoveError(f"Invalid bid type: {bid_type}")

        # Validate bid based on auction phase
        self._validate_bid(player_id, btype, value, auction)

        # Create and record bid
        bid = Bid(player_id=player_id, bid_type=btype, value=value)
        auction.add_bid(bid)
        auction.players_bid_this_phase.append(player_id)

        # Advance auction state
        self._advance_auction()

        return bid

    def _validate_bid(self, player_id: int, bid_type: BidType, value: int, auction: Auction):
        """Validate a bid based on the current auction phase and rules."""

        if bid_type == BidType.PASS:
            return  # Pass is always valid

        if auction.phase == AuctionPhase.INITIAL:
            # Initial phase: can bid pass, game (must be 2), in_hand, betl, or sans
            if bid_type == BidType.GAME:
                if value != 2:
                    raise InvalidMoveError("First game bid must be 2")
            elif bid_type == BidType.IN_HAND:
                if value != 0:
                    raise InvalidMoveError("In_hand intent should not have a value")
            elif bid_type == BidType.BETL:
                pass  # Betl is valid as first bid
            elif bid_type == BidType.SANS:
                pass  # Sans is valid as first bid

        elif auction.phase == AuctionPhase.GAME_BIDDING:
            # Game bidding: must follow the progression
            if bid_type == BidType.IN_HAND:
                raise InvalidMoveError("Cannot bid in_hand after game bidding has started")

            current_high = auction.highest_game_bid.effective_value if auction.highest_game_bid else 1

            if bid_type == BidType.GAME:
                if value < 2 or value > 5:
                    raise InvalidMoveError("Game bid must be between 2 and 5")

                # Check if this is the first game bidder (can "hold")
                is_first_bidder = player_id == auction.first_game_bidder_id

                if is_first_bidder:
                    # First bidder can match (hold) or raise
                    if value < current_high:
                        raise InvalidMoveError(f"Bid must be at least {current_high}")
                else:
                    # Other bidders must raise
                    if value <= current_high:
                        raise InvalidMoveError(f"Bid must be higher than {current_high}")

            elif bid_type == BidType.BETL:
                if current_high >= 6:
                    raise InvalidMoveError("Cannot bid betl, already at betl or higher")
            elif bid_type == BidType.SANS:
                if current_high >= 7:
                    raise InvalidMoveError("Sans already bid")

        elif auction.phase == AuctionPhase.IN_HAND_DECIDING:
            # Other players deciding: can pass, in_hand, betl, or sans
            if bid_type not in [BidType.PASS, BidType.IN_HAND, BidType.BETL, BidType.SANS]:
                raise InvalidMoveError("Can only pass or declare in_hand/betl/sans")
            if bid_type == BidType.IN_HAND and value != 0:
                raise InvalidMoveError("In_hand intent should not have a value")

        elif auction.phase == AuctionPhase.IN_HAND_DECLARING:
            # In_hand players declaring their values
            if bid_type != BidType.IN_HAND and bid_type != BidType.PASS:
                raise InvalidMoveError("Must declare in_hand value or pass")
            if bid_type == BidType.IN_HAND:
                if value < 2 or value > 5:
                    raise InvalidMoveError("In_hand value must be between 2 and 5")
                if auction.highest_in_hand_bid and value <= auction.highest_in_hand_bid.value:
                    raise InvalidMoveError(f"Must bid higher than {auction.highest_in_hand_bid.value}")

    def _advance_auction(self):
        """Advance to the next bidder or end the auction."""
        auction = self.game.current_round.auction
        round = self.game.current_round

        # Handle phase transitions based on last bid
        last_bid = auction.bids[-1] if auction.bids else None

        if auction.phase == AuctionPhase.INITIAL:
            self._handle_initial_phase_advance(auction, last_bid)
        elif auction.phase == AuctionPhase.GAME_BIDDING:
            self._handle_game_bidding_advance(auction)
        elif auction.phase == AuctionPhase.IN_HAND_DECIDING:
            self._handle_in_hand_deciding_advance(auction)
        elif auction.phase == AuctionPhase.IN_HAND_DECLARING:
            self._handle_in_hand_declaring_advance(auction)

        # Check if auction is complete
        if auction.phase == AuctionPhase.COMPLETE:
            self._finalize_auction()

    def _handle_initial_phase_advance(self, auction: Auction, last_bid: Bid):
        """Handle advancement from initial phase."""
        # If someone bid in_hand, betl, or sans, switch to in_hand_deciding for others
        # (betl and sans in initial phase are in_hand variants)
        if last_bid and (last_bid.is_in_hand() or last_bid.is_betl() or last_bid.is_sans()):
            # Mark as in_hand player
            if last_bid.player_id not in auction.in_hand_players:
                auction.in_hand_players.append(last_bid.player_id)
            # Set their declared value for betl/sans
            if last_bid.is_betl() or last_bid.is_sans():
                auction.highest_in_hand_bid = last_bid
            auction.phase = AuctionPhase.IN_HAND_DECIDING
            auction.players_bid_this_phase = [last_bid.player_id]
            self._set_next_bidder_for_in_hand_deciding(auction)
            return

        # If someone bid a game, switch to game_bidding
        if last_bid and last_bid.is_game():
            auction.phase = AuctionPhase.GAME_BIDDING
            auction.players_bid_this_phase = [last_bid.player_id]
            self._set_next_bidder_for_game_bidding(auction)
            return

        # If pass, check if all players in initial phase have bid
        all_initial = len(auction.players_bid_this_phase) >= 3
        if all_initial:
            # Everyone passed in initial - everyone passed overall
            if len(auction.passed_players) >= 3:
                # Redeal
                auction.phase = AuctionPhase.COMPLETE
                return

        # Move to next player in initial phase
        self._set_next_bidder_initial(auction)

    def _handle_game_bidding_advance(self, auction: Auction):
        """Handle advancement during game bidding."""
        active_players = [p for p in self.game.players
                        if p.id not in auction.passed_players]

        # If only one player left, auction complete
        if len(active_players) == 1:
            auction.phase = AuctionPhase.COMPLETE
            return

        # If no active players, redeal
        if len(active_players) == 0:
            auction.phase = AuctionPhase.COMPLETE
            return

        # Move to next active bidder
        self._set_next_bidder_for_game_bidding(auction)

    def _handle_in_hand_deciding_advance(self, auction: Auction):
        """Handle advancement during in_hand deciding phase."""
        last_bid = auction.bids[-1] if auction.bids else None

        # If someone just bid betl/sans, mark them as in_hand player
        if last_bid and (last_bid.is_betl() or last_bid.is_sans()):
            if last_bid.player_id not in auction.in_hand_players:
                auction.in_hand_players.append(last_bid.player_id)
            # Update highest in_hand bid
            if auction.highest_in_hand_bid is None or last_bid.effective_value > auction.highest_in_hand_bid.effective_value:
                auction.highest_in_hand_bid = last_bid
        elif last_bid and last_bid.is_in_hand():
            if last_bid.player_id not in auction.in_hand_players:
                auction.in_hand_players.append(last_bid.player_id)

        # Check if all non-passed players have decided
        all_players_decided = all(
            p.id in auction.players_bid_this_phase or p.id in auction.passed_players
            for p in self.game.players
        )

        if all_players_decided:
            # Check if any in_hand player needs to declare a value
            undeclared = [pid for pid in auction.in_hand_players
                        if not any(b.player_id == pid and b.value > 0
                                  for b in auction.bids if b.is_in_hand() or b.is_betl() or b.is_sans())]

            if len(auction.in_hand_players) > 1 and undeclared:
                # Multiple in_hand players with undeclared values - move to declaring
                auction.phase = AuctionPhase.IN_HAND_DECLARING
                auction.players_bid_this_phase = []
                self._set_next_bidder_for_in_hand_declaring(auction)
            else:
                # Single in_hand player or all have declared - auction complete
                auction.phase = AuctionPhase.COMPLETE
            return

        # Move to next player who hasn't decided
        self._set_next_bidder_for_in_hand_deciding(auction)

    def _handle_in_hand_declaring_advance(self, auction: Auction):
        """Handle advancement during in_hand declaring phase."""
        last_bid = auction.bids[-1] if auction.bids else None

        # Check if all in_hand players have declared
        in_hand_declared = [
            p_id for p_id in auction.in_hand_players
            if p_id in auction.players_bid_this_phase
        ]

        if len(in_hand_declared) >= len(auction.in_hand_players):
            auction.phase = AuctionPhase.COMPLETE
            return

        # Move to next in_hand player
        self._set_next_bidder_for_in_hand_declaring(auction)

    def _set_next_bidder_initial(self, auction: Auction):
        """Set next bidder in initial phase (clockwise: 1→3→2)."""
        current = self._get_player(auction.current_bidder_id)
        for i in range(1, 4):
            # Clockwise order: position 1→3→2→1
            next_pos = ((current.position + i) % 3) + 1
            next_player = self._get_player_by_position(next_pos)
            if next_player.id not in auction.players_bid_this_phase:
                auction.current_bidder_id = next_player.id
                return
        # All have bid
        auction.phase = AuctionPhase.COMPLETE

    def _set_next_bidder_for_game_bidding(self, auction: Auction):
        """Set next bidder for game bidding (clockwise, skip passed players)."""
        current = self._get_player(auction.current_bidder_id)
        for i in range(1, 4):
            # Clockwise order: position 1→3→2→1
            next_pos = ((current.position + i) % 3) + 1
            next_player = self._get_player_by_position(next_pos)
            if next_player.id not in auction.passed_players:
                auction.current_bidder_id = next_player.id
                return

    def _set_next_bidder_for_in_hand_deciding(self, auction: Auction):
        """Set next bidder for in_hand deciding phase (clockwise)."""
        current = self._get_player(auction.current_bidder_id)
        for i in range(1, 4):
            # Clockwise order: position 1→3→2→1
            next_pos = ((current.position + i) % 3) + 1
            next_player = self._get_player_by_position(next_pos)
            if (next_player.id not in auction.players_bid_this_phase and
                next_player.id not in auction.passed_players):
                auction.current_bidder_id = next_player.id
                return

    def _set_next_bidder_for_in_hand_declaring(self, auction: Auction):
        """Set next bidder for in_hand declaring phase (clockwise, in_hand players only)."""
        current = self._get_player(auction.current_bidder_id)
        for i in range(1, 4):
            # Clockwise order: position 1→3→2→1
            next_pos = ((current.position + i) % 3) + 1
            next_player = self._get_player_by_position(next_pos)
            if (next_player.id in auction.in_hand_players and
                next_player.id not in auction.players_bid_this_phase):
                auction.current_bidder_id = next_player.id
                return

    def _finalize_auction(self):
        """Finalize the auction and set up the declarer."""
        auction = self.game.current_round.auction
        round = self.game.current_round

        winner_bid = auction.get_winner_bid()

        if not winner_bid or (len(auction.passed_players) >= 3 and not auction.in_hand_players):
            # Everyone passed - redeal
            self.start_new_round()
            return

        winner = self._get_player(winner_bid.player_id)
        winner.is_declarer = True
        round.declarer_id = winner.id

        # Check if this is an in_hand game (in_hand bid, or betl/sans from in_hand_players)
        is_in_hand = winner_bid.is_in_hand() or (
            (winner_bid.is_betl() or winner_bid.is_sans()) and
            winner_bid.player_id in auction.in_hand_players
        )

        if is_in_hand:
            # Skip exchange phase for in_hand games
            round.phase = RoundPhase.PLAYING
            round.start_new_trick(lead_player_id=winner.id)
        else:
            round.phase = RoundPhase.EXCHANGING

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
        """Declarer announces the contract after discarding (or for in_hand, at any time)."""
        round = self.game.current_round

        if round.declarer_id != player_id:
            raise InvalidMoveError("Only declarer can announce contract")

        winner_bid = round.auction.get_winner_bid()
        is_in_hand = winner_bid and winner_bid.is_in_hand()

        # For regular games, must be in exchanging phase and have discarded
        if not is_in_hand:
            self._validate_phase(RoundPhase.EXCHANGING)
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
        if winner_bid:
            if winner_bid.is_betl():
                bid_value = 6
            elif winner_bid.is_sans():
                bid_value = 7
            else:
                bid_value = winner_bid.value if winner_bid.value > 0 else 2
        else:
            bid_value = 2

        # Create contract
        round.contract = Contract(
            type=ctype,
            trump_suit=trump,
            bid_value=bid_value,
            is_in_hand=is_in_hand
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
        """Get all legal bids for a player based on auction phase."""
        if self.game.current_round.phase != RoundPhase.AUCTION:
            return []

        auction = self.game.current_round.auction
        if auction.current_bidder_id != player_id:
            return []

        if auction.phase == AuctionPhase.COMPLETE:
            return []

        legal_bids = [{"bid_type": "pass", "value": 0, "label": "Pass"}]

        if auction.phase == AuctionPhase.INITIAL:
            # Initial phase: pass, game 2, in_hand, betl, sans
            legal_bids.append({"bid_type": "game", "value": 2, "label": "Game 2"})
            legal_bids.append({"bid_type": "in_hand", "value": 0, "label": "In Hand"})
            legal_bids.append({"bid_type": "betl", "value": 6, "label": "Betl"})
            legal_bids.append({"bid_type": "sans", "value": 7, "label": "Sans"})

        elif auction.phase == AuctionPhase.GAME_BIDDING:
            current_high = auction.highest_game_bid.effective_value if auction.highest_game_bid else 1
            is_first_bidder = player_id == auction.first_game_bidder_id

            # Game bids (2-5)
            for value in range(2, 6):
                if is_first_bidder:
                    # First bidder can hold (match) or raise
                    if value >= current_high:
                        label = f"Hold {value}" if value == current_high else f"Game {value}"
                        legal_bids.append({"bid_type": "game", "value": value, "label": label})
                else:
                    # Others must raise
                    if value > current_high:
                        legal_bids.append({"bid_type": "game", "value": value, "label": f"Game {value}"})

            # Betl (after 5)
            if current_high < 6:
                legal_bids.append({"bid_type": "betl", "value": 6, "label": "Betl"})

            # Sans (after betl)
            if current_high < 7:
                legal_bids.append({"bid_type": "sans", "value": 7, "label": "Sans"})

        elif auction.phase == AuctionPhase.IN_HAND_DECIDING:
            # Pass, in_hand, betl, or sans
            legal_bids.append({"bid_type": "in_hand", "value": 0, "label": "In Hand"})
            legal_bids.append({"bid_type": "betl", "value": 6, "label": "Betl"})
            legal_bids.append({"bid_type": "sans", "value": 7, "label": "Sans"})

        elif auction.phase == AuctionPhase.IN_HAND_DECLARING:
            # Declare in_hand value (2-5), must be higher than current
            min_value = 2
            if auction.highest_in_hand_bid:
                min_value = auction.highest_in_hand_bid.value + 1

            for value in range(min_value, 6):
                legal_bids.append({"bid_type": "in_hand", "value": value, "label": f"In Hand {value}"})

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
                current_bidder_id = round.auction.current_bidder_id
                state["current_bidder_id"] = current_bidder_id
                state["auction_phase"] = round.auction.phase.value
                # Always include legal_bids for the current bidder
                if current_bidder_id:
                    state["legal_bids"] = self.get_legal_bids(current_bidder_id)

            elif round.phase == RoundPhase.PLAYING:
                trick = round.current_trick
                if trick:
                    current_player_id = self._get_next_player_in_trick(trick)
                    state["current_player_id"] = current_player_id
                    # Always include legal_cards for current player
                    state["legal_cards"] = [c.to_dict() for c in self.get_legal_cards(current_player_id)]

        return state
