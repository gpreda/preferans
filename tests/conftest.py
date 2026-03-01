"""Pytest fixtures for Preferans integration tests."""
import sys
import os
import pytest

# Add server directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'server'))

from app import app as flask_app


@pytest.fixture
def app():
    """Create Flask app for testing."""
    flask_app.config.update({
        'TESTING': True,
    })
    yield flask_app


@pytest.fixture
def client(app):
    """Create Flask test client."""
    return app.test_client()


@pytest.fixture
def new_game(client):
    """Create a new game and return the response data."""
    response = client.post('/api/game/new', json={
        'players': ['Alice', 'Bob', 'Charlie']
    })
    assert response.status_code == 200
    return response.get_json()
