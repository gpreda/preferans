"""Test script for the Preferans game engine."""
import sys
from models import Game, Player, PlayerType, SUIT_NAMES
from engine import GameEngine, InvalidMoveError


def print_hand(player: Player):
    """Print a player's hand."""
    cards = " ".join([c.id for c in player.hand])
    print(f"  {player.name} (pos {player.position}): {cards}")


def print_game_state(engine: GameEngine):
    """Print current game state."""
    game = engine.game
    round = game.current_round

    print(f"\n{'='*60}")
    print(f"Round {game.round_number} - Phase: {round.phase.value}")
    print(f"{'='*60}")

    print("\nPlayers:")
    for p in sorted(game.players, key=lambda x: x.position):
        role = "DECLARER" if p.is_declarer else ""
        print(f"  P{p.id} {p.name} (pos {p.position}) - {len(p.hand)} cards, {p.tricks_won} tricks {role}")
        if p.hand:
            cards = " ".join([c.id for c in p.hand])
            print(f"      Hand: {cards}")

    print(f"\nTalon: {len(round.talon)} cards")

    if round.contract:
        c = round.contract
        trump = SUIT_NAMES[c.trump_suit] if c.trump_suit else "none"
        print(f"Contract: {c.type.value}, trump={trump}, bid={c.bid_value}")

    if round.tricks:
        print(f"\nTricks played: {len(round.tricks)}")
        for trick in round.tricks[-3:]:  # Show last 3 tricks
            cards = ", ".join([f"P{pid}:{c.id}" for pid, c in trick.cards])
            winner = f"-> P{trick.winner_id}" if trick.winner_id else ""
            print(f"  Trick {trick.number}: {cards} {winner}")


def test_basic_game():
    """Test a basic game flow."""
    print("="*60)
    print("PREFERANS GAME ENGINE TEST")
    print("="*60)

    # Create game with 3 players
    game = Game(id="test-game-1")
    game.add_human_player("Alice")
    game.add_human_player("Bob")
    game.add_human_player("Charlie")

    engine = GameEngine(game)

    # Start game
    print("\n>>> Starting game...")
    engine.start_game()
    print_game_state(engine)

    # Bidding phase
    print("\n>>> BIDDING PHASE")
    auction = game.current_round.auction

    # Get forehand (position 1) to bid first
    forehand = [p for p in game.players if p.position == 1][0]
    print(f"\nForehand is P{forehand.id} ({forehand.name})")
    print(f"Current bidder: P{auction.current_bidder_id}")

    # Forehand bids 2
    print(f"\nP{forehand.id} bids 2...")
    engine.place_bid(forehand.id, value=2, suit="spades")
    print(f"Current bidder: P{auction.current_bidder_id}")

    # Next player passes
    next_bidder_id = auction.current_bidder_id
    print(f"\nP{next_bidder_id} passes...")
    engine.place_bid(next_bidder_id, value=0)
    print(f"Current bidder: P{auction.current_bidder_id}")

    # Third player passes
    if game.current_round.phase.value == "auction":
        next_bidder_id = auction.current_bidder_id
        print(f"\nP{next_bidder_id} passes...")
        engine.place_bid(next_bidder_id, value=0)

    print_game_state(engine)

    # Check if auction ended
    print(f"\nPhase after bidding: {game.current_round.phase.value}")
    print(f"Declarer: P{game.current_round.declarer_id}")

    # Exchange phase
    if game.current_round.phase.value == "exchanging":
        print("\n>>> EXCHANGE PHASE")
        declarer_id = game.current_round.declarer_id
        declarer = game.get_player(declarer_id)

        print(f"\nDeclarer P{declarer_id} picks up talon...")
        talon_cards = engine.pick_up_talon(declarer_id)
        print(f"Talon: {[c.id for c in talon_cards]}")
        print(f"Declarer now has {len(declarer.hand)} cards")

        # Discard last 2 cards
        cards_to_discard = [declarer.hand[-1].id, declarer.hand[-2].id]
        print(f"\nDiscarding: {cards_to_discard}")
        engine.discard_cards(declarer_id, cards_to_discard)
        print(f"Declarer now has {len(declarer.hand)} cards")

        # Announce contract
        print("\nAnnouncing contract: suit (spades)...")
        engine.announce_contract(declarer_id, "suit", "spades")

        print_game_state(engine)

    # Playing phase
    if game.current_round.phase.value == "playing":
        print("\n>>> PLAYING PHASE")

        # Play all 10 tricks
        for trick_num in range(10):
            trick = game.current_round.current_trick
            print(f"\n--- Trick {trick_num + 1} ---")

            for _ in range(3):
                current_player_id = engine._get_next_player_in_trick(trick)
                player = game.get_player(current_player_id)
                legal_cards = engine.get_legal_cards(current_player_id)

                if not legal_cards:
                    print(f"ERROR: No legal cards for P{current_player_id}")
                    break

                # Play first legal card
                card_to_play = legal_cards[0]
                print(f"P{current_player_id} plays {card_to_play.id}")
                result = engine.play_card(current_player_id, card_to_play.id)

                if result.get("trick_complete"):
                    print(f"  -> Trick won by P{result['trick_winner_id']}")

                if result.get("round_complete"):
                    print("\n>>> ROUND COMPLETE!")
                    break

            if game.current_round.phase.value == "scoring":
                break

    # Final state
    print_game_state(engine)

    print("\n>>> FINAL SCORES:")
    for p in game.players:
        print(f"  P{p.id} {p.name}: {p.score} points ({p.tricks_won} tricks)")

    print("\n" + "="*60)
    print("TEST COMPLETE")
    print("="*60)


if __name__ == "__main__":
    test_basic_game()
