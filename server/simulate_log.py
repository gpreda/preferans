"""Generate game simulations via the engine service API and store to PostgreSQL.

The engine service must be running at ENGINE_URL (default: http://localhost:3001).

Row format stored in sim_steps (player → commands → executed per step):
  type='player'   → content: Px
  type='commands' → content: cmd1,cmd2,...
  type='executed' → content: K cmdK   (K = 1-based index)

Special rows:
  commands → start_game     final sentinel (who leads first trick)
  commands → discard        combined two-card discard (single step)

The last two rows of each playable game:
  player   → who would lead the first trick
  commands → start_game
"""

import os
import sys
import random
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ENGINE_URL = os.environ.get("ENGINE_URL", "http://localhost:3001")

TARGET_GAMES = 50

# Auction weights (used as relative probabilities)
W_PASS    = 50
W_GAME    = 30   # each of game 2, 3, 4, 5
W_IN_HAND = 10
W_BETL    = 10
W_SANS    = 10

# Whist probabilities
PROB_FOLLOW         = 0.50
PROB_COUNTER        = 0.10
PROB_DOUBLE_COUNTER = 0.05


# ---------------------------------------------------------------------------
# Engine service helpers
# ---------------------------------------------------------------------------

def api_new_game() -> str:
    """POST /new-game → engine game_id (UUID)."""
    r = requests.post(f"{ENGINE_URL}/new-game",
                      json={"players": ["Alice", "Bob", "Carol"]}, timeout=5)
    r.raise_for_status()
    return r.json()["game_id"]


def api_commands(game_id: str) -> tuple:
    """GET /commands → (commands: list, player_position: int|None, phase: str|None)."""
    r = requests.get(f"{ENGINE_URL}/commands",
                     params={"game_id": game_id}, timeout=5)
    r.raise_for_status()
    d = r.json()
    return d.get("commands", []), d.get("player_position"), d.get("phase")


def api_execute(game_id: str, command_id: int) -> None:
    """POST /execute with 1-based command_id."""
    r = requests.post(f"{ENGINE_URL}/execute",
                      json={"game_id": game_id, "command_id": command_id},
                      timeout=5)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"Execute error: {data['error']}")


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------

def _is_card_cmd(cmd: str) -> bool:
    """True if the command is a card label (contains a suit symbol)."""
    return any(s in cmd for s in ("♠", "♦", "♣", "♥"))


def _is_exchange_discard(phase: str, commands: list) -> bool:
    """True if this is an exchange discard step (numbered card slots)."""
    return phase == "exchanging" and commands and commands[0].isdigit()


def choose_auction_cmd(rng, commands: list) -> int:
    """Return 1-based index using bid-type weights."""
    weights = []
    for cmd in commands:
        if cmd == "Pass":
            w = W_PASS
        elif cmd.startswith("Game ") or cmd.startswith("in_hand "):
            w = W_GAME
        elif cmd in ("In Hand", "in_hand"):
            w = W_IN_HAND
        elif cmd == "Betl":
            w = W_BETL
        elif cmd == "Sans":
            w = W_SANS
        else:
            w = W_GAME
        weights.append(w)
    return rng.choices(range(1, len(commands) + 1), weights=weights, k=1)[0]


def choose_whist_cmd(rng, commands: list) -> int:
    """Return 1-based index using whist probability constants."""
    amap = {cmd: i + 1 for i, cmd in enumerate(commands)}

    if "Double counter" in amap:
        return amap["Double counter"] if rng.random() < PROB_DOUBLE_COUNTER else amap["Start game"]

    if "Start game" in amap and "Counter" in amap:
        return amap["Counter"] if rng.random() < PROB_COUNTER else amap["Start game"]

    r = rng.random()
    if r < PROB_FOLLOW and "Follow" in amap:
        return amap["Follow"]
    if "Counter" in amap and r < PROB_FOLLOW + PROB_COUNTER:
        return amap["Counter"]
    return amap.get("Pass", 1)


# ---------------------------------------------------------------------------
# Fingerprint: command chain ignoring exchange discards
# ---------------------------------------------------------------------------

def fingerprint(rows: list) -> str:
    """Build a dedup key from the executed commands, skipping discard steps."""
    parts = []
    i = 0
    while i < len(rows):
        if i + 1 < len(rows):
            _, _, cmds_content = rows[i + 1]
            if cmds_content == "start_game":
                parts.append(cmds_content)
                i += 2
                continue
            if cmds_content == "discard":
                i += 3
                continue
        if i + 2 < len(rows):
            _, _, exec_content = rows[i + 2]
            parts.append(exec_content)
            i += 3
        else:
            i += 1
    return "|".join(parts)


# ---------------------------------------------------------------------------
# Single game simulation
# ---------------------------------------------------------------------------

def simulate_game(seed: int, rows: list) -> None:
    rng = random.Random(seed)
    engine_id = api_new_game()

    def emit(rtype: str, content: str) -> None:
        rows.append(("", rtype, content))  # game_label filled later

    prev_phase = "auction"
    max_steps = 60

    while max_steps > 0:
        max_steps -= 1

        commands, player_pos, phase = api_commands(engine_id)

        if not commands or player_pos is None or phase == "scoring":
            break

        # Redeal detection: returned to auction after leaving it
        if phase == "auction" and prev_phase != "auction":
            break

        # Stop before card play — emit sentinel player + start_game
        if phase == "playing" and commands and _is_card_cmd(commands[0]):
            emit("player", f"P{player_pos}")
            emit("commands", "start_game")
            break

        # Exchange discard: pick two cards, emit single "discard" step
        if _is_exchange_discard(phase, commands):
            idx1 = rng.randint(1, len(commands))
            api_execute(engine_id, idx1)
            # Second discard
            commands2, _, _ = api_commands(engine_id)
            idx2 = rng.randint(1, len(commands2))
            api_execute(engine_id, idx2)

            emit("player", f"P{player_pos}")
            emit("commands", "discard")
            emit("executed", "1 discard")
            prev_phase = phase
            continue

        # Choose command index (1-based)
        if phase == "auction":
            idx = choose_auction_cmd(rng, commands)
        elif phase == "whisting":
            idx = choose_whist_cmd(rng, commands)
        else:
            idx = rng.randint(1, len(commands))

        chosen = commands[idx - 1]

        emit("player", f"P{player_pos}")
        emit("commands", ",".join(commands))
        emit("executed", f"{idx} {chosen}")

        api_execute(engine_id, idx)
        prev_phase = phase


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    from sim_db import init_db, clear_simulations, insert_rows
    init_db()

    seen_fingerprints = set()
    games = []  # list of (game_label, rows)
    seed = 0
    dupes = 0

    print(f"Generating {TARGET_GAMES} unique simulations...")

    while len(games) < TARGET_GAMES:
        seed += 1
        rows = []
        try:
            simulate_game(seed=seed, rows=rows)
        except Exception as e:
            print(f"  seed {seed} failed: {e}")
            continue

        if not rows:
            continue  # redeal or empty — skip

        fp = fingerprint(rows)
        if fp in seen_fingerprints:
            dupes += 1
            continue

        seen_fingerprints.add(fp)
        game_num = len(games) + 1
        game_label = f"game_{game_num:03d}"
        labeled_rows = [(game_label, rtype, content) for _, rtype, content in rows]
        games.append((game_label, labeled_rows))
        print(f"  {game_label} (seed {seed})", flush=True)

    all_rows = []
    for _, labeled_rows in games:
        all_rows.extend(labeled_rows)

    print(f"\nDone. {len(games)} unique games, {dupes} duplicates skipped, {len(all_rows)} rows total.")

    try:
        clear_simulations()
        insert_rows(all_rows)
        print(f"{len(all_rows)} rows written to PostgreSQL.")
    except Exception as e:
        print(f"DB write failed: {e}")


if __name__ == "__main__":
    main()
