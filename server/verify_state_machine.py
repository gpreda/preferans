"""Verify simulation DB data against the state machine JSON.

No engine service or game logic needed — purely walks state machine transitions.

For each game in the DB:
  1. Start at state 1 (initial auction state).
  2. For each stored step (player → commands → executed):
     a. Verify current state's player and commands match stored values.
     b. Follow the edge matching the executed command.
     c. Move to the next state.
  3. The final step should be a start_game sentinel reaching terminal state 0,
     or the game should end at terminal state -1.
  4. Report first mismatch and stop.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# State machine loader
# ---------------------------------------------------------------------------

def load_state_machine() -> dict:
    """Load state machine JSON and return {state_id: state_dict}."""
    sm_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "bidding_state_machine.json")
    with open(sm_path, "r", encoding="utf-8") as f:
        states = json.load(f)
    return {s["state_id"]: s for s in states}


# ---------------------------------------------------------------------------
# Step grouping (same as verify.py)
# ---------------------------------------------------------------------------

def group_steps(rows: list) -> list:
    """Group DB rows into (player_row, commands_row, executed_row_or_None) tuples."""
    result = []
    i = 0
    while i < len(rows):
        if rows[i]["type"] != "player":
            i += 1
            continue
        player_row = rows[i]
        commands_row = rows[i + 1] if i + 1 < len(rows) and rows[i + 1]["type"] == "commands" else None
        if not commands_row:
            i += 1
            continue
        if commands_row["content"] == "start_game":
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

def verify_game(game_id: str, steps: list, sm: dict) -> tuple:
    """Verify one game against the state machine. Returns (ok, error_message)."""
    if not steps:
        return True, ""

    groups = group_steps(steps)
    state_id = 1  # initial state

    for i, (player_row, commands_row, executed_row) in enumerate(groups):
        stored_player = player_row["content"]
        stored_commands = commands_row["content"]

        # start_game sentinel — we should be at a state whose edge leads to 0
        if stored_commands == "start_game":
            if state_id == 0:
                return True, ""
            # Check the state we're at — the previous edge should have led to 0
            # Actually, we should already be at terminal 0 from the last edge
            return False, (
                f"Expected terminal state 0 (game_start) at step id={commands_row['id']}\n"
                f"  but current state_id={state_id}"
            )

        # Terminal check — we shouldn't be at a terminal and still have steps
        if state_id == 0 or state_id == -1:
            return False, (
                f"Reached terminal state {state_id} but still have steps at id={player_row['id']}\n"
                f"  stored: P={stored_player} cmds={stored_commands}"
            )

        # Get current state
        state = sm.get(state_id)
        if state is None:
            return False, (
                f"State {state_id} not found in state machine at step id={player_row['id']}"
            )

        # Verify player
        expected_player = f"P{state['player']}"
        if expected_player != stored_player:
            return False, (
                f"Player mismatch at step id={player_row['id']} (state {state_id})\n"
                f"  stored : {stored_player}\n"
                f"  sm     : {expected_player}"
            )

        # Verify commands
        sm_commands = ",".join(state["commands"])
        if sm_commands != stored_commands:
            return False, (
                f"Commands mismatch at step id={commands_row['id']} (state {state_id})\n"
                f"  stored : {stored_commands}\n"
                f"  sm     : {sm_commands}"
            )

        # Follow edge
        if executed_row:
            exec_parts = executed_row["content"].split(" ", 1)
            exec_idx = int(exec_parts[0])
            exec_label = exec_parts[1] if len(exec_parts) > 1 else ""

            edge = None
            for e in state["edges"]:
                if e["cmd_idx"] == exec_idx:
                    edge = e
                    break

            if edge is None:
                return False, (
                    f"No edge for cmd_idx={exec_idx} at step id={executed_row['id']} (state {state_id})\n"
                    f"  executed: {executed_row['content']}\n"
                    f"  available edges: {[e['cmd_idx'] for e in state['edges']]}"
                )

            if edge["cmd_label"] != exec_label:
                return False, (
                    f"Edge label mismatch at step id={executed_row['id']} (state {state_id})\n"
                    f"  stored : {exec_label}\n"
                    f"  sm edge: {edge['cmd_label']}"
                )

            state_id = edge["next_state_id"]

    # After all steps, we should be at terminal 0 or -1
    if state_id == 0 or state_id == -1:
        return True, ""

    return False, (
        f"Game ended without reaching a terminal state\n"
        f"  final state_id={state_id}"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    from sim_db import get_all_steps, get_game_ids

    sm = load_state_machine()
    print(f"Loaded state machine: {len(sm)} states\n")

    game_ids = get_game_ids()
    print(f"Verifying {len(game_ids)} games against state machine...\n")

    for game_id in game_ids:
        steps = get_all_steps(game_id)
        ok, err = verify_game(game_id, steps, sm)
        if ok:
            print(f"  {game_id}: OK")
        else:
            print(f"\n  {game_id}: MISMATCH")
            print(f"  {err}")
            return

    print("\nAll games verified OK.")


if __name__ == "__main__":
    main()
