from flask import Flask, send_from_directory, jsonify, Response, request
from flask_cors import CORS
import os
import requests as http

# Get absolute path to web folder
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE_DIR, 'web')

app = Flask(__name__, static_folder=WEB_DIR, static_url_path='')
CORS(app)

# Service URLs
BIDDING_URL = os.environ.get('BIDDING_ENGINE_URL', 'http://localhost:3001')
GAME_URL = os.environ.get('GAME_ENGINE_URL', 'http://localhost:3002')

# Single game session
session = None


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


# ── Game API (orchestrates bidding-engine + game-engine) ─────────────

LEVEL_TO_TRUMP = {2: 'spades', 3: 'diamonds', 4: 'hearts', 5: 'clubs'}
SUIT_TO_LEVEL = {'spades': 2, 'diamonds': 3, 'hearts': 4, 'clubs': 5}

# Card sorting (matches models.py: suit spades>diamonds>clubs>hearts, rank 7>8>...>A)
_SUIT_ORDER = {'spades': 4, 'diamonds': 3, 'clubs': 2, 'hearts': 1}
_RANK_ORDER = {'7': 8, '8': 7, '9': 6, '10': 5, 'J': 4, 'Q': 3, 'K': 2, 'A': 1}


def _sort_cards(card_ids):
    """Sort card ID strings the same way as models.py sort_hand."""
    def key(cid):
        rank, suit = cid.split('_')
        return (-_SUIT_ORDER.get(suit, 0), -_RANK_ORDER.get(rank, 0))
    return sorted(card_ids, key=key)


def _turn_order(active, lead):
    """Return active players in clockwise order starting from lead."""
    cycle = [1, 2, 3, 1, 2, 3]
    start = cycle.index(lead)
    order = []
    for p in cycle[start:start + 3]:
        if p in active:
            order.append(p)
    return order


def _be_commands():
    """Get current state from bidding engine."""
    return http.get(f'{BIDDING_URL}/commands',
                    params={'game_id': session['be_id']}).json()


def _be_execute(cmd_idx):
    """Execute a command on the bidding engine."""
    return http.post(f'{BIDDING_URL}/execute', json={
        'game_id': session['be_id'],
        'command_id': cmd_idx,
    })


@app.route('/api/game/new', methods=['POST'])
def new_game():
    global session

    ge = http.post(f'{GAME_URL}/new-game').json()
    be = http.post(f'{BIDDING_URL}/new-game').json()

    session = {
        'ge_id': ge['game_id'],
        'be_id': be['game_id'],
        'phase': 'auction',
        'be_phase': 'auction',
        'hands': ge['hands'],
        'talon': ge['talon'],
        'declarer': None,
        'followers': [],
        'contract': None,
        'trick_lead': 1,
        'trick_cards': [],
        'tricks_played': 0,
    }

    return jsonify(_build_state())


@app.route('/api/game/state')
def game_state():
    if not session:
        return jsonify({'error': 'No active game'}), 400
    return jsonify(_build_state())


@app.route('/api/game/execute', methods=['POST'])
def game_execute():
    global session
    if not session:
        return jsonify({'error': 'No active game'}), 400

    data = request.get_json() or {}
    phase = session['phase']

    # Pre-play phases all route through the bidding engine
    if phase in ('auction', 'exchanging', 'whisting'):
        return _exec_bidding_phase(data)
    elif phase == 'exchange_cards':
        return _exec_exchange_cards(data)
    elif phase == 'playing':
        return _exec_play(data)
    elif phase == 'scoring':
        return jsonify({'error': 'Game is over'}), 400
    else:
        return jsonify({'error': f'Unknown phase {phase}'}), 400


def _exec_bidding_phase(data):
    """Execute a command on the bidding engine, then sync phase."""
    cmd_idx = data.get('command_id')

    # Before executing, check if this is a suit choice — remember it
    cmds = _be_commands()
    commands = cmds.get('commands', [])
    suit_map = {'Spades': 'spades', 'Diamonds': 'diamonds',
                'Clubs': 'clubs', 'Hearts': 'hearts'}
    if cmd_idx and 1 <= cmd_idx <= len(commands):
        chosen = commands[cmd_idx - 1]
        if chosen in suit_map:
            session['chosen_trump'] = suit_map[chosen]

    r = _be_execute(cmd_idx)
    if not r.ok:
        return jsonify(r.json()), r.status_code

    _sync_phase()
    return jsonify(_build_state())


def _sync_phase():
    """Read bidding engine state and update session phase accordingly."""
    cmds = _be_commands()
    be_phase = cmds.get('phase', '')
    ctx = cmds.get('context')

    if be_phase in ('scoring', 'playing'):
        # Bidding engine done — extract result
        if ctx and ctx[0] is None:
            session['phase'] = 'scoring'
            session['scoring_reason'] = 'all_pass'
        else:
            # ctx = [declarer, contract_type, [[player, action], ...]]
            declarer = ctx[0]
            ctype = ctx[1]
            others = ctx[2]
            followers = [int(o[0]) for o in others if o[1] != 'pass']
            session['declarer'] = declarer
            session['followers'] = followers
            session['contract_type_raw'] = ctype  # 'suit' or 'betl' or 'sans'

            # Determine contract details
            if ctype == 'betl':
                session['contract'] = {'type': 'betl', 'trump': None, 'level': 6}
            elif ctype == 'sans':
                session['contract'] = {'type': 'sans', 'trump': None, 'level': 7}
            else:
                # Suit contract — trump was chosen during exchanging phase
                trump = session.get('chosen_trump')
                level = SUIT_TO_LEVEL.get(trump, 2)
                session['contract'] = {'type': 'suit', 'trump': trump, 'level': level}

            # Set contract on game engine
            contract = session['contract']
            http.post(f'{GAME_URL}/contract', json={
                'game_id': session['ge_id'],
                'declarer': declarer,
                'followers': followers,
                'contract_type': contract['type'],
                'trump': contract['trump'],
            })

            session['active'] = [declarer] + followers
            session['trick_lead'] = declarer
            session['trick_cards'] = []
            session['tricks_played'] = 0
            session['phase'] = 'playing'

    elif be_phase == 'exchanging':
        # Check if this is the discard step or the suit-choice step
        commands = cmds.get('commands', [])
        if commands == ['discard']:
            # Transition to card-level exchange (handled by game engine)
            session['declarer'] = cmds.get('player_position')
            session['be_phase'] = 'exchanging'
            session['phase'] = 'exchange_cards'
        else:
            # Suit/contract choice — stays with bidding engine
            session['be_phase'] = 'exchanging'
            session['phase'] = 'exchanging'

    elif be_phase == 'whisting':
        session['be_phase'] = 'whisting'
        session['phase'] = 'whisting'

    else:
        session['be_phase'] = be_phase
        session['phase'] = be_phase


def _exec_exchange_cards(data):
    """Player discards 2 cards via game engine, then advances bidding engine past 'discard'."""
    cards = data.get('cards', [])
    r = http.post(f'{GAME_URL}/discard', json={
        'game_id': session['ge_id'],
        'player': session['declarer'],
        'cards': cards,
    })
    if not r.ok:
        return jsonify(r.json()), r.status_code

    resp = r.json()
    session['hands'][str(session['declarer'])] = resp['hand']
    session['talon'] = []

    # Advance bidding engine past the 'discard' step
    _be_execute(1)  # cmd_idx 1 = discard
    _sync_phase()

    return jsonify(_build_state())


def _exec_play(data):
    card = data.get('card')
    player = data.get('player')
    r = http.post(f'{GAME_URL}/play-card', json={
        'game_id': session['ge_id'],
        'player': player,
        'card': card,
    })
    if not r.ok:
        return jsonify(r.json()), r.status_code

    resp = r.json()
    hand = session['hands'][str(player)]
    if card in hand:
        hand.remove(card)

    session['trick_cards'].append({'player': player, 'card': card})

    result = {'trick_complete': resp.get('trick_complete', False)}

    if resp.get('trick_complete'):
        winner = resp['winner']
        result['winner'] = winner
        session['tricks_played'] += 1
        session['trick_lead'] = winner
        session['last_trick'] = session['trick_cards']
        session['trick_cards'] = []

        total_tricks = 10
        if session['tricks_played'] >= total_tricks:
            tricks = http.get(f'{GAME_URL}/tricks',
                              params={'game_id': session['ge_id']}).json()
            session['tricks_won'] = tricks['tricks']
            session['phase'] = 'scoring'

    state = _build_state()
    state['play_result'] = result
    return jsonify(state)


def _build_state():
    s = session
    state = {
        'ge_id': s['ge_id'],
        'be_id': s['be_id'],
        'phase': s['phase'],
        'hands': s['hands'],
        'talon': s['talon'],
        'declarer': s['declarer'],
        'followers': s.get('followers', []),
        'contract': s.get('contract'),
        'tricks_played': s.get('tricks_played', 0),
        'trick_cards': s.get('trick_cards', []),
        'last_trick': s.get('last_trick'),
    }

    if s['phase'] in ('auction', 'exchanging', 'whisting'):
        # These phases are driven by the bidding engine
        cmds = _be_commands()
        state['commands'] = cmds.get('commands', [])
        state['player_on_move'] = cmds.get('player_position')
        state['be_phase'] = cmds.get('phase')

    elif s['phase'] == 'exchange_cards':
        decl = str(s['declarer'])
        state['exchange_cards'] = _sort_cards(s['hands'][decl] + s['talon'])
        state['player_on_move'] = s['declarer']
        state['commands'] = []

    elif s['phase'] == 'playing':
        active = s.get('active', [1, 2, 3])
        order = _turn_order(active, s['trick_lead'])
        played_in_trick = [tc['player'] for tc in s.get('trick_cards', [])]
        remaining = [p for p in order if p not in played_in_trick]
        current = remaining[0] if remaining else None
        state['player_on_move'] = current
        if current:
            state['commands'] = s['hands'][str(current)]
        else:
            state['commands'] = []

    elif s['phase'] == 'scoring':
        state['tricks_won'] = s.get('tricks_won', {})
        state['all_pass'] = s.get('scoring_reason') == 'all_pass'
        state['player_on_move'] = None
        state['commands'] = []

    return state


# === Simulations Tests Page ===

@app.route('/tests')
def tests_page():
    """Serve the simulations tests page."""
    return send_from_directory(WEB_DIR, 'tests.html')


# === Bidding State Machine Page ===

@app.route('/statemachine')
def statemachine_page():
    """Serve the bidding state machine visualisation."""
    return send_from_directory(WEB_DIR, 'statemachine.html')


@app.route('/api/statemachine')
def statemachine_data():
    """Serve the pre-built bidding state machine JSON."""
    import json as _json
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'bidding_state_machine.json')
    try:
        with open(path, encoding='utf-8') as f:
            return jsonify(_json.load(f))
    except FileNotFoundError:
        return jsonify({'error': 'State machine not yet generated. Run bidding_state_machine.py first.'}), 404


@app.route('/api/sim/steps', methods=['GET'])
def sim_steps():
    """Get all simulation steps, optionally filtered by game_id."""
    from sim_db import get_all_steps, get_game_ids
    game_id = request.args.get('game_id')
    try:
        steps = get_all_steps(game_id=game_id)
        game_ids = get_game_ids()
        return jsonify({'steps': steps, 'game_ids': game_ids})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/sim/steps/<int:step_id>', methods=['PATCH'])
def update_sim_step(step_id):
    """Update content or verified flag of a simulation step."""
    from sim_db import update_step, set_verified
    data = request.get_json() or {}
    try:
        if 'content' in data:
            update_step(step_id, data['content'])
        if 'verified' in data:
            set_verified(step_id, bool(data['verified']))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/sim/steps/<int:step_id>', methods=['DELETE'])
def delete_sim_step(step_id):
    """Delete a simulation step row."""
    from sim_db import delete_step
    try:
        delete_step(step_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    import os
    debug_mode = os.environ.get('FLASK_DEBUG', '1') == '1'
    port = int(os.environ.get('FLASK_PORT', '3000'))
    app.run(debug=debug_mode, host='127.0.0.1', port=port)
