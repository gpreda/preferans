from flask import Flask, send_from_directory, jsonify, Response, request
from flask_cors import CORS
import os
import uuid

# Get absolute path to web folder
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE_DIR, 'web')

app = Flask(__name__, static_folder=WEB_DIR, static_url_path='')
CORS(app)

# Store current game in memory (single game session for now)
current_game = None
current_engine = None


def get_image_response(image_data):
    """Create appropriate response based on image type."""
    if not image_data:
        return jsonify({'error': 'Image not found'}), 404

    img_type = image_data['type']
    if img_type == 'svg':
        return Response(image_data['svg'], mimetype='image/svg+xml')
    else:
        mime_types = {'png': 'image/png', 'jpg': 'image/jpeg', 'webp': 'image/webp'}
        return Response(image_data['binary'], mimetype=mime_types.get(img_type, 'application/octet-stream'))


@app.route('/')
def index():
    return send_from_directory(WEB_DIR, 'index.html')


@app.route('/i18n')
def i18n_editor():
    return send_from_directory(WEB_DIR, 'i18n.html')


@app.route('/api/health')
def health():
    return {'status': 'ok'}


# Deck Styles API

@app.route('/api/styles')
def get_styles():
    """Get all available deck styles."""
    from db import get_all_styles
    styles = get_all_styles()
    return jsonify([dict(s) for s in styles])


@app.route('/api/styles/<style_name>')
def get_style(style_name):
    """Get a specific deck style by name."""
    from db import get_style as db_get_style
    style = db_get_style(name=style_name)
    if style:
        return jsonify({
            'id': style['id'],
            'name': style['name'],
            'description': style['description'],
            'back_image_type': style['back_image_type'],
            'is_default': style['is_default']
        })
    return jsonify({'error': 'Style not found'}), 404


@app.route('/api/styles/<style_name>/back')
def get_style_back(style_name):
    """Get the card back image for a style."""
    from db import get_style_back_image
    image_data = get_style_back_image(name=style_name)
    return get_image_response(image_data)


# Cards API (with style support)

@app.route('/api/cards')
def get_cards():
    """Get all cards metadata. Use ?style=name to specify style."""
    from db import get_all_cards
    style_name = request.args.get('style')
    cards = get_all_cards(style_name=style_name)
    return jsonify([{
        'card_id': c['card_id'],
        'rank': c['rank'],
        'suit': c['suit'],
        'image_type': c['image_type']
    } for c in cards])


@app.route('/api/cards/<card_id>')
def get_card(card_id):
    """Get a single card by ID. Use ?style=name to specify style."""
    from db import get_card as db_get_card
    style_name = request.args.get('style')
    card = db_get_card(card_id, style_name=style_name)
    if card:
        return jsonify({
            'card_id': card['card_id'],
            'rank': card['rank'],
            'suit': card['suit'],
            'image_type': card['image_type']
        })
    return jsonify({'error': 'Card not found'}), 404


@app.route('/api/cards/<card_id>/image')
def get_card_image(card_id):
    """Get card image directly. Use ?style=name to specify style."""
    from db import get_card_image as db_get_card_image
    style_name = request.args.get('style')
    image_data = db_get_card_image(card_id, style_name=style_name)
    return get_image_response(image_data)


# Game API

@app.route('/api/game/new', methods=['POST'])
def new_game():
    """Start a new game with 3 human players."""
    global current_game, current_engine
    from models import Game
    from engine import GameEngine

    # Get player names from request or use defaults
    data = request.get_json() or {}
    player_names = data.get('players', ['Player 1', 'Player 2', 'Player 3'])

    # Create new game
    current_game = Game(id=str(uuid.uuid4()))
    for name in player_names[:3]:
        current_game.add_human_player(name)

    # Create engine and start game
    current_engine = GameEngine(current_game)
    current_engine.start_game()

    return jsonify({
        'success': True,
        'game_id': current_game.id,
        'state': current_engine.get_game_state()
    })


@app.route('/api/game/state')
def game_state():
    """Get current game state."""
    global current_engine
    if not current_engine:
        return jsonify({'error': 'No active game'}), 400

    viewer_id = request.args.get('player_id', type=int)
    return jsonify(current_engine.get_game_state(viewer_id=viewer_id))


@app.route('/api/game/bid', methods=['POST'])
def place_bid():
    """Place a bid during auction phase."""
    global current_engine
    if not current_engine:
        return jsonify({'error': 'No active game'}), 400

    from engine import InvalidMoveError, InvalidPhaseError

    data = request.get_json()
    player_id = data.get('player_id')
    value = data.get('value', 0)
    suit = data.get('suit')
    is_hand = data.get('is_hand', False)

    try:
        bid = current_engine.place_bid(player_id, value, suit, is_hand)
        return jsonify({
            'success': True,
            'bid': bid.to_dict(),
            'state': current_engine.get_game_state()
        })
    except (InvalidMoveError, InvalidPhaseError) as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/game/talon', methods=['POST'])
def pick_up_talon():
    """Declarer picks up talon cards."""
    global current_engine
    if not current_engine:
        return jsonify({'error': 'No active game'}), 400

    from engine import InvalidMoveError, InvalidPhaseError

    data = request.get_json()
    player_id = data.get('player_id')

    try:
        cards = current_engine.pick_up_talon(player_id)
        return jsonify({
            'success': True,
            'talon': [c.to_dict() for c in cards],
            'state': current_engine.get_game_state()
        })
    except (InvalidMoveError, InvalidPhaseError) as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/game/discard', methods=['POST'])
def discard_cards():
    """Declarer discards two cards."""
    global current_engine
    if not current_engine:
        return jsonify({'error': 'No active game'}), 400

    from engine import InvalidMoveError, InvalidPhaseError

    data = request.get_json()
    player_id = data.get('player_id')
    card_ids = data.get('card_ids', [])

    try:
        discarded = current_engine.discard_cards(player_id, card_ids)
        return jsonify({
            'success': True,
            'discarded': [c.to_dict() for c in discarded],
            'state': current_engine.get_game_state()
        })
    except (InvalidMoveError, InvalidPhaseError) as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/game/contract', methods=['POST'])
def announce_contract():
    """Declarer announces contract."""
    global current_engine
    if not current_engine:
        return jsonify({'error': 'No active game'}), 400

    from engine import InvalidMoveError, InvalidPhaseError

    data = request.get_json()
    player_id = data.get('player_id')
    contract_type = data.get('type')
    trump_suit = data.get('trump_suit')

    try:
        current_engine.announce_contract(player_id, contract_type, trump_suit)
        return jsonify({
            'success': True,
            'state': current_engine.get_game_state()
        })
    except (InvalidMoveError, InvalidPhaseError) as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/game/play', methods=['POST'])
def play_card():
    """Play a card to the current trick."""
    global current_engine
    if not current_engine:
        return jsonify({'error': 'No active game'}), 400

    from engine import InvalidMoveError, InvalidPhaseError

    data = request.get_json()
    player_id = data.get('player_id')
    card_id = data.get('card_id')

    try:
        result = current_engine.play_card(player_id, card_id)
        return jsonify({
            'success': True,
            'result': result,
            'state': current_engine.get_game_state()
        })
    except (InvalidMoveError, InvalidPhaseError) as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/game/next-round', methods=['POST'])
def next_round():
    """Start the next round after scoring."""
    global current_engine
    if not current_engine:
        return jsonify({'error': 'No active game'}), 400

    try:
        current_engine.start_next_round()
        return jsonify({
            'success': True,
            'state': current_engine.get_game_state()
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400


# i18n API

@app.route('/api/i18n/languages')
def get_languages():
    """Get all available languages."""
    from db import get_all_languages
    languages = get_all_languages()
    return jsonify([dict(l) for l in languages])


@app.route('/api/i18n/languages', methods=['POST'])
def add_language():
    """Add a new language."""
    from db import add_language as db_add_language
    data = request.get_json()
    code = data.get('code')
    name = data.get('name')
    native_name = data.get('native_name')

    if not code or not name:
        return jsonify({'error': 'Code and name are required'}), 400

    try:
        lang = db_add_language(code, name, native_name)
        return jsonify({'success': True, 'language': dict(lang)})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/i18n/translations')
def get_all_translations():
    """Get all translations for all languages."""
    from db import get_all_translations as db_get_all_translations
    return jsonify(db_get_all_translations())


@app.route('/api/i18n/translations/<language_code>')
def get_translations(language_code):
    """Get translations for a specific language."""
    from db import get_translations as db_get_translations
    return jsonify(db_get_translations(language_code))


@app.route('/api/i18n/translations/<language_code>', methods=['POST'])
def update_translation(language_code):
    """Update or create a translation."""
    from db import update_translation as db_update_translation
    data = request.get_json()
    key = data.get('key')
    value = data.get('value')

    if not key:
        return jsonify({'error': 'Key is required'}), 400

    result = db_update_translation(language_code, key, value or '')
    if result:
        return jsonify({'success': True, 'translation': dict(result)})
    return jsonify({'error': 'Language not found'}), 404


@app.route('/api/i18n/translations/<language_code>/<path:key>', methods=['DELETE'])
def delete_translation(language_code, key):
    """Delete a translation."""
    from db import delete_translation as db_delete_translation
    if db_delete_translation(language_code, key):
        return jsonify({'success': True})
    return jsonify({'error': 'Translation not found'}), 404


@app.route('/api/i18n/keys')
def get_translation_keys():
    """Get all unique translation keys."""
    from db import get_all_translation_keys
    return jsonify(get_all_translation_keys())


if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=3000)
