"""Stress test: generate random games and verify each against the state machine.

Runs up to N iterations (default 1000). Stops at first mismatch.
The engine service must be running at ENGINE_URL (default http://localhost:3001).
"""

import json
import os
import sys
import random
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from simulate_log import (
    api_new_game, api_commands, api_execute,
    choose_auction_cmd, choose_whist_cmd,
    _is_card_cmd, _is_exchange_discard,
)
from verify_state_machine import load_state_machine, group_steps, verify_game

ENGINE_URL = os.environ.get("ENGINE_URL", "http://localhost:3001")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 1000


def simulate_game_rows(seed: int) -> list:
    """Run one random game, return rows as list of dicts (like sim_db format)."""
    rng = random.Random(seed)
    engine_id = api_new_game()
    rows = []
    row_id = 0

    def emit(rtype, content):
        nonlocal row_id
        row_id += 1
        rows.append({"id": row_id, "type": rtype, "content": content})

    prev_phase = "auction"
    max_steps = 60

    while max_steps > 0:
        max_steps -= 1
        commands, player_pos, phase = api_commands(engine_id)

        if not commands or player_pos is None or phase == "scoring":
            break
        if phase == "auction" and prev_phase != "auction":
            break
        if phase == "playing" and commands and _is_card_cmd(commands[0]):
            emit("player", f"P{player_pos}")
            emit("commands", "start_game")
            break

        if _is_exchange_discard(phase, commands):
            idx1 = rng.randint(1, len(commands))
            api_execute(engine_id, idx1)
            commands2, _, _ = api_commands(engine_id)
            idx2 = rng.randint(1, len(commands2))
            api_execute(engine_id, idx2)
            emit("player", f"P{player_pos}")
            emit("commands", "discard")
            emit("executed", "1 discard")
            prev_phase = phase
            continue

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

    return rows


def main():
    sm = load_state_machine()
    print(f"Loaded state machine: {len(sm)} states")
    print(f"Running {N} random games...\n")

    for i in range(1, N + 1):
        seed = random.randint(1, 10_000_000)
        rows = simulate_game_rows(seed)
        if not rows:
            continue

        ok, err = verify_game(f"seed_{seed}", rows, sm)
        if ok:
            print(f"\r  {i}/{N} OK (seed {seed})", end="", flush=True)
        else:
            print(f"\n\n  MISMATCH at iteration {i} (seed {seed}):")
            print(f"  {err}")
            print(f"\n  Steps:")
            for r in rows:
                print(f"    id={r['id']:3d}  type={r['type']:10s}  content={r['content']}")
            return

    print(f"\n\nAll {N} games verified OK.")


if __name__ == "__main__":
    main()
