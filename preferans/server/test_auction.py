"""Unit tests for auction/bidding logic based on detailed rules and examples."""
import unittest
from models import Game, Player, BidType, AuctionPhase
from engine import GameEngine


class TestAuction(unittest.TestCase):
    """Test auction bidding scenarios from the detailed examples.

    Note: Bidding order is clockwise by position: pos1 -> pos3 -> pos2 -> pos1 ...
    In the examples, "Player1" means position 1 (forehand), not player ID 1.
    We set up P1=pos1, P2=pos3, P3=pos2 so the order is P1 -> P2 -> P3 (matching examples).
    """

    def setUp(self):
        """Create a game with 3 players for testing."""
        self.game = Game(id="test")
        self.game.add_player(Player(id=1, name='Player 1'))
        self.game.add_player(Player(id=2, name='Player 2'))
        self.game.add_player(Player(id=3, name='Player 3'))
        self.engine = GameEngine(self.game)
        self.engine.start_game()
        # Set positions so bidding order matches examples: P1 -> P2 -> P3
        # Clockwise: pos1 -> pos3 -> pos2
        # So: P1=pos1, P2=pos3, P3=pos2 gives order P1 -> P2 -> P3
        for p in self.game.players:
            if p.id == 1:
                p.position = 1  # forehand (first)
            elif p.id == 2:
                p.position = 3  # dealer (second in clockwise from pos1)
            else:
                p.position = 2  # middlehand (third in clockwise from pos1)
        # Reset auction with P1 as first bidder (position 1)
        self.game.current_round.auction.current_bidder_id = 1

    def get_legal_bid_labels(self, player_id):
        """Helper to get legal bid labels for a player."""
        bids = self.engine.get_legal_bids(player_id)
        return [b['label'] for b in bids]

    def get_current_bidder(self):
        """Get the current bidder's player ID."""
        return self.game.current_round.auction.current_bidder_id

    def test_initial_options(self):
        """Test: Player1 options: pass, 2, in_hand, betl, sans"""
        auction = self.game.current_round.auction
        self.assertEqual(auction.phase, AuctionPhase.INITIAL)
        labels = self.get_legal_bid_labels(1)
        self.assertEqual(labels, ['Pass', '2', 'In Hand', 'Betl', 'Sans'])

    def test_example1_game_bidding_with_hold(self):
        """
        Example 1: Game bidding with hold mechanism
        P1: 2, P2: pass, P3: 3, P1: 3 (hold), P2: skipped, P3: pass
        Winner: P1, game 3
        """
        auction = self.game.current_round.auction

        # P1 bids 2
        self.engine.place_bid(1, 'game', 2)
        self.assertEqual(auction.phase, AuctionPhase.GAME_BIDDING)

        # P2 options: pass, 3, in_hand, betl, sans (first bid)
        labels = self.get_legal_bid_labels(2)
        self.assertIn('Pass', labels)
        self.assertIn('3', labels)
        self.assertIn('In Hand', labels)
        self.assertIn('Betl', labels)
        self.assertIn('Sans', labels)

        # P2 passes
        self.engine.place_bid(2, 'pass', 0)

        # P3 options: pass, 3, in_hand, betl, sans (first bid)
        labels = self.get_legal_bid_labels(3)
        self.assertIn('Pass', labels)
        self.assertIn('3', labels)
        self.assertIn('In Hand', labels)

        # P3 bids 3
        self.engine.place_bid(3, 'game', 3)

        # P1 options: pass, 3 (hold only, cannot bid 4)
        labels = self.get_legal_bid_labels(1)
        self.assertEqual(labels, ['Pass', '3'])

        # P1 holds at 3
        self.engine.place_bid(1, 'game', 3)

        # P2 is skipped (already passed)
        # P3 options: pass, 4
        labels = self.get_legal_bid_labels(3)
        self.assertEqual(labels, ['Pass', '4'])

        # P3 passes
        self.engine.place_bid(3, 'pass', 0)

        # Auction complete
        self.assertEqual(auction.phase, AuctionPhase.COMPLETE)
        winner_bid = auction.get_winner_bid()
        self.assertEqual(winner_bid.player_id, 1)
        self.assertEqual(winner_bid.effective_value, 3)

    def test_example2_game_bidding_different_first_bidder(self):
        """
        Example 2: P1 passes, P2 is first game bidder
        P1: pass, P2: 2, P3: 3, P1: skipped, P2: 3 (hold), P3: pass
        Winner: P2, game 3
        """
        auction = self.game.current_round.auction

        # P1 passes
        self.engine.place_bid(1, 'pass', 0)

        # P2 bids 2
        self.engine.place_bid(2, 'game', 2)
        self.assertEqual(auction.first_game_bidder_id, 2)

        # P3 bids 3
        self.engine.place_bid(3, 'game', 3)

        # P1 is skipped (already passed)
        # P2 options: pass, 3 (hold)
        labels = self.get_legal_bid_labels(2)
        self.assertEqual(labels, ['Pass', '3'])

        # P2 holds at 3
        self.engine.place_bid(2, 'game', 3)

        # P3 options: pass, 4
        labels = self.get_legal_bid_labels(3)
        self.assertEqual(labels, ['Pass', '4'])

        # P3 passes
        self.engine.place_bid(3, 'pass', 0)

        # Auction complete
        self.assertEqual(auction.phase, AuctionPhase.COMPLETE)
        winner_bid = auction.get_winner_bid()
        self.assertEqual(winner_bid.player_id, 2)
        self.assertEqual(winner_bid.effective_value, 3)

    def test_example3_in_hand_both_declare(self):
        """
        Example 3: Multiple in_hand players, both declare
        P1: in_hand, P2: in_hand, P3: pass
        P1 reveals: 3, P2 reveals: 4
        Winner: P2, game 4
        """
        auction = self.game.current_round.auction

        # P1 bids in_hand (undeclared)
        self.engine.place_bid(1, 'in_hand', 0)
        self.assertEqual(auction.phase, AuctionPhase.IN_HAND_DECIDING)

        # P2 options: pass, in_hand, betl, sans
        labels = self.get_legal_bid_labels(2)
        self.assertIn('Pass', labels)
        self.assertIn('In Hand', labels)
        self.assertIn('Betl', labels)
        self.assertIn('Sans', labels)

        # P2 bids in_hand
        self.engine.place_bid(2, 'in_hand', 0)

        # P3 passes
        self.engine.place_bid(3, 'pass', 0)

        # Move to IN_HAND_DECLARING
        self.assertEqual(auction.phase, AuctionPhase.IN_HAND_DECLARING)

        # P1 reveals 3
        labels = self.get_legal_bid_labels(1)
        self.assertIn('Pass', labels)
        self.assertIn('in_hand 2', labels)
        self.assertIn('in_hand 3', labels)
        self.assertIn('in_hand 4', labels)
        self.assertIn('in_hand 5', labels)

        self.engine.place_bid(1, 'in_hand', 3)

        # P2 must declare higher than 3 or pass
        labels = self.get_legal_bid_labels(2)
        self.assertEqual(labels, ['Pass', 'in_hand 4', 'in_hand 5'])

        # P2 reveals 4
        self.engine.place_bid(2, 'in_hand', 4)

        # Auction complete
        self.assertEqual(auction.phase, AuctionPhase.COMPLETE)
        winner_bid = auction.get_winner_bid()
        self.assertEqual(winner_bid.player_id, 2)
        self.assertEqual(winner_bid.value, 4)

    def test_example4_in_hand_second_passes(self):
        """
        Example 4: Multiple in_hand players, second passes in declare
        P1: in_hand, P2: in_hand, P3: pass
        P1 reveals: 4, P2 reveals: pass
        Winner: P1, game 4
        """
        auction = self.game.current_round.auction

        # P1 bids in_hand
        self.engine.place_bid(1, 'in_hand', 0)

        # P2 bids in_hand
        self.engine.place_bid(2, 'in_hand', 0)

        # P3 passes
        self.engine.place_bid(3, 'pass', 0)

        # IN_HAND_DECLARING
        self.assertEqual(auction.phase, AuctionPhase.IN_HAND_DECLARING)

        # P1 reveals 4
        self.engine.place_bid(1, 'in_hand', 4)

        # P2 options: pass, in_hand 5
        labels = self.get_legal_bid_labels(2)
        self.assertEqual(labels, ['Pass', 'in_hand 5'])

        # P2 passes (can't beat 4 or doesn't want to bid 5)
        self.engine.place_bid(2, 'pass', 0)

        # Auction complete
        self.assertEqual(auction.phase, AuctionPhase.COMPLETE)
        winner_bid = auction.get_winner_bid()
        self.assertEqual(winner_bid.player_id, 1)
        self.assertEqual(winner_bid.value, 4)

    def test_example5_betl_bid(self):
        """
        Example 5 (labeled 4/ in examples): Betl bid
        P1: pass, P2: betl, P3: pass
        Winner: P2, betl
        """
        auction = self.game.current_round.auction

        # P1 passes
        self.engine.place_bid(1, 'pass', 0)

        # P2 bids betl
        self.engine.place_bid(2, 'betl', 6)
        self.assertEqual(auction.phase, AuctionPhase.IN_HAND_DECIDING)

        # P3 options: pass, sans only (betl is bid)
        labels = self.get_legal_bid_labels(3)
        self.assertEqual(labels, ['Pass', 'Sans'])

        # P3 passes
        self.engine.place_bid(3, 'pass', 0)

        # Auction complete
        self.assertEqual(auction.phase, AuctionPhase.COMPLETE)
        winner_bid = auction.get_winner_bid()
        self.assertEqual(winner_bid.player_id, 2)
        self.assertTrue(winner_bid.is_betl())

    def test_example6_betl_then_sans(self):
        """
        Example 6 (labeled 5/): Betl then Sans
        P1: betl, P2: sans, P3: skipped
        Winner: P2, sans
        """
        auction = self.game.current_round.auction

        # P1 bids betl
        self.engine.place_bid(1, 'betl', 6)
        self.assertEqual(auction.phase, AuctionPhase.IN_HAND_DECIDING)

        # P2 options: pass, sans
        labels = self.get_legal_bid_labels(2)
        self.assertEqual(labels, ['Pass', 'Sans'])

        # P2 bids sans
        self.engine.place_bid(2, 'sans', 7)

        # Auction completes immediately (sans is highest, no options for P3)
        self.assertEqual(auction.phase, AuctionPhase.COMPLETE)
        winner_bid = auction.get_winner_bid()
        self.assertEqual(winner_bid.player_id, 2)
        self.assertTrue(winner_bid.is_sans())

    def test_example7_in_hand_then_betl(self):
        """
        Example 7 (labeled 6/): In_hand then betl beats it
        P1: pass, P2: in_hand, P3: betl
        Winner: P3, betl
        """
        auction = self.game.current_round.auction

        # P1 passes
        self.engine.place_bid(1, 'pass', 0)

        # P2 bids in_hand (undeclared)
        self.engine.place_bid(2, 'in_hand', 0)
        self.assertEqual(auction.phase, AuctionPhase.IN_HAND_DECIDING)

        # P3 options: pass, in_hand, betl, sans
        labels = self.get_legal_bid_labels(3)
        self.assertIn('Pass', labels)
        self.assertIn('In Hand', labels)
        self.assertIn('Betl', labels)
        self.assertIn('Sans', labels)

        # P3 bids betl
        self.engine.place_bid(3, 'betl', 6)

        # Auction complete (betl beats any undeclared in_hand which max is 5)
        self.assertEqual(auction.phase, AuctionPhase.COMPLETE)
        winner_bid = auction.get_winner_bid()
        self.assertEqual(winner_bid.player_id, 3)
        self.assertTrue(winner_bid.is_betl())

    def test_example8_all_pass(self):
        """
        Example 8 (labeled 7/): Everyone passes
        P1: pass, P2: pass, P3: pass
        Winner: none
        """
        auction = self.game.current_round.auction

        # P1 passes
        self.engine.place_bid(1, 'pass', 0)

        # P2 passes
        self.engine.place_bid(2, 'pass', 0)

        # P3 passes
        self.engine.place_bid(3, 'pass', 0)

        # Auction complete with no winner
        self.assertEqual(auction.phase, AuctionPhase.COMPLETE)
        winner_bid = auction.get_winner_bid()
        self.assertIsNone(winner_bid)

    def test_pass_always_option(self):
        """Rule 1: Pass is always an option."""
        auction = self.game.current_round.auction

        # INITIAL phase
        labels = self.get_legal_bid_labels(1)
        self.assertIn('Pass', labels)

        # Start game bidding
        self.engine.place_bid(1, 'game', 2)

        # GAME_BIDDING phase
        labels = self.get_legal_bid_labels(2)
        self.assertIn('Pass', labels)

        # Start in_hand deciding
        self.game = Game(id="test2")
        self.game.add_player(Player(id=1, name='Player 1'))
        self.game.add_player(Player(id=2, name='Player 2'))
        self.game.add_player(Player(id=3, name='Player 3'))
        self.engine = GameEngine(self.game)
        self.engine.start_game()
        # Set positions so P1->P2->P3 order (same as setUp)
        for p in self.game.players:
            if p.id == 1:
                p.position = 1
            elif p.id == 2:
                p.position = 3
            else:
                p.position = 2
        self.game.current_round.auction.current_bidder_id = 1

        self.engine.place_bid(1, 'in_hand', 0)

        # IN_HAND_DECIDING phase - P2 is next
        labels = self.get_legal_bid_labels(2)
        self.assertIn('Pass', labels)

    def test_sequential_game_bids(self):
        """Rule 5: Game bids must go sequentially from 2 to 7."""
        auction = self.game.current_round.auction

        # P1 bids 2
        self.engine.place_bid(1, 'game', 2)

        # P2 cannot jump to 4, must bid 3
        labels = self.get_legal_bid_labels(2)
        self.assertIn('3', labels)
        self.assertNotIn('4', labels)
        self.assertNotIn('5', labels)

        # P2 bids 3
        self.engine.place_bid(2, 'game', 3)

        # P3 can bid 4 (next sequential)
        labels = self.get_legal_bid_labels(3)
        self.assertIn('4', labels)
        self.assertNotIn('3', labels)  # Can't match (only first bidder can hold)
        self.assertNotIn('5', labels)

    def test_first_bidder_hold_only(self):
        """Rule 6: First game bidder can only hold, not bid higher."""
        auction = self.game.current_round.auction

        # P1 bids 2
        self.engine.place_bid(1, 'game', 2)
        self.assertEqual(auction.first_game_bidder_id, 1)

        # P2 bids 3
        self.engine.place_bid(2, 'game', 3)

        # P3 bids 4
        self.engine.place_bid(3, 'game', 4)

        # P1 can only hold at 4, not bid 5
        labels = self.get_legal_bid_labels(1)
        self.assertIn('4', labels)
        self.assertNotIn('5', labels)

    def test_in_hand_only_first_bid(self):
        """Rule 4: In_hand bid can only be the first bid a player makes."""
        auction = self.game.current_round.auction

        # P1 bids 2
        self.engine.place_bid(1, 'game', 2)

        # P2 bids 3
        self.engine.place_bid(2, 'game', 3)

        # P3 bids in_hand (their first bid - allowed)
        labels = self.get_legal_bid_labels(3)
        self.assertIn('In Hand', labels)

        # P3 passes instead
        self.engine.place_bid(3, 'pass', 0)

        # P1 already bid, so no in_hand option
        labels = self.get_legal_bid_labels(1)
        self.assertNotIn('In Hand', labels)
        self.assertNotIn('Betl', labels)  # Also not available (already bid)
        self.assertNotIn('Sans', labels)


if __name__ == '__main__':
    unittest.main()
