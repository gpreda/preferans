"""REINFORCE self-play training for PrefNet.

Three RLNeuralPlayer instances (sharing the same model) play against each other.
Each game produces a score for each player, used as the reward signal for
policy gradient updates.

Usage:
    python neural/self_play.py [--episodes N] [--lr LR] [--temperature T] ...
"""

import os
import sys
import random
import argparse
import time

import torch
import torch.nn.functional as F
import numpy as np

_repo_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, _repo_root)
sys.path.insert(0, os.path.join(_repo_root, "server"))

# Suppress engine prints
import builtins
_real_print = builtins.print
def _quiet_print(*args, **kwargs):
    import inspect
    frame = inspect.currentframe().f_back
    caller_file = frame.f_code.co_filename if frame else ""
    if "self_play" in caller_file:
        _real_print(*args, **kwargs)
builtins.print = _quiet_print

from neural.model import PrefNet
from neural.rl_player import RLNeuralPlayer
from PrefTestSingleGame import play_game, PlayerAlice, PlayerBob, PlayerCarol


POSITION_NAMES = ["Alice", "Bob", "Carol"]


def parse_scores(compact_lines):
    """Extract scores from compact log lines.

    Returns dict like {"Alice": 10, "Bob": -5, "Carol": -5}.
    """
    scores = {}
    for line in compact_lines:
        if " score: " in line:
            parts = line.split(" score: ")
            name = parts[0].strip()
            score = int(parts[1].strip())
            scores[name] = score
    return scores


def is_redeal(scores):
    """Check if all scores are zero (redeal — everyone passed)."""
    return all(v == 0 for v in scores.values())


def evaluate(model, num_games=100, seed=42):
    """Evaluate current model against heuristic players.

    Plays num_games games of Neural vs Alice+Bob+Carol (3 chosen randomly
    from pool of 4 each game). Returns mean score for Neural.
    """
    from PrefTestSingleGame import NeuralPlayer

    rng = random.Random(seed)
    total_score = 0
    game_count = 0
    player_names = ["Alice", "Bob", "Carol", "Neural"]

    # Save model to temp file for NeuralPlayer to load
    tmp_path = "neural/models/_eval_tmp.pt"
    torch.save(model.state_dict(), tmp_path)

    for g in range(num_games):
        game_seed = rng.randint(0, 10**9)
        chosen = rng.sample(player_names, 3)

        players_map = {
            "Alice": PlayerAlice(seed=game_seed),
            "Bob": PlayerBob(seed=game_seed + 1),
            "Carol": PlayerCarol(seed=game_seed + 2),
            "Neural": NeuralPlayer("Neural", seed=game_seed + 3,
                                   model_path=tmp_path, temperature=0.0),
        }

        strategies = {}
        name_map = {}
        for i, pool_name in enumerate(chosen):
            pos = i + 1
            strategies[pos] = players_map[pool_name]
            name_map[POSITION_NAMES[i]] = pool_name

        try:
            random.seed(game_seed)
            _, compact = play_game(strategies, seed=game_seed)
            scores = parse_scores(compact)

            for engine_name, score in scores.items():
                pool_name = name_map.get(engine_name, engine_name)
                if pool_name == "Neural":
                    total_score += score
                    game_count += 1
        except Exception:
            pass

    # Clean up temp file
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    return total_score / max(game_count, 1), game_count


class RunningStats:
    """Welford's online algorithm for running mean and variance."""

    def __init__(self):
        self.n = 0
        self.mean = 0.0
        self.M2 = 0.0

    def update(self, x):
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        delta2 = x - self.mean
        self.M2 += delta * delta2

    @property
    def std(self):
        if self.n < 2:
            return 1.0
        return max((self.M2 / (self.n - 1)) ** 0.5, 1e-6)

    def normalize(self, x):
        return (x - self.mean) / self.std


def self_play_train(
    model_path="neural/models/pref_net.pt",
    output_path="neural/models/pref_net_rl.pt",
    num_episodes=50000,
    lr=1e-4,
    gamma=1.0,
    entropy_coeff=0.01,
    reward_clip=3.0,
    save_every=1000,
    eval_every=500,
    eval_games=100,
    temperature=0.5,
    temp_end=0.1,
):
    """Main REINFORCE self-play training loop."""

    # Load model (start from IL checkpoint)
    model = PrefNet()
    if os.path.exists(model_path):
        model.load_state_dict(torch.load(model_path, map_location="cpu",
                                          weights_only=True))
        print(f"Loaded IL checkpoint from {model_path}")
    else:
        print(f"WARNING: No checkpoint at {model_path}, starting from scratch")
    model.train()

    optimizer = torch.optim.Adam(model.parameters(), lr=lr)

    # Running stats for reward normalization (Welford's algorithm)
    reward_stats = RunningStats()

    # Best eval score tracking
    best_eval_score = float('-inf')

    # Training stats
    rng = random.Random(42)
    episode_rewards = []
    episode_losses = []
    episode_grad_norms = []
    redeals = 0
    errors = 0

    os.makedirs("neural/models", exist_ok=True)

    print(f"REINFORCE self-play training")
    print(f"  Episodes: {num_episodes}")
    print(f"  LR: {lr}")
    print(f"  Temperature: {temperature} → {temp_end}")
    print(f"  Entropy coeff: {entropy_coeff}")
    print(f"  Reward clip: ±{reward_clip}")
    print(f"  Eval every: {eval_every} episodes ({eval_games} games)")
    print(f"  Save every: {save_every} episodes")
    print(f"  Model params: {model.param_count()}")
    print(f"  Output: {output_path}")
    print(f"")

    t_start = time.time()

    for episode in range(num_episodes):
        # Anneal temperature linearly
        progress = episode / max(num_episodes - 1, 1)
        current_temp = temperature + (temp_end - temperature) * progress

        # Create 3 RLNeuralPlayer instances sharing the same model
        players = {}
        for pid in [1, 2, 3]:
            p = RLNeuralPlayer(model, temperature=current_temp,
                               name=POSITION_NAMES[pid - 1])
            p.reset_trajectory()
            players[pid] = p

        # Play one game
        game_seed = rng.randint(0, 10**9)
        try:
            random.seed(game_seed)
            _, compact = play_game(players, seed=game_seed)
            scores = parse_scores(compact)
        except Exception as e:
            errors += 1
            if errors <= 5:
                _real_print(f"  [Episode {episode}] Game error: {e}")
            continue

        # Skip redeals (no signal)
        if is_redeal(scores):
            redeals += 1
            continue

        # Compute centered rewards (zero-sum)
        mean_score = sum(scores.values()) / 3
        centered = {name: score - mean_score for name, score in scores.items()}

        # Update running stats with each player's centered reward
        for r in centered.values():
            reward_stats.update(r)

        # Normalize and clip rewards
        rewards = {}
        for name, r in centered.items():
            normed = reward_stats.normalize(r)
            rewards[name] = max(-reward_clip, min(reward_clip, normed))

        # Compute REINFORCE loss
        policy_loss = torch.tensor(0.0)
        entropy_loss = torch.tensor(0.0)
        total_steps = 0

        for pid in [1, 2, 3]:
            player_name = POSITION_NAMES[pid - 1]
            R = rewards[player_name]

            for head_name, log_prob, ent in players[pid].trajectory:
                policy_loss = policy_loss - log_prob * R
                entropy_loss = entropy_loss - ent
                total_steps += 1

        if total_steps == 0:
            continue

        # Normalize by number of steps
        loss = (policy_loss + entropy_coeff * entropy_loss) / total_steps

        optimizer.zero_grad()
        loss.backward()
        grad_norm = torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()

        # Track stats
        episode_rewards.append(mean_score)
        episode_losses.append(loss.item())
        episode_grad_norms.append(grad_norm.item() if hasattr(grad_norm, 'item') else grad_norm)

        # Periodic logging
        if (episode + 1) % 100 == 0:
            recent_rewards = episode_rewards[-100:]
            recent_losses = episode_losses[-100:]
            recent_gnorms = episode_grad_norms[-100:]
            elapsed = time.time() - t_start
            eps_per_sec = (episode + 1) / elapsed

            print(f"Episode {episode + 1}/{num_episodes}  "
                  f"reward={np.mean(recent_rewards):+.1f}  "
                  f"loss={np.mean(recent_losses):.4f}  "
                  f"gnorm={np.mean(recent_gnorms):.3f}  "
                  f"temp={current_temp:.3f}  "
                  f"rstd={reward_stats.std:.1f}  "
                  f"redeals={redeals}  errors={errors}  "
                  f"eps/s={eps_per_sec:.1f}")

        # Periodic checkpointing
        if (episode + 1) % save_every == 0:
            ckpt_path = f"neural/models/pref_net_rl_ep{episode + 1}.pt"
            torch.save(model.state_dict(), ckpt_path)
            print(f"  Checkpoint saved: {ckpt_path}")

        # Periodic evaluation
        if (episode + 1) % eval_every == 0:
            model.eval()
            eval_score, eval_count = evaluate(model, num_games=eval_games,
                                               seed=episode)
            model.train()

            improved = eval_score > best_eval_score
            if improved:
                best_eval_score = eval_score
                torch.save(model.state_dict(), output_path)

            print(f"  Eval ({eval_count} games): Neural mean={eval_score:+.1f}  "
                  f"best={best_eval_score:+.1f}  "
                  f"{'*** NEW BEST ***' if improved else ''}")

    # Final save
    final_path = output_path.replace(".pt", "_final.pt")
    torch.save(model.state_dict(), final_path)

    elapsed = time.time() - t_start
    print(f"\nTraining complete in {elapsed / 3600:.1f} hours")
    print(f"  Total episodes: {num_episodes}")
    print(f"  Redeals skipped: {redeals}")
    print(f"  Errors: {errors}")
    print(f"  Best eval score: {best_eval_score:+.1f}")
    print(f"  Best model: {output_path}")
    print(f"  Final model: {final_path}")


def main():
    parser = argparse.ArgumentParser(description="REINFORCE self-play training for PrefNet")
    parser.add_argument("--model", default="neural/models/pref_net.pt",
                        help="Path to IL checkpoint (default: neural/models/pref_net.pt)")
    parser.add_argument("--output", default="neural/models/pref_net_rl.pt",
                        help="Output path for best RL model")
    parser.add_argument("--episodes", type=int, default=50000,
                        help="Number of training episodes")
    parser.add_argument("--lr", type=float, default=1e-4,
                        help="Learning rate")
    parser.add_argument("--temperature", type=float, default=0.5,
                        help="Initial sampling temperature")
    parser.add_argument("--temp-end", type=float, default=0.1,
                        help="Final sampling temperature")
    parser.add_argument("--entropy-coeff", type=float, default=0.01,
                        help="Entropy regularization coefficient")
    parser.add_argument("--reward-clip", type=float, default=3.0,
                        help="Clip normalized rewards to ±this value")
    parser.add_argument("--save-every", type=int, default=1000,
                        help="Save checkpoint every N episodes")
    parser.add_argument("--eval-every", type=int, default=500,
                        help="Evaluate every N episodes")
    parser.add_argument("--eval-games", type=int, default=100,
                        help="Number of games per evaluation")
    args = parser.parse_args()

    self_play_train(
        model_path=args.model,
        output_path=args.output,
        num_episodes=args.episodes,
        lr=args.lr,
        temperature=args.temperature,
        temp_end=args.temp_end,
        entropy_coeff=args.entropy_coeff,
        reward_clip=args.reward_clip,
        save_every=args.save_every,
        eval_every=args.eval_every,
        eval_games=args.eval_games,
    )


if __name__ == "__main__":
    main()
