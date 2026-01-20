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


@app.route('/api/health')
def health():
    return {'status': 'ok'}


# i18n Editor Page

@app.route('/i18n')
def i18n_editor():
    """Serve the i18n editor page."""
    return send_from_directory(WEB_DIR, 'i18n.html')


# i18n API

@app.route('/api/i18n/languages', methods=['GET', 'POST'])
def i18n_languages():
    """Get all languages or add a new one."""
    from db import get_all_languages, add_language

    if request.method == 'POST':
        data = request.get_json()
        code = data.get('code')
        name = data.get('name')
        native_name = data.get('native_name')
        is_default = data.get('is_default', False)

        if not code or not name:
            return jsonify({'error': 'code and name are required'}), 400

        try:
            lang_id = add_language(code, name, native_name, is_default)
            return jsonify({'success': True, 'id': lang_id})
        except Exception as e:
            return jsonify({'error': str(e)}), 400

    languages = get_all_languages()
    return jsonify([dict(l) for l in languages])


@app.route('/api/i18n/translations')
def i18n_all_translations():
    """Get all translations for all languages."""
    from db import get_all_translations
    return jsonify(get_all_translations())


@app.route('/api/i18n/translations/<language_code>', methods=['GET', 'POST'])
def i18n_translations(language_code):
    """Get or update translations for a language."""
    from db import get_translations, update_translation

    if request.method == 'POST':
        data = request.get_json()
        key = data.get('key')
        value = data.get('value')

        if not key:
            return jsonify({'error': 'key is required'}), 400

        try:
            update_translation(language_code, key, value)
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'error': str(e)}), 400

    translations = get_translations(language_code)
    return jsonify(translations)


@app.route('/api/i18n/keys')
def i18n_keys():
    """Get all unique translation keys."""
    from db import get_all_translation_keys
    keys = get_all_translation_keys()
    return jsonify(keys)


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
    """Start a new game with 2 AI players and 1 human player."""
    global current_game, current_engine
    from models import Game
    from engine import GameEngine

    # Get player names from request or use defaults
    data = request.get_json() or {}
    player_names = data.get('players', ['Player 1', 'Player 2', 'Player 3'])

    # Create new game with 2 AI players and 1 human player
    current_game = Game(id=str(uuid.uuid4()))
    # Players 1 and 2 are AI, Player 3 is human
    current_game.add_ai_player(player_names[0] if len(player_names) > 0 else 'Player 1')
    current_game.add_ai_player(player_names[1] if len(player_names) > 1 else 'Player 2')
    current_game.add_human_player(player_names[2] if len(player_names) > 2 else 'Player 3')

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
    bid_type = data.get('bid_type', 'pass')
    value = data.get('value', 0)

    try:
        bid = current_engine.place_bid(player_id, bid_type, value)
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


@app.route('/api/game/exchange', methods=['POST'])
def complete_exchange():
    """Complete exchange atomically - picks up talon and discards specified cards."""
    global current_engine
    if not current_engine:
        return jsonify({'error': 'No active game'}), 400

    from engine import InvalidMoveError, InvalidPhaseError

    data = request.get_json()
    player_id = data.get('player_id')
    card_ids = data.get('card_ids', [])

    if len(card_ids) != 2:
        return jsonify({'error': 'Must specify exactly 2 cards to discard'}), 400

    try:
        current_engine.complete_exchange(player_id, card_ids)
        return jsonify({
            'success': True,
            'state': current_engine.get_game_state()
        })
    except (InvalidMoveError, InvalidPhaseError) as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/game/contract', methods=['POST'])
def announce_contract():
    """Declarer announces contract by level (2-7)."""
    global current_engine
    if not current_engine:
        return jsonify({'error': 'No active game'}), 400

    from engine import InvalidMoveError, InvalidPhaseError

    data = request.get_json()
    player_id = data.get('player_id')
    level = data.get('level')

    if level is None or level < 2 or level > 7:
        return jsonify({'error': 'Invalid contract level (must be 2-7)'}), 400

    # Map level to contract type and trump suit
    # In Preferans: 2=Spades, 3=Diamonds, 4=Clubs, 5=Hearts, 6=Betl, 7=Sans
    LEVEL_TO_TRUMP = {
        2: 'spades',
        3: 'diamonds',
        4: 'clubs',
        5: 'hearts',
    }

    if level == 6:
        contract_type = 'betl'
        trump_suit = None
    elif level == 7:
        contract_type = 'sans'
        trump_suit = None
    else:
        # Levels 2-5 are suit contracts with fixed trump based on level
        contract_type = 'suit'
        trump_suit = LEVEL_TO_TRUMP[level]

    try:
        current_engine.announce_contract(player_id, contract_type, trump_suit, level=level)
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

    from engine import InvalidMoveError, InvalidPhaseError, GameError

    data = request.get_json()
    player_id = data.get('player_id')
    card_id = data.get('card_id')

    print(f"[API play_card] player_id={player_id}, card_id={card_id}")

    try:
        result = current_engine.play_card(player_id, card_id)
        return jsonify({
            'success': True,
            'result': result,
            'state': current_engine.get_game_state()
        })
    except (InvalidMoveError, InvalidPhaseError, GameError) as e:
        print(f"[API play_card] ERROR: {type(e).__name__}: {e}")
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


if __name__ == '__main__':
    import os
    debug_mode = os.environ.get('FLASK_DEBUG', '1') == '1'
    port = int(os.environ.get('FLASK_PORT', '3000'))
    app.run(debug=debug_mode, host='127.0.0.1', port=port)
