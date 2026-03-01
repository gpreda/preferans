"""Generate 100 game simulations by calling the engine service (port 3001).

Output: simulations/simulations_log.csv
Format (semicolon-separated):  game_id ; type ; content
  type='commands' → content: 1) label1, 2) label2, ...
  type='player'   → content: Px
  type='executed' → content: K          (single index)
                             i,j        (discard — two 1-based indices)

Stops after all post-contract declarations and before the first card is played.
"""

import os
import random
import requests

BASE_URL = "http://localhost:3001"

# With dealer_index=2: P3→pos3 (dealer), P1→pos1 (forehand), P2→pos2 (middlehand)
POSITION_TO_PLAYER = {1: "P1", 2: "P2", 3: "P3"}

CONTRACT_LABELS = {"Spades", "Diamonds", "Hearts", "Clubs", "Betl", "Sans"}
SUIT_SYMBOLS    = {"♠", "♦", "♣", "♥"}

TARGET_PROBS = [
    (("pass",         0), 34),
    (("game",         2), 10), (("game",         3), 10),
    (("game",         4), 10), (("game",         5), 10),
    (("in_hand",      2),  5), (("in_hand",      3),  5),
    (("in_hand",      4),  5), (("in_hand",      5),  5),
    (("betl",         0),  2), (("in_hand_betl", 0),  1),
    (("sans",         0),  2), (("in_hand_sans", 0),  1),
]


# ── target assignment ────────────────────────────────────────────────────────

def assign_targets(rng):
    bids, weights = zip(*TARGET_PROBS)
    return [rng.choices(bids, weights=weights, k=1)[0] for _ in range(3)]


# ── label display ────────────────────────────────────────────────────────────

def transform_label(label):
    """Convert service label to compact display form."""
    if label.startswith("Game "):
        return label[5:]          # "Game 2" → "2"
    if label == "In Hand":
        return "InHand"           # "In Hand" → "InHand"
    if label.startswith("in_hand "):
        return "InHand " + label[8:]  # "in_hand 2" → "InHand 2"
    return label                  # Pass, Betl, Sans, Spades, … unchanged


def fmt_commands(labels):
    """Format a list of display labels as '1) l1 2) l2 ...'"""
    return " ".join(f"{i + 1}) {l}" for i, l in enumerate(labels))


# ── command classification ───────────────────────────────────────────────────

def is_contract_cmds(cmds):
    """All commands are contract-level labels (no Pass present)."""
    return bool(cmds) and all(c in CONTRACT_LABELS for c in cmds)


def is_card_cmds(cmds):
    """Commands contain card labels (suit symbols present)."""
    return any(any(sym in c for sym in SUIT_SYMBOLS) for c in cmds)


# ── bid/contract choice ──────────────────────────────────────────────────────

def choose_contract_cmd(target, cmds):
    """Return 1-based index of the contract level to choose."""
    label_to_level = {"Spades": 2, "Diamonds": 3, "Hearts": 4,
                      "Clubs": 5, "Betl": 6, "Sans": 7}
    levels = [(i + 1, label_to_level[c]) for i, c in enumerate(cmds)]

    t, v = target
    if t in ("game", "in_hand"):
        want = v
    elif t in ("betl", "in_hand_betl"):
        want = 6
    elif t in ("sans", "in_hand_sans"):
        want = 7
    else:
        want = min(l for _, l in levels)

    exact = next((idx for idx, lvl in levels if lvl == want), None)
    return exact if exact else levels[0][0]


def choose_auction_bid(target, cmds, player_has_bid):
    """Return 1-based index of the auction command to execute."""
    t, v = target

    pass_idx      = next((i + 1 for i, c in enumerate(cmds) if c == "Pass"), 1)
    in_hand_idx   = next((i + 1 for i, c in enumerate(cmds) if c == "In Hand"), None)
    betl_idx      = next((i + 1 for i, c in enumerate(cmds) if c == "Betl"), None)
    sans_idx      = next((i + 1 for i, c in enumerate(cmds) if c == "Sans"), None)
    game_cmds     = [(i + 1, int(c.split()[1])) for i, c in enumerate(cmds) if c.startswith("Game ")]
    in_hand_decls = [(i + 1, int(c.split()[1])) for i, c in enumerate(cmds) if c.startswith("in_hand ")]

    if t == "pass":
        return pass_idx

    # IN_HAND_DECLARING phase — commands contain "in_hand N"
    if in_hand_decls:
        if t in ("in_hand", "in_hand_betl", "in_hand_sans"):
            max_v = v if t == "in_hand" else 5
            valid = [(idx, val) for idx, val in in_hand_decls if val <= max_v]
            if valid:
                return max(valid, key=lambda x: x[1])[0]
        return pass_idx

    # IN_HAND_DECIDING phase — has "In Hand" but no "Game N"
    if in_hand_idx and not game_cmds:
        if t == "in_hand":
            return in_hand_idx
        if t == "in_hand_betl":
            return betl_idx or in_hand_idx
        if t == "in_hand_sans":
            return sans_idx or betl_idx or in_hand_idx
        return pass_idx

    # INITIAL / GAME_BIDDING — in_hand/betl/sans targets on first bid only
    if t == "in_hand":
        return in_hand_idx if (in_hand_idx and not player_has_bid) else pass_idx

    if t == "in_hand_betl":
        return (betl_idx or in_hand_idx or pass_idx) if not player_has_bid else pass_idx

    if t == "in_hand_sans":
        return (sans_idx or betl_idx or in_hand_idx or pass_idx) if not player_has_bid else pass_idx

    # game / betl / sans — pick highest legal ≤ target
    target_eff = v if t == "game" else 6 if t == "betl" else 7

    candidates = [(val, idx) for idx, val in game_cmds if val <= target_eff]
    if betl_idx and player_has_bid and target_eff >= 6:
        candidates.append((6, betl_idx))
    if sans_idx and player_has_bid and target_eff >= 7:
        candidates.append((7, sans_idx))

    if candidates:
        return max(candidates)[1]

    return pass_idx


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def api_new_game(names):
    r = requests.post(f"{BASE_URL}/new-game", json={"players": names})
    return r.json()["game_id"]


def api_commands(gid):
    r = requests.get(f"{BASE_URL}/commands", params={"game_id": gid})
    d = r.json()
    return d["commands"], d["player_position"]


def api_execute(gid, command_id):
    requests.post(f"{BASE_URL}/execute", json={"game_id": gid, "command_id": command_id})


# ── single game ───────────────────────────────────────────────────────────────

def simulate_game(rng, game_id, rows):
    gid = api_new_game(["P1", "P2", "P3"])

    targets = assign_targets(rng)
    # players sorted by id: P1→targets[0], P2→targets[1], P3→targets[2]
    # position mapping (dealer_index=2): pos1=P1, pos2=P2, pos3=P3
    pos_to_target = {1: targets[0], 2: targets[1], 3: targets[2]}

    player_has_bid    = {1: False, 2: False, 3: False}
    passed_positions  = set()
    any_non_pass      = False
    contract_announced = False

    def emit(rtype, content):
        rows.append(f"{game_id};{rtype};{content}")

    for _ in range(100):
        cmds, pos = api_commands(gid)

        if not cmds or pos is None:
            break

        player_label = POSITION_TO_PLAYER.get(pos, f"P{pos}")

        # ── stop before first card in playing phase (after contract) ─────────
        if is_card_cmds(cmds) and contract_announced:
            break

        # ── discard (exchange phase — before contract) ───────────────────────
        if is_card_cmds(cmds):
            n      = len(cmds)
            i1, i2 = sorted(rng.sample(range(n), 2))
            emit("player",   player_label)
            emit("commands", fmt_commands([str(i + 1) for i in range(n)]))
            emit("executed", f"{i1 + 1},{i2 + 1}")
            api_execute(gid, i1 + 1)
            # After removing card at position i1, the card originally at i2
            # is now at 0-based position i2-1, i.e. 1-based command_id = i2
            api_execute(gid, i2)
            continue

        # ── contract announcement ────────────────────────────────────────────
        if is_contract_cmds(cmds):
            target = pos_to_target[pos]
            idx    = choose_contract_cmd(target, cmds)
            emit("player",   player_label)
            emit("commands", fmt_commands([transform_label(c) for c in cmds]))
            emit("executed", f"{idx} ({transform_label(cmds[idx - 1])})")
            api_execute(gid, idx)
            contract_announced = True
            continue   # don't break — handle any post-contract declarations

        # ── post-contract declaration (e.g. whisting — not yet implemented) ──
        if contract_announced:
            emit("player",   player_label)
            emit("commands", fmt_commands([transform_label(c) for c in cmds]))
            emit("executed", f"1 ({transform_label(cmds[0])})")
            api_execute(gid, 1)
            continue

        # ── auction bid ──────────────────────────────────────────────────────
        target = pos_to_target[pos]
        idx    = choose_auction_bid(target, cmds, player_has_bid[pos])
        label  = cmds[idx - 1]
        emit("player",   player_label)
        emit("commands", fmt_commands([transform_label(c) for c in cmds]))
        emit("executed", f"{idx} ({transform_label(label)})")
        api_execute(gid, idx)

        player_has_bid[pos] = True
        if label == "Pass":
            passed_positions.add(pos)
        else:
            any_non_pass = True

        # All-pass: engine will redeal; stop here
        if len(passed_positions) == 3 and not any_non_pass:
            break


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    out_dir  = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "simulations",
    )
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "simulations_log.csv")

    rows = ["game_id;type;content"]
    for i in range(1, 101):
        rng = random.Random(i)
        simulate_game(rng, f"game_{i:03d}", rows)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(rows) + "\n")

    print(f"Done. {len(rows) - 1} rows written to {out_path}")


if __name__ == "__main__":
    main()
