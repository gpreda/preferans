from flask import Flask, send_from_directory, jsonify, Response, request
from flask_cors import CORS
import os
import random
import requests as http

# Get absolute path to web folder
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE_DIR, 'web')

app = Flask(__name__, static_folder=WEB_DIR, static_url_path='')
CORS(app)

# Engine service URL (unified)
ENGINE_URL = os.environ.get('ENGINE_URL', 'http://localhost:3001')

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


# ── Game API (unified engine service on port 3001) ────────────────────

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
    """Return active players in counter-clockwise order starting from lead.

    Engine rotation: 1→3→2→1 (counter-clockwise).
    """
    ccw = [1, 3, 2, 1, 3, 2]
    start = ccw.index(lead)
    order = []
    for p in ccw[start:start + 3]:
        if p in active:
            order.append(p)
    return order


def _engine_commands():
    """Get current state from unified engine service."""
    return http.get(f'{ENGINE_URL}/commands',
                    params={'game_id': session['game_id']}).json()


def _engine_execute(cmd_idx):
    """Execute a command on the unified engine service."""
    return http.post(f'{ENGINE_URL}/execute', json={
        'game_id': session['game_id'],
        'command_id': cmd_idx,
    })


def _fetch_scoring():
    """Fetch scoring results from engine and store in session."""
    cmds = _engine_commands()
    scoring = cmds.get('scoring')
    if scoring:
        session['scoring_data'] = scoring


AI_NAMES = ['Alice', 'Bob', 'Carol', 'Neural']


@app.route('/api/game/new', methods=['POST'])
def new_game():
    global session

    data = request.get_json() or {}
    debug = bool(data.get('debug'))

    r = http.post(f'{ENGINE_URL}/new-game', json={}).json()

    ai_names = random.sample(AI_NAMES, 2)

    session = {
        'game_id': r['game_id'],
        'phase': 'auction',
        'hands': r['hands'],
        'talon': r['talon'],
        'declarer': None,
        'followers': [],
        'contract': None,
        'trick_lead': 1,
        'trick_cards': [],
        'tricks_played': 0,
        'players': {1: 'You', 2: ai_names[0], 3: ai_names[1]},
        'human': 1,
        'discarded': [],
        'debug': debug,
    }

    return jsonify(_build_state())


@app.route('/api/game/ai-move', methods=['POST'])
def ai_move():
    """AI auto-plays: pick a random valid command/card."""
    global session
    if not session:
        return jsonify({'error': 'No active game'}), 400

    phase = session['phase']
    human = session.get('human', 1)

    if phase in ('auction', 'exchanging', 'whisting'):
        cmds = _engine_commands()
        player = cmds.get('player_position')
        if player == human:
            return jsonify({'error': 'Not AI turn'}), 400
        commands = cmds.get('commands', [])
        cmd_idx = random.randint(1, len(commands))
        chosen = commands[cmd_idx - 1]
        session['_ai_action'] = {'player': player, 'command': chosen}
        return _exec_bidding_phase({'command_id': cmd_idx})

    elif phase == 'playing':
        # All 3 players participate in every trick (even the passer)
        order = _turn_order([1, 2, 3], session['trick_lead'])
        played = [tc['player'] for tc in session.get('trick_cards', [])]
        remaining = [p for p in order if p not in played]
        current = remaining[0] if remaining else None
        if current is None or current == human:
            return jsonify({'error': 'Not AI turn'}), 400
        # Get legal cards from engine service
        r = http.get(f'{ENGINE_URL}/legal-cards', params={
            'game_id': session['game_id'],
            'player': current,
        })
        if r.ok:
            legal = r.json().get('cards', session['hands'][str(current)])
        else:
            legal = session['hands'][str(current)]
        card = random.choice(legal)
        session['_ai_action'] = {'player': current, 'card': card}
        return _exec_play({'player': current, 'card': card})

    return jsonify({'error': f'Cannot AI move in phase {phase}'}), 400


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
        # Translate command_id into player + card for human plays
        cmd_idx = data.get('command_id')
        if cmd_idx and 'card' not in data:
            human = session.get('human', 1)
            # Get legal cards for this player from state
            r = http.get(f'{ENGINE_URL}/legal-cards', params={
                'game_id': session['game_id'],
                'player': human,
            })
            if r.ok:
                legal = r.json().get('cards', [])
                idx = cmd_idx - 1
                if 0 <= idx < len(legal):
                    data = {'player': human, 'card': legal[idx]}
                else:
                    return jsonify({'error': 'Invalid command_id'}), 400
            else:
                return jsonify({'error': 'Could not get legal cards'}), 400
        return _exec_play(data)
    elif phase == 'scoring':
        return jsonify({'error': 'Game is over'}), 400
    else:
        return jsonify({'error': f'Unknown phase {phase}'}), 400


def _exec_bidding_phase(data):
    """Execute a command on the engine service, then sync phase."""
    cmd_idx = data.get('command_id')

    # Before executing, check if this is a suit choice — remember it
    cmds = _engine_commands()
    commands = cmds.get('commands', [])
    suit_map = {'Spades': 'spades', 'Diamonds': 'diamonds',
                'Clubs': 'clubs', 'Hearts': 'hearts'}
    if cmd_idx and 1 <= cmd_idx <= len(commands):
        chosen = commands[cmd_idx - 1]
        if chosen in suit_map:
            session['chosen_trump'] = suit_map[chosen]

    r = _engine_execute(cmd_idx)
    if not r.ok:
        return jsonify(r.json()), r.status_code

    _sync_phase()
    return jsonify(_build_state())


def _sync_phase():
    """Read engine service state and update session phase accordingly."""
    cmds = _engine_commands()
    engine_phase = cmds.get('phase', '')
    ctx = cmds.get('context')
    if engine_phase in ('scoring', 'playing'):
        # Engine done with pre-play phases — extract result from context
        if ctx and ctx[0] is None:
            session['phase'] = 'scoring'
            session['scoring_reason'] = 'all_pass'
        else:
            # ctx = [declarer_pos, contract_type, [[player_pos, action], ...]]
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

            # If no followers, declarer wins without playing
            if not followers:
                session['phase'] = 'scoring'
                session['scoring_reason'] = 'no_followers'
                _fetch_scoring()
                return

            session['active'] = [declarer] + followers
            # The first trick leader depends on contract type
            # (e.g. Sans: player before declarer leads). Use engine's
            # player-on-move which points at the trick leader.
            session['trick_lead'] = cmds.get('player_position', declarer)
            session['trick_cards'] = []
            session['tricks_played'] = 0
            session['phase'] = 'playing'

    elif engine_phase == 'exchanging':
        # Check if this is the discard step or the suit-choice step
        # Discard step: talon is present, commands are card indices
        # Suit-choice step: commands are suit/contract labels
        commands = cmds.get('commands', [])
        has_talon = bool(cmds.get('context') and len(commands) > 4)
        # If commands are numeric strings (card indices), it's the discard step
        is_discard = commands and all(c.isdigit() for c in commands)
        if is_discard:
            # Transition to card-level exchange
            declarer = cmds.get('player_position')
            session['declarer'] = declarer

            # If AI is declarer, auto-exchange immediately
            human = session.get('human', 1)
            if declarer != human:
                session['phase'] = 'exchange_cards'
                _ai_exchange()
            else:
                session['phase'] = 'exchange_cards'
        else:
            # Suit/contract choice — stays with engine service
            session['phase'] = 'exchanging'

    elif engine_phase == 'whisting':
        session['phase'] = 'whisting'

    elif engine_phase == 'redeal':
        # All players passed — treat as all-pass scoring
        session['phase'] = 'scoring'
        session['scoring_reason'] = 'all_pass'

    else:
        session['phase'] = engine_phase


def _exec_exchange_cards(data):
    """Player discards 2 cards via engine service."""
    cards = data.get('cards', [])
    r = http.post(f'{ENGINE_URL}/discard', json={
        'game_id': session['game_id'],
        'player': session['declarer'],
        'cards': cards,
    })
    if not r.ok:
        return jsonify(r.json()), r.status_code

    resp = r.json()
    session['hands'][str(session['declarer'])] = resp['hand']
    session['talon'] = []
    session['discarded'] = cards

    # Engine already advanced past exchange — sync to next phase
    _sync_phase()

    return jsonify(_build_state())


def _ai_exchange():
    """AI declarer auto-discards 2 random cards."""
    decl = session['declarer']
    all_cards = session['hands'][str(decl)] + session['talon']
    discard = random.sample(all_cards, 2)

    r = http.post(f'{ENGINE_URL}/discard', json={
        'game_id': session['game_id'],
        'player': decl,
        'cards': discard,
    })
    if not r.ok:
        return

    resp = r.json()
    session['hands'][str(decl)] = resp['hand']
    session['talon'] = []
    session['discarded'] = discard

    # Engine already advanced past exchange — sync to next phase
    _sync_phase()


def _exec_play(data):
    card = data.get('card')
    player = data.get('player')
    r = http.post(f'{ENGINE_URL}/play-card', json={
        'game_id': session['game_id'],
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
            tricks = http.get(f'{ENGINE_URL}/tricks',
                              params={'game_id': session['game_id']}).json()
            session['tricks_won'] = tricks['tricks']
            session['phase'] = 'scoring'
            _fetch_scoring()

    state = _build_state()
    state['play_result'] = result
    return jsonify(state)


def _build_state():
    s = session
    human = s.get('human', 1)
    players = s.get('players', {1: 'You', 2: 'P2', 3: 'P3'})

    debug = s.get('debug', False)

    # Build hands with visibility rules
    hands = {}
    for p in [1, 2, 3]:
        h = s['hands'][str(p)]
        if p == human or debug:
            hands[str(p)] = h
        else:
            hands[str(p)] = len(h)  # AI: send count only

    # Talon visibility: hidden unless exchange phase and declarer is human
    if (s['phase'] == 'exchange_cards' and s['declarer'] == human) or debug:
        talon = s['talon']
    else:
        talon = len(s['talon'])

    state = {
        'game_id': s['game_id'],
        'phase': s['phase'],
        'hands': hands,
        'talon': talon,
        'declarer': s['declarer'],
        'followers': s.get('followers', []),
        'contract': s.get('contract'),
        'tricks_played': s.get('tricks_played', 0),
        'trick_cards': s.get('trick_cards', []),
        'last_trick': s.get('last_trick'),
        'players': players,
        'human': human,
        'discarded': s.get('discarded', []) if s['phase'] in ('whisting', 'playing', 'scoring') else [],
        'ai_action': s.pop('_ai_action', None),
    }

    if s['phase'] in ('auction', 'exchanging', 'whisting'):
        # These phases are driven by the engine service
        cmds = _engine_commands()
        state['commands'] = cmds.get('commands', [])
        state['player_on_move'] = cmds.get('player_position')
        state['be_phase'] = cmds.get('phase')

    elif s['phase'] == 'exchange_cards':
        decl = s['declarer']
        if decl == human:
            state['exchange_cards'] = _sort_cards(s['hands'][str(decl)] + s['talon'])
        else:
            state['exchange_cards'] = []
        state['player_on_move'] = decl
        state['commands'] = []

    elif s['phase'] == 'playing':
        # All 3 players participate in every trick
        order = _turn_order([1, 2, 3], s['trick_lead'])
        played_in_trick = [tc['player'] for tc in s.get('trick_cards', [])]
        remaining = [p for p in order if p not in played_in_trick]
        current = remaining[0] if remaining else None
        state['player_on_move'] = current
        if current and current == human:
            # Get legal cards from engine service
            r = http.get(f'{ENGINE_URL}/legal-cards', params={
                'game_id': s['game_id'],
                'player': current,
            })
            if r.ok:
                state['commands'] = r.json().get('cards', s['hands'][str(current)])
            else:
                state['commands'] = s['hands'][str(current)]
        else:
            state['commands'] = []

    elif s['phase'] == 'scoring':
        state['tricks_won'] = s.get('tricks_won', {})
        state['all_pass'] = s.get('scoring_reason') == 'all_pass'
        state['no_followers'] = s.get('scoring_reason') == 'no_followers'
        state['scoring'] = s.get('scoring_data')
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
