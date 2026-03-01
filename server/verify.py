"""PREF_VERIFY: verify simulation DB data against the engine service.

For each game in the DB:
  1. Create a fresh game via POST /new-game
  2. For each stored step (player → commands → executed):
     a. GET /commands from engine
     b. Verify player on move matches stored player row
     c. Verify commands list matches stored commands row
     d. If stored commands == "start_game" → game verified, move to next
     e. Otherwise execute the stored command via POST /execute
  3. Report first mismatch (game_id, step id, what differed) and stop.

Never attempts any fix — read-only verification only.
"""

import os
import sys
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ENGINE_URL = os.environ.get("ENGINE_URL", "http://localhost:3001")


# ---------------------------------------------------------------------------
# Engine service helpers
# ---------------------------------------------------------------------------

def api_new_game() -> str:
    r = requests.post(f"{ENGINE_URL}/new-game",
                      json={"players": ["P1", "P2", "P3"]}, timeout=5)
    r.raise_for_status()
    return r.json()["game_id"]


def api_commands(game_id: str) -> tuple:
    """Returns (commands: list, player_position: int|None, phase: str|None)."""
    r = requests.get(f"{ENGINE_URL}/commands",
                     params={"game_id": game_id}, timeout=5)
    r.raise_for_status()
    d = r.json()
    return d.get("commands", []), d.get("player_position"), d.get("phase")


def api_execute(game_id: str, command_id: int) -> None:
    r = requests.post(f"{ENGINE_URL}/execute",
                      json={"game_id": game_id, "command_id": command_id},
                      timeout=5)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"Execute error: {data['error']}")


# ---------------------------------------------------------------------------
# Step grouping
# ---------------------------------------------------------------------------

def group_steps(rows: list) -> list:
    """Group DB rows into (player_row, commands_row, executed_row_or_None) tuples."""
    result = []
    i = 0
    while i < len(rows):
        if rows[i]["type"] != "player":
            i += 1
            continue
        player_row   = rows[i]
        commands_row = rows[i + 1] if i + 1 < len(rows) and rows[i + 1]["type"] == "commands" else None
        if not commands_row:
            i += 1
            continue
        if commands_row["content"] == "start_game":
            # Final sentinel — no executed row follows
            result.append((player_row, commands_row, None))
            i += 2
        elif i + 2 < len(rows) and rows[i + 2]["type"] == "executed":
            result.append((player_row, commands_row, rows[i + 2]))
            i += 3
        else:
            result.append((player_row, commands_row, None))
            i += 2
    return result


# ---------------------------------------------------------------------------
# Single-game verification
# ---------------------------------------------------------------------------

def verify_game(game_id: str, steps: list) -> tuple:
    """Verify one game. Returns (ok, error_message)."""
    if not steps:
        return True, ""  # all-pass redeal — nothing to verify

    engine_id = api_new_game()
    groups    = group_steps(steps)

    for player_row, commands_row, executed_row in groups:
        stored_player   = player_row["content"]
        stored_commands = commands_row["content"]

        live_commands, live_pos, _ = api_commands(engine_id)
        live_player = f"P{live_pos}" if live_pos is not None else None

        # 1. Verify player
        if live_player != stored_player:
            return False, (
                f"Player mismatch at step id={player_row['id']}\n"
                f"  stored : {stored_player}\n"
                f"  engine : {live_player}"
            )

        # 2. Verify commands (or detect start_game / discard sentinel)
        if stored_commands == "start_game":
            return True, ""   # reached card-play boundary — game OK

        if stored_commands == "discard":
            # Combined discard step — execute two random discards
            api_execute(engine_id, 1)
            cmds2, _, _ = api_commands(engine_id)
            api_execute(engine_id, 1)
            continue

        live_cmd_str = ",".join(live_commands)
        if live_cmd_str != stored_commands:
            return False, (
                f"Commands mismatch at step id={commands_row['id']}  player={stored_player}\n"
                f"  stored : {stored_commands}\n"
                f"  engine : {live_cmd_str}"
            )

        # 3. Execute stored command
        if executed_row:
            idx = int(executed_row["content"].split()[0])
            api_execute(engine_id, idx)

    return True, ""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    from sim_db import get_all_steps, get_game_ids

    game_ids = get_game_ids()
    print(f"Verifying {len(game_ids)} games via engine service...\n")

    for game_id in game_ids:
        steps = get_all_steps(game_id)
        ok, err = verify_game(game_id, steps)
        if ok:
            print(f"  {game_id}: OK")
        else:
            print(f"\n  {game_id}: MISMATCH")
            print(f"  {err}")
            return

    print("\nAll games verified OK.")


if __name__ == "__main__":
    main()
