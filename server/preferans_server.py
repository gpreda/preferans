from flask import Flask, send_from_directory, jsonify, Response, request
from flask_cors import CORS
import os
import random
import logging
import time
from datetime import datetime
import requests as http

# Get absolute path to web folder
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(BASE_DIR, 'web')

# Import player strategy classes
import sys as _sys
_sys.path.insert(0, BASE_DIR)
from PrefTestSingleGame import PlayerAlice, PlayerBob, PlayerCarol, NeuralPlayer, Sim3000, make_simsim_cls, CardPlayContext, BasePlayer, Trojka
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

# Game sessions: {game_id: session_dict}
sessions = {}
MAX_SESSIONS = 100
SESSION_TIMEOUT = 3600  # 1 hour


def _get_session(game_id=None):
    """Resolve session from explicit game_id, request param, or request body."""
    if game_id is None:
        game_id = request.args.get('game_id')
    if game_id is None:
        data = request.get_json(silent=True) or {}
        game_id = data.get('game_id')
    if game_id and game_id in sessions:
        return sessions[game_id]
    return None


def _cleanup_sessions():
    """Remove old sessions if too many exist."""
    if len(sessions) <= MAX_SESSIONS:
        return
    now = time.time()
    expired = [gid for gid, s in sessions.items()
               if now - s.get('_created_at', 0) > SESSION_TIMEOUT]
    for gid in expired:
        del sessions[gid]

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


def _engine_commands(session):
    """Get current state from unified engine service."""
    try:
        r = http.get(f'{ENGINE_URL}/commands',
                      params={'game_id': session['game_id']})
        r.raise_for_status()
        return r.json()
    except Exception as e:
        raise RuntimeError(f'Engine service error (commands): {e}')


def _engine_execute(session, cmd_idx):
    """Execute a command on the unified engine service."""
    try:
        r = http.post(f'{ENGINE_URL}/execute', json={
            'game_id': session['game_id'],
            'command_id': cmd_idx,
        })
        return r
    except Exception as e:
        raise RuntimeError(f'Engine service error (execute): {e}')


def _fetch_scoring(session):
    """Fetch scoring results from engine and store in session."""
    cmds = _engine_commands(session)
    scoring = cmds.get('scoring')
    if scoring:
        session['scoring_data'] = scoring


AI_NAMES = ['S50S20a-A', 'S20S10a-A', 'Alice', 'Sim50-Alice', 'Sim10-Alice']

# ---------------------------------------------------------------------------
# Game logging
# ---------------------------------------------------------------------------
GAME_LOG_DIR = os.path.join(BASE_DIR, 'logs', 'games')
os.makedirs(GAME_LOG_DIR, exist_ok=True)

_game_logger = logging.getLogger('game_log')
_game_logger.setLevel(logging.INFO)
_game_log_handler = logging.FileHandler(
    os.path.join(GAME_LOG_DIR, 'games.log'), encoding='utf-8')
_game_log_handler.setFormatter(logging.Formatter('%(message)s'))
_game_logger.addHandler(_game_log_handler)
_game_logger.propagate = False


BENCHMARK_FILE = os.path.join(BASE_DIR, 'games_benchmark.txt')


def _append_benchmark(session):
    """Append game data to benchmark file for offline re-evaluation."""
    import json
    s = session
    if not s:
        return
    reason = s.get('scoring_reason')
    if reason == 'all_pass':
        return  # skip redeals — no meaningful game to replay

    initial_hands = s.get('initial_hands', {})
    initial_talon = s.get('initial_talon', [])
    contract = s.get('contract')
    declarer = s.get('declarer')
    followers = s.get('followers', [])
    whist_actions = s.get('whist_actions', {})
    discarded = s.get('discarded', [])
    scoring = s.get('scoring_data')
    tricks_per_player = s.get('tricks_per_player', {})

    record = {
        'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'hands': initial_hands,
        'talon': initial_talon,
        'declarer': declarer,
        'contract': contract,
        'followers': followers,
        'whist_actions': whist_actions,
        'discarded': discarded,
        'tricks_per_player': tricks_per_player,
        'scoring': scoring,
        'no_followers': reason == 'no_followers',
    }

    try:
        with open(BENCHMARK_FILE, 'a') as f:
            f.write(json.dumps(record) + '\n')
    except Exception as e:
        logging.getLogger(__name__).error(f'Failed to write benchmark: {e}')


def _write_game_log(session):
    """Write a concise game summary to the log file."""
    s = session
    if not s:
        return
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    players = s.get('players', {})
    lines = [
        f'=== Game {s.get("game_id", "?")} | {ts} ===',
        f'Players: {", ".join(f"P{p} {n}" for p, n in sorted(players.items()))}',
        f'Human: P{s.get("human", "?")}',
    ]

    # Initial hands
    for p in sorted(players):
        h = s.get('initial_hands', {}).get(str(p), [])
        lines.append(f'P{p} hand: {" ".join(h)}')
    talon = s.get('initial_talon', [])
    if talon:
        lines.append(f'Talon: {" ".join(talon)}')

    # Contract
    contract = s.get('contract')
    declarer = s.get('declarer')
    reason = s.get('scoring_reason')
    if reason == 'all_pass':
        lines.append('Result: All pass (redeal)')
    elif reason == 'no_followers':
        lines.append(f'Declarer: P{declarer} | Contract: {contract} | No followers')
    elif contract:
        ctype = contract.get('type', '?')
        trump = contract.get('trump', '')
        level = contract.get('level', '')
        lines.append(f'Declarer: P{declarer} | Contract: {ctype} {trump} level={level}')
        followers = s.get('followers', [])
        whist_actions = s.get('whist_actions', {})
        lines.append(f'Followers: {followers} | Whist: {whist_actions}')
        discarded = s.get('discarded', [])
        if discarded:
            lines.append(f'Discarded: {" ".join(discarded)}')

    # Tricks from action_log
    action_log = s.get('action_log', [])
    trick_num = 0
    trick_plays = []
    for entry in action_log:
        if entry.get('phase') != 'playing':
            continue
        p = entry.get('player')
        action = entry.get('action', '')
        trick_plays.append(f'P{p} {action}')
        # Check if trick is complete (2 or 3 players depending on active)
        active_count = len(s.get('active', [1, 2, 3]))
        if len(trick_plays) >= active_count:
            trick_num += 1
            lines.append(f'  Trick {trick_num}: {" | ".join(trick_plays)}')
            trick_plays = []
    if trick_plays:
        trick_num += 1
        lines.append(f'  Trick {trick_num}: {" | ".join(trick_plays)} (incomplete)')

    # Scoring
    scoring = s.get('scoring_data')
    tpp = s.get('tricks_per_player', {})
    if tpp:
        lines.append(f'Tricks: {", ".join(f"P{p}={t}" for p, t in sorted(tpp.items()))}')
    if scoring:
        lines.append(f'Scoring: {scoring}')

    lines.append('')  # blank line separator
    _game_logger.info('\n'.join(lines))


@app.route('/api/game/new', methods=['POST'])
def new_game():
    _cleanup_sessions()

    data = request.get_json() or {}
    debug = bool(data.get('debug'))
    picked = data.get('players')  # e.g. ['Sim50T', 'Alice', 'Human']

    r = http.post(f'{ENGINE_URL}/new-game', json={}).json()

    # Build player map from client selection (or fall back to random)
    if picked and len(picked) == 3:
        # Shuffle positions randomly
        positions = [1, 2, 3]
        random.shuffle(positions)
        players = {}
        human = None
        for i, name in enumerate(picked):
            pos = positions[i]
            if name == 'Human':
                players[pos] = 'You'
                human = pos
            else:
                players[pos] = name
        if human is None:
            human = 0  # all-AI game
    else:
        ai_names = random.sample(AI_NAMES, 2)
        positions = [1, 2, 3]
        random.shuffle(positions)
        human = positions[0]
        players = {human: 'You', positions[1]: ai_names[0], positions[2]: ai_names[1]}

    # Instantiate AI strategies
    strategies = {}
    for pos, name in players.items():
        if name == 'You':
            continue
        if name == 'S50S20a-A':
            helper_cls = make_simsim_cls(num_simulations=20, helper_cls=PlayerAlice, adaptive=True)
            strategies[pos] = Sim3000(name, num_simulations=50, helper_cls=helper_cls, adaptive=True)
        elif name == 'S20S10a-A':
            helper_cls = make_simsim_cls(num_simulations=10, helper_cls=PlayerAlice, adaptive=True)
            strategies[pos] = Sim3000(name, num_simulations=20, helper_cls=helper_cls, adaptive=True)
        elif name == 'Alice':
            strategies[pos] = PlayerAlice(name=name)
        elif name == 'Trojka':
            strategies[pos] = Trojka(name=name)
        elif name == 'Sim10-Alice':
            strategies[pos] = Sim3000(name, num_simulations=10, helper_cls=PlayerAlice)
        elif name == 'Sim50-Alice':
            strategies[pos] = Sim3000(name, num_simulations=50, helper_cls=PlayerAlice)
        elif name == 'Sim50T':
            strategies[pos] = Sim3000(name, num_simulations=50, helper_cls=Trojka)
        elif name == 'Neural':
            profile_name, aggr = random.choice(NEURAL_PROFILES)
            players[pos] = profile_name
            strategies[pos] = NeuralPlayer(profile_name, aggressiveness=aggr)
        elif name.startswith('Bot:'):
            from bot_db import get_bot, get_bot_functions
            bot_id = int(name.split(':')[1])
            bot = get_bot(bot_id)
            if bot:
                funcs = get_bot_functions(bot_id)
                func_code = {f['function_name']: f['code'] for f in funcs}
                from prefbot import PrefBot
                strategies[pos] = PrefBot(bot['name'], func_code)
                players[pos] = bot['name']
        elif name in PLAYER_CLASSES:
            strategies[pos] = PLAYER_CLASSES[name]()

    import copy
    game_id = r['game_id']
    session = {
        'game_id': game_id,
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
        'played_tricks': [],  # list of completed tricks: [[(player, card_id), ...], ...]
        'players': players,
        'human': human,
        'discarded': [],
        'debug': debug,
        'action_log': [],
        'strategies': strategies,
        '_created_at': time.time(),
    }
    sessions[game_id] = session

    return jsonify(_build_state(session))


@app.route('/api/game/ai-move', methods=['POST'])
def ai_move():
    """AI auto-plays: pick a random valid command/card."""
    session = _get_session()
    if not session:
        return jsonify({'error': 'No active game'}), 400

    phase = session['phase']
    human = session.get('human', 1)

    if phase in ('auction', 'exchanging', 'whisting'):
        cmds = _engine_commands(session)
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
        return _exec_bidding_phase(session, {'command_id': cmd_idx})

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
            # Talon and remaining cards
            is_in_hand = contract.get('is_in_hand', False) if contract else False
            talon_cards = [] if is_in_hand else [
                _Card.from_id(cid) for cid in session.get('initial_talon', [])
            ]
            # Remaining cards: all active hands minus my hand, minus played, minus trick
            played_ids = set(c.id for c in played_card_objs)
            trick_card_ids = set(c.id for _, c in trick_cards_tuples)
            my_ids = set(c.id for c in strat._hand)
            remaining = []
            for pos_str, hand_ids in session.get('hands', {}).items():
                for cid in hand_ids:
                    if cid not in played_ids and cid not in trick_card_ids and cid not in my_ids:
                        remaining.append(_Card.from_id(cid))
            # Build played_tricks for void tracking
            played_tricks_ctx = []
            for pt in session.get('played_tricks', []):
                played_tricks_ctx.append([
                    (p, _Card.from_id(c)) for p, c in pt
                ])
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
                talon_cards=talon_cards,
                is_in_hand=is_in_hand,
                remaining_cards=remaining,
                played_tricks=played_tricks_ctx,
            )
            strat._ctx = ctx
            # Pass tricks-per-player for Sim3000 (no _rnd in web context)
            tpp = session.get('tricks_per_player', {})
            strat._tricks_per_player = {int(k): v for k, v in tpp.items()}
            card = strat.choose_card(legal_objs)
        else:
            card = random.choice(legal)
        session['_ai_action'] = {'player': current, 'card': card}
        return _exec_play(session, {'player': current, 'card': card})

    return jsonify({'error': f'Cannot AI move in phase {phase}'}), 400


@app.route('/api/game/state')
def game_state():
    session = _get_session()
    if not session:
        return jsonify({'error': 'No active game'}), 400
    return jsonify(_build_state(session))


@app.route('/api/game/execute', methods=['POST'])
def game_execute():
    session = _get_session()
    if not session:
        return jsonify({'error': 'No active game'}), 400

    data = request.get_json() or {}
    phase = session['phase']

    # Pre-play phases all route through the bidding engine
    if phase in ('auction', 'exchanging', 'whisting'):
        return _exec_bidding_phase(session, data)
    elif phase == 'exchange_cards':
        return _exec_exchange_cards(session, data)
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
        return _exec_play(session, data)
    elif phase == 'scoring':
        return jsonify({'error': 'Game is over'}), 400
    else:
        return jsonify({'error': f'Unknown phase {phase}'}), 400


def _exec_bidding_phase(session, data):
    """Execute a command on the engine service, then sync phase."""
    cmd_idx = data.get('command_id')

    # Before executing, check if this is a suit choice — remember it
    cmds = _engine_commands(session)
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

    r = _engine_execute(session, cmd_idx)
    if not r.ok:
        return jsonify(_safe_json(r)), r.status_code

    _sync_phase(session)
    return jsonify(_build_state(session))


def _sync_phase(session):
    """Read engine service state and update session phase accordingly."""
    cmds = _engine_commands(session)
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
            _write_game_log(session)
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
            is_in_hand = cmds.get('is_in_hand', False)
            if ctype == 'betl':
                session['contract'] = {'type': 'betl', 'trump': None, 'level': 6, 'is_in_hand': is_in_hand}
            elif ctype == 'sans':
                session['contract'] = {'type': 'sans', 'trump': None, 'level': 7, 'is_in_hand': is_in_hand}
            else:
                # Suit contract — trump from exchange phase or engine (in-hand)
                trump = session.get('chosen_trump') or cmds.get('trump')
                level = cmds.get('bid_level') or SUIT_TO_LEVEL.get(trump, 2)
                session['contract'] = {'type': 'suit', 'trump': trump, 'level': level, 'is_in_hand': is_in_hand}

            # If no followers, declarer wins without playing
            if not followers:
                session['phase'] = 'scoring'
                session['scoring_reason'] = 'no_followers'
                _fetch_scoring(session)
                _write_game_log(session)
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
                _ai_exchange(session)
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
        _write_game_log(session)

    else:
        session['phase'] = engine_phase


def _exec_exchange_cards(session, data):
    """Player discards 2 cards via SM execute flow."""
    cards = data.get('cards', [])
    if len(cards) != 2:
        return jsonify({'error': 'Must discard exactly 2 cards'}), 400

    gid = session['game_id']
    decl = session['declarer']

    # Get current available cards from engine (hand + talon, as indices)
    cmds1 = _engine_commands(session)
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
    _engine_execute(session, idx1)

    # Second discard: get updated available list and find index
    cmds2 = _engine_commands(session)
    commands2 = cmds2.get('commands', [])
    remaining = [c for c in all_cards if c != cards[0]]
    try:
        idx2 = remaining.index(cards[1]) + 1
    except ValueError:
        return jsonify({'error': f'Card {cards[1]} not found'}), 400
    _engine_execute(session, idx2)

    # Update session
    r = http.get(f'{ENGINE_URL}/hand', params={'game_id': gid, 'player': decl})
    if r.ok:
        session['hands'][str(decl)] = r.json().get('cards', [])
    session['talon'] = []
    session['discarded'] = cards

    # Sync to next phase (contract selection)
    _sync_phase(session)

    return jsonify(_build_state(session))


def _ai_exchange(session):
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
        _engine_execute(session, idx1)

        # Second discard (re-index after first removal)
        remaining = [c for c in all_cards if c != discard_ids[0]]
        try:
            idx2 = remaining.index(discard_ids[1]) + 1
        except (ValueError, IndexError):
            idx2 = 1
        _engine_execute(session, idx2)
    else:
        # Fallback to random
        cmds1 = _engine_commands(session)
        commands1 = cmds1.get('commands', [])
        if not commands1:
            return
        idx1 = random.randint(1, len(commands1))
        _engine_execute(session, idx1)

        cmds2 = _engine_commands(session)
        commands2 = cmds2.get('commands', [])
        if not commands2:
            return
        idx2 = random.randint(1, len(commands2))
        _engine_execute(session, idx2)

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
    _sync_phase(session)


def _exec_play(session, data):
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
        # Track played cards and completed trick for CardPlayContext
        session.setdefault('played_tricks', []).append(
            [(tc['player'], tc['card']) for tc in session['trick_cards']]
        )
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
            _fetch_scoring(session)
            _write_game_log(session)

    state = _build_state(session)
    state['play_result'] = result
    return jsonify(state)


def _build_state(session):
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
        cmds = _engine_commands(session)
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


# === Save Game to Benchmark ===

@app.route('/api/game/save-benchmark', methods=['POST'])
def save_benchmark():
    """Save current game to benchmark file for offline re-evaluation."""
    import json as _json
    s = _get_session()
    if not s or s.get('phase') != 'scoring':
        return jsonify({'error': 'No completed game to save'}), 400

    reason = s.get('scoring_reason')
    if reason == 'all_pass':
        return jsonify({'error': 'All-pass games cannot be benchmarked'}), 400

    record = {
        'ts': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'hands': s.get('initial_hands', {}),
        'talon': s.get('initial_talon', []),
        'declarer': s.get('declarer'),
        'contract': s.get('contract'),
        'followers': s.get('followers', []),
        'whist_actions': s.get('whist_actions', {}),
        'discarded': s.get('discarded', []),
        'tricks_per_player': s.get('tricks_per_player', {}),
        'scoring': s.get('scoring_data'),
        'no_followers': reason == 'no_followers',
    }

    try:
        with open(BENCHMARK_FILE, 'a') as f:
            f.write(_json.dumps(record) + '\n')
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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

def _build_agent_prompt(session, question):
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

    session = _get_session(data.get('game_id'))
    prompt = _build_agent_prompt(session, question)
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


# ── Hand probability ──────────────────────────────────────────────────

_CANONICAL_SUITS = ['spades', 'diamonds', 'hearts', 'clubs']

def _cards_to_canonical(card_ids):
    """Convert card IDs to canonical encoding + suit mapping.

    Returns (encoding, suit_map) where suit_map maps real suit -> canonical suit.
    """
    RANK_CH = {'7': 'x', '8': 'x', '9': 'x', '10': 'x',
               'J': 'J', 'Q': 'D', 'K': 'K', 'A': 'A'}
    CARD_ORDER = {'A': 0, 'K': 1, 'D': 2, 'J': 3, 'x': 4}

    suits = {}
    for cid in card_ids:
        rank, suit = cid.split('_')
        suits.setdefault(suit, []).append(RANK_CH[rank])
    for s in suits:
        suits[s].sort(key=lambda c: CARD_ORDER[c])

    # Build (real_suit, pattern) pairs, sort canonically
    pairs = [(s, ''.join(suits[s])) for s in suits]
    # Add empty suits for suits not in hand
    all_suits = set(suits.keys())
    for s in ['spades', 'diamonds', 'hearts', 'clubs']:
        if s not in all_suits:
            pairs.append((s, ''))

    def sort_key(pair):
        pat = pair[1]
        return (-len(pat), [CARD_ORDER[c] for c in pat])
    pairs.sort(key=sort_key)

    # suit_map: real suit -> canonical suit
    suit_map = {}
    for i, (real_suit, _) in enumerate(pairs):
        suit_map[real_suit] = _CANONICAL_SUITS[i]

    encoding = '-'.join(pat for _, pat in pairs if pat)
    return encoding, suit_map


def _remap_card(card_id, suit_map):
    """Remap a card ID to canonical suit."""
    rank, suit = card_id.split('_')
    return rank + '_' + suit_map[suit]


@app.route('/api/hand-probability')
def hand_probability():
    cards_param = request.args.get('cards', '')
    if not cards_param:
        return jsonify({'error': 'cards parameter required'}), 400
    card_ids = [c.strip() for c in cards_param.split(',') if c.strip()]
    if len(card_ids) != 10:
        return jsonify({'error': f'expected 10 cards, got {len(card_ids)}'}), 400

    discarded_param = request.args.get('discarded', '')
    discard_ids = [c.strip() for c in discarded_param.split(',') if c.strip()] if discarded_param else []

    encoding, suit_map = _cards_to_canonical(card_ids)

    if discard_ids:
        from compute_probabilities import simulate_with_known_cards
        canonical_hand = [_remap_card(c, suit_map) for c in card_ids]
        canonical_discard = [_remap_card(c, suit_map) for c in discard_ids]
        seed = hash(tuple(sorted(canonical_hand + canonical_discard))) & 0x7FFFFFFF
        result = simulate_with_known_cards(canonical_hand, canonical_discard, seed=seed)
    else:
        from compute_probabilities import simulate_combination
        result = simulate_combination(encoding, seed=hash(encoding) & 0x7FFFFFFF)

    result['encoding'] = encoding
    return jsonify(result)


# ── Available Players ──────────────────────────────────────────────────

@app.route('/api/players', methods=['GET'])
def list_players():
    """List all available player types (built-in + user bots)."""
    from bot_db import get_all_bots
    builtin = ['Sim50T', 'Alice', 'Trojka', 'Sim50-Alice', 'Human']
    bots = get_all_bots()
    bot_entries = [{'name': f'Bot:{b["id"]}', 'label': b['name']} for b in bots]
    result = [{'name': n, 'label': n} for n in builtin] + bot_entries
    return jsonify(result)


# ── Bot Editor ─────────────────────────────────────────────────────────

@app.route('/bot')
def bot_editor():
    """Serve the bot editor page."""
    return send_from_directory(WEB_DIR, 'bot.html')


@app.route('/api/bots', methods=['GET'])
def list_bots():
    """List all bots."""
    from bot_db import get_all_bots
    bots = get_all_bots()
    return jsonify([dict(b) for b in bots])


@app.route('/api/bots', methods=['POST'])
def create_bot():
    """Create a new bot."""
    from bot_db import create_bot as db_create_bot
    data = request.get_json()
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    try:
        bot = db_create_bot(name)
        return jsonify(dict(bot)), 201
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/bots/<int:bot_id>', methods=['GET'])
def get_bot(bot_id):
    """Get a bot with all its functions."""
    from bot_db import get_bot as db_get_bot, get_bot_functions
    bot = db_get_bot(bot_id)
    if not bot:
        return jsonify({'error': 'Bot not found'}), 404
    result = dict(bot)
    funcs = get_bot_functions(bot_id)
    result['functions'] = {f['function_name']: f['code'] for f in funcs}
    return jsonify(result)


@app.route('/api/bots/<int:bot_id>', methods=['DELETE'])
def delete_bot_route(bot_id):
    """Delete a bot."""
    from bot_db import delete_bot as db_delete_bot
    if db_delete_bot(bot_id):
        return jsonify({'success': True})
    return jsonify({'error': 'Bot not found'}), 404


@app.route('/api/bots/<int:bot_id>', methods=['PATCH'])
def update_bot_route(bot_id):
    """Rename a bot."""
    from bot_db import rename_bot
    data = request.get_json()
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': 'Name is required'}), 400
    try:
        result = rename_bot(bot_id, name)
        if result:
            return jsonify(dict(result))
        return jsonify({'error': 'Bot not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/bots/<int:bot_id>/functions/<function_name>', methods=['PUT'])
def save_bot_function(bot_id, function_name):
    """Save a bot function's code."""
    from bot_db import upsert_bot_function, get_bot as db_get_bot
    valid_functions = ['choose_bid', 'choose_discard', 'choose_contract',
                       'choose_whist_action', 'choose_card']
    if function_name not in valid_functions:
        return jsonify({'error': f'Invalid function name. Must be one of: {valid_functions}'}), 400
    bot = db_get_bot(bot_id)
    if not bot:
        return jsonify({'error': 'Bot not found'}), 404
    data = request.get_json()
    code = data.get('code', '')
    # Validate syntax
    if code.strip():
        try:
            compile(code, f'<bot:{bot["name"]}:{function_name}>', 'exec')
        except SyntaxError as e:
            return jsonify({'error': f'Syntax error at line {e.lineno}: {e.msg}'}), 400
    result = upsert_bot_function(bot_id, function_name, code)
    return jsonify(dict(result))


# ── Bot Benchmark ──────────────────────────────────────────────────────

@app.route('/api/bots/<int:bot_id>/benchmark', methods=['POST'])
def benchmark_bot(bot_id):
    """Run a quick benchmark: Bot vs Alice vs Sim50T across all 6 permutations."""
    import json as _json
    import time as _time
    from itertools import permutations as _perms
    from bot_db import get_bot as db_get_bot, get_bot_functions
    from prefbot import PrefBot
    from game_engine_service import GameSession
    from models import Card as _Card, Contract, RoundPhase, ContractType, NAME_TO_SUIT as _NTS

    bot = db_get_bot(bot_id)
    if not bot:
        return jsonify({'error': 'Bot not found'}), 404

    funcs = get_bot_functions(bot_id)
    func_code = {f['function_name']: f['code'] for f in funcs}
    bot_name = bot['name']

    benchmark_file = os.path.join(BASE_DIR, 'games_benchmark.txt')
    if not os.path.exists(benchmark_file):
        return jsonify({'error': 'No benchmark file found'}), 404

    with open(benchmark_file) as f:
        records = [_json.loads(line) for line in f if line.strip()]

    if not records:
        return jsonify({'error': 'No games in benchmark file'}), 400

    player_types = [bot_name, 'Alice', 'Sim50T']
    all_perms = list(_perms(player_types, 3))

    def make_strat(ptype, name):
        if ptype == bot_name:
            return PrefBot(name, func_code)
        elif ptype == 'Alice':
            return PlayerAlice(name=name)
        elif ptype == 'Sim50T':
            return Sim3000(name, num_simulations=50, helper_cls=Trojka)
        raise ValueError(f"Unknown: {ptype}")

    type_scores = {pt: 0.0 for pt in player_types}
    type_games = {pt: 0 for pt in player_types}
    type_dcl_scores = {pt: 0.0 for pt in player_types}
    type_dcl_count = {pt: 0 for pt in player_types}
    type_def_scores = {pt: 0.0 for pt in player_types}
    type_def_count = {pt: 0 for pt in player_types}
    errors = []

    t_start = _time.time()

    for gi, rec in enumerate(records):
        dcl_pos = rec.get('declarer', 1)
        no_followers = rec.get('no_followers', False)

        for perm in all_perms:
            if no_followers:
                for pos in [1, 2, 3]:
                    ptype = perm[pos - 1]
                    type_games[ptype] += 1
                    if pos == dcl_pos:
                        type_dcl_count[ptype] += 1
                    else:
                        type_def_count[ptype] += 1
                continue

            try:
                # Replay logic (same as eval_benchmark.py)
                hands = rec['hands']
                talon = rec['talon']
                orig_contract = rec['contract']
                orig_declarer = dcl_pos
                orig_followers = rec.get('followers', [])
                orig_whist_actions = rec.get('whist_actions', {})
                orig_discarded = rec.get('discarded', [])
                is_in_hand = orig_contract.get('is_in_hand', False)

                import random as _rng
                _rng.seed(0)
                sess = GameSession(["P1", "P2", "P3"])
                eng = sess.engine
                gm = eng.game
                rnd = gm.current_round

                pos_to_id = {p.position: p.id for p in gm.players}
                declarer_id = pos_to_id[orig_declarer]

                ctype = ContractType(orig_contract['type'])
                trump_suit = _NTS.get(orig_contract.get('trump')) if orig_contract.get('trump') else None
                bid_value = orig_contract.get('level', 2)

                if orig_discarded and not is_in_hand:
                    dcl_cards = set(hands[str(orig_declarer)] + talon) - set(orig_discarded)
                    dcl_hand = [_Card.from_id(cid) for cid in dcl_cards]
                else:
                    dcl_hand = [_Card.from_id(cid) for cid in hands[str(orig_declarer)]]

                for p in gm.players:
                    if p.position == orig_declarer:
                        p.hand = dcl_hand
                    else:
                        p.hand = [_Card.from_id(cid) for cid in hands[str(p.position)]]
                    p.sort_hand()

                rnd.declarer_id = declarer_id
                rnd.contract = Contract(type=ctype, trump_suit=trump_suit,
                                        bid_value=bid_value, is_in_hand=is_in_hand)
                rnd.talon = []
                rnd.original_talon = [_Card.from_id(cid) for cid in talon]
                rnd.discarded = [_Card.from_id(cid) for cid in orig_discarded]

                follower_ids = [pos_to_id[f] for f in orig_followers]
                rnd.whist_followers = follower_ids
                rnd.whist_declarations = {}
                for ps, act in orig_whist_actions.items():
                    rnd.whist_declarations[pos_to_id[int(ps)]] = act

                gm.get_player(declarer_id).is_declarer = True
                for p in gm.players:
                    p.has_dropped_out = (p.id != declarer_id and p.id not in follower_ids)

                rnd.phase = RoundPhase.PLAYING
                first_lead = eng._get_first_lead_player_id(declarer_id, ctype)
                rnd.start_new_trick(lead_player_id=first_lead)

                strategies = {}
                for p in gm.players:
                    s = make_strat(perm[p.position - 1], f"{perm[p.position - 1]}-P{p.position}")
                    s._cards_played = 0
                    s._total_hand_size = len(p.hand)
                    strategies[p.id] = s

                active_ids = [p.id for p in sorted(gm.players, key=lambda p: p.position)
                              if not p.has_dropped_out]
                ctx_trump = trump_suit if ctype == ContractType.SUIT else None
                ctx_contract_type = ctype.value
                played_cards_history = []
                tricks_completed = 0
                ccw = [1, 3, 2, 1, 3, 2]

                while rnd.phase == RoundPhase.PLAYING:
                    trick = rnd.current_trick
                    if trick is None:
                        break
                    next_id = eng._get_next_player_in_trick(trick)
                    player = gm.get_player(next_id)
                    legal_cards = eng.get_legal_cards(next_id)
                    if not legal_cards:
                        break

                    lead_id = trick.lead_player_id
                    lead_pos = gm.get_player(lead_id).position
                    start = ccw.index(lead_pos)
                    trick_order = []
                    for p_pos in ccw[start:start + 3]:
                        pid = next((pl.id for pl in gm.players if pl.position == p_pos), None)
                        if pid and pid in active_ids:
                            trick_order.append(pid)

                    played_ids = set(c.id for c in played_cards_history)
                    trick_ids = set(c.id for _, c in trick.cards)
                    my_ids = set(c.id for c in player.hand)
                    all_active_cids = set()
                    for p in gm.players:
                        if not p.has_dropped_out:
                            for c in p.hand:
                                all_active_cids.add(c.id)
                    remaining = [_Card.from_id(cid) for cid in all_active_cids
                                 if cid not in played_ids and cid not in trick_ids and cid not in my_ids]
                    ctx_talon = [] if is_in_hand else [_Card.from_id(cid) for cid in talon]

                    from PrefTestSingleGame import CardPlayContext as _CPC
                    ctx = _CPC(
                        trick_cards=list(trick.cards),
                        declarer_id=declarer_id, my_id=next_id,
                        active_players=trick_order,
                        played_cards=list(played_cards_history),
                        trump_suit=ctx_trump, contract_type=ctx_contract_type,
                        is_declarer=(next_id == declarer_id),
                        tricks_played=tricks_completed,
                        my_hand=list(player.hand),
                        talon_cards=ctx_talon, is_in_hand=is_in_hand,
                        remaining_cards=remaining,
                    )

                    strat = strategies[next_id]
                    strat._rnd = rnd
                    strat._player_id = next_id
                    strat._ctx = ctx
                    strat._hand = list(player.hand)
                    strat._contract_type = ctx_contract_type
                    strat._trump_suit = ctx_trump
                    strat._is_declarer = (next_id == declarer_id)
                    strat._total_hand_size = len(player.hand) + strat._cards_played
                    card_id = strat.choose_card(legal_cards)
                    result = eng.play_card(next_id, card_id)
                    if result.get("trick_complete"):
                        for _pid, _card in trick.cards:
                            played_cards_history.append(_card)
                        tricks_completed += 1

                scores = {p.position: p.score for p in gm.players}

                for pos in [1, 2, 3]:
                    ptype = perm[pos - 1]
                    sc = scores.get(pos, 0)
                    type_scores[ptype] += sc
                    type_games[ptype] += 1
                    if pos == dcl_pos:
                        type_dcl_scores[ptype] += sc
                        type_dcl_count[ptype] += 1
                    else:
                        type_def_scores[ptype] += sc
                        type_def_count[ptype] += 1

            except Exception as e:
                import traceback
                errors.append(f"Game {gi+1}: {e}")
                traceback.print_exc()

    elapsed = _time.time() - t_start
    total_replays = len(records) * len(all_perms)

    results = []
    for pt in player_types:
        n = type_games[pt]
        total = type_scores[pt]
        avg_sc = total / n if n else 0
        dcl_avg = type_dcl_scores[pt] / type_dcl_count[pt] if type_dcl_count[pt] else 0
        def_avg = type_def_scores[pt] / type_def_count[pt] if type_def_count[pt] else 0
        results.append({
            'name': pt,
            'games': n,
            'total_score': round(total, 1),
            'avg_score': round(avg_sc, 2),
            'dcl_avg': round(dcl_avg, 2),
            'def_avg': round(def_avg, 2),
        })

    return jsonify({
        'results': results,
        'total_replays': total_replays,
        'elapsed': round(elapsed, 1),
        'errors': errors[:5],
    })


if __name__ == '__main__':
    import os
    debug_mode = os.environ.get('FLASK_DEBUG', '1') == '1'
    port = int(os.environ.get('FLASK_PORT', '3000'))
    app.run(debug=debug_mode, host='127.0.0.1', port=port)
