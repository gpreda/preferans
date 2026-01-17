"""Integration tests for i18n API endpoints."""
import pytest


class TestI18nAPI:
    """Tests for internationalization API endpoints."""

    def test_get_languages(self, client):
        """GET /api/i18n/languages returns language list."""
        response = client.get('/api/i18n/languages')
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)

    def test_get_translations(self, client):
        """GET /api/i18n/translations/<code> returns translations."""
        # First get available languages
        response = client.get('/api/i18n/languages')
        languages = response.get_json()

        if languages:
            # Get translations for first language
            lang_code = languages[0].get('code', 'en')
            response = client.get(f'/api/i18n/translations/{lang_code}')
            assert response.status_code == 200
            data = response.get_json()
            assert isinstance(data, dict)

    def test_get_all_translations(self, client):
        """GET /api/i18n/translations returns all translations."""
        response = client.get('/api/i18n/translations')
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, dict)

    def test_get_translation_keys(self, client):
        """GET /api/i18n/keys returns all translation keys."""
        response = client.get('/api/i18n/keys')
        assert response.status_code == 200
        data = response.get_json()
        assert isinstance(data, list)

    def test_add_language_missing_code(self, client):
        """POST /api/i18n/languages without code returns error."""
        response = client.post('/api/i18n/languages', json={
            'name': 'Test Language'
        })
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data

    def test_add_language_missing_name(self, client):
        """POST /api/i18n/languages without name returns error."""
        response = client.post('/api/i18n/languages', json={
            'code': 'xx'
        })
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data

    def test_update_translation_missing_key(self, client):
        """POST /api/i18n/translations/<code> without key returns error."""
        response = client.post('/api/i18n/translations/en', json={
            'value': 'Test Value'
        })
        assert response.status_code == 400
        data = response.get_json()
        assert 'error' in data
