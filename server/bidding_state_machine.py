"""Build a full pre-play state machine via BFS using the engine service API.

Covers every phase from auction through to game start or game end:
  auction → exchange (discard + contract) → whisting → game_start / game_end

State identity: (player_position, context, tuple_of_commands)
  context disambiguates same-looking states from different game paths.

Terminal states:
  state_id 0: game_start  (playing phase reached — cards are about to be played)
  state_id -1: game_end   (redeal / no followers — round ends without card play)

Output: bidding_state_machine.json in the same directory.

The engine service must be running at ENGINE_URL (default http://localhost:3001).

Exchange discard steps (numbered card slots) are collapsed into a single
"discard" command since the specific cards are hand-dependent but the state
machine transition is always the same.
"""

import json
import os
import requests
from collections import deque

ENGINE_URL = os.environ.get("ENGINE_URL", "http://localhost:3001")


# ---------------------------------------------------------------------------
# Engine service helpers
# ---------------------------------------------------------------------------

def api_new_game() -> str:
    r = requests.post(f"{ENGINE_URL}/new-game",
                      json={"players": ["P1", "P2", "P3"]}, timeout=5)
    r.raise_for_status()
    return r.json()["game_id"]


def api_commands(game_id: str) -> dict:
    r = requests.get(f"{ENGINE_URL}/commands",
                     params={"game_id": game_id}, timeout=5)
    r.raise_for_status()
    return r.json()


def api_execute(game_id: str, command_id: int) -> None:
    r = requests.post(f"{ENGINE_URL}/execute",
                      json={"game_id": game_id, "command_id": command_id},
                      timeout=5)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"Execute error: {data['error']}")


def _is_card_cmd(cmd: str) -> bool:
    return any(s in cmd for s in ("♠", "♦", "♣", "♥"))


def _is_exchange_discard(phase: str, commands: list) -> bool:
    return phase == "exchanging" and commands and commands[0].isdigit()


# ---------------------------------------------------------------------------
# Path replay
# ---------------------------------------------------------------------------

def _replay(game_id: str, path: list) -> None:
    """Replay a path of steps on a fresh game.

    Each step is either an int (1-based command index) or 'discard'.
    """
    for step in path:
        if step == "discard":
            api_execute(game_id, 1)
            api_commands(game_id)  # fetch state for second discard pick
            api_execute(game_id, 1)
        else:
            api_execute(game_id, step)


# ---------------------------------------------------------------------------
# BFS state machine builder
# ---------------------------------------------------------------------------

STATE_GAME_START = 0
STATE_GAME_END = -1


def _observe(game_id: str) -> tuple:
    """Get the engine state and classify it.

    Returns (phase, player_pos, context_pos, commands, terminal_id_or_None).
    context_pos disambiguates states that look the same but differ by game path:
      - auction: highest_bidder_position (who will win if everyone passes)
      - exchanging/whisting: declarer_position
      - other phases: None
    """
    d = api_commands(game_id)
    cmds = d.get("commands", [])
    player_pos = d.get("player_position")
    phase = d.get("phase")
    if not cmds or player_pos is None:
        return phase, player_pos, None, cmds, STATE_GAME_END
    if phase == "playing" and cmds and _is_card_cmd(cmds[0]):
        return phase, player_pos, None, cmds, STATE_GAME_START
    if phase == "scoring":
        return phase, player_pos, None, cmds, STATE_GAME_END
    # Context for state disambiguation — combines multiple signals
    # into a single hashable value for the state key
    if phase == "exchanging":
        # Exchange states depend on declarer AND bid level (contract options differ)
        ctx = (d.get("declarer_position"), d.get("bid_level"))
    elif phase == "whisting":
        # Whist states depend on declarer, contract type, and current declarations
        # (declaration order + contract type affect counter sub-phase logic)
        decls = d.get("whist_declarations", {})
        decls_frozen = tuple(sorted(decls.items()))
        ctx = (d.get("declarer_position"), d.get("contract_type"), decls_frozen)
    elif phase == "auction":
        passed = tuple(d.get("passed_positions", []))
        ctx = (d.get("highest_bidder_position"), passed)
    else:
        ctx = None
    return phase, player_pos, ctx, cmds, None


def build_state_machine() -> list[dict]:
    """Return a list of state dicts covering auction, exchange, and whisting."""

    state_registry: dict[tuple, int] = {}
    states: dict[int, dict] = {}
    next_id = 1

    def get_or_create(phase: str, player_pos: int, ctx, cmds: list) -> tuple[int, bool]:
        nonlocal next_id
        key = (player_pos, ctx, tuple(cmds))
        if key in state_registry:
            return state_registry[key], False
        sid = next_id
        next_id += 1
        state_registry[key] = sid
        state_dict = {
            "state_id": sid,
            "player": player_pos,
            "phase": phase,
            "commands": list(cmds),
            "edges": [],
        }
        if ctx is not None:
            state_dict["context"] = ctx
        states[sid] = state_dict
        return sid, True

    # ── Bootstrap ────────────────────────────────────────────────────────
    game_id = api_new_game()
    d = api_commands(game_id)
    cmds = d.get("commands", [])
    player_pos = d.get("player_position")
    phase = d.get("phase")
    if not cmds or player_pos is None:
        raise RuntimeError(f"Initial state has no commands (phase={phase!r})")

    # Initial auction state has no highest bidder yet
    init_sid, _ = get_or_create(phase, player_pos, None, cmds)
    init_key = (player_pos, None, tuple(cmds))

    # BFS queue: (path, state_key)
    queue: deque = deque()
    queue.append(([], init_key))
    enqueued: set = {init_key}

    MAX_STATES = 10_000
    explored = 0

    while queue:
        path, state_key = queue.popleft()
        state_id = state_registry[state_key]
        s_player, s_ctx, s_cmds_tuple = state_key
        commands = list(s_cmds_tuple)
        explored += 1
        print(
            f"\rState {state_id:4d} | explored {explored:4d} | queue {len(queue):4d} | total {next_id-1:4d}",
            end="", flush=True,
        )

        if next_id - 1 > MAX_STATES:
            print(f"\nSafety limit reached: {MAX_STATES} states. Stopping BFS.")
            break

        # Build the list of actions to try from this state.
        # Each action is (cmd_idx, cmd_label, path_step) where path_step
        # is what to append to the path for replay.
        if commands == ["discard"]:
            # Discard state: single action
            actions = [(1, "discard", "discard")]
        else:
            actions = [(i, cmd, i) for i, cmd in enumerate(commands, 1)]

        for cmd_idx, cmd_label, path_step in actions:
            gid = api_new_game()
            _replay(gid, path)

            if path_step == "discard":
                api_execute(gid, 1)
                api_commands(gid)
                api_execute(gid, 1)
            else:
                api_execute(gid, cmd_idx)

            new_phase, new_player, new_ctx, new_cmds, terminal = _observe(gid)
            new_path = path + [path_step]

            if terminal is not None:
                next_state_id = terminal
            elif _is_exchange_discard(new_phase, new_cmds):
                # Collapse exchange discard into a canonical "discard" state
                display_cmds = ["discard"]
                new_sid, _ = get_or_create(new_phase, new_player, new_ctx, display_cmds)
                next_state_id = new_sid
                new_key = (new_player, new_ctx, tuple(display_cmds))
                if new_key not in enqueued:
                    enqueued.add(new_key)
                    queue.append((new_path, new_key))
            else:
                new_sid, _ = get_or_create(new_phase, new_player, new_ctx, new_cmds)
                next_state_id = new_sid
                new_key = (new_player, new_ctx, tuple(new_cmds))
                if new_key not in enqueued:
                    enqueued.add(new_key)
                    queue.append((new_path, new_key))

            states[state_id]["edges"].append({
                "cmd_idx": cmd_idx,
                "cmd_label": cmd_label,
                "next_state_id": next_state_id,
            })

    print(f"\nDone — {next_id - 1} states, {explored} explored.")
    return list(states.values())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    print("Building state machine via BFS (auction → exchange → whisting)…")
    machine = build_state_machine()

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "bidding_state_machine.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(machine, f, ensure_ascii=False, indent=2)
    print(f"Written {len(machine)} states → {out_path}")


if __name__ == "__main__":
    main()
