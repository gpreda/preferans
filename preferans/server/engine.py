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

        # Auto-bid if first bidder is AI
        self._auto_bid_if_ai()

    def _auto_bid_if_ai(self):
        """If the current bidder is AI, automatically make a bid.

        AI uses a simple strategy: mostly pass, occasionally bid game 2.
        This keeps the auction moving without complex AI logic.
        """
        if not self.game.current_round:
            return

        if self.game.current_round.phase != RoundPhase.AUCTION:
            return

        auction = self.game.current_round.auction
        if auction.phase == AuctionPhase.COMPLETE:
            return

        if not auction.current_bidder_id:
            return

        player = self._get_player(auction.current_bidder_id)
        if not player.is_ai:
            return  # Human player - wait for user input

        # Get legal bids for this AI player
        legal_bids = self.get_legal_bids(player.id)
        if not legal_bids:
            return

        # Simple AI strategy:
        # - In initial phase: always bid game 2 (to ensure someone becomes declarer)
        # - In other phases: always pass (let auction complete quickly)
        chosen_bid = None
        if auction.phase == AuctionPhase.INITIAL:
            # Find game 2 option - always bid to ensure a declarer
            game_bid = next((b for b in legal_bids if b["bid_type"] == "game" and b["value"] == 2), None)
            pass_bid = next((b for b in legal_bids if b["bid_type"] == "pass"), None)
            # Always bid game 2 if available, otherwise pass
            chosen_bid = game_bid if game_bid else pass_bid
        else:
            # In other phases, just pass to let auction complete
            chosen_bid = next((b for b in legal_bids if b["bid_type"] == "pass"), legal_bids[0])

        if chosen_bid:
            # Place the bid - this will recursively call _auto_bid_if_ai for next bidder
            self.place_bid(player.id, chosen_bid["bid_type"], chosen_bid.get("value", 0))

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
            current_high = auction.highest_game_bid.effective_value if auction.highest_game_bid else 1

            # Check if this is the player's first bid (they can still bid in_hand/betl/sans)
            player_has_bid = any(b.player_id == player_id for b in auction.bids)

            if bid_type == BidType.IN_HAND:
                # In_hand can only be bid as player's first bid
                if player_has_bid:
                    raise InvalidMoveError("Can only bid in_hand as your first bid")
                if value != 0:
                    raise InvalidMoveError("In_hand intent should not have a value")

            elif bid_type == BidType.BETL:
                # Betl as first bid (in_hand variant) - always allowed
                # Betl as game progression - only after game 5
                if player_has_bid and current_high < 5:
                    raise InvalidMoveError("Cannot bid betl until after game 5")
                if current_high >= 6:
                    raise InvalidMoveError("Cannot bid betl, already at betl or higher")

            elif bid_type == BidType.SANS:
                # Sans as first bid (in_hand variant) - always allowed
                # Sans as game progression - only after betl
                if player_has_bid and current_high < 6:
                    raise InvalidMoveError("Cannot bid sans until after betl")
                if current_high >= 7:
                    raise InvalidMoveError("Sans already bid")

            elif bid_type == BidType.GAME:
                if value < 2 or value > 5:
                    raise InvalidMoveError("Game bid must be between 2 and 5")

                # Check if this is the first game bidder (can "hold")
                is_first_bidder = player_id == auction.first_game_bidder_id

                if is_first_bidder:
                    # First bidder can ONLY hold (match current), cannot bid higher
                    if value != current_high:
                        raise InvalidMoveError(f"Must hold at {current_high}")
                else:
                    # Other bidders must bid exactly one higher (no jumping)
                    if value != current_high + 1:
                        raise InvalidMoveError(f"Must bid exactly {current_high + 1}")

        elif auction.phase == AuctionPhase.IN_HAND_DECIDING:
            # Other players deciding based on current highest in_hand bid
            highest = auction.highest_in_hand_bid

            if highest and highest.is_sans():
                # Sans is highest - can only pass
                if bid_type != BidType.PASS:
                    raise InvalidMoveError("Sans is already bid, can only pass")
            elif highest and highest.is_betl():
                # Betl is highest - can only pass or bid sans
                if bid_type not in [BidType.PASS, BidType.SANS]:
                    raise InvalidMoveError("Betl is bid, can only pass or bid sans")
            else:
                # Undeclared in_hand - can pass, in_hand, betl, or sans
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
        else:
            # Auto-bid if next bidder is AI
            self._auto_bid_if_ai()

    def _handle_initial_phase_advance(self, auction: Auction, last_bid: Bid):
        """Handle advancement from initial phase."""
        # If someone bid in_hand, betl, or sans, switch to in_hand_deciding for others
        # (betl and sans in initial phase are in_hand variants)
        if last_bid and (last_bid.is_in_hand() or last_bid.is_betl() or last_bid.is_sans()):
            # Mark as in_hand player
            if last_bid.player_id not in auction.in_hand_players:
                auction.in_hand_players.append(last_bid.player_id)
            # Set highest_in_hand_bid for all in_hand variants
            if last_bid.is_betl() or last_bid.is_sans():
                auction.highest_in_hand_bid = last_bid
            elif last_bid.is_in_hand() and not auction.highest_in_hand_bid:
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
        last_bid = auction.bids[-1] if auction.bids else None

        # If someone bid in_hand/betl/sans as their first bid, switch to in_hand deciding
        if last_bid and (last_bid.is_in_hand() or last_bid.is_betl() or last_bid.is_sans()):
            # Check if this was their first bid (in_hand variant)
            player_previous_bids = [b for b in auction.bids[:-1] if b.player_id == last_bid.player_id]
            if not player_previous_bids:
                # This was their first bid - it's an in_hand declaration
                if last_bid.player_id not in auction.in_hand_players:
                    auction.in_hand_players.append(last_bid.player_id)
                # Set highest_in_hand_bid for all in_hand variants
                if last_bid.is_betl() or last_bid.is_sans():
                    auction.highest_in_hand_bid = last_bid
                elif last_bid.is_in_hand():
                    # For undeclared in_hand, set it as highest if no other in_hand bid
                    if not auction.highest_in_hand_bid:
                        auction.highest_in_hand_bid = last_bid

                # Players who already bid game are eliminated (can't switch to in_hand)
                for bid in auction.bids[:-1]:
                    if bid.is_game() and bid.player_id not in auction.in_hand_players:
                        if bid.player_id not in auction.passed_players:
                            auction.passed_players.append(bid.player_id)

                # Check if other players still haven't bid (can also go in_hand)
                players_without_bids = [p for p in self.game.players
                                       if not any(b.player_id == p.id for b in auction.bids)
                                       and p.id not in auction.passed_players]
                if players_without_bids:
                    auction.phase = AuctionPhase.IN_HAND_DECIDING
                    auction.players_bid_this_phase = [last_bid.player_id]
                    self._set_next_bidder_for_in_hand_deciding(auction)
                    return
                else:
                    # No other players can join in_hand, auction complete
                    auction.phase = AuctionPhase.COMPLETE
                    return

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

            # If sans is bid, auction completes immediately (nothing higher)
            if last_bid.is_sans():
                auction.phase = AuctionPhase.COMPLETE
                return

            # If betl is bid, give other in_hand players a chance to respond with sans
            # Any undeclared in_hand player should get a chance to respond
            if last_bid.is_betl():
                undeclared_in_hand = [pid for pid in auction.in_hand_players
                                      if pid != last_bid.player_id
                                      and not any(b.player_id == pid and (b.is_betl() or b.is_sans())
                                                for b in auction.bids)]
                if undeclared_in_hand:
                    # Reset players_bid_this_phase so undeclared can respond
                    auction.players_bid_this_phase = [last_bid.player_id]
                    self._set_next_bidder_for_in_hand_deciding(auction)
                    return

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
            # betl and sans are already declared (value 6 and 7), so they don't need to reveal
            # If betl or sans is the highest, undeclared players can't beat it (max in_hand is 5)
            highest = auction.highest_in_hand_bid
            if highest and (highest.is_betl() or highest.is_sans()):
                # betl/sans wins - no need for undeclared players to reveal
                auction.phase = AuctionPhase.COMPLETE
                return

            def is_declared(player_id):
                for b in auction.bids:
                    if b.player_id == player_id:
                        if b.is_betl() or b.is_sans():
                            return True  # betl/sans are declared
                        if b.is_in_hand() and b.value > 0:
                            return True  # in_hand with value is declared
                return False

            undeclared = [pid for pid in auction.in_hand_players if not is_declared(pid)]

            if len(undeclared) > 1:
                # Multiple undeclared in_hand players - move to declaring
                auction.phase = AuctionPhase.IN_HAND_DECLARING
                auction.players_bid_this_phase = []
                self._set_next_bidder_for_in_hand_declaring(auction)
            elif len(undeclared) == 1:
                # Single undeclared player - they win with their undeclared value
                # (will be declared when contract is made)
                auction.phase = AuctionPhase.COMPLETE
            else:
                # All have declared - auction complete
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
        """Set next bidder for game bidding (clockwise, skip passed and current player)."""
        current = self._get_player(auction.current_bidder_id)
        for i in range(1, 4):
            # Clockwise order: position 1→3→2→1
            next_pos = ((current.position + i) % 3) + 1
            next_player = self._get_player_by_position(next_pos)
            # Skip passed players AND the current bidder (who just bid)
            if next_player.id not in auction.passed_players and next_player.id != current.id:
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
            # Don't start trick yet - wait for announce_contract to be called
            # so the player can choose their contract level (2-5 for undeclared in_hand)
            round.phase = RoundPhase.PLAYING
        else:
            round.phase = RoundPhase.EXCHANGING

    # === Exchange Phase ===

    def pick_up_talon(self, player_id: int) -> list[Card]:
        """Declarer picks up the talon cards."""
        self._validate_phase(RoundPhase.EXCHANGING)
        round = self.game.current_round

        if round.declarer_id != player_id:
            raise InvalidMoveError("Only declarer can pick up talon")

        if len(round.talon) == 0:
            raise InvalidMoveError("Talon already picked up")

        player = self._get_player(player_id)

        # Add talon cards to player's hand
        talon_cards = list(round.talon)  # Copy before clearing
        for card in talon_cards:
            player.add_card(card)

        # Clear talon after pickup
        round.talon = []

        player.sort_hand()
        return talon_cards

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

    def complete_exchange(self, player_id: int, card_ids: list[str]) -> list[Card]:
        """Complete exchange atomically - picks up talon and discards specified cards.

        This is a single atomic operation that:
        1. Takes the original talon cards
        2. Combines them with the player's hand
        3. Removes the specified cards as discards

        Args:
            player_id: The declarer's ID
            card_ids: List of 2 card IDs to discard (can be from original hand or talon)

        Returns:
            List of discarded cards
        """
        self._validate_phase(RoundPhase.EXCHANGING)
        round = self.game.current_round

        if round.declarer_id != player_id:
            raise InvalidMoveError("Only declarer can complete exchange")

        if len(card_ids) != 2:
            raise InvalidMoveError("Must discard exactly 2 cards")

        player = self._get_player(player_id)

        # Get original talon cards
        original_talon = list(round.talon)
        if len(original_talon) != 2:
            raise InvalidMoveError("Talon should have exactly 2 cards")

        # Build combined pool of cards (hand + talon)
        all_cards = {card.id: card for card in player.hand}
        for card in original_talon:
            all_cards[card.id] = card

        # Validate all specified cards exist in the combined pool
        for card_id in card_ids:
            if card_id not in all_cards:
                raise InvalidMoveError(f"Card {card_id} not in hand or talon")

        # Get the cards to discard
        discarded = [all_cards[card_id] for card_id in card_ids]

        # Determine which cards go to hand (everything except discards)
        discard_set = set(card_ids)
        new_hand = [card for card_id, card in all_cards.items() if card_id not in discard_set]

        # Update player's hand
        player.hand = new_hand

        # Clear talon and set discarded
        round.talon = []
        round.discarded = discarded

        player.sort_hand()
        return discarded

    def announce_contract(self, player_id: int, contract_type: str, trump_suit: Optional[str] = None, level: Optional[int] = None):
        """Declarer announces the contract after discarding (or for in_hand, at any time).

        Args:
            player_id: The declarer's ID
            contract_type: One of "suit", "betl", "sans"
            trump_suit: Required for suit contracts
            level: The contract level (2-7). Required for in_hand games without declared value.
        """
        print(f"[announce_contract] player_id={player_id}, contract_type={contract_type}, trump_suit={trump_suit}, level={level}")

        round = self.game.current_round

        if round.declarer_id != player_id:
            raise InvalidMoveError("Only declarer can announce contract")

        winner_bid = round.auction.get_winner_bid()
        print(f"[announce_contract] winner_bid={winner_bid}, is_in_hand={winner_bid.is_in_hand() if winner_bid else None}")
        print(f"[announce_contract] in_hand_players={round.auction.in_hand_players}")

        is_in_hand = winner_bid and (winner_bid.is_in_hand() or (
            (winner_bid.is_betl() or winner_bid.is_sans()) and
            winner_bid.player_id in round.auction.in_hand_players
        ))
        print(f"[announce_contract] is_in_hand={is_in_hand}")

        # For regular games, must be in exchanging phase and have completed exchange
        if not is_in_hand:
            self._validate_phase(RoundPhase.EXCHANGING)
            # Check that talon was picked up (talon should be empty)
            if len(round.talon) > 0:
                raise InvalidMoveError("Must pick up talon before announcing contract")
            # Check that cards were discarded
            if len(round.discarded) != 2:
                raise InvalidMoveError("Must discard 2 cards before announcing contract")
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

        # Determine and validate bid value
        legal_levels = self.get_legal_contract_levels(player_id)
        print(f"[announce_contract] legal_levels={legal_levels}")

        if level is not None:
            # Validate provided level
            if level not in legal_levels:
                raise InvalidMoveError(f"Invalid contract level {level}. Legal levels: {legal_levels}")
            bid_value = level
        elif len(legal_levels) == 1:
            # Only one legal level - use it
            bid_value = legal_levels[0]
        else:
            # Multiple legal levels but none specified
            raise InvalidMoveError(f"Must specify level. Legal levels: {legal_levels}")

        print(f"[announce_contract] bid_value={bid_value}")

        # Create contract
        round.contract = Contract(
            type=ctype,
            trump_suit=trump,
            bid_value=bid_value,
            is_in_hand=is_in_hand
        )
        print(f"[announce_contract] Created contract: type={ctype}, trump={trump}, bid_value={bid_value}, is_in_hand={is_in_hand}")

        # Move to playing phase
        round.phase = RoundPhase.PLAYING

        # Determine who leads the first trick
        first_lead_id = self._get_first_lead_player_id(player_id, ctype)
        print(f"[announce_contract] First lead player: P{first_lead_id}")
        round.start_new_trick(lead_player_id=first_lead_id)

    # === Playing Phase ===

    def play_card(self, player_id: int, card_id: str) -> dict:
        """Play a card to the current trick."""
        print(f"[play_card] player_id={player_id}, card_id={card_id}")
        print(f"[play_card] phase={self.game.current_round.phase if self.game.current_round else None}")
        print(f"[play_card] contract={self.game.current_round.contract if self.game.current_round else None}")
        print(f"[play_card] current_trick={self.game.current_round.current_trick if self.game.current_round else None}")
        print(f"[play_card] num_tricks={len(self.game.current_round.tricks) if self.game.current_round else 0}")

        self._validate_phase(RoundPhase.PLAYING)
        round = self.game.current_round
        trick = round.current_trick

        if not trick:
            print(f"[play_card] ERROR: No active trick!")
            raise GameError("No active trick")

        # Validate it's this player's turn
        expected_player_id = self._get_next_player_in_trick(trick)
        print(f"[play_card] expected_player_id={expected_player_id}")
        if player_id != expected_player_id:
            raise InvalidMoveError(f"Not player {player_id}'s turn")

        player = self._get_player(player_id)

        # Find card in hand
        card = self._find_card_in_hand(player, card_id)
        if not card:
            print(f"[play_card] ERROR: Card {card_id} not in hand. Player hand: {[c.id for c in player.hand]}")
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
            print(f"[play_card] Trick complete! Cards: {[(pid, c.id) for pid, c in trick.cards]}")
            print(f"[play_card] Trump suit: {trump}, Contract type: {round.contract.type}")
            winner_id = trick.determine_winner(trump_suit=trump)
            print(f"[play_card] Winner: P{winner_id}")
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

        # Get lead player position and rotate counter-clockwise (same as bidding: 1→3→2)
        lead_player = self._get_player(trick.lead_player_id)

        for i in range(1, 4):
            # Counter-clockwise order: position 1→3→2→1 (same as bidding)
            next_position = ((lead_player.position + i) % 3) + 1
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

    def get_best_trump_suit(self, player_id: int) -> str:
        """Determine the best trump suit based on the player's hand (most common suit)."""
        player = self._get_player(player_id)
        suit_counts = {}
        for card in player.hand:
            suit_name = SUIT_NAMES[card.suit]
            suit_counts[suit_name] = suit_counts.get(suit_name, 0) + 1

        # Return the suit with most cards, defaulting to spades if tied or empty
        if not suit_counts:
            return 'spades'
        return max(suit_counts, key=suit_counts.get)

    def _get_player_by_position(self, position: int) -> Player:
        """Get a player by position (1-3)."""
        for p in self.game.players:
            if p.position == position:
                return p
        raise GameError(f"No player at position {position}")

    def _get_first_lead_player_id(self, declarer_id: int, contract_type: ContractType) -> int:
        """Determine who leads the first trick.

        Rules:
        - For Sans: always the left player from the declarer (counter-clockwise)
        - For other contracts: forehand (position 1) plays first
        - If forehand is the declarer, the next defender in counter-clockwise order plays first
        """
        declarer = self._get_player(declarer_id)

        if contract_type == ContractType.SANS:
            # For Sans, always the left player from declarer (counter-clockwise)
            next_position = ((declarer.position) % 3) + 1
            return self._get_player_by_position(next_position).id

        # For other contracts: forehand plays first, unless they are the declarer
        forehand = self._get_player_by_position(1)
        if forehand.id != declarer_id:
            return forehand.id

        # Forehand is the declarer, so next defender in counter-clockwise order plays first
        # Counter-clockwise from position 1: 1 → 3 → 2
        for i in range(1, 3):
            next_position = ((1 + i) % 3) + 1  # 1→3, then 3→2
            player = self._get_player_by_position(next_position)
            if player.id != declarer_id:
                return player.id

        # Shouldn't reach here, but fallback to declarer
        return declarer_id

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
            legal_bids.append({"bid_type": "game", "value": 2, "label": "2"})
            legal_bids.append({"bid_type": "in_hand", "value": 0, "label": "In Hand"})
            legal_bids.append({"bid_type": "betl", "value": 6, "label": "Betl"})
            legal_bids.append({"bid_type": "sans", "value": 7, "label": "Sans"})

        elif auction.phase == AuctionPhase.GAME_BIDDING:
            current_high = auction.highest_game_bid.effective_value if auction.highest_game_bid else 1
            is_first_game_bidder = player_id == auction.first_game_bidder_id
            next_value = current_high + 1

            # Check if this is the player's first bid
            player_has_bid = any(b.player_id == player_id for b in auction.bids)

            # Game bids - only sequential (no jumping)
            if is_first_game_bidder:
                # First game bidder can ONLY hold (match current), not bid higher
                if current_high >= 2 and current_high <= 5:
                    legal_bids.append({"bid_type": "game", "value": current_high, "label": f"{current_high}"})
            else:
                # Others can only bid exactly next value
                if next_value <= 5:
                    legal_bids.append({"bid_type": "game", "value": next_value, "label": f"{next_value}"})

            # If this is player's first bid, they can also bid in_hand/betl/sans
            if not player_has_bid:
                legal_bids.append({"bid_type": "in_hand", "value": 0, "label": "In Hand"})
                legal_bids.append({"bid_type": "betl", "value": 6, "label": "Betl"})
                legal_bids.append({"bid_type": "sans", "value": 7, "label": "Sans"})
            else:
                # Player has already bid - betl/sans only as game progression
                # Betl (6) - only after game 5
                if current_high == 5:
                    legal_bids.append({"bid_type": "betl", "value": 6, "label": "Betl"})

                # Sans (7) - only after betl
                if current_high == 6:
                    legal_bids.append({"bid_type": "sans", "value": 7, "label": "Sans"})

        elif auction.phase == AuctionPhase.IN_HAND_DECIDING:
            # Options depend on current highest in_hand bid
            highest = auction.highest_in_hand_bid

            if highest and highest.is_sans():
                # Sans is highest - can only pass (already included)
                pass
            elif highest and highest.is_betl():
                # Betl is highest - can only pass or bid sans
                legal_bids.append({"bid_type": "sans", "value": 7, "label": "Sans"})
            else:
                # Undeclared in_hand - can pass, in_hand, betl, or sans
                legal_bids.append({"bid_type": "in_hand", "value": 0, "label": "In Hand"})
                legal_bids.append({"bid_type": "betl", "value": 6, "label": "Betl"})
                legal_bids.append({"bid_type": "sans", "value": 7, "label": "Sans"})

        elif auction.phase == AuctionPhase.IN_HAND_DECLARING:
            # Declare in_hand value (2-5), must be higher than current
            min_value = 2
            if auction.highest_in_hand_bid:
                min_value = auction.highest_in_hand_bid.value + 1

            for value in range(min_value, 6):
                legal_bids.append({"bid_type": "in_hand", "value": value, "label": f"in_hand {value}"})

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

    def get_legal_contract_levels(self, player_id: int) -> list[int]:
        """Get legal contract levels for the declarer based on the winning bid.

        For in_hand winners (without declared value): 2-5 (not limited by previous bids)
        For in_hand winners (with declared value): that specific value
        For regular game winners: the winning bid level
        For betl winners: 6
        For sans winners: 7
        """
        round = self.game.current_round
        if round.declarer_id != player_id:
            print(f"[get_legal_contract_levels] player_id={player_id} is not declarer (declarer_id={round.declarer_id})")
            return []

        winner_bid = round.auction.get_winner_bid()
        if not winner_bid:
            print(f"[get_legal_contract_levels] No winner_bid found")
            return []

        print(f"[get_legal_contract_levels] winner_bid: player_id={winner_bid.player_id}, bid_type={winner_bid.bid_type}, value={winner_bid.value}")

        # Check if this is an in_hand game
        is_in_hand = winner_bid.is_in_hand() or (
            (winner_bid.is_betl() or winner_bid.is_sans()) and
            winner_bid.player_id in round.auction.in_hand_players
        )

        if winner_bid.is_betl():
            print(f"[get_legal_contract_levels] Betl bid -> [6]")
            return [6]
        elif winner_bid.is_sans():
            print(f"[get_legal_contract_levels] Sans bid -> [7]")
            return [7]
        elif winner_bid.is_in_hand():
            if winner_bid.value > 0:
                # In_hand with declared value
                print(f"[get_legal_contract_levels] In_hand with value {winner_bid.value} -> [{winner_bid.value}]")
                return [winner_bid.value]
            else:
                # In_hand without declared value - can choose 2-5
                print(f"[get_legal_contract_levels] In_hand undeclared -> [2, 3, 4, 5]")
                return [2, 3, 4, 5]
        else:
            # Regular game bid - must use the winning bid level
            print(f"[get_legal_contract_levels] Regular game bid value={winner_bid.value} -> [{winner_bid.value}]")
            return [winner_bid.value]

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

            # Include legal contract levels when declarer needs to choose
            if round.declarer_id and not round.contract:
                legal_levels = self.get_legal_contract_levels(round.declarer_id)
                if legal_levels:
                    state["legal_contract_levels"] = legal_levels

        return state
