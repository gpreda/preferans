"""State-machine-based engine service — walks bidding_state_machine.json.

No game engine or models imports. Same 4 endpoints as before:

  POST /new-game      → {game_id}
  GET  /commands      → {commands, player_position, phase}
  POST /execute       → {ok: true}
  GET  /player-on-move → {player_position}
"""

import json
import os
import uuid
from flask import Flask, jsonify, request

app = Flask(__name__)

# Load state machine at startup
_sm_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "bidding_state_machine.json")
with open(_sm_path, "r", encoding="utf-8") as _f:
    _sm_list = json.load(_f)
SM = {s["state_id"]: s for s in _sm_list}

# Session state: {game_id: {"state_id": int}}
sessions = {}

STATE_GAME_START = 0
STATE_GAME_END = -1


@app.route('/new-game', methods=['POST'])
def ep_new_game():
    gid = str(uuid.uuid4())
    sessions[gid] = {"state_id": 1}
    return jsonify({"game_id": gid})


@app.route('/commands')
def ep_commands():
    gid = request.args.get('game_id')
    sess = sessions.get(gid)
    if not sess:
        return jsonify({"error": "Game not found"}), 404

    sid = sess["state_id"]

    if sid == STATE_GAME_START:
        return jsonify({
            "commands": ["7\u2660"],
            "player_position": 1,
            "phase": "playing",
        })

    if sid == STATE_GAME_END:
        return jsonify({
            "commands": [],
            "player_position": None,
            "phase": "scoring",
        })

    state = SM.get(sid)
    if state is None:
        return jsonify({"error": f"State {sid} not found"}), 500

    return jsonify({
        "commands": state["commands"],
        "player_position": state["player"],
        "phase": state["phase"],
    })


@app.route('/execute', methods=['POST'])
def ep_execute():
    data = request.get_json() or {}
    gid = data.get('game_id')
    cid = data.get('command_id')
    sess = sessions.get(gid)
    if not sess:
        return jsonify({"error": "Game not found"}), 404

    sid = sess["state_id"]
    state = SM.get(sid)
    if state is None:
        return jsonify({"error": f"State {sid} not found"}), 500

    cid = int(cid)
    for edge in state["edges"]:
        if edge["cmd_idx"] == cid:
            sess["state_id"] = edge["next_state_id"]
            return jsonify({"ok": True})

    return jsonify({"error": f"Invalid command_id {cid}"}), 400


@app.route('/player-on-move')
def ep_player_on_move():
    gid = request.args.get('game_id')
    sess = sessions.get(gid)
    if not sess:
        return jsonify({"error": "Game not found"}), 404

    sid = sess["state_id"]

    if sid == STATE_GAME_START or sid == STATE_GAME_END:
        return jsonify({"player_position": None})

    state = SM.get(sid)
    if state is None:
        return jsonify({"error": f"State {sid} not found"}), 500

    return jsonify({"player_position": state["player"]})


if __name__ == '__main__':
    port = int(os.environ.get('ENGINE_PORT', '3001'))
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug)
