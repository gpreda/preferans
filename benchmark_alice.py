"""Benchmark Alice variants with different probability thresholds.

Players:
  - Alice (default thresholds)
  - A-suit50  (suit_threshold=50%)
  - A-suit70  (suit_threshold=70%)
  - A-sans50  (sans_threshold=50%)
  - A-inH50   (in_hand_threshold=50%)
  - Bob
  - Carol
"""

import os
import sys
import re
import random
import numpy as np

_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_this_dir, "server"))
os.chdir(os.path.join(_this_dir, "server"))

import builtins
_real_print = builtins.print
def _quiet_print(*args, **kwargs):
    import inspect
    frame = inspect.currentframe().f_back
    caller_file = frame.f_code.co_filename if frame else ""
    if "benchmark_alice" in caller_file:
        _real_print(*args, **kwargs)
builtins.print = _quiet_print

from PrefTestSingleGame import PlayerAlice, PlayerBob, PlayerCarol, play_game


PLAYERS_PER_GAME = 3
GAMES_PER_TRIAL = 100
NUM_TRIALS = 3

PLAYER_NAMES = [
    "Alice",
    "A-suit50",
    "A-suit70",
    "A-sans50",
    "A-inH50",
    "Bob",
    "Carol",
]


def make_players(master_seed: int):
    rng = random.Random(master_seed)
    seeds = [rng.randint(0, 10**9) for _ in range(len(PLAYER_NAMES))]
    return {
        "Alice":    PlayerAlice(seed=seeds[0]),
        "A-suit50": PlayerAlice(seed=seeds[1], name="A-suit50", suit_threshold=0.50),
        "A-suit70": PlayerAlice(seed=seeds[2], name="A-suit70", suit_threshold=0.70),
        "A-sans50": PlayerAlice(seed=seeds[3], name="A-sans50", sans_threshold=0.50),
        "A-inH50":  PlayerAlice(seed=seeds[4], name="A-inH50", in_hand_threshold=0.50),
        "Bob":      PlayerBob(seed=seeds[5]),
        "Carol":    PlayerCarol(seed=seeds[6]),
    }


def parse_game(log_lines, name_map):
    result = {
        "declarer": None,
        "contract_type": None,
        "declarer_won": None,
        "followers": [],
        "scores": {},
        "is_redeal": False,
    }

    declarer_engine = None
    whist_section = False

    for line in log_lines:
        if line.startswith("["):
            continue

        m = re.search(r"Auction winner: P\d+ ([\w-]+)", line)
        if m:
            declarer_engine = m.group(1)
            result["declarer"] = name_map.get(declarer_engine, declarer_engine)

        if line.startswith("Contract: "):
            ctype = line.split("Contract: ")[1].strip().split()[0].rstrip(",")
            result["contract_type"] = ctype

        if "--- Whisting ---" in line:
            whist_section = True
            continue
        if whist_section and not line.startswith("  =>"):
            m_whist = re.match(r"\s+P\d+ ([\w-]+): (follow|pass|start_game)", line)
            if m_whist:
                eng_name = m_whist.group(1)
                action = m_whist.group(2)
                if action == "follow":
                    pool_name = name_map.get(eng_name, eng_name)
                    result["followers"].append(pool_name)
        if "--- Playing" in line or "--- Scoring" in line:
            whist_section = False

        m_res = re.search(r"Declarer [\w-]+: \d+ tricks .* (WON|LOST)", line)
        if m_res:
            result["declarer_won"] = m_res.group(1) == "WON"

        if "All passed" in line and "redeal" in line.lower():
            result["is_redeal"] = True

        m_score = re.match(r"\s+P\d+ ([\w-]+): tricks=\d+, score=(-?[\d.]+)", line)
        if m_score:
            eng = m_score.group(1)
            score = float(m_score.group(2))
            pool_name = name_map.get(eng, eng)
            result["scores"][pool_name] = score

    return result


def run_trial(trial_idx, master_seed):
    rng = random.Random(master_seed)

    total_scores = {n: 0 for n in PLAYER_NAMES}
    game_counts = {n: 0 for n in PLAYER_NAMES}

    bid_counts = {n: 0 for n in PLAYER_NAMES}
    bid_wins = {n: 0 for n in PLAYER_NAMES}
    bid_suit = {n: 0 for n in PLAYER_NAMES}
    bid_suit_wins = {n: 0 for n in PLAYER_NAMES}
    bid_betl = {n: 0 for n in PLAYER_NAMES}
    bid_betl_wins = {n: 0 for n in PLAYER_NAMES}
    bid_sans = {n: 0 for n in PLAYER_NAMES}
    bid_sans_wins = {n: 0 for n in PLAYER_NAMES}

    follow_counts = {n: 0 for n in PLAYER_NAMES}
    follow_pos_score = {n: 0 for n in PLAYER_NAMES}

    move_times = {n: [] for n in PLAYER_NAMES}

    errors = 0

    for g in range(GAMES_PER_TRIAL):
        game_seed = rng.randint(0, 10**9)
        chosen_names = rng.sample(PLAYER_NAMES, PLAYERS_PER_GAME)
        players = make_players(game_seed)

        strategies = {}
        position_names = ["Alice", "Bob", "Carol"]
        name_map = {}
        for i, pool_name in enumerate(chosen_names):
            strategies[i + 1] = players[pool_name]
            name_map[position_names[i]] = pool_name

        try:
            log_lines, compact_lines, game_timing = play_game(strategies, seed=game_seed)
        except Exception:
            errors += 1
            continue

        for eng_name, durations in game_timing.items():
            pool_name = name_map.get(eng_name, eng_name)
            move_times[pool_name].extend(durations)

        for line in compact_lines:
            if " score: " in line:
                parts = line.split(" score: ")
                engine_name = parts[0].strip()
                score = float(parts[1].strip())
                pool_name = name_map.get(engine_name, engine_name)
                total_scores[pool_name] += score
                game_counts[pool_name] += 1

        info = parse_game(log_lines, name_map)
        if info["is_redeal"]:
            continue

        decl = info["declarer"]
        ctype = info["contract_type"]
        won = info["declarer_won"]

        if decl:
            bid_counts[decl] += 1
            if won:
                bid_wins[decl] += 1
            if ctype == "suit":
                bid_suit[decl] += 1
                if won:
                    bid_suit_wins[decl] += 1
            elif ctype == "betl":
                bid_betl[decl] += 1
                if won:
                    bid_betl_wins[decl] += 1
            elif ctype == "sans":
                bid_sans[decl] += 1
                if won:
                    bid_sans_wins[decl] += 1

        for f_name in info["followers"]:
            follow_counts[f_name] += 1
            if f_name in info["scores"]:
                follow_pos_score[f_name] += info["scores"][f_name]

    return {
        "scores": total_scores,
        "game_counts": game_counts,
        "bid_counts": bid_counts,
        "bid_wins": bid_wins,
        "bid_suit": bid_suit,
        "bid_suit_wins": bid_suit_wins,
        "bid_betl": bid_betl,
        "bid_betl_wins": bid_betl_wins,
        "bid_sans": bid_sans,
        "bid_sans_wins": bid_sans_wins,
        "follow_counts": follow_counts,
        "follow_pos_score": follow_pos_score,
        "move_times": move_times,
        "errors": errors,
    }


def pct(num, denom):
    return f"{100 * num / denom:.0f}%" if denom > 0 else "n/a"


def main():
    master_rng = random.Random(42)

    cum = {
        key: {n: 0 for n in PLAYER_NAMES}
        for key in [
            "scores", "game_counts",
            "bid_counts", "bid_wins",
            "bid_suit", "bid_suit_wins",
            "bid_betl", "bid_betl_wins",
            "bid_sans", "bid_sans_wins",
            "follow_counts", "follow_pos_score",
        ]
    }
    all_means = {n: [] for n in PLAYER_NAMES}
    all_move_times = {n: [] for n in PLAYER_NAMES}
    total_errors = 0

    total_games = GAMES_PER_TRIAL * NUM_TRIALS
    print(f"Benchmark: {len(PLAYER_NAMES)} players, {GAMES_PER_TRIAL} games/trial, "
          f"{NUM_TRIALS} trials ({total_games} total)")
    print(f"Each game picks 3 random players from the pool of {len(PLAYER_NAMES)}")
    print("=" * 80)

    for t in range(NUM_TRIALS):
        trial_seed = master_rng.randint(0, 10**9)
        result = run_trial(t, trial_seed)
        total_errors += result["errors"]

        print(f"\n--- Trial {t+1}/{NUM_TRIALS} (seed={trial_seed}) ---")
        if result["errors"] > 0:
            print(f"  ({result['errors']} games crashed)")

        for name in PLAYER_NAMES:
            n_games = result["game_counts"][name]
            mean = result["scores"][name] / n_games if n_games > 0 else 0.0
            all_means[name].append(mean)

        for key in cum:
            for name in PLAYER_NAMES:
                cum[key][name] += result[key][name]
        for name in PLAYER_NAMES:
            all_move_times[name].extend(result["move_times"][name])

        print(f"  {'Player':>14s}  {'Mean':>8s}  {'Stdev':>7s}  {'Games':>6s}  {'ms/move':>8s}")
        print(f"  {'-'*49}")
        for name in PLAYER_NAMES:
            arr = np.array(all_means[name])
            m = arr.mean()
            s = arr.std(ddof=1) if len(arr) > 1 else 0.0
            gc = cum["game_counts"][name]
            times = all_move_times[name]
            ms = f"{np.mean(times)*1000:.1f}" if times else "n/a"
            print(f"  {name:>14s}  {m:+8.1f}  {s:7.1f}  {gc:6d}  {ms:>8s}")

    # FINAL SUMMARY
    print("\n" + "=" * 80)
    print("FINAL RESULTS")
    print("=" * 80)
    if total_errors > 0:
        print(f"Total crashed games: {total_errors}")

    print(f"\n{'Player':>14s}  {'Mean':>8s}  {'Stdev':>7s}  {'Games':>6s}  {'ms/move':>8s}")
    print("-" * 51)
    for name in PLAYER_NAMES:
        arr = np.array(all_means[name])
        m = arr.mean()
        s = arr.std(ddof=1)
        gc = cum["game_counts"][name]
        times = all_move_times[name]
        ms = f"{np.mean(times)*1000:.2f}" if times else "n/a"
        print(f"{name:>14s}  {m:+8.1f}  {s:7.1f}  {gc:6d}  {ms:>8s}")

    # BID STATS
    print(f"\n{'':=<80}")
    print("BID STATS (as declarer)")
    print(f"{'':=<80}")
    print(f"{'Player':>14s}  {'Bids':>6s}  {'Won':>5s}  {'Win%':>6s}  "
          f"{'Suit':>5s}  {'S.W%':>5s}  "
          f"{'Betl':>5s}  {'B.W%':>5s}  "
          f"{'Sans':>5s}  {'X.W%':>5s}")
    print("-" * 78)
    for name in PLAYER_NAMES:
        b = cum["bid_counts"][name]
        bw = cum["bid_wins"][name]
        bs = cum["bid_suit"][name]
        bsw = cum["bid_suit_wins"][name]
        bb = cum["bid_betl"][name]
        bbw = cum["bid_betl_wins"][name]
        bx = cum["bid_sans"][name]
        bxw = cum["bid_sans_wins"][name]
        print(f"{name:>14s}  {b:6d}  {bw:5d}  {pct(bw, b):>6s}  "
              f"{bs:5d}  {pct(bsw, bs):>5s}  "
              f"{bb:5d}  {pct(bbw, bb):>5s}  "
              f"{bx:5d}  {pct(bxw, bx):>5s}")

    # FOLLOW STATS
    print(f"\n{'':=<80}")
    print("FOLLOW STATS (whisting as defender)")
    print(f"{'':=<80}")
    print(f"{'Player':>14s}  {'Games':>6s}  {'Follow':>7s}  {'Fol%':>6s}  "
          f"{'FolScore':>9s}  {'Avg':>7s}")
    print("-" * 58)
    for name in PLAYER_NAMES:
        gc = cum["game_counts"][name]
        bc = cum["bid_counts"][name]
        defender_opps = gc - bc
        fc = cum["follow_counts"][name]
        fs = cum["follow_pos_score"][name]
        avg = f"{fs / fc:+.1f}" if fc > 0 else "n/a"
        print(f"{name:>14s}  {gc:6d}  {fc:7d}  {pct(fc, defender_opps):>6s}  "
              f"{fs:+9.0f}  {avg:>7s}")


if __name__ == "__main__":
    main()
