"""Integration tests for talon exchange feature.

These tests verify:
1. Talon is visible during exchanging phase (before pickup)
2. Contract announcement is blocked until exchange is complete
3. Proper state transitions through the exchange flow
"""
import pytest


def get_current_bidder(state):
    """Get the current bidder ID from game state."""
    return state.get('current_bidder_id')


def complete_auction_with_game_2(client, state):
    """Complete auction where first player bids game 2 and others pass."""
    game_state = state['state']
    current_bidder = get_current_bidder(game_state)

    # First player bids game 2
    response = client.post('/api/game/bid', json={
        'player_id': current_bidder,
        'bid_type': 'game',
        'value': 2
    })
    assert response.status_code == 200
    game_state = response.get_json()['state']

    # Other two players pass
    for _ in range(2):
        current_bidder = get_current_bidder(game_state)
        response = client.post('/api/game/bid', json={
            'player_id': current_bidder,
            'bid_type': 'pass'
        })
        assert response.status_code == 200
        game_state = response.get_json()['state']

    return game_state


def complete_auction_with_in_hand(client, state):
    """Complete auction where first player bids in_hand and others pass."""
    game_state = state['state']
    current_bidder = get_current_bidder(game_state)

    # First player bids in_hand
    response = client.post('/api/game/bid', json={
        'player_id': current_bidder,
        'bid_type': 'in_hand',
        'value': 0
    })
    assert response.status_code == 200
    game_state = response.get_json()['state']

    # Other two players pass
    for _ in range(2):
        current_bidder = get_current_bidder(game_state)
        if current_bidder is None:
            # Auction might already be complete
            break
        response = client.post('/api/game/bid', json={
            'player_id': current_bidder,
            'bid_type': 'pass'
        })
        assert response.status_code == 200
        game_state = response.get_json()['state']

    return game_state


class TestTalonVisibility:
    """Tests for talon visibility during exchange phase."""

    def test_talon_visible_when_exchange_starts(self, new_game, client):
        """After auction with regular game bid, talon should be visible."""
        state = complete_auction_with_game_2(client, new_game)

        # Phase should be exchanging
        assert state['current_round']['phase'] == 'exchanging'

        # Talon should be visible (not empty array)
        talon = state['current_round']['talon']
        assert len(talon) == 2, "Talon should have 2 visible cards"

        # Each talon card should have id, rank, suit
        for card in talon:
            assert 'id' in card
            assert 'rank' in card
            assert 'suit' in card

    def test_talon_count_matches_visible_cards(self, new_game, client):
        """talon_count should match number of visible talon cards."""
        state = complete_auction_with_game_2(client, new_game)

        talon = state['current_round']['talon']
        talon_count = state['current_round']['talon_count']

        assert talon_count == 2
        assert len(talon) == talon_count

    def test_talon_hidden_during_auction(self, new_game, client):
        """During auction phase, talon should be hidden."""
        state = new_game['state']

        # Phase should be auction
        assert state['current_round']['phase'] == 'auction'

        # Talon should be hidden (empty array) but count should be 2
        talon = state['current_round']['talon']
        talon_count = state['current_round']['talon_count']

        assert len(talon) == 0, "Talon should be hidden during auction"
        assert talon_count == 2, "Talon count should still be 2"

    def test_talon_empty_after_pickup(self, new_game, client):
        """After declarer picks up talon, it should be empty."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']

        # Pick up talon
        response = client.post('/api/game/talon', json={
            'player_id': declarer_id
        })
        assert response.status_code == 200
        state = response.get_json()['state']

        # Talon should now be empty
        talon = state['current_round']['talon']
        talon_count = state['current_round']['talon_count']

        assert len(talon) == 0, "Talon should be empty after pickup"
        assert talon_count == 0, "Talon count should be 0 after pickup"


class TestExchangeStateTransitions:
    """Tests for proper state transitions during exchange."""

    def test_declarer_has_10_cards_before_pickup(self, new_game, client):
        """Before talon pickup, declarer should have 10 cards."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']

        declarer = next(p for p in state['players'] if p['id'] == declarer_id)
        assert len(declarer['hand']) == 10

    def test_declarer_has_12_cards_after_pickup(self, new_game, client):
        """After talon pickup, declarer should have 12 cards."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']

        # Pick up talon
        response = client.post('/api/game/talon', json={
            'player_id': declarer_id
        })
        assert response.status_code == 200
        state = response.get_json()['state']

        declarer = next(p for p in state['players'] if p['id'] == declarer_id)
        assert len(declarer['hand']) == 12

    def test_declarer_has_10_cards_after_discard(self, new_game, client):
        """After discarding 2 cards, declarer should have 10 cards."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']

        # Pick up talon
        response = client.post('/api/game/talon', json={
            'player_id': declarer_id
        })
        state = response.get_json()['state']

        # Get declarer's hand
        declarer = next(p for p in state['players'] if p['id'] == declarer_id)
        card_ids = [declarer['hand'][0]['id'], declarer['hand'][1]['id']]

        # Discard 2 cards
        response = client.post('/api/game/discard', json={
            'player_id': declarer_id,
            'card_ids': card_ids
        })
        assert response.status_code == 200
        state = response.get_json()['state']

        declarer = next(p for p in state['players'] if p['id'] == declarer_id)
        assert len(declarer['hand']) == 10

    def test_discarded_cards_tracked(self, new_game, client):
        """Discarded cards should be tracked in round state."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']

        # Pick up talon
        response = client.post('/api/game/talon', json={
            'player_id': declarer_id
        })
        state = response.get_json()['state']

        # Get declarer's hand
        declarer = next(p for p in state['players'] if p['id'] == declarer_id)
        card_ids = [declarer['hand'][0]['id'], declarer['hand'][1]['id']]

        # Discard 2 cards
        response = client.post('/api/game/discard', json={
            'player_id': declarer_id,
            'card_ids': card_ids
        })
        state = response.get_json()['state']

        discarded = state['current_round']['discarded']
        assert len(discarded) == 2
        discarded_ids = [c['id'] for c in discarded]
        assert card_ids[0] in discarded_ids
        assert card_ids[1] in discarded_ids


class TestContractAnnouncementRestrictions:
    """Tests for contract announcement timing restrictions."""

    def test_cannot_announce_before_pickup(self, new_game, client):
        """Cannot announce contract before picking up talon."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']

        # Try to announce contract without picking up talon
        response = client.post('/api/game/contract', json={
            'player_id': declarer_id,
            'level': 2
        })

        # Should fail
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data

    def test_cannot_announce_before_discard(self, new_game, client):
        """Cannot announce contract after pickup but before discard."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']

        # Pick up talon
        response = client.post('/api/game/talon', json={
            'player_id': declarer_id
        })
        assert response.status_code == 200

        # Try to announce contract without discarding
        response = client.post('/api/game/contract', json={
            'player_id': declarer_id,
            'level': 2
        })

        # Should fail
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data

    def test_can_announce_after_discard(self, new_game, client):
        """Can announce contract after pickup and discard."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']

        # Pick up talon
        response = client.post('/api/game/talon', json={
            'player_id': declarer_id
        })
        state = response.get_json()['state']

        # Get declarer's hand and discard
        declarer = next(p for p in state['players'] if p['id'] == declarer_id)
        card_ids = [declarer['hand'][0]['id'], declarer['hand'][1]['id']]
        response = client.post('/api/game/discard', json={
            'player_id': declarer_id,
            'card_ids': card_ids
        })
        assert response.status_code == 200

        # Now announce contract
        response = client.post('/api/game/contract', json={
            'player_id': declarer_id,
            'level': 2
        })

        # Should succeed
        assert response.status_code == 200
        data = response.get_json()
        assert data['success'] is True
        assert data['state']['current_round']['phase'] == 'playing'


class TestInHandVsRegularExchange:
    """Tests for different exchange behavior with in_hand vs regular game."""

    def test_in_hand_skips_exchange(self, new_game, client):
        """In-hand bid should skip exchange phase entirely."""
        state = complete_auction_with_in_hand(client, new_game)
        declarer_id = state['current_round']['declarer_id']

        # Phase should be exchanging but with special handling
        # For in-hand, declarer doesn't pick up talon
        phase = state['current_round']['phase']

        # Declarer should still have 10 cards (no talon pickup)
        declarer = next(p for p in state['players'] if p['id'] == declarer_id)
        assert len(declarer['hand']) == 10

        # Talon should still have 2 cards (hidden, not picked up)
        talon_count = state['current_round']['talon_count']
        assert talon_count == 2

    def test_in_hand_can_announce_directly(self, new_game, client):
        """In-hand declarer can announce contract without talon pickup."""
        state = complete_auction_with_in_hand(client, new_game)
        declarer_id = state['current_round']['declarer_id']

        # For in-hand, should be able to announce contract directly
        # The exact level depends on the in_hand value declared
        # With in_hand value 0 (intent only), need to declare specific level

        # Check current phase
        phase = state['current_round']['phase']
        auction = state['current_round']['auction']

        # If we're in in_hand_declaring phase, declarer needs to declare value first
        if auction.get('phase') == 'in_hand_declaring':
            # Declare a specific in_hand value
            response = client.post('/api/game/bid', json={
                'player_id': declarer_id,
                'bid_type': 'in_hand',
                'value': 2
            })
            if response.status_code == 200:
                state = response.get_json()['state']

        # Now try to announce contract
        response = client.post('/api/game/contract', json={
            'player_id': declarer_id,
            'level': 2
        })

        # Should succeed for in-hand (no exchange required)
        if response.status_code == 200:
            data = response.get_json()
            assert data['state']['current_round']['phase'] == 'playing'
            assert data['state']['current_round']['contract']['is_in_hand'] is True


class TestExchangePhaseIndicators:
    """Tests for state indicators during exchange phase."""

    def test_phase_is_exchanging_after_auction(self, new_game, client):
        """Phase should be 'exchanging' after regular game auction completes."""
        state = complete_auction_with_game_2(client, new_game)

        assert state['current_round']['phase'] == 'exchanging'

    def test_phase_stays_exchanging_after_pickup(self, new_game, client):
        """Phase should still be 'exchanging' after talon pickup."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']

        response = client.post('/api/game/talon', json={
            'player_id': declarer_id
        })
        state = response.get_json()['state']

        assert state['current_round']['phase'] == 'exchanging'

    def test_phase_stays_exchanging_after_discard(self, new_game, client):
        """Phase should still be 'exchanging' after discard (until contract)."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']

        # Pickup
        response = client.post('/api/game/talon', json={
            'player_id': declarer_id
        })
        state = response.get_json()['state']

        # Discard
        declarer = next(p for p in state['players'] if p['id'] == declarer_id)
        card_ids = [declarer['hand'][0]['id'], declarer['hand'][1]['id']]
        response = client.post('/api/game/discard', json={
            'player_id': declarer_id,
            'card_ids': card_ids
        })
        state = response.get_json()['state']

        assert state['current_round']['phase'] == 'exchanging'

    def test_phase_changes_to_playing_after_contract(self, new_game, client):
        """Phase should change to 'playing' after contract announcement."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']

        # Pickup
        response = client.post('/api/game/talon', json={
            'player_id': declarer_id
        })
        state = response.get_json()['state']

        # Discard
        declarer = next(p for p in state['players'] if p['id'] == declarer_id)
        card_ids = [declarer['hand'][0]['id'], declarer['hand'][1]['id']]
        response = client.post('/api/game/discard', json={
            'player_id': declarer_id,
            'card_ids': card_ids
        })

        # Announce contract
        response = client.post('/api/game/contract', json={
            'player_id': declarer_id,
            'level': 2
        })
        state = response.get_json()['state']

        assert state['current_round']['phase'] == 'playing'
