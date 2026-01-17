"""Integration tests for Preferans game API."""
import pytest


def get_current_bidder(state):
    """Get the current bidder ID from game state."""
    return state.get('current_bidder_id')


def get_forehand_player(state):
    """Get the forehand player (position 1) from game state."""
    for player in state['state']['players']:
        if player['position'] == 1:
            return player
    return None


def complete_auction_with_game_2(client, state):
    """Complete auction where player bids game 2 and others pass."""
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


def complete_exchange_phase(client, state, declarer_id):
    """Complete exchange phase: pick up talon, discard 2 cards, announce contract."""
    # Pick up talon
    response = client.post('/api/game/talon', json={
        'player_id': declarer_id
    })
    assert response.status_code == 200
    game_state = response.get_json()['state']

    # Find declarer's hand (should have 12 cards now)
    declarer = None
    for p in game_state['players']:
        if p['id'] == declarer_id:
            declarer = p
            break

    # Discard first 2 cards
    card_ids = [declarer['hand'][0]['id'], declarer['hand'][1]['id']]
    response = client.post('/api/game/discard', json={
        'player_id': declarer_id,
        'card_ids': card_ids
    })
    assert response.status_code == 200
    game_state = response.get_json()['state']

    # Announce contract (spades as trump)
    response = client.post('/api/game/contract', json={
        'player_id': declarer_id,
        'type': 'suit',
        'trump_suit': 'spades'
    })
    assert response.status_code == 200
    return response.get_json()['state']


class TestHealthAndBasicEndpoints:
    """Tests for health check and basic API endpoints."""

    def test_health_check(self, client):
        """GET /api/health returns ok."""
        response = client.get('/api/health')
        assert response.status_code == 200
        data = response.get_json()
        assert data['status'] == 'ok'

    def test_get_styles(self, client):
        """GET /api/styles returns deck styles."""
        response = client.get('/api/styles')
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)

    def test_get_cards(self, client):
        """GET /api/cards returns card list."""
        response = client.get('/api/cards')
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)


class TestGameLifecycle:
    """Tests for game creation and state management."""

    def test_new_game(self, client):
        """POST /api/game/new creates game with 3 players."""
        response = client.post('/api/game/new', json={
            'players': ['Alice', 'Bob', 'Charlie']
        })
        assert response.status_code == 200
        data = response.get_json()

        assert data['success'] is True
        assert 'game_id' in data
        assert 'state' in data

        state = data['state']
        assert len(state['players']) == 3
        assert state['players'][0]['name'] == 'Alice'
        assert state['players'][1]['name'] == 'Bob'
        assert state['players'][2]['name'] == 'Charlie'

    def test_new_game_default_names(self, client):
        """POST /api/game/new uses default names if not provided."""
        response = client.post('/api/game/new', json={})
        assert response.status_code == 200
        data = response.get_json()
        assert len(data['state']['players']) == 3

    def test_game_state(self, new_game, client):
        """GET /api/game/state returns current state."""
        response = client.get('/api/game/state')
        assert response.status_code == 200
        data = response.get_json()

        assert 'players' in data
        assert 'current_round' in data
        assert data['current_round']['phase'] == 'auction'

    def test_game_state_with_player_id(self, new_game, client):
        """GET /api/game/state with player_id hides other hands."""
        response = client.get('/api/game/state?player_id=1')
        assert response.status_code == 200
        data = response.get_json()

        # Player 1 should see their own hand
        player1 = next(p for p in data['players'] if p['id'] == 1)
        assert len(player1['hand']) == 10

        # Other players' hands should be hidden
        player2 = next(p for p in data['players'] if p['id'] == 2)
        assert len(player2['hand']) == 0
        assert player2['hand_count'] == 10

    def test_game_state_no_game(self, app, client):
        """GET /api/game/state returns error when no game exists."""
        # Reset game state by accessing internal state via the app context
        import sys
        app_module = sys.modules.get('app')
        if app_module:
            app_module.current_engine = None
            app_module.current_game = None

        response = client.get('/api/game/state')
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data


class TestBiddingFlow:
    """Tests for auction/bidding phase."""

    def test_bidding_pass(self, new_game, client):
        """Player can pass during auction."""
        state = new_game['state']
        current_bidder = get_current_bidder(state)

        response = client.post('/api/game/bid', json={
            'player_id': current_bidder,
            'bid_type': 'pass'
        })
        assert response.status_code == 200
        data = response.get_json()

        assert data['success'] is True
        assert data['bid']['bid_type'] == 'pass'
        assert data['bid']['is_pass'] is True

    def test_bidding_game(self, new_game, client):
        """Player can bid game value."""
        state = new_game['state']
        current_bidder = get_current_bidder(state)

        response = client.post('/api/game/bid', json={
            'player_id': current_bidder,
            'bid_type': 'game',
            'value': 2
        })
        assert response.status_code == 200
        data = response.get_json()

        assert data['success'] is True
        assert data['bid']['bid_type'] == 'game'
        assert data['bid']['value'] == 2

    def test_bidding_invalid_player(self, new_game, client):
        """Wrong player cannot bid."""
        state = new_game['state']
        current_bidder = get_current_bidder(state)

        # Find a player who is NOT the current bidder
        other_player = 1 if current_bidder != 1 else 2

        response = client.post('/api/game/bid', json={
            'player_id': other_player,
            'bid_type': 'pass'
        })
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data

    def test_bidding_invalid_type(self, new_game, client):
        """Invalid bid type returns error."""
        state = new_game['state']
        current_bidder = get_current_bidder(state)

        response = client.post('/api/game/bid', json={
            'player_id': current_bidder,
            'bid_type': 'invalid_bid'
        })
        assert response.status_code == 400

    def test_legal_bids_in_state(self, new_game, client):
        """Game state includes legal bids for current bidder."""
        state = new_game['state']
        assert 'legal_bids' in state
        assert isinstance(state['legal_bids'], list)
        assert len(state['legal_bids']) > 0

        # Should include pass option
        bid_types = [b['bid_type'] for b in state['legal_bids']]
        assert 'pass' in bid_types

    def test_initial_phase_all_players_get_full_options(self, new_game, client):
        """All three players should have full INITIAL options during first bid round."""
        state = new_game['state']

        # Player 1 bids game 2
        current_bidder = get_current_bidder(state)
        response = client.post('/api/game/bid', json={
            'player_id': current_bidder,
            'bid_type': 'game',
            'value': 2
        })
        assert response.status_code == 200
        state = response.get_json()['state']

        # Player 2 should still have INITIAL options (including in_hand, betl, sans)
        legal_bids = state.get('legal_bids', [])
        bid_types = [b['bid_type'] for b in legal_bids]
        assert 'pass' in bid_types
        assert 'in_hand' in bid_types
        assert 'betl' in bid_types
        assert 'sans' in bid_types

    def test_betl_wins_over_game_and_in_hand(self, new_game, client):
        """Player bidding betl wins over game and undeclared in_hand bids.

        Scenario:
        - Player1 bids game 2
        - Player2 bids in_hand (intent, no value)
        - Player3 bids betl (value 6)
        - Player1 is eliminated (can't switch to in_hand after bidding game)
        - Player2 gets a chance to bid sans or pass (in_hand_deciding phase)
        - Player2 passes (can't beat betl with in_hand values 2-5)
        - Player3 wins with betl
        """
        state = new_game['state']

        # Player 1 bids game 2
        p1_id = get_current_bidder(state)
        response = client.post('/api/game/bid', json={
            'player_id': p1_id,
            'bid_type': 'game',
            'value': 2
        })
        assert response.status_code == 200
        state = response.get_json()['state']

        # Player 2 bids in_hand (intent)
        p2_id = get_current_bidder(state)
        response = client.post('/api/game/bid', json={
            'player_id': p2_id,
            'bid_type': 'in_hand',
            'value': 0
        })
        assert response.status_code == 200
        state = response.get_json()['state']

        # Player 3 bids betl
        p3_id = get_current_bidder(state)
        response = client.post('/api/game/bid', json={
            'player_id': p3_id,
            'bid_type': 'betl'
        })
        assert response.status_code == 200
        state = response.get_json()['state']

        # After all initial bids, auction goes to in_hand_deciding
        # Player1 (game bidder) is eliminated, Player2 can bid sans or pass
        auction = state['current_round']['auction']
        assert auction['phase'] == 'in_hand_deciding'

        # Player 1 should be in passed_players (eliminated for bidding game)
        assert p1_id in auction['passed_players']

        # Current highest is betl
        assert auction['highest_in_hand_bid']['bid_type'] == 'betl'
        assert auction['highest_in_hand_bid']['player_id'] == p3_id

        # Player 2 is current bidder and can only pass or bid sans
        current_bidder = get_current_bidder(state)
        assert current_bidder == p2_id

        # Player 2 passes (can't beat betl with in_hand values 2-5)
        response = client.post('/api/game/bid', json={
            'player_id': p2_id,
            'bid_type': 'pass'
        })
        assert response.status_code == 200
        state = response.get_json()['state']

        # Now auction should be complete - betl wins
        auction = state['current_round']['auction']
        assert auction['phase'] == 'complete'

        # Declarer should be Player 3
        assert state['current_round']['declarer_id'] == p3_id

    def test_multiple_in_hand_bidders_go_to_declaring(self, new_game, client):
        """When multiple players bid in_hand (without betl/sans), they go to declaring phase."""
        state = new_game['state']

        # Player 1 passes
        p1_id = get_current_bidder(state)
        response = client.post('/api/game/bid', json={
            'player_id': p1_id,
            'bid_type': 'pass'
        })
        assert response.status_code == 200
        state = response.get_json()['state']

        # Player 2 bids in_hand
        p2_id = get_current_bidder(state)
        response = client.post('/api/game/bid', json={
            'player_id': p2_id,
            'bid_type': 'in_hand',
            'value': 0
        })
        assert response.status_code == 200
        state = response.get_json()['state']

        # Player 3 bids in_hand
        p3_id = get_current_bidder(state)
        response = client.post('/api/game/bid', json={
            'player_id': p3_id,
            'bid_type': 'in_hand',
            'value': 0
        })
        assert response.status_code == 200
        state = response.get_json()['state']

        # Should be in in_hand_declaring phase
        auction = state['current_round']['auction']
        assert auction['phase'] == 'in_hand_declaring'
        assert p2_id in auction['in_hand_players']
        assert p3_id in auction['in_hand_players']


class TestExchangeFlow:
    """Tests for talon pickup and card exchange phase."""

    def test_talon_pickup(self, new_game, client):
        """Declarer can pick up talon cards."""
        state = complete_auction_with_game_2(client, new_game)

        # Find declarer
        declarer_id = state['current_round']['declarer_id']

        response = client.post('/api/game/talon', json={
            'player_id': declarer_id
        })
        assert response.status_code == 200
        data = response.get_json()

        assert data['success'] is True
        assert 'talon' in data
        assert len(data['talon']) == 2

    def test_talon_pickup_wrong_player(self, new_game, client):
        """Non-declarer cannot pick up talon."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']

        # Find a non-declarer
        other_player = 1 if declarer_id != 1 else 2

        response = client.post('/api/game/talon', json={
            'player_id': other_player
        })
        assert response.status_code == 400

    def test_discard_cards(self, new_game, client):
        """Declarer can discard 2 cards after talon pickup."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']

        # Pick up talon first
        client.post('/api/game/talon', json={'player_id': declarer_id})

        # Get updated state to see declarer's hand
        response = client.get('/api/game/state')
        state = response.get_json()

        declarer = next(p for p in state['players'] if p['id'] == declarer_id)
        card_ids = [declarer['hand'][0]['id'], declarer['hand'][1]['id']]

        response = client.post('/api/game/discard', json={
            'player_id': declarer_id,
            'card_ids': card_ids
        })
        assert response.status_code == 200
        data = response.get_json()

        assert data['success'] is True
        assert len(data['discarded']) == 2

    def test_discard_wrong_count(self, new_game, client):
        """Must discard exactly 2 cards."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']

        client.post('/api/game/talon', json={'player_id': declarer_id})

        response = client.get('/api/game/state')
        state = response.get_json()
        declarer = next(p for p in state['players'] if p['id'] == declarer_id)

        # Try to discard only 1 card
        response = client.post('/api/game/discard', json={
            'player_id': declarer_id,
            'card_ids': [declarer['hand'][0]['id']]
        })
        assert response.status_code == 400

    def test_announce_contract(self, new_game, client):
        """Declarer can announce contract after discarding."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']

        # Pick up talon
        client.post('/api/game/talon', json={'player_id': declarer_id})

        # Get hand and discard
        response = client.get('/api/game/state')
        state = response.get_json()
        declarer = next(p for p in state['players'] if p['id'] == declarer_id)
        card_ids = [declarer['hand'][0]['id'], declarer['hand'][1]['id']]
        client.post('/api/game/discard', json={
            'player_id': declarer_id,
            'card_ids': card_ids
        })

        # Announce contract
        response = client.post('/api/game/contract', json={
            'player_id': declarer_id,
            'type': 'suit',
            'trump_suit': 'spades'
        })
        assert response.status_code == 200
        data = response.get_json()

        assert data['success'] is True
        assert data['state']['current_round']['phase'] == 'playing'
        assert data['state']['current_round']['contract']['type'] == 'suit'
        assert data['state']['current_round']['contract']['trump_suit'] == 'spades'


class TestPlayingFlow:
    """Tests for card playing phase."""

    def test_play_card(self, new_game, client):
        """Player can play a valid card."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']
        state = complete_exchange_phase(client, state, declarer_id)

        # Get current player and their legal cards
        current_player_id = state.get('current_player_id')
        legal_cards = state.get('legal_cards', [])

        assert current_player_id is not None
        assert len(legal_cards) > 0

        # Play first legal card
        response = client.post('/api/game/play', json={
            'player_id': current_player_id,
            'card_id': legal_cards[0]['id']
        })
        assert response.status_code == 200
        data = response.get_json()

        assert data['success'] is True
        assert 'card' in data['result']

    def test_play_invalid_card(self, new_game, client):
        """Playing a card not in hand fails."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']
        state = complete_exchange_phase(client, state, declarer_id)

        current_player_id = state.get('current_player_id')

        response = client.post('/api/game/play', json={
            'player_id': current_player_id,
            'card_id': 'invalid_card_id'
        })
        assert response.status_code == 400

    def test_play_wrong_player(self, new_game, client):
        """Wrong player cannot play."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']
        state = complete_exchange_phase(client, state, declarer_id)

        current_player_id = state.get('current_player_id')

        # Find a different player
        other_player = 1 if current_player_id != 1 else 2

        response = client.post('/api/game/play', json={
            'player_id': other_player,
            'card_id': 'any_card'
        })
        assert response.status_code == 400


class TestFullGameRound:
    """Test complete game flow from start to scoring."""

    def test_complete_game_round(self, new_game, client):
        """Play through an entire round from start to scoring."""
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']
        state = complete_exchange_phase(client, state, declarer_id)

        # Play 10 tricks (3 cards each)
        tricks_played = 0
        max_tricks = 10

        while tricks_played < max_tricks:
            response = client.get('/api/game/state')
            state = response.get_json()

            # Check if round is complete
            if state['current_round']['phase'] == 'scoring':
                break

            current_player_id = state.get('current_player_id')
            legal_cards = state.get('legal_cards', [])

            if not legal_cards or not current_player_id:
                break

            # Play first legal card
            response = client.post('/api/game/play', json={
                'player_id': current_player_id,
                'card_id': legal_cards[0]['id']
            })
            assert response.status_code == 200

            result = response.get_json()['result']
            if result.get('trick_complete'):
                tricks_played += 1

        # Verify round completed
        response = client.get('/api/game/state')
        final_state = response.get_json()

        assert final_state['current_round']['phase'] == 'scoring'
        assert len(final_state['current_round']['tricks']) == 10

        # Check that scores were updated
        total_tricks = sum(p['tricks_won'] for p in final_state['players'])
        assert total_tricks == 10

    def test_next_round(self, new_game, client):
        """After scoring, can start next round."""
        # Play a complete round first
        state = complete_auction_with_game_2(client, new_game)
        declarer_id = state['current_round']['declarer_id']
        state = complete_exchange_phase(client, state, declarer_id)

        # Play all tricks
        for _ in range(30):  # Max 30 card plays (3 players * 10 tricks)
            response = client.get('/api/game/state')
            state = response.get_json()

            if state['current_round']['phase'] == 'scoring':
                break

            current_player_id = state.get('current_player_id')
            legal_cards = state.get('legal_cards', [])

            if not legal_cards or not current_player_id:
                break

            client.post('/api/game/play', json={
                'player_id': current_player_id,
                'card_id': legal_cards[0]['id']
            })

        # Start next round
        response = client.post('/api/game/next-round')
        assert response.status_code == 200
        data = response.get_json()

        assert data['success'] is True
        assert data['state']['current_round']['phase'] == 'auction'
        assert data['state']['round_number'] == 2
