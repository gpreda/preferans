"""Unified engine service — owns all game state via GameSession.

Endpoints (bidding + card-play):

  POST /new-game      → {game_id, hands, talon}
  GET  /commands      → {commands, player_position, phase, context?, ...}
  POST /execute       → {ok: true}
  GET  /player-on-move → {player_position}

Card-play endpoints (delegate to engine.py):

  GET  /hand          → {cards}
  GET  /talon         → {cards}
  POST /discard       → {ok, hand}
  POST /contract      → {ok}
  GET  /legal-cards   → {cards}
  POST /play-card     → {ok, trick_complete, winner?}
  GET  /tricks        → {tricks}
"""

import os
import uuid
from flask import Flask, jsonify, request
from models import Game, SUIT_SORT_ORDER, RANK_SORT_ORDER
from engine import GameEngine, InvalidMoveError, InvalidPhaseError, GameError

app = Flask(__name__)

SUIT_SYMBOLS  = {'spades': '\u2660', 'diamonds': '\u2666', 'clubs': '\u2663', 'hearts': '\u2665'}
LEVEL_LABELS  = {2: 'Spades', 3: 'Diamonds', 4: 'Hearts', 5: 'Clubs', 6: 'Betl', 7: 'Sans'}
LEVEL_TO_TRUMP = {2: 'spades', 3: 'diamonds', 4: 'hearts', 5: 'clubs'}
SUIT_TO_LEVEL = {'spades': 2, 'diamonds': 3, 'hearts': 4, 'clubs': 5}

sessions = {}   # {game_id: GameSession}


def fmt_card(card):
    return card['rank'] + SUIT_SYMBOLS.get(card['suit'], card['suit'])


class GameSession:
    def __init__(self, player_names):
        game = Game(id=str(uuid.uuid4()))
        for name in player_names:
            game.add_human_player(name)
        game.dealer_index = 2   # P3 is dealer; P1 (index 0) becomes forehand
        self.engine = GameEngine(game)
        self.engine.start_game()
        self.exchange_discards = []   # card dicts picked for discard so far

    # ── helpers ──────────────────────────────────────────────────────────────

    def _st(self):
        return self.engine.get_game_state()

    def _acting_player_position(self, st, rnd, phase):
        pid = (
            st.get('current_bidder_id') if phase == 'auction' else
            (rnd.get('declarer_id') if not rnd.get('contract') else st.get('current_player_id')) if phase == 'playing' else
            (rnd.get('declarer_id') if rnd else None) if phase == 'exchanging' else
            rnd.get('whist_current_id') if phase == 'whisting' else
            None
        )
        if pid:
            p = next((p for p in st.get('players', []) if p['id'] == pid), None)
            return p.get('position') if p else None
        return None

    def _pid_to_position(self, st, pid):
        """Translate a player id to their position (1/2/3)."""
        if pid is None:
            return None
        p = next((p for p in st.get('players', []) if p['id'] == pid), None)
        return p.get('position') if p else None

    # ── public API ───────────────────────────────────────────────────────────

    def get_commands(self):
        """Return (commands: list[str], player_position: int|None)."""
        st  = self._st()
        rnd = st.get('current_round') or {}
        phase = rnd.get('phase')
        cmds = []

        if phase == 'auction':
            for b in st.get('legal_bids', []):
                if b['bid_type'] == 'game':
                    v = b['value']
                    cmds.append('Betl' if v == 6 else 'Sans' if v == 7 else f'Game {v}')
                else:
                    cmds.append(b.get('label') or b['bid_type'])

        elif phase == 'exchanging':
            if rnd.get('talon'):
                # discard-picking: hand cards first (C1-C10), then talon (C11-C12)
                did = rnd['declarer_id']
                declarer = next((p for p in st['players'] if p['id'] == did), None)
                hand  = declarer['hand'] if declarer else []
                talon = rnd['talon']
                chosen = {c['id'] for c in self.exchange_discards}
                for i, card in enumerate(hand + talon, 1):
                    if card['id'] not in chosen:
                        cmds.append(str(i))
            elif st.get('legal_contract_levels'):
                for lvl in st['legal_contract_levels']:
                    cmds.append(LEVEL_LABELS.get(lvl, f'Level {lvl}'))

        elif phase == 'whisting':
            wid = rnd.get('whist_current_id')
            if wid:
                for a in self.engine.get_legal_whist_actions(wid):
                    cmds.append(a['label'])

        elif phase == 'playing':
            if not rnd.get('contract') and st.get('legal_contract_levels'):
                for lvl in st['legal_contract_levels']:
                    cmds.append(LEVEL_LABELS.get(lvl, f'Level {lvl}'))
            else:
                for card in st.get('legal_cards', []):
                    cmds.append(fmt_card(card))

        elif phase == 'scoring':
            cmds.append('Next Round')

        return cmds, self._acting_player_position(st, rnd, phase)

    def execute(self, command_id):
        """Execute the command at 1-based index. Raises on bad index or invalid move."""
        st  = self._st()
        rnd = st.get('current_round') or {}
        phase = rnd.get('phase')
        i = command_id - 1

        if phase == 'auction':
            b = st['legal_bids'][i]
            self.engine.place_bid(st['current_bidder_id'], b['bid_type'], b.get('value', 0))

        elif phase == 'exchanging':
            if rnd.get('talon'):
                did = rnd['declarer_id']
                declarer = next((p for p in st['players'] if p['id'] == did), None)
                hand  = declarer['hand'] if declarer else []
                talon = rnd['talon']
                chosen = {c['id'] for c in self.exchange_discards}
                available = [c for c in hand + talon if c['id'] not in chosen]
                self.exchange_discards.append(available[i])
                if len(self.exchange_discards) == 2:
                    self.engine.complete_exchange(did, [c['id'] for c in self.exchange_discards])
                    self.exchange_discards = []

            elif st.get('legal_contract_levels'):
                lvl = st['legal_contract_levels'][i]
                did = rnd['declarer_id']
                if lvl == 6:
                    self.engine.announce_contract(did, 'betl', None, level=lvl)
                elif lvl == 7:
                    self.engine.announce_contract(did, 'sans', None, level=lvl)
                else:
                    self.engine.announce_contract(did, 'suit', LEVEL_TO_TRUMP[lvl], level=lvl)

        elif phase == 'whisting':
            round_obj = self.engine.game.current_round
            wid = round_obj.whist_current_id
            actions = self.engine.get_legal_whist_actions(wid)
            action_str = actions[i]['action']
            if round_obj.whist_declaring_done or round_obj.declarer_responding:
                self.engine.declare_counter_action(wid, action_str)
            else:
                self.engine.declare_whist(wid, action_str)

        elif phase == 'playing':
            if not rnd.get('contract') and st.get('legal_contract_levels'):
                lvl = st['legal_contract_levels'][i]
                did = rnd['declarer_id']
                if lvl == 6:
                    self.engine.announce_contract(did, 'betl', None, level=lvl)
                elif lvl == 7:
                    self.engine.announce_contract(did, 'sans', None, level=lvl)
                else:
                    self.engine.announce_contract(did, 'suit', LEVEL_TO_TRUMP[lvl], level=lvl)
            else:
                card = st['legal_cards'][i]
                self.engine.play_card(st['current_player_id'], card['id'])

        elif phase == 'scoring':
            self.engine.start_next_round()

        else:
            raise GameError(f'No executable commands in phase {phase!r}')


# ── HTTP endpoints ────────────────────────────────────────────────────────────

def _get_session(gid):
    sess = sessions.get(gid)
    if sess is None:
        return None, (jsonify({"error": "Game not found"}), 404)
    return sess, None


@app.route('/new-game', methods=['POST'])
def ep_new_game():
    data  = request.get_json() or {}
    names = data.get('players', ['Player 1', 'Player 2', 'Player 3'])
    gid   = str(uuid.uuid4())
    sess  = GameSession(names)
    sessions[gid] = sess

    # Build hands and talon for the response
    st  = sess._st()
    rnd = st.get('current_round') or {}
    hands = {}
    for p in st.get('players', []):
        hands[str(p['position'])] = [c['id'] for c in p['hand']]
    talon = [c['id'] for c in rnd.get('talon', [])]

    return jsonify({'game_id': gid, 'hands': hands, 'talon': talon})


@app.route('/commands')
def ep_commands():
    gid  = request.args.get('game_id')
    sess = sessions.get(gid)
    if not sess:
        return jsonify({'error': 'Game not found'}), 404
    cmds, pos = sess.get_commands()
    st  = sess._st()
    rnd = st.get('current_round') or {}
    phase = rnd.get('phase')
    resp = {'commands': cmds, 'player_position': pos, 'phase': phase}

    # ── context construction (for preferans_server.py _sync_phase) ────────

    if phase in ('scoring', 'playing'):
        # Build context: [declarer_pos, contract_type, [[pos, action], ...]]
        round_obj = sess.engine.game.current_round
        did = rnd.get('declarer_id')
        if did is None:
            # All passed
            resp['context'] = [None, None, []]
        else:
            declarer_pos = sess._pid_to_position(st, did)
            contract = round_obj.contract
            ctype = contract.type.value if contract else None
            whist_decls = []
            for pid, action in round_obj.whist_declarations.items():
                p_pos = sess._pid_to_position(st, pid)
                if p_pos is not None:
                    whist_decls.append([p_pos, action])
            resp['context'] = [declarer_pos, ctype, whist_decls]

        # Include scoring results (keyed by position)
        if phase == 'scoring' and hasattr(round_obj, 'results') and round_obj.results:
            res = round_obj.results
            scoring = {
                'declarer': declarer_pos,
                'declarer_won': res.get('declarer_won', False),
                'declarer_tricks': res.get('declarer_tricks', 0),
                'contract_type': res.get('contract_type'),
                'game_value': res.get('game_value', 0),
                'players': {},
            }
            for p in st.get('players', []):
                pid = p['id']
                pos = str(p['position'])
                score = res['scores'].get(pid, 0)
                scoring['players'][pos] = {
                    'score': round(score, 1),
                    'tricks': next((pp for pp in sess.engine.game.players if pp.id == pid), None).tricks_won,
                }
            # Defender details (role, score_change)
            for dr in res.get('defender_results', []):
                dp = str(sess._pid_to_position(st, dr['player_id']))
                if dp in scoring['players']:
                    scoring['players'][dp]['score_change'] = round(dr.get('score_change', 0), 1)
                    if dr.get('role'):
                        scoring['players'][dp]['role'] = dr['role']
            resp['scoring'] = scoring

    elif phase == 'exchanging':
        # For discard step: context = [declarer_pos, bid_level]
        did = rnd.get('declarer_id')
        if did:
            declarer_pos = sess._pid_to_position(st, did)
            auction = rnd.get('auction', {})
            winner_bid = auction.get('highest_in_hand_bid') or auction.get('highest_game_bid')
            bid_level = winner_bid.get('effective_value', winner_bid.get('value', 0)) if winner_bid else 0
            resp['context'] = [declarer_pos, bid_level]

    # ── additional metadata for state machine consumers ───────────────────

    # Include declarer position for exchanging/whisting phases
    if phase in ('exchanging', 'whisting') and rnd.get('declarer_id'):
        did = rnd['declarer_id']
        dp = sess._pid_to_position(st, did)
        if dp is not None:
            resp['declarer_position'] = dp

    # Include whist declarations and contract type for whisting phase
    if phase == 'whisting':
        round_obj = sess.engine.game.current_round
        decls = {}
        for pid, action in round_obj.whist_declarations.items():
            p = next((p for p in st.get('players', []) if p['id'] == pid), None)
            if p:
                decls[str(p['position'])] = action
        if decls:
            resp['whist_declarations'] = decls
        if round_obj.contract:
            resp['contract_type'] = round_obj.contract.type.value

    # Include winning bid value for exchange phase
    if phase == 'exchanging':
        auction = rnd.get('auction', {})
        winner_bid = auction.get('highest_in_hand_bid') or auction.get('highest_game_bid')
        if winner_bid:
            resp['bid_level'] = winner_bid.get('effective_value', winner_bid.get('value', 0))

    # Include auction context for disambiguation
    if phase == 'auction':
        auction = rnd.get('auction', {})
        winner_bid = auction.get('highest_in_hand_bid') or auction.get('highest_game_bid')
        if winner_bid:
            wid = winner_bid.get('player_id')
            if wid:
                wp = sess._pid_to_position(st, wid)
                if wp is not None:
                    resp['highest_bidder_position'] = wp
        passed_ids = auction.get('passed_players', [])
        passed_positions = []
        for pid in passed_ids:
            p = next((p for p in st.get('players', []) if p['id'] == pid), None)
            if p:
                passed_positions.append(p['position'])
        if passed_positions:
            resp['passed_positions'] = sorted(passed_positions)

    return jsonify(resp)


@app.route('/player-on-move')
def ep_player_on_move():
    gid  = request.args.get('game_id')
    sess = sessions.get(gid)
    if not sess:
        return jsonify({'error': 'Game not found'}), 404
    st   = sess._st()
    rnd  = st.get('current_round') or {}
    pos  = sess._acting_player_position(st, rnd, rnd.get('phase'))
    return jsonify({'player_position': pos})


@app.route('/execute', methods=['POST'])
def ep_execute():
    data = request.get_json() or {}
    gid  = data.get('game_id')
    cid  = data.get('command_id')
    sess = sessions.get(gid)
    if not sess:
        return jsonify({'error': 'Game not found'}), 404
    try:
        sess.execute(int(cid))
        return jsonify({'ok': True})
    except (IndexError, KeyError):
        return jsonify({'error': f'Invalid command_id {cid}'}), 400
    except (InvalidMoveError, InvalidPhaseError, GameError) as e:
        return jsonify({'error': str(e)}), 400


# ── Card-play endpoints (delegate to engine) ─────────────────────────────────

@app.route('/hand')
def ep_hand():
    gid = request.args.get("game_id")
    player = int(request.args.get("player"))
    sess, err = _get_session(gid)
    if err:
        return err
    st = sess._st()
    p = next((p for p in st.get('players', []) if p['position'] == player), None)
    if not p:
        return jsonify({"error": "Invalid player"}), 400
    return jsonify({"cards": [c['id'] for c in p['hand']]})


@app.route('/talon')
def ep_talon():
    gid = request.args.get("game_id")
    sess, err = _get_session(gid)
    if err:
        return err
    st = sess._st()
    rnd = st.get('current_round') or {}
    return jsonify({"cards": [c['id'] for c in rnd.get('talon', [])]})


@app.route('/discard', methods=['POST'])
def ep_discard():
    data = request.get_json() or {}
    gid = data.get("game_id")
    player = data.get("player")
    card_ids = data.get("cards", [])

    sess, err = _get_session(gid)
    if err:
        return err
    if len(card_ids) != 2:
        return jsonify({"error": "Must discard exactly 2 cards"}), 400

    # Find the player id from position
    st = sess._st()
    p = next((p for p in st.get('players', []) if p['position'] == player), None)
    if not p:
        return jsonify({"error": "Invalid player"}), 400

    try:
        sess.engine.complete_exchange(p['id'], card_ids)
    except (InvalidMoveError, InvalidPhaseError, GameError) as e:
        return jsonify({"error": str(e)}), 400

    # Return updated hand
    st = sess._st()
    p = next((pp for pp in st.get('players', []) if pp['position'] == player), None)
    hand_ids = [c['id'] for c in p['hand']] if p else []
    return jsonify({"ok": True, "hand": hand_ids})


@app.route('/contract', methods=['POST'])
def ep_contract():
    data = request.get_json() or {}
    gid = data.get("game_id")
    sess, err = _get_session(gid)
    if err:
        return err

    declarer = data.get("declarer")
    contract_type = data.get("contract_type")
    trump = data.get("trump")
    followers = data.get("followers", [])

    # Find declarer player id from position
    st = sess._st()
    p = next((p for p in st.get('players', []) if p['position'] == declarer), None)
    if not p:
        return jsonify({"error": "Invalid declarer"}), 400

    # Determine level from contract type
    if contract_type == 'betl':
        level = 6
    elif contract_type == 'sans':
        level = 7
    else:
        level = SUIT_TO_LEVEL.get(trump, 2)

    try:
        sess.engine.announce_contract(p['id'], contract_type, trump, level=level)
    except (InvalidMoveError, InvalidPhaseError, GameError) as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"ok": True})


@app.route('/legal-cards')
def ep_legal_cards():
    gid = request.args.get("game_id")
    player = int(request.args.get("player"))
    sess, err = _get_session(gid)
    if err:
        return err

    st = sess._st()
    p = next((p for p in st.get('players', []) if p['position'] == player), None)
    if not p:
        return jsonify({"error": "Invalid player"}), 400

    try:
        legal = sess.engine.get_legal_cards(p['id'])
        return jsonify({"cards": [c.id for c in legal]})
    except (GameError,) as e:
        return jsonify({"error": str(e)}), 400


@app.route('/play-card', methods=['POST'])
def ep_play_card():
    data = request.get_json() or {}
    gid = data.get("game_id")
    player = data.get("player")
    card_id = data.get("card")

    sess, err = _get_session(gid)
    if err:
        return err

    st = sess._st()
    p = next((p for p in st.get('players', []) if p['position'] == player), None)
    if not p:
        return jsonify({"error": "Invalid player"}), 400

    try:
        result = sess.engine.play_card(p['id'], card_id)
    except (InvalidMoveError, InvalidPhaseError, GameError) as e:
        return jsonify({"error": str(e)}), 400

    trick_complete = result.get('trick_complete', False)
    resp = {"ok": True, "trick_complete": trick_complete}
    if trick_complete:
        winner_id = result.get('trick_winner_id')
        winner_pos = sess._pid_to_position(st, winner_id)
        resp['winner'] = winner_pos

    return jsonify(resp)


@app.route('/tricks')
def ep_tricks():
    gid = request.args.get("game_id")
    sess, err = _get_session(gid)
    if err:
        return err
    tricks = {}
    for p in sess.engine.game.players:
        tricks[str(p.position)] = p.tricks_won
    return jsonify({"tricks": tricks})


if __name__ == '__main__':
    port  = int(os.environ.get('ENGINE_PORT', '3001'))
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug)
