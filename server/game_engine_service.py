"""Game engine service — card dealing, discarding, trick-taking.

Endpoints:
  POST /new-game       → {game_id, hands, talon}
  GET  /hand           → {cards}
  GET  /talon          → {cards}
  POST /discard        → {ok, hand}
  POST /contract       → {ok}
  POST /play-card      → {ok, trick_complete, winner?}
  GET  /tricks         → {tricks}
"""

import os
import uuid
import random
from flask import Flask, jsonify, request
from models import (
    Card, Suit, Rank,
    SUIT_SORT_ORDER, RANK_SORT_ORDER, NAME_TO_SUIT,
)

app = Flask(__name__)

sessions = {}


# ── helpers ──────────────────────────────────────────────────────────

def _create_deck():
    cards = []
    for suit in Suit:
        for rank in Rank:
            cards.append(Card(rank=rank, suit=suit))
    return cards


def _deal(deck):
    """Deal 3-talon(2)-4-3 to players 1, 2, 3."""
    hands = {1: [], 2: [], 3: []}
    idx = 0
    for p in (1, 2, 3):
        for _ in range(3):
            hands[p].append(deck[idx]); idx += 1
    talon = [deck[idx], deck[idx + 1]]; idx += 2
    for p in (1, 2, 3):
        for _ in range(4):
            hands[p].append(deck[idx]); idx += 1
    for p in (1, 2, 3):
        for _ in range(3):
            hands[p].append(deck[idx]); idx += 1
    return hands, talon


def _sort(cards):
    cards.sort(
        key=lambda c: (SUIT_SORT_ORDER[c.suit], RANK_SORT_ORDER[c.rank]),
        reverse=True,
    )


def _ids(cards):
    return [c.id for c in cards]


def _find(cards, card_id):
    return next((c for c in cards if c.id == card_id), None)


def _get_session(gid):
    sess = sessions.get(gid)
    if sess is None:
        return None, (jsonify({"error": "Game not found"}), 404)
    return sess, None


# ── endpoints ────────────────────────────────────────────────────────

@app.route('/new-game', methods=['POST'])
def ep_new_game():
    gid = str(uuid.uuid4())
    deck = _create_deck()
    random.shuffle(deck)
    hands, talon = _deal(deck)
    for p in (1, 2, 3):
        _sort(hands[p])

    sessions[gid] = {
        "hands": hands,
        "talon": talon,
        "discarded": [],
        "contract": None,
        "tricks_won": {1: 0, 2: 0, 3: 0},
        "current_trick": [],
        "trick_number": 0,
    }

    return jsonify({
        "game_id": gid,
        "hands": {str(p): _ids(cards) for p, cards in hands.items()},
        "talon": _ids(talon),
    })


@app.route('/hand')
def ep_hand():
    gid = request.args.get("game_id")
    player = int(request.args.get("player"))
    sess, err = _get_session(gid)
    if err:
        return err
    if player not in (1, 2, 3):
        return jsonify({"error": "Invalid player"}), 400
    return jsonify({"cards": _ids(sess["hands"][player])})


@app.route('/talon')
def ep_talon():
    gid = request.args.get("game_id")
    sess, err = _get_session(gid)
    if err:
        return err
    return jsonify({"cards": _ids(sess["talon"])})


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

    hand = sess["hands"][player]

    # pick up talon
    hand.extend(sess["talon"])
    sess["talon"] = []

    # remove discarded cards
    to_discard = []
    for cid in card_ids:
        card = _find(hand, cid)
        if not card:
            return jsonify({"error": f"Card {cid} not in hand"}), 400
        to_discard.append(card)
    for card in to_discard:
        hand.remove(card)

    sess["discarded"] = to_discard
    _sort(hand)

    return jsonify({"ok": True, "hand": _ids(hand)})


@app.route('/contract', methods=['POST'])
def ep_contract():
    data = request.get_json() or {}
    gid = data.get("game_id")
    sess, err = _get_session(gid)
    if err:
        return err

    trump_name = data.get("trump")
    sess["contract"] = {
        "declarer": data.get("declarer"),
        "followers": data.get("followers", []),
        "type": data.get("contract_type"),
        "trump": NAME_TO_SUIT[trump_name] if trump_name else None,
    }
    sess["tricks_won"] = {1: 0, 2: 0, 3: 0}
    sess["current_trick"] = []
    sess["trick_number"] = 0

    return jsonify({"ok": True})


@app.route('/play-card', methods=['POST'])
def ep_play_card():
    data = request.get_json() or {}
    gid = data.get("game_id")
    player = data.get("player")
    card_id = data.get("card")

    sess, err = _get_session(gid)
    if err:
        return err
    if not sess["contract"]:
        return jsonify({"error": "No contract set"}), 400

    hand = sess["hands"][player]
    card = _find(hand, card_id)
    if not card:
        return jsonify({"error": f"Card {card_id} not in hand"}), 400

    if not sess["current_trick"]:
        sess["trick_number"] += 1

    hand.remove(card)
    sess["current_trick"].append((player, card))

    # trick complete when all active players have played
    contract = sess["contract"]
    num_active = 1 + len(contract["followers"])

    if len(sess["current_trick"]) < num_active:
        return jsonify({"ok": True, "trick_complete": False})

    # resolve trick
    trump_suit = contract["trump"]
    led_suit = sess["current_trick"][0][1].suit
    best_player, best_card = sess["current_trick"][0]
    for p, c in sess["current_trick"][1:]:
        if c.beats(best_card, trump_suit=trump_suit, led_suit=led_suit):
            best_player, best_card = p, c

    sess["tricks_won"][best_player] += 1
    sess["current_trick"] = []

    return jsonify({"ok": True, "trick_complete": True, "winner": best_player})


@app.route('/tricks')
def ep_tricks():
    gid = request.args.get("game_id")
    sess, err = _get_session(gid)
    if err:
        return err
    return jsonify({
        "tricks": {str(p): n for p, n in sess["tricks_won"].items()}
    })


if __name__ == '__main__':
    port = int(os.environ.get('GAME_ENGINE_PORT', '3002'))
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    app.run(host='0.0.0.0', port=port, debug=debug)
