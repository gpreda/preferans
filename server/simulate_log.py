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


def api_commands(game_id: str) -> dict:
    """GET /commands → full response dict."""
    r = requests.get(f"{ENGINE_URL}/commands",
                     params={"game_id": game_id}, timeout=5)
    r.raise_for_status()
    return r.json()


def api_execute(game_id: str, command_id: int) -> None:
    """POST /execute with 1-based command_id."""
    r = requests.post(f"{ENGINE_URL}/execute",
                      json={"game_id": game_id, "command_id": command_id},
                      timeout=5)
    r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"Execute error: {data['error']}")


def api_hand(game_id: str, player: int) -> list:
    """GET /hand → list of card IDs for the player."""
    r = requests.get(f"{ENGINE_URL}/hand",
                     params={"game_id": game_id, "player": player}, timeout=5)
    r.raise_for_status()
    return r.json().get("cards", [])


def api_original_talon(game_id: str) -> list:
    """GET /original-talon → list of card IDs from the revealed talon."""
    r = requests.get(f"{ENGINE_URL}/original-talon",
                     params={"game_id": game_id}, timeout=5)
    r.raise_for_status()
    return r.json().get("cards", [])


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
        elif cmd in ("2", "3", "4", "5") or cmd.startswith("in_hand "):
            w = W_GAME
        elif cmd in ("Hand", "in_hand"):
            w = W_IN_HAND
        elif cmd == "Betl":
            w = W_BETL
        elif cmd == "Sans":
            w = W_SANS
        else:
            w = W_GAME
        weights.append(w)
    return rng.choices(range(1, len(commands) + 1), weights=weights, k=1)[0]


def _parse_hand_by_suit(hand: list) -> dict:
    """Parse card IDs into {suit: set(rank_str)}."""
    suits = {}
    for card_id in hand:
        parts = card_id.split("_", 1)
        if len(parts) == 2:
            suits.setdefault(parts[1], set()).add(parts[0])
    return suits


def _count_trump_tricks(trump_ranks: set, trump_count: int) -> int:
    """Count guaranteed trump tricks.

    1-trick: A, Kx, Dxx (10xx), Jxxx
    2-trick: AK, AD (A+10), AJxe (AJ+2x), KJx, DJxx (10+J+xx)
    """
    tricks = 0
    has_A = "A" in trump_ranks
    has_K = "K" in trump_ranks
    has_D = "10" in trump_ranks
    has_J = "J" in trump_ranks

    # 2-trick combinations (check first)
    if has_A and has_K:
        return 2
    if has_A and has_D:
        return 2
    if has_A and has_J and trump_count >= 4:
        return 2
    if has_K and has_J and trump_count >= 3:
        return 2
    if has_D and has_J and trump_count >= 4:
        return 2

    # 1-trick combinations
    if has_A:
        tricks = 1
    elif has_K and trump_count >= 2:
        tricks = 1
    elif has_D and trump_count >= 3:
        tricks = 1
    elif has_J and trump_count >= 4:
        tricks = 1

    return tricks


def _suit_reason(ranks: set, count: int, is_trump: bool) -> float:
    """Compute reason strength for a non-trump suit.

    Strong (1.0): A with <5 cards, Kx with <4 cards
    Medium (0.5): A with <6, Kx with <5, Dxx (10xx) with <4
    Weak (0.25): A/Kx/Dxx any count, or 3 trump cards without safe trick
    """
    has_A = "A" in ranks
    has_K = "K" in ranks
    has_D = "10" in ranks

    # Strong
    if has_A and count < 5:
        return 1.0
    if has_K and count >= 2 and count < 4:
        return 1.0

    # Medium
    if has_A and count < 6:
        return 0.5
    if has_K and count >= 2 and count < 5:
        return 0.5
    if has_D and count >= 3 and count < 4:
        return 0.5

    # Weak
    if has_A:
        return 0.25
    if has_K and count >= 2:
        return 0.25
    if has_D and count >= 3:
        return 0.25

    return 0.0


def _compute_follow_stats(hand: list, trump: str) -> tuple:
    """Compute (num_trump_tricks, sum_reasons) for follow decision.

    Returns (int, float).
    """
    by_suit = _parse_hand_by_suit(hand)

    trump_ranks = by_suit.get(trump, set())
    trump_count = len(trump_ranks)
    num_trump_tricks = _count_trump_tricks(trump_ranks, trump_count)

    sum_reasons = 0.0
    for suit, ranks in by_suit.items():
        if suit == trump:
            continue
        reason = _suit_reason(ranks, len(ranks), False)
        sum_reasons += reason

    # Weak reason: 3+ trump cards without a safe trick (no 1+ trick)
    if trump_count >= 3 and num_trump_tricks == 0:
        sum_reasons += 0.25

    return num_trump_tricks, sum_reasons


def _boost_for_talon(hand: list, talon: list, trump: str) -> float:
    """For each revealed talon card where the player has a reason in that suit,
    increase by 0.25. Only considers non-trump suits."""
    by_suit = _parse_hand_by_suit(hand)
    boost = 0.0
    for card_id in talon:
        parts = card_id.split("_", 1)
        if len(parts) != 2:
            continue
        suit = parts[1]
        if suit == trump:
            continue
        ranks = by_suit.get(suit, set())
        if not ranks:
            continue
        reason = _suit_reason(ranks, len(ranks), False)
        if reason > 0:
            boost += 0.25
    return boost


def _should_follow(hand: list, trump: str, talon: list = None,
                   is_aggressive: bool = False, first_defender_passed: bool = False) -> bool:
    """Determine if the player should follow based on hand analysis.

    Rules:
    1. Any player: follow if trump_tricks >= 2 OR sum_reasons >= 3
       OR (trump_tricks == 1 AND sum_reasons >= 2)
    2. Aggressive: also follow if sum_reasons >= 2.5
    3. Aggressive + first defender passed: also follow if sum_reasons >= 2
    """
    num_trump_tricks, sum_reasons = _compute_follow_stats(hand, trump)

    if talon:
        sum_reasons += _boost_for_talon(hand, talon, trump)

    if num_trump_tricks >= 2:
        return True
    if sum_reasons >= 3:
        return True
    if num_trump_tricks >= 1 and sum_reasons >= 2:
        return True
    if is_aggressive and sum_reasons >= 2.5:
        return True
    if is_aggressive and first_defender_passed and sum_reasons >= 2:
        return True

    return False


def choose_whist_cmd(rng, commands: list, hand: list = None, trump: str = None,
                     talon: list = None, is_aggressive: bool = False,
                     first_defender_passed: bool = False) -> int:
    """Return 1-based index for whisting commands with hand-based heuristics."""
    amap = {cmd: i + 1 for i, cmd in enumerate(commands)}

    if "Double counter" in amap:
        return amap["Double counter"] if rng.random() < PROB_DOUBLE_COUNTER else amap["Start game"]

    if "Start game" in amap and "Counter" in amap:
        return amap["Counter"] if rng.random() < PROB_COUNTER else amap["Start game"]

    # Heuristic-based follow decision
    if hand and trump and "Follow" in amap:
        if _should_follow(hand, trump, talon, is_aggressive, first_defender_passed):
            return amap["Follow"]

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

        cmd_resp = api_commands(engine_id)
        commands = cmd_resp.get("commands", [])
        player_pos = cmd_resp.get("player_position")
        phase = cmd_resp.get("phase")

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
            commands2 = api_commands(engine_id).get("commands", [])
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
            trump = cmd_resp.get("trump")
            hand = api_hand(engine_id, player_pos) if trump else None
            talon = api_original_talon(engine_id) if trump else None
            is_aggressive = (player_pos == 1)  # Alice = P1
            whist_decls = cmd_resp.get("whist_declarations", {})
            first_passed = any(v == "pass" for v in whist_decls.values())
            idx = choose_whist_cmd(rng, commands, hand=hand, trump=trump,
                                   talon=talon, is_aggressive=is_aggressive,
                                   first_defender_passed=first_passed)
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
