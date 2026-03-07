"""Data collection v2: Neural plays against Sim50-Alice and Alice.

Collects training data from ALL three players' decisions. This gives the
model exposure to strong expert play alongside its own exploration.

The --expert-only flag skips recording Neural's decisions (only experts).

Usage:
    python neural/collect_v2.py --num-games 10000 --output-dir neural/data/
"""

import sys
import os
import argparse
import random
import time
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# Import collect first — it patches builtins.print to suppress engine noise
from neural.collect import DataRecorder, DataCollectingPlayer, _real_print


def collect_data(num_games: int, output_dir: str, seed: int = 42,
                 expert_only: bool = False):
    """Play games with Neural vs Sim50-Alice + Alice, collect training data.

    Args:
        expert_only: If True, only collect from Sim50-Alice and Alice (not Neural).
    """
    from PrefTestSingleGame import PlayerAlice, Sim3000, NeuralPlayer, play_game

    recorder = DataRecorder()
    rng = random.Random(seed)

    completed = 0
    errors = 0
    t0 = time.time()

    for i in range(num_games):
        game_seed = rng.randint(1, 999999)

        # Create fresh players each game (resets internal state)
        players_raw = [
            NeuralPlayer("Neural", seed=game_seed, aggressiveness=0.5),
            Sim3000("Sim50-Alice", num_simulations=50,
                    helper_cls=PlayerAlice, seed=game_seed + 1),
            PlayerAlice(seed=game_seed + 2),
        ]

        # Rotate positions so each player gets each seat
        rotation = i % 6
        orders = [
            [0, 1, 2], [0, 2, 1], [1, 0, 2],
            [1, 2, 0], [2, 0, 1], [2, 1, 0],
        ]
        order = orders[rotation]

        # Wrap players with data collection
        # Position IDs are 1, 2, 3
        wrapped = {}
        for pos_idx, pid in enumerate([1, 2, 3]):
            player = players_raw[order[pos_idx]]
            is_neural = isinstance(player, NeuralPlayer)
            if expert_only and is_neural:
                # Don't collect from Neural, but still need it to play
                wrapped[pid] = player
            else:
                wrapped[pid] = DataCollectingPlayer(player, recorder, player_id=pid)

        recorder.start_game([1, 2, 3])
        try:
            _, _, _ = play_game(wrapped, seed=game_seed)
            completed += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                _real_print(f"  Game {i+1} error: {e}")
        recorder.end_game()

        if (i + 1) % 500 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed
            eta = (num_games - i - 1) / rate if rate > 0 else 0
            _real_print(f"  Progress: {i+1}/{num_games} games "
                        f"({completed} ok, {errors} errors) "
                        f"[{rate:.1f} games/s, ETA {eta/60:.1f}m]")

    elapsed = time.time() - t0
    _real_print(f"\nCollection complete: {completed}/{num_games} games "
                f"in {elapsed:.0f}s ({completed/elapsed:.1f} games/s)")
    _real_print(f"Saving data to {output_dir}...")
    recorder.save(output_dir)

    # Print bid label distribution
    bid_labels = [e[2] for e in recorder.bid_examples]
    if bid_labels:
        labels_arr = np.array(bid_labels)
        BID_TYPES = ['pass', 'game', 'in_hand', 'betl', 'sans']
        _real_print("\nBid distribution:")
        for idx, bt in enumerate(BID_TYPES):
            count = (labels_arr == idx).sum()
            _real_print(f"  {bt:10s}: {count:6d} ({100*count/len(labels_arr):.1f}%)")

    _real_print("Done!")


def main():
    parser = argparse.ArgumentParser(
        description="Collect training data: Neural vs Sim50-Alice + Alice")
    parser.add_argument("--num-games", type=int, default=10000,
                        help="Number of games to play (default: 10000)")
    parser.add_argument("--output-dir", type=str, default="neural/data/",
                        help="Output directory for .npz files (default: neural/data/)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed (default: 42)")
    parser.add_argument("--expert-only", action="store_true",
                        help="Only collect data from expert players, not Neural")
    args = parser.parse_args()
    collect_data(args.num_games, args.output_dir, args.seed, args.expert_only)


if __name__ == "__main__":
    main()
