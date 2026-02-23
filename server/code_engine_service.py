"""Engine service — owns all game state and exposes three APIs:

  POST /new-game
      body: {players: [name, name, name]}
      returns: {game_id}

  GET  /commands?game_id=<id>
      returns: {commands: [str, ...], player_position: int|null}
               commands are the labels for the current legal moves;
               player_position is 1/2/3 for the acting player, null if none.

  POST /execute
      body: {game_id, command_id}   (command_id is 1-based index into commands)
      returns: {ok: true} | {error: str}
"""

import os
import uuid
from flask import Flask, jsonify, request
from models import Game
from engine import GameEngine, InvalidMoveError, InvalidPhaseError, GameError

app = Flask(__name__)

SUIT_SYMBOLS  = {'spades': '\u2660', 'diamonds': '\u2666', 'clubs': '\u2663', 'hearts': '\u2665'}
LEVEL_LABELS  = {2: 'Spades', 3: 'Diamonds', 4: 'Hearts', 5: 'Clubs', 6: 'Betl', 7: 'Sans'}
LEVEL_TO_TRUMP = {2: 'spades', 3: 'diamonds', 4: 'hearts', 5: 'clubs'}

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

@app.route('/new-game', methods=['POST'])
def ep_new_game():
    data  = request.get_json() or {}
    names = data.get('players', ['Player 1', 'Player 2', 'Player 3'])
    gid   = str(uuid.uuid4())
    sessions[gid] = GameSession(names)
    return jsonify({'game_id': gid})


@app.route('/commands')
def ep_commands():
    gid  = request.args.get('game_id')
    sess = sessions.get(gid)
    if not sess:
        return jsonify({'error': 'Game not found'}), 404
    cmds, pos = sess.get_commands()
    st  = sess._st()
    rnd = st.get('current_round') or {}
    resp = {'commands': cmds, 'player_position': pos, 'phase': rnd.get('phase')}
    # Include declarer position for exchanging/whisting phases (needed for state machine)
    if rnd.get('phase') in ('exchanging', 'whisting') and rnd.get('declarer_id'):
        did = rnd['declarer_id']
        dp = next((p['position'] for p in st.get('players', []) if p['id'] == did), None)
        if dp is not None:
            resp['declarer_position'] = dp
    # Include whist declarations and contract type for state machine disambiguation
    if rnd.get('phase') == 'whisting':
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
    # Include winning bid value for exchange phase (contract options depend on it)
    if rnd.get('phase') == 'exchanging':
        auction = rnd.get('auction', {})
        winner_bid = auction.get('highest_in_hand_bid') or auction.get('highest_game_bid')
        if winner_bid:
            resp['bid_level'] = winner_bid.get('effective_value', winner_bid.get('value', 0))
    # Include auction context for state machine disambiguation
    if rnd.get('phase') == 'auction':
        auction = rnd.get('auction', {})
        winner_bid = auction.get('highest_in_hand_bid') or auction.get('highest_game_bid')
        if winner_bid:
            wid = winner_bid.get('player_id')
            if wid:
                wp = next((p['position'] for p in st.get('players', []) if p['id'] == wid), None)
                if wp is not None:
                    resp['highest_bidder_position'] = wp
        # Track which players have passed (affects transitions)
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


if __name__ == '__main__':
    port  = int(os.environ.get('ENGINE_PORT', '3001'))
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug)
