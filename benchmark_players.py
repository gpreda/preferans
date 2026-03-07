"""Benchmark four players (Alice, Bob, Carol, Neural) over repeated trials.

Each trial plays 1000 games. In each game, 3 random players are chosen from the
pool of 4. After 1000 games, per-player mean scores are recorded. This is
repeated 10 times, and overall mean +/- stdev are reported.

Reports:
  - Per-player mean scores
  - Bid stats: how often each player bids (becomes declarer) and win rate
  - Bid breakdown by contract type (suit/betl/sans)
  - Follow stats: how often each player follows (whists) and win rate
"""

import os
import sys
import re
import random
import numpy as np

_this_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_this_dir, "server"))
os.chdir(os.path.join(_this_dir, "server"))

# Suppress debug prints from the engine during import and gameplay
import builtins
_real_print = builtins.print
def _quiet_print(*args, **kwargs):
    """Suppress prints unless they come from this benchmark script."""
    import inspect
    frame = inspect.currentframe().f_back
    caller_file = frame.f_code.co_filename if frame else ""
    if "benchmark_players" in caller_file:
        _real_print(*args, **kwargs)
builtins.print = _quiet_print

from PrefTestSingleGame import (
    PlayerAlice, PlayerBob, PlayerCarol, NeuralPlayer, Sim3000, make_simsim_cls,
    NoisyPlayer, play_game
)


PLAYERS_PER_GAME = 3
GAMES_PER_TRIAL = 500
NUM_TRIALS = 5
class _NeuralAggressive(NeuralPlayer):
    """NeuralPlayer with aggressiveness=1.0, instantiable with just a name."""
    def __init__(self, name="N-aggr", seed=None):
        super().__init__(name, seed=seed, aggressiveness=1.0)

PLAYER_NAMES = ["Alice", "Sim10-Alice", "Sim50-Alice", "S50S20a-A", "S20S10a-A"]


def _make_noisy_cls(noise, helper_cls):
    """Create a NoisyPlayer subclass usable as a Sim3000 helper_cls."""
    _n = noise
    _h = helper_cls
    class _NoisyHelper(NoisyPlayer):
        def __init__(self, name, seed=None):
            super().__init__(name, noise=_n, helper_cls=_h, seed=seed)
    return _NoisyHelper


def make_players(master_seed: int):
    """Create player strategy objects with deterministic seeds."""
    rng = random.Random(master_seed)
    seeds = [rng.randint(0, 10**9) for _ in range(7)]
    _S20aAlice = make_simsim_cls(num_simulations=20, helper_cls=PlayerAlice, adaptive=True)
    _S10aAlice = make_simsim_cls(num_simulations=10, helper_cls=PlayerAlice, adaptive=True)
    return {
        "Alice":        PlayerAlice(seed=seeds[0]),
        "Sim10-Alice":  Sim3000("Sim10-Alice", num_simulations=10, helper_cls=PlayerAlice, seed=seeds[1]),
        "Sim50-Alice":  Sim3000("Sim50-Alice", num_simulations=50, helper_cls=PlayerAlice, seed=seeds[2]),
        "S50S20a-A":    Sim3000("S50S20a-A", num_simulations=50, helper_cls=_S20aAlice, seed=seeds[3], adaptive=True),
        "S20S10a-A":    Sim3000("S20S10a-A", num_simulations=20, helper_cls=_S10aAlice, seed=seeds[4], adaptive=True),
    }


def parse_game(log_lines, name_map):
    """Parse emit'd log lines and extract game stats.

    Returns dict with:
      declarer: pool_name of declarer (or None for redeal)
      contract_type: "suit"/"betl"/"sans" (or None)
      declarer_won: bool
      followers: list of pool_names who followed (whisted)
      follower_scores: {pool_name: score} for followers
      scores: {pool_name: score} for all players
      is_redeal: bool
    """
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
        # Skip engine debug lines
        if line.startswith("["):
            continue

        # Auction winner
        m = re.search(r"Auction winner: P\d+ ([\w-]+)", line)
        if m:
            declarer_engine = m.group(1)
            result["declarer"] = name_map.get(declarer_engine, declarer_engine)

        # Contract type
        if line.startswith("Contract: "):
            ctype = line.split("Contract: ")[1].strip().split()[0].rstrip(",")
            result["contract_type"] = ctype

        # Whisting section — track follow/pass per player
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

        # Declarer result
        m_res = re.search(r"Declarer [\w-]+: \d+ tricks .* (WON|LOST)", line)
        if m_res:
            result["declarer_won"] = m_res.group(1) == "WON"

        # Redeal
        if "All passed" in line and "redeal" in line.lower():
            result["is_redeal"] = True

        # Per-player scores
        m_score = re.match(r"\s+P\d+ ([\w-]+): tricks=\d+, score=(-?[\d.]+)", line)
        if m_score:
            eng = m_score.group(1)
            score = float(m_score.group(2))
            pool_name = name_map.get(eng, eng)
            result["scores"][pool_name] = score

    return result


def run_trial(trial_idx, master_seed):
    """Play GAMES_PER_TRIAL games, return per-player stats."""
    rng = random.Random(master_seed)

    # Accumulators
    total_scores = {n: 0 for n in PLAYER_NAMES}
    game_counts = {n: 0 for n in PLAYER_NAMES}

    bid_counts = {n: 0 for n in PLAYER_NAMES}       # times as declarer
    bid_wins = {n: 0 for n in PLAYER_NAMES}          # times declarer won
    bid_suit = {n: 0 for n in PLAYER_NAMES}          # suit bids
    bid_suit_wins = {n: 0 for n in PLAYER_NAMES}
    bid_betl = {n: 0 for n in PLAYER_NAMES}          # betl bids
    bid_betl_wins = {n: 0 for n in PLAYER_NAMES}
    bid_sans = {n: 0 for n in PLAYER_NAMES}          # sans bids
    bid_sans_wins = {n: 0 for n in PLAYER_NAMES}

    follow_counts = {n: 0 for n in PLAYER_NAMES}     # times followed (whisted)
    follow_pos_score = {n: 0 for n in PLAYER_NAMES}  # total score when following

    move_times = {n: [] for n in PLAYER_NAMES}       # per-move durations (seconds)

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

        # Collect timing: game_timing keys are position names, map to pool names
        for eng_name, durations in game_timing.items():
            pool_name = name_map.get(eng_name, eng_name)
            move_times[pool_name].extend(durations)

        # Extract scores from compact log
        for line in compact_lines:
            if " score: " in line:
                parts = line.split(" score: ")
                engine_name = parts[0].strip()
                score = float(parts[1].strip())
                pool_name = name_map.get(engine_name, engine_name)
                total_scores[pool_name] += score
                game_counts[pool_name] += 1

        # Parse detailed stats
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
    """Format percentage, return 'n/a' if denom is 0."""
    return f"{100 * num / denom:.0f}%" if denom > 0 else "n/a"


def main():
    master_rng = random.Random(42)

    # Cumulative accumulators
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

        # Accumulate
        for key in cum:
            for name in PLAYER_NAMES:
                cum[key][name] += result[key][name]
        for name in PLAYER_NAMES:
            all_move_times[name].extend(result["move_times"][name])

        # Show running cumulative table
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

    # ================================================================
    # FINAL SUMMARY
    # ================================================================
    print("\n" + "=" * 80)
    print("FINAL RESULTS")
    print("=" * 80)
    if total_errors > 0:
        print(f"Total crashed games: {total_errors}")

    # --- Main table ---
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

    # --- Bid stats ---
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

    # --- Follow stats ---
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
