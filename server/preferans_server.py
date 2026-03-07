from flask import Flask, send_from_directory, jsonify, Response, request
from flask_cors import CORS
import os
import random
import requests as http

# Get absolute path to web folder
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE_DIR, 'web')

# Import player strategy classes
import sys as _sys
_sys.path.insert(0, BASE_DIR)
from PrefTestSingleGame import PlayerAlice, PlayerBob, PlayerCarol, NeuralPlayer, Sim3000, CardPlayContext, BasePlayer
from models import Card as _Card, NAME_TO_SUIT as _NAME_TO_SUIT

PLAYER_CLASSES = {
    'Alice': PlayerAlice,
    'Bob': PlayerBob,
    'Carol': PlayerCarol,
}

NEURAL_PROFILES = [
    ('Neural-cautious',  0.0),
    ('Neural-balanced',  0.5),
    ('Neural-aggressive', 1.0),
]


class _BidStub:
    """Minimal bid object for choose_contract()."""
    def __init__(self, bid_type, value):
        self.bid_type = bid_type
        self.value = value
        self.effective_value = value


def _sm_cmd_to_bid(cmd):
    """Convert SM command string to bid dict for strategy classes."""
    if cmd == 'Pass':
        return {"bid_type": "pass", "value": 0, "label": "Pass"}
    if cmd in ('2', '3', '4', '5'):
        return {"bid_type": "game", "value": int(cmd), "label": cmd}
    if cmd == 'Hand':
        return {"bid_type": "in_hand", "value": 0, "label": "Hand"}
    if cmd.startswith('in_hand '):
        return {"bid_type": "in_hand", "value": int(cmd.split()[-1]), "label": cmd}
    if cmd == 'Betl':
        return {"bid_type": "betl", "value": 6, "label": "Betl"}
    if cmd == 'Sans':
        return {"bid_type": "sans", "value": 7, "label": "Sans"}
    return {"bid_type": cmd.lower(), "value": 0, "label": cmd}


_WHIST_CMD_MAP = {
    'Pass': 'pass', 'Follow': 'follow', 'Call': 'call',
    'Counter': 'counter', 'Double counter': 'double_counter',
    'Start game': 'start_game',
}
_WHIST_ACTION_TO_CMD = {v: k for k, v in _WHIST_CMD_MAP.items()}


def _sm_cmd_to_whist_action(cmd):
    """Convert SM whisting command to action dict for strategy classes."""
    return {"action": _WHIST_CMD_MAP.get(cmd, cmd.lower())}


app = Flask(__name__, static_folder=WEB_DIR, static_url_path='')
CORS(app)


@app.errorhandler(500)
def handle_500(e):
    return jsonify({'error': f'Internal server error: {e}'}), 500


@app.errorhandler(Exception)
def handle_exception(e):
    return jsonify({'error': str(e)}), 500


# Engine service URL (unified)
ENGINE_URL = os.environ.get('ENGINE_URL', 'http://localhost:3001')

# Single game session
session = None

# Agent service URL (independent process)
AGENT_URL = os.environ.get('AGENT_URL', 'http://localhost:3002')


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


@app.route('/debug')
def debug_page():
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
    """Return active players in clockwise order starting from lead.

    Engine rotation: 1→2→3→1 (clockwise).
    """
    order = []
    for i in range(3):
        p = ((lead - 1 + i) % 3) + 1
        if p in active:
            order.append(p)
    return order


def _safe_json(r):
    """Parse JSON from response, returning error dict on failure."""
    try:
        return r.json()
    except Exception:
        return {'error': r.text or 'Unknown engine error'}


def _engine_commands():
    """Get current state from unified engine service."""
    try:
        r = http.get(f'{ENGINE_URL}/commands',
                      params={'game_id': session['game_id']})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise RuntimeError(f'Engine service error (commands): {e}')


def _engine_execute(cmd_idx):
    """Execute a command on the unified engine service."""
    try:
        r = http.post(f'{ENGINE_URL}/execute', json={
            'game_id': session['game_id'],
            'command_id': cmd_idx,
        })
        return r
    except Exception as e:
        raise RuntimeError(f'Engine service error (execute): {e}')


def _fetch_scoring():
    """Fetch scoring results from engine and store in session."""
    cmds = _engine_commands()
    scoring = cmds.get('scoring')
    if scoring:
        session['scoring_data'] = scoring


AI_NAMES = ['Sim10-Alice', 'Sim50-Alice']


@app.route('/api/game/new', methods=['POST'])
def new_game():
    global session

    data = request.get_json() or {}
    debug = bool(data.get('debug'))

    r = http.post(f'{ENGINE_URL}/new-game', json={}).json()

    ai_names = random.sample(AI_NAMES, 2)

    # Randomise human position (1, 2, or 3)
    positions = [1, 2, 3]
    random.shuffle(positions)
    human = positions[0]
    players = {human: 'You', positions[1]: ai_names[0], positions[2]: ai_names[1]}

    # Instantiate AI strategies
    strategies = {}
    for pos, name in players.items():
        if pos == human:
            continue
        if name == 'Sim10-Alice':
            strategies[pos] = Sim3000(name, num_simulations=10, helper_cls=PlayerAlice)
        elif name == 'Sim50-Alice':
            strategies[pos] = Sim3000(name, num_simulations=50, helper_cls=PlayerAlice)
        elif name == 'Neural':
            profile_name, aggr = random.choice(NEURAL_PROFILES)
            players[pos] = profile_name
            strategies[pos] = NeuralPlayer(profile_name, aggressiveness=aggr)
        elif name in PLAYER_CLASSES:
            strategies[pos] = PLAYER_CLASSES[name]()

    import copy
    session = {
        'game_id': r['game_id'],
        'phase': 'auction',
        'hands': r['hands'],
        'talon': r['talon'],
        'initial_hands': copy.deepcopy(r['hands']),
        'initial_talon': list(r['talon']),
        'declarer': None,
        'followers': [],
        'contract': None,
        'trick_lead': 1,
        'trick_cards': [],
        'tricks_played': 0,
        'played_cards_history': [],
        'players': players,
        'human': human,
        'discarded': [],
        'debug': debug,
        'action_log': [],
        'strategies': strategies,
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

        strat = session.get('strategies', {}).get(player)
        cmd_idx = None

        if strat and phase == 'auction':
            legal_bids = [_sm_cmd_to_bid(c) for c in commands]
            strat._hand = [_Card.from_id(cid) for cid in session['hands'][str(player)]]
            chosen_bid = strat.choose_bid(legal_bids)
            cmd_idx = next((i + 1 for i, b in enumerate(legal_bids)
                            if b['label'] == chosen_bid['label']), None)

        elif strat and phase == 'whisting':
            legal_actions = [_sm_cmd_to_whist_action(c) for c in commands]
            strat._hand = [_Card.from_id(cid) for cid in session['hands'][str(player)]]
            contract = session.get('contract')
            if contract:
                strat._contract_type = contract.get('type')
                trump_name = contract.get('trump')
                strat._trump_suit = _NAME_TO_SUIT.get(trump_name) if trump_name else None
            chosen_action = strat.choose_whist_action(legal_actions)
            target_cmd = _WHIST_ACTION_TO_CMD.get(chosen_action, chosen_action)
            cmd_idx = next((i + 1 for i, c in enumerate(commands)
                            if c == target_cmd), None)

        elif strat and phase == 'exchanging':
            # Suit choice: commands are "Spades", "Diamonds", etc.
            suit_map = {'Spades': 'spades', 'Diamonds': 'diamonds',
                        'Hearts': 'hearts', 'Clubs': 'clubs'}
            hand = [_Card.from_id(cid) for cid in session['hands'][str(player)]]
            legal_levels = [SUIT_TO_LEVEL[suit_map[c]] for c in commands if c in suit_map]
            if not legal_levels:
                legal_levels = [2, 3, 4, 5]
            winner_bid = _BidStub('game', min(legal_levels))
            ctype, trump, level = strat.choose_contract(legal_levels, hand, winner_bid)
            if ctype == 'betl':
                target_cmd = 'Betl'
            elif ctype == 'sans':
                target_cmd = 'Sans'
            elif trump:
                trump_to_cmd = {v: k for k, v in suit_map.items()}
                target_cmd = trump_to_cmd.get(trump)
            else:
                target_cmd = None
            if target_cmd:
                cmd_idx = next((i + 1 for i, c in enumerate(commands)
                                if c == target_cmd), None)

        # Fallback to random if strategy didn't produce a valid index
        if cmd_idx is None:
            cmd_idx = random.randint(1, len(commands))

        chosen = commands[cmd_idx - 1]
        session['_ai_action'] = {'player': player, 'command': chosen}
        return _exec_bidding_phase({'command_id': cmd_idx})

    elif phase == 'playing':
        active = session.get('active', [1, 2, 3])
        order = _turn_order(active, session['trick_lead'])
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
        strat = session.get('strategies', {}).get(current)
        if strat:
            legal_objs = [_Card.from_id(cid) for cid in legal]
            strat._hand = [_Card.from_id(cid) for cid in session['hands'][str(current)]]
            strat._rnd = None
            strat._player_id = current
            contract = session.get('contract')
            trump_val = None
            contract_type = None
            if contract:
                contract_type = contract.get('type')
                strat._contract_type = contract_type
                trump_name = contract.get('trump')
                trump_val = _NAME_TO_SUIT.get(trump_name) if trump_name else None
                strat._trump_suit_val = trump_val
            strat._is_declarer = (current == session.get('declarer'))

            # Build CardPlayContext from session data
            trick_cards_tuples = [
                (tc['player'], _Card.from_id(tc['card']))
                for tc in session.get('trick_cards', [])
            ]
            played_card_objs = [
                _Card.from_id(cid)
                for cid in session.get('played_cards_history', [])
            ]
            ctx = CardPlayContext(
                trick_cards=trick_cards_tuples,
                declarer_id=session.get('declarer', 1),
                my_id=current,
                active_players=order,
                played_cards=played_card_objs,
                trump_suit=trump_val,
                contract_type=contract_type or 'suit',
                is_declarer=(current == session.get('declarer')),
                tricks_played=session.get('tricks_played', 0),
                my_hand=strat._hand,
            )
            strat._ctx = ctx
            # Pass tricks-per-player for Sim3000 (no _rnd in web context)
            tpp = session.get('tricks_per_player', {})
            strat._tricks_per_player = {int(k): v for k, v in tpp.items()}
            card = strat.choose_card(legal_objs)
        else:
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

    # Log the action
    player_pos = cmds.get('player_position')
    if cmd_idx and 1 <= cmd_idx <= len(commands):
        action_label = commands[cmd_idx - 1]
    else:
        action_label = f'cmd#{cmd_idx}'
    display_label = 'Not follow' if session['phase'] == 'whisting' and action_label == 'Pass' else action_label
    session.setdefault('action_log', []).append({
        'player': player_pos,
        'action': display_label,
        'phase': session['phase'],
    })

    r = _engine_execute(cmd_idx)
    if not r.ok:
        return jsonify(_safe_json(r)), r.status_code

    _sync_phase()
    return jsonify(_build_state())


def _sync_phase():
    """Read engine service state and update session phase accordingly."""
    cmds = _engine_commands()
    engine_phase = cmds.get('phase', '')
    ctx = cmds.get('context')
    commands = cmds.get('commands', [])

    # In-hand undeclared suit selection: SM reports phase='playing' but commands
    # are suit names (Spades/Diamonds/etc), not actual card play. Treat as
    # exchanging (suit choice) in the web server.
    suit_names = {'Spades', 'Diamonds', 'Hearts', 'Clubs'}
    if engine_phase == 'playing' and commands and set(commands) <= suit_names:
        session['phase'] = 'exchanging'
        session['declarer'] = cmds.get('player_position')
        return

    if engine_phase in ('scoring', 'playing'):
        # Engine done with pre-play phases — extract result from context
        if not ctx or ctx[0] is None:
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
                # Suit contract — trump from exchange phase or engine (in-hand)
                trump = session.get('chosen_trump') or cmds.get('trump')
                level = SUIT_TO_LEVEL.get(trump, 2)
                session['contract'] = {'type': 'suit', 'trump': trump, 'level': level}

            # If no followers, declarer wins without playing
            if not followers:
                session['phase'] = 'scoring'
                session['scoring_reason'] = 'no_followers'
                _fetch_scoring()
                return

            session['active'] = [declarer] + followers
            session['whist_actions'] = {str(int(o[0])): o[1] for o in others}
            session['tricks_per_player'] = {str(p): 0 for p in [1, 2, 3]}
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

            # Save talon for display
            gid = session['game_id']
            talon_r = http.get(f'{ENGINE_URL}/talon', params={'game_id': gid})
            session['revealed_talon'] = talon_r.json().get('cards', []) if talon_r.ok else []

            # If AI is declarer, auto-exchange immediately
            human = session.get('human', 1)
            if declarer != human:
                session['phase'] = 'exchange_cards'
                _ai_exchange()
            else:
                session['phase'] = 'exchange_cards'
        else:
            # Suit/contract choice — stays with engine service
            session['declarer'] = cmds.get('declarer_position') or cmds.get('player_position')
            session['phase'] = 'exchanging'

    elif engine_phase == 'whisting':
        # Set declarer from declarer_position (context is not set for whisting)
        dp = cmds.get('declarer_position')
        if dp is not None:
            session['declarer'] = dp
        # For in-hand suit bids, chosen_trump was never set — grab from engine
        if cmds.get('trump') and not session.get('chosen_trump'):
            session['chosen_trump'] = cmds['trump']
        session['phase'] = 'whisting'

    elif engine_phase == 'redeal':
        # All players passed — treat as all-pass scoring
        session['phase'] = 'scoring'
        session['scoring_reason'] = 'all_pass'

    else:
        session['phase'] = engine_phase


def _exec_exchange_cards(data):
    """Player discards 2 cards via SM execute flow."""
    cards = data.get('cards', [])
    if len(cards) != 2:
        return jsonify({'error': 'Must discard exactly 2 cards'}), 400

    gid = session['game_id']
    decl = session['declarer']

    # Get current available cards from engine (hand + talon, as indices)
    cmds1 = _engine_commands()
    commands1 = cmds1.get('commands', [])

    # Get actual card list from engine to map card IDs to indices
    hand_r = http.get(f'{ENGINE_URL}/hand', params={'game_id': gid, 'player': decl})
    talon_r = http.get(f'{ENGINE_URL}/talon', params={'game_id': gid})
    hand_cards = hand_r.json().get('cards', []) if hand_r.ok else []
    talon_cards = talon_r.json().get('cards', []) if talon_r.ok else []
    all_cards = hand_cards + talon_cards

    # First discard: find index of first card
    try:
        idx1 = all_cards.index(cards[0]) + 1
    except ValueError:
        return jsonify({'error': f'Card {cards[0]} not found'}), 400
    _engine_execute(idx1)

    # Second discard: get updated available list and find index
    cmds2 = _engine_commands()
    commands2 = cmds2.get('commands', [])
    remaining = [c for c in all_cards if c != cards[0]]
    try:
        idx2 = remaining.index(cards[1]) + 1
    except ValueError:
        return jsonify({'error': f'Card {cards[1]} not found'}), 400
    _engine_execute(idx2)

    # Update session
    r = http.get(f'{ENGINE_URL}/hand', params={'game_id': gid, 'player': decl})
    if r.ok:
        session['hands'][str(decl)] = r.json().get('cards', [])
    session['talon'] = []
    session['discarded'] = cards

    # Sync to next phase (contract selection)
    _sync_phase()

    return jsonify(_build_state())


def _ai_exchange():
    """AI declarer auto-discards 2 random cards via SM execute flow."""
    gid = session['game_id']
    decl = session['declarer']

    # Capture talon and hand before exchange for UI display
    talon_r = http.get(f'{ENGINE_URL}/talon', params={'game_id': gid})
    talon_cards = talon_r.json().get('cards', []) if talon_r.ok else []
    hand_r = http.get(f'{ENGINE_URL}/hand', params={'game_id': gid, 'player': decl})
    hand_before = hand_r.json().get('cards', []) if hand_r.ok else []
    all_before = set(hand_before + talon_cards)

    strat = session.get('strategies', {}).get(decl)
    if strat:
        # Use strategy to choose discards
        strat._winner_bid = _BidStub('game', 2)
        discard_ids = strat.choose_discard(hand_before, talon_cards)
        all_cards = hand_before + talon_cards

        # First discard
        try:
            idx1 = all_cards.index(discard_ids[0]) + 1
        except (ValueError, IndexError):
            idx1 = 1
        _engine_execute(idx1)

        # Second discard (re-index after first removal)
        remaining = [c for c in all_cards if c != discard_ids[0]]
        try:
            idx2 = remaining.index(discard_ids[1]) + 1
        except (ValueError, IndexError):
            idx2 = 1
        _engine_execute(idx2)
    else:
        # Fallback to random
        cmds1 = _engine_commands()
        commands1 = cmds1.get('commands', [])
        if not commands1:
            return
        idx1 = random.randint(1, len(commands1))
        _engine_execute(idx1)

        cmds2 = _engine_commands()
        commands2 = cmds2.get('commands', [])
        if not commands2:
            return
        idx2 = random.randint(1, len(commands2))
        _engine_execute(idx2)

    # Update session hands from engine
    r = http.get(f'{ENGINE_URL}/hand', params={'game_id': gid, 'player': decl})
    hand_after = r.json().get('cards', []) if r.ok else []
    session['hands'][str(decl)] = hand_after

    # Derive discarded cards and save for UI
    discarded = list(all_before - set(hand_after))
    session['discarded'] = discarded
    session['revealed_talon'] = talon_cards
    session['talon'] = []

    # Sync to next phase (contract selection)
    _sync_phase()


def _exec_play(data):
    card = data.get('card')
    player = data.get('player')

    session.setdefault('action_log', []).append({
        'player': player,
        'action': f'plays {card}',
        'phase': 'playing',
    })

    r = http.post(f'{ENGINE_URL}/play-card', json={
        'game_id': session['game_id'],
        'player': player,
        'card': card,
    })
    if not r.ok:
        return jsonify(_safe_json(r)), r.status_code

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
        tpp = session.setdefault('tricks_per_player', {str(p): 0 for p in [1, 2, 3]})
        tpp[str(winner)] = tpp.get(str(winner), 0) + 1
        session['trick_lead'] = winner
        # Track played cards for CardPlayContext
        for tc in session['trick_cards']:
            session.setdefault('played_cards_history', []).append(tc['card'])
        session['last_trick'] = session['trick_cards']
        session['last_trick_winner'] = winner
        session['trick_cards'] = []

        # Round ends at 10 tricks, or early: betl declarer wins a trick,
        # or followers combined win 5 tricks
        round_over = resp.get('round_complete', False)
        if round_over or session['tricks_played'] >= 10:
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
        'last_trick_winner': s.get('last_trick_winner'),
        'players': players,
        'human': human,
        'discarded': s.get('discarded', []) if s['phase'] in ('exchanging', 'whisting', 'playing', 'scoring') else [],
        'revealed_talon': s.get('revealed_talon', []),
        'ai_action': s.pop('_ai_action', None),
        'debug': debug,
        'tricks_per_player': s.get('tricks_per_player', {str(p): 0 for p in [1, 2, 3]}),
        'whist_actions': s.get('whist_actions', {}),
        'dealer': 3,  # position 3 is always dealer (dealer_index=2)
        'played_cards': s.get('played_cards_history', []),
        'trick_lead': s.get('trick_lead'),
    }

    # Last action per player (most recent from action_log)
    last_actions = {}
    for entry in s.get('action_log', []):
        p = entry.get('player')
        if p is not None:
            last_actions[str(p)] = entry['action']
    state['last_actions'] = last_actions

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

        # Compute discard scores for the human player's 12 cards
        if decl == human:
            all_ids = s['hands'][str(decl)] + s['talon']
            suit_scores = BasePlayer.score_discard_cards(all_ids, 'suit')
            betl_scores = BasePlayer.score_discard_cards(all_ids, 'betl')
            state['discard_scores'] = {'suit': suit_scores, 'betl': betl_scores}

    elif s['phase'] == 'playing':
        active = s.get('active', [1, 2, 3])
        order = _turn_order(active, s['trick_lead'])
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
        state['initial_hands'] = s.get('initial_hands', {})
        state['initial_talon'] = s.get('initial_talon', [])

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


# ── Agent endpoints (proxy to agent_service on port 3002) ─────────────

def _build_agent_prompt(question):
    """Build the prompt string with game context for the agent service."""
    parts = []

    if not session:
        return (f"No active game is running.\n\n"
                f"User asked: {question}\n\n"
                f"Tell the user to start a game first.")

    s = session
    players = s.get('players', {})

    parts.append("=== CURRENT GAME STATE ===")
    parts.append(f"Phase: {s['phase']}")
    parts.append(f"Players: {', '.join(f'P{k}={v}' for k, v in players.items())}")
    parts.append(f"Human player: P{s.get('human', 1)}")

    if s.get('contract'):
        c = s['contract']
        parts.append(f"Contract: type={c.get('type')} trump={c.get('trump')} "
                      f"level={c.get('level')}")
    if s.get('declarer'):
        parts.append(f"Declarer: P{s['declarer']} "
                      f"({players.get(str(s['declarer']), '?')})")
    if s.get('followers'):
        fnames = [f"P{f}({players.get(str(f), '?')})" for f in s['followers']]
        parts.append(f"Followers: {', '.join(fnames)}")

    parts.append("\n=== HANDS ===")
    for p in [1, 2, 3]:
        h = s['hands'].get(str(p), [])
        pname = players.get(str(p), f'P{p}')
        if isinstance(h, list):
            parts.append(f"P{p} ({pname}): {', '.join(h)}")
        else:
            parts.append(f"P{p} ({pname}): {h} cards (hidden)")

    if s.get('tricks_per_player'):
        parts.append(f"\nTricks per player: {s['tricks_per_player']}")
    parts.append(f"Tricks played: {s.get('tricks_played', 0)}")

    action_log = s.get('action_log', [])
    if action_log:
        parts.append("\n=== GAME LOG (recent actions) ===")
        for entry in action_log[-30:]:
            pname = players.get(str(entry.get('player')), f"P{entry.get('player')}")
            parts.append(f"  {pname}: {entry['action']} ({entry['phase']})")

    context = '\n'.join(parts)

    return f"""You are an expert Preferans (Russian card game) analyst embedded in a game UI.
The user is watching a Preferans game and wants your help understanding decisions or improving player strategies.

{context}

=== CODEBASE CONTEXT ===
- AI player strategies are in PrefTestSingleGame.py (PlayerAlice=aggressive, PlayerBob=cautious, PlayerCarol=pragmatic)
- Each player has: bid_intent(), choose_card(), choose_discard(), choose_contract(), choose_whist_action(), decide_to_call(), decide_to_counter()
- Each decision returns an "intent" string explaining the reasoning
- Game engine is in server/engine.py, models in server/models.py
- Card format: rank_suit (e.g. A_spades, 10_hearts, J_diamonds)
- Suits ranked: spades(4) > diamonds(3) > clubs(2) > hearts(1) but in Preferans bidding: spades < diamonds < hearts < clubs
- Ranks: 7,8,9,10,J,Q,K,A (A=8 is highest)

=== USER QUESTION ===
{question}

Provide a clear, concise answer. If the user asks about a specific decision, reference the relevant strategy code and explain the logic. If they suggest an improvement, explain whether it makes sense and what the trade-offs would be. If they ask you to fix or improve code, do so and summarize what you changed."""


@app.route('/api/agent/ask', methods=['POST'])
def agent_ask():
    data = request.get_json() or {}
    question = data.get('question', '').strip()
    if not question:
        return jsonify({'error': 'No question provided'}), 400

    prompt = _build_agent_prompt(question)
    try:
        r = http.post(f'{AGENT_URL}/ask',
                       json={'prompt': prompt, 'question': question}, timeout=5)
        return jsonify(_safe_json(r)), r.status_code
    except Exception as e:
        return jsonify({'error': f'Agent service unavailable: {e}'}), 503


@app.route('/api/agent/status')
def agent_status():
    try:
        r = http.get(f'{AGENT_URL}/status', timeout=5)
        return jsonify(_safe_json(r)), r.status_code
    except Exception as e:
        return jsonify({'error': f'Agent service unavailable: {e}'}), 503


if __name__ == '__main__':
    import os
    debug_mode = os.environ.get('FLASK_DEBUG', '1') == '1'
    port = int(os.environ.get('FLASK_PORT', '3000'))
    app.run(debug=debug_mode, host='127.0.0.1', port=port)
