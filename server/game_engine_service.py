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

import json
import os
import uuid
from flask import Flask, jsonify, request
from models import Game, RoundPhase, ContractType, SUIT_SORT_ORDER, RANK_SORT_ORDER
from engine import GameEngine, InvalidMoveError, InvalidPhaseError, GameError

# Load bidding state machine (covers auction → exchange → whisting → terminal)
_SM_PATH = os.path.join(os.path.dirname(__file__), 'bidding_state_machine.json')
with open(_SM_PATH) as f:
    _SM_STATES = {s['state_id']: s for s in json.load(f)}

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
        self.sm_state_id = 1          # state machine state (initial auction)
        self.sm_active = True         # True while state machine is driving pre-play

    # ── helpers ──────────────────────────────────────────────────────────────

    def get_phase(self):
        """Return current phase string."""
        if self.sm_active:
            return _SM_STATES[self.sm_state_id]['phase']
        rnd = self.engine.game.current_round
        return rnd.phase.value if rnd else None

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
        if self.sm_active:
            state = _SM_STATES[self.sm_state_id]
            player_pos = state['player']

            if state['commands'] == ['discard']:
                # For discard, return actual card choices from engine
                st = self._st()
                rnd = st.get('current_round') or {}
                did = rnd['declarer_id']
                declarer = next((p for p in st['players'] if p['id'] == did), None)
                hand = declarer['hand'] if declarer else []
                talon = rnd['talon']
                chosen = {c['id'] for c in self.exchange_discards}
                cmds = []
                for i, card in enumerate(hand + talon, 1):
                    if card['id'] not in chosen:
                        cmds.append(str(i))
                return cmds, player_pos

            return list(state['commands']), player_pos

        # State machine not active — playing / scoring phases
        st  = self._st()
        rnd = st.get('current_round') or {}
        phase = rnd.get('phase')
        cmds = []

        if phase == 'playing':
            for card in st.get('legal_cards', []):
                cmds.append(fmt_card(card))
        elif phase == 'scoring':
            cmds.append('Next Round')

        return cmds, self._acting_player_position(st, rnd, phase)

    # ── position / player helpers ────────────────────────────────────────────

    def _pos_to_pid(self, pos):
        """Translate player position (1/2/3) to engine player id."""
        p = self.engine._get_player_by_position(pos)
        return p.id

    # ── state-machine side-effect helpers ─────────────────────────────────

    def _sm_setup_declarer(self, declarer_pos):
        """Set declarer on engine round (bypass engine auction)."""
        rnd = self.engine.game.current_round
        pid = self._pos_to_pid(declarer_pos)
        player = self.engine._get_player(pid)
        player.is_declarer = True
        rnd.declarer_id = pid

    def _sm_announce_contract(self, cmd_label, declarer_pos, bid_level, is_in_hand=False):
        """Inject winner bid + announce contract from a command label."""
        self._sm_inject_winner_bid(declarer_pos, bid_level, is_in_hand)
        self._sm_do_announce_contract(cmd_label, declarer_pos)

    def _sm_do_announce_contract(self, cmd_label, declarer_pos):
        """Announce contract from a command label (no bid injection)."""
        pid = self._pos_to_pid(declarer_pos)
        if cmd_label in ('Betl', 'betl'):
            self.engine.announce_contract(pid, 'betl', None, level=6)
        elif cmd_label in ('Sans', 'sans'):
            self.engine.announce_contract(pid, 'sans', None, level=7)
        else:
            trump = cmd_label.lower()   # "Spades" → "spades"
            lvl = SUIT_TO_LEVEL[trump]
            self.engine.announce_contract(pid, 'suit', trump, level=lvl)

    def _sm_inject_winner_bid(self, declarer_pos, bid_level, is_in_hand=False):
        """Inject a synthetic winner bid into the auction so engine validation works."""
        from models import Bid, BidType
        pid = self._pos_to_pid(declarer_pos)
        auction = self.engine.game.current_round.auction

        if bid_level == 6:
            bid = Bid(player_id=pid, bid_type=BidType.BETL, value=6)
        elif bid_level == 7:
            bid = Bid(player_id=pid, bid_type=BidType.SANS, value=7)
        elif is_in_hand:
            bid = Bid(player_id=pid, bid_type=BidType.IN_HAND, value=bid_level)
        else:
            bid = Bid(player_id=pid, bid_type=BidType.GAME, value=bid_level)
        # Mark player as in-hand so engine skips exchange validation
        if is_in_hand and pid not in auction.in_hand_players:
            auction.in_hand_players.append(pid)
        auction.add_bid(bid)
        # For in-hand bids, ensure the bid is set as the winner even with value=0
        if is_in_hand and auction.highest_in_hand_bid is None:
            auction.highest_in_hand_bid = bid

    def _sm_handle_discard(self, command_id):
        """Pick a card for discard during exchange phase."""
        st = self._st()
        rnd = st.get('current_round') or {}
        did = rnd['declarer_id']
        declarer = next((p for p in st['players'] if p['id'] == did), None)
        hand = declarer['hand'] if declarer else []
        talon = rnd['talon']
        chosen = {c['id'] for c in self.exchange_discards}
        available = [c for c in hand + talon if c['id'] not in chosen]
        self.exchange_discards.append(available[command_id - 1])
        if len(self.exchange_discards) == 2:
            self.engine.complete_exchange(did, [c['id'] for c in self.exchange_discards])
            self.exchange_discards = []

    def _sm_apply_preset_declarations(self, ctx):
        """Apply pre-set whist declarations from SM context.

        Context format: [declarer_pos, contract_type, [[pos, action], ...]]
        The third element contains pre-set declarations (e.g. betl forces follow).
        """
        if not ctx or len(ctx) < 3:
            return
        declarations = ctx[2]
        if not isinstance(declarations, list):
            return
        rnd = self.engine.game.current_round
        for pos_str, action in declarations:
            pid = self._pos_to_pid(int(pos_str))
            if action == 'follow':
                rnd.whist_declarations[pid] = 'follow'
                if pid not in rnd.whist_followers:
                    rnd.whist_followers.append(pid)

    def _sm_handle_whist_action(self, cmd_label, player_pos):
        """Record a whist declaration on the round object.

        All decision logic (who moves next, what options are available) lives
        in the SM JSON.  The engine only needs the final declarations to know
        who plays, who called/countered, etc.
        """
        pid = self._pos_to_pid(player_pos)
        rnd = self.engine.game.current_round
        label_to_action = {
            'Pass': 'pass', 'Follow': 'follow', 'Call': 'call',
            'Counter': 'counter', 'Double counter': 'double_counter',
            'Start game': 'start_game',
        }
        action = label_to_action[cmd_label]

        if action == 'follow':
            rnd.whist_declarations[pid] = 'follow'
            if pid not in rnd.whist_followers:
                rnd.whist_followers.append(pid)
        elif action == 'pass':
            rnd.whist_declarations[pid] = 'pass'
        elif action == 'call':
            rnd.whist_declarations[pid] = 'call'
            if pid not in rnd.whist_followers:
                rnd.whist_followers.append(pid)
        elif action == 'counter':
            rnd.has_counter = True
            rnd.counter_player_id = pid
        elif action == 'double_counter':
            rnd.has_double_counter = True
        # 'start_game' — no state change needed

    def _sm_finalize_whist(self):
        """Transition from WHISTING to PLAYING or SCORING after SM completes."""
        self.engine.finalize_whist()

    # ── main execute ──────────────────────────────────────────────────────

    def execute(self, command_id):
        """Execute the command at 1-based index. Raises on bad index or invalid move."""
        if self.sm_active:
            state = _SM_STATES[self.sm_state_id]
            phase = state['phase']
            ctx = state.get('context')

            # Handle discard separately: two picks on the same SM state,
            # only advance after the second pick completes the exchange
            if state['commands'] == ['discard']:
                self._sm_handle_discard(command_id)
                if self.exchange_discards:
                    # First card picked, stay on same state
                    return
                # Both picked; complete_exchange already called, advance SM
                edge = state['edges'][0]
            else:
                edge = next(e for e in state['edges'] if e['cmd_idx'] == command_id)

            next_id = edge['next_state_id']
            cmd_label = edge['cmd_label']

            # ── Apply side effects based on phase & transition ──

            if next_id > 0:
                next_state = _SM_STATES[next_id]
                next_phase = next_state['phase']
                next_ctx = next_state.get('context')

                # Auction → Exchanging: set declarer, set engine phase
                if phase == 'auction' and next_phase == 'exchanging':
                    declarer_pos = next_ctx[0]
                    self._sm_setup_declarer(declarer_pos)
                    self.engine.game.current_round.phase = RoundPhase.EXCHANGING

                # Auction → Whisting: in-hand or betl/sans (contract known, no exchange)
                elif phase == 'auction' and next_phase == 'whisting':
                    declarer_pos = next_ctx[0]
                    contract_type_str = next_ctx[1]  # 'betl', 'sans', or 'suit'
                    if contract_type_str == 'betl':
                        bid_level = 6
                    elif contract_type_str == 'sans':
                        bid_level = 7
                    elif cmd_label.startswith('in_hand '):
                        bid_level = int(cmd_label.split()[-1])  # "in_hand 3" → 3
                    else:
                        bid_level = 2
                    self._sm_setup_declarer(declarer_pos)
                    # All auction→whisting paths skip exchange; mark as in-hand so
                    # announce_contract skips exchange validation
                    self._sm_inject_winner_bid(declarer_pos, bid_level, is_in_hand=True)
                    if contract_type_str == 'suit':
                        # In-hand suit: derive trump from level
                        trump = LEVEL_TO_TRUMP[bid_level]
                        pid = self._pos_to_pid(declarer_pos)
                        self.engine.announce_contract(pid, 'suit', trump, level=bid_level)
                    else:
                        self._sm_do_announce_contract(contract_type_str, declarer_pos)

                # Auction → Playing: in-hand undeclared (declarer picks suit later)
                elif phase == 'auction' and next_phase == 'playing':
                    # Declarer is the player who will select the contract suit
                    declarer_pos = next_state['player']
                    self._sm_setup_declarer(declarer_pos)
                    self._sm_inject_winner_bid(declarer_pos, 0, is_in_hand=True)
                    self.engine.game.current_round.phase = RoundPhase.PLAYING

                # Exchanging contract selection (Spades/Diamonds/Hearts/Clubs/Betl/Sans)
                elif phase == 'exchanging' and cmd_label != 'discard':
                    declarer_pos = ctx[0]
                    bid_level = ctx[1]
                    self._sm_announce_contract(cmd_label, declarer_pos, bid_level)

                # Playing phase contract selection (in-hand undeclared)
                elif phase == 'playing':
                    # Declarer picking suit for in-hand game;
                    # winner bid already injected during auction→playing transition
                    declarer_pos = state['player']
                    self._sm_do_announce_contract(cmd_label, declarer_pos)

                # Whisting actions
                elif phase == 'whisting':
                    self._sm_handle_whist_action(cmd_label, state['player'])

                self.sm_state_id = next_id

                # Apply pre-set declarations when entering whisting
                if next_phase == 'whisting' and phase != 'whisting':
                    self._sm_apply_preset_declarations(next_ctx)

            elif next_id == 0:
                # Terminal: game_start → transition to PLAYING phase
                if phase == 'whisting':
                    self._sm_handle_whist_action(cmd_label, state['player'])
                    self._sm_finalize_whist()
                elif phase == 'playing':
                    # In-hand undeclared suit selection → announce contract
                    self._sm_do_announce_contract(cmd_label, state['player'])
                self.sm_active = False

            elif next_id == -1:
                # Terminal: game_end → redeal (all passed) or no-followers scoring
                if phase == 'auction':
                    # All players passed — redeal
                    self.engine.game.current_round.phase = RoundPhase.REDEAL
                elif phase == 'whisting':
                    # Last whist action — no followers, go to scoring
                    self._sm_handle_whist_action(cmd_label, state['player'])
                    self._sm_finalize_whist()
                self.sm_active = False

            return

        # ── State machine not active — playing / scoring ──
        st  = self._st()
        rnd = st.get('current_round') or {}
        phase = rnd.get('phase')
        i = command_id - 1

        if phase == 'playing':
            card = st['legal_cards'][i]
            self.engine.play_card(st['current_player_id'], card['id'])

        elif phase == 'scoring':
            self.engine.start_next_round()
            # Reset state machine for next round
            self.sm_state_id = 1
            self.sm_active = True

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
    hands = {}
    for p in st.get('players', []):
        hands[str(p['position'])] = [c['id'] for c in p['hand']]
    # Get talon directly from engine (to_dict hides it during auction)
    rnd_obj = sess.engine.game.current_round
    talon = [c.to_dict()['id'] for c in rnd_obj.talon] if rnd_obj else []

    return jsonify({'game_id': gid, 'hands': hands, 'talon': talon})


@app.route('/commands')
def ep_commands():
    gid  = request.args.get('game_id')
    sess = sessions.get(gid)
    if not sess:
        return jsonify({'error': 'Game not found'}), 404
    cmds, pos = sess.get_commands()

    # ── Determine phase from state machine or engine ──────────────────────

    if sess.sm_active:
        sm_state = _SM_STATES[sess.sm_state_id]
        phase = sm_state['phase']
        ctx = sm_state.get('context')
    else:
        st  = sess._st()
        rnd = st.get('current_round') or {}
        phase = rnd.get('phase')
        ctx = None

    resp = {'commands': cmds, 'player_position': pos, 'phase': phase}

    # ── Context from state machine ────────────────────────────────────────

    if sess.sm_active:
        if phase == 'auction':
            # Auction context: [highest_bidder_pos_or_null, [passed_positions]]
            if ctx:
                if ctx[0] is not None:
                    resp['highest_bidder_position'] = ctx[0]
                if ctx[1]:
                    resp['passed_positions'] = sorted(ctx[1])

        elif phase == 'exchanging':
            # Exchanging context: [declarer_pos, bid_level]
            if ctx:
                resp['context'] = [ctx[0], ctx[1]]
                resp['declarer_position'] = ctx[0]
                resp['bid_level'] = ctx[1]

        elif phase == 'whisting':
            # Whisting context: [declarer_pos, contract_type_str, [[pos_str, action], ...]]
            if ctx:
                resp['declarer_position'] = ctx[0]
                resp['contract_type'] = ctx[1]
                decls = {}
                for item in ctx[2]:
                    decls[str(item[0])] = item[1]
                if decls:
                    resp['whist_declarations'] = decls
                # Include trump suit from engine contract
                contract = sess.engine.game.current_round.contract
                if contract and contract.trump_suit:
                    resp['trump'] = contract.trump_suit

        elif phase == 'playing':
            # Playing context (in-hand undeclared): [declarer_pos, ...]
            if ctx:
                resp['context'] = [ctx[0], None, []]

    # ── Context from engine (when state machine not active) ───────────────

    elif phase in ('scoring', 'playing'):
        st  = sess._st()
        rnd = st.get('current_round') or {}
        round_obj = sess.engine.game.current_round
        did = rnd.get('declarer_id')
        if did is None:
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
            if contract and contract.trump_suit:
                resp['trump'] = contract.trump_suit

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
                pos_str = str(p['position'])
                score = res['scores'].get(pid, 0)
                scoring['players'][pos_str] = {
                    'score': round(score, 1),
                    'tricks': next((pp for pp in sess.engine.game.players if pp.id == pid), None).tricks_won,
                }
            for dr in res.get('defender_results', []):
                dp = str(sess._pid_to_position(st, dr['player_id']))
                if dp in scoring['players']:
                    scoring['players'][dp]['score_change'] = round(dr.get('score_change', 0), 1)
                    if dr.get('role'):
                        scoring['players'][dp]['role'] = dr['role']
            resp['scoring'] = scoring

    return jsonify(resp)


@app.route('/player-on-move')
def ep_player_on_move():
    gid  = request.args.get('game_id')
    sess = sessions.get(gid)
    if not sess:
        return jsonify({'error': 'Game not found'}), 404
    if sess.sm_active:
        state = _SM_STATES[sess.sm_state_id]
        return jsonify({'player_position': state['player']})
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
    # Get talon directly from engine (to_dict hides it during auction)
    rnd_obj = sess.engine.game.current_round
    cards = [c.to_dict()['id'] for c in rnd_obj.talon] if rnd_obj else []
    return jsonify({"cards": cards})


@app.route('/original-talon')
def ep_original_talon():
    gid = request.args.get("game_id")
    sess, err = _get_session(gid)
    if err:
        return err
    rnd_obj = sess.engine.game.current_round
    cards = [c.to_dict()['id'] for c in rnd_obj.original_talon] if rnd_obj else []
    return jsonify({"cards": cards})


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
        if result.get('round_complete'):
            resp['round_complete'] = True

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
