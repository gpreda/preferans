"""Phase 1 imitation learning training script.

Usage:
    python neural/train.py --data-dir neural/data/ --epochs 50 --output neural/models/pref_net.pt
"""

import os
import sys
import argparse
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader, random_split

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from neural.model import PrefNet


# ---------------------------------------------------------------------------
# Datasets
# ---------------------------------------------------------------------------

def _load_aggressiveness(data):
    """Load aggressiveness array, defaulting to 0.5 if not present."""
    if "aggressiveness" in data:
        return torch.from_numpy(data["aggressiveness"]).unsqueeze(-1)  # (N, 1)
    n = len(data[list(data.keys())[0]])
    return torch.full((n, 1), 0.5)


class BidDataset(Dataset):
    def __init__(self, path):
        data = np.load(path)
        self.hands = torch.from_numpy(data["hands"])
        self.masks = torch.from_numpy(data["masks"])
        self.labels = torch.from_numpy(data["labels"])
        self.aggr = _load_aggressiveness(data)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        return self.hands[idx], self.masks[idx], self.labels[idx], self.aggr[idx]


class DiscardDataset(Dataset):
    def __init__(self, path):
        data = np.load(path)
        self.hands = torch.from_numpy(data["hands"])
        self.card_feats = torch.from_numpy(data["card_feats"])
        self.labels = torch.from_numpy(data["labels"])
        self.aggr = _load_aggressiveness(data)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        return self.hands[idx], self.card_feats[idx], self.labels[idx], self.aggr[idx]


class ContractDataset(Dataset):
    def __init__(self, path):
        data = np.load(path)
        self.hands = torch.from_numpy(data["hands"])
        self.contexts = torch.from_numpy(data["contexts"])
        self.type_labels = torch.from_numpy(data["type_labels"])
        self.trump_labels = torch.from_numpy(data["trump_labels"])
        self.aggr = _load_aggressiveness(data)

    def __len__(self):
        return len(self.type_labels)

    def __getitem__(self, idx):
        return (self.hands[idx], self.contexts[idx],
                self.type_labels[idx], self.trump_labels[idx], self.aggr[idx])


class FollowingDataset(Dataset):
    def __init__(self, path):
        data = np.load(path)
        self.hands = torch.from_numpy(data["hands"])
        self.contexts = torch.from_numpy(data["contexts"])
        self.masks = torch.from_numpy(data["masks"])
        self.labels = torch.from_numpy(data["labels"])
        self.aggr = _load_aggressiveness(data)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        return (self.hands[idx], self.contexts[idx],
                self.masks[idx], self.labels[idx], self.aggr[idx])


class CallingDataset(Dataset):
    def __init__(self, path):
        data = np.load(path)
        self.hands = torch.from_numpy(data["hands"])
        self.contexts = torch.from_numpy(data["contexts"])
        self.masks = torch.from_numpy(data["masks"])
        self.labels = torch.from_numpy(data["labels"])
        self.aggr = _load_aggressiveness(data)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        return (self.hands[idx], self.contexts[idx],
                self.masks[idx], self.labels[idx], self.aggr[idx])


class CounteringDataset(Dataset):
    def __init__(self, path):
        data = np.load(path)
        self.hands = torch.from_numpy(data["hands"])
        self.contexts = torch.from_numpy(data["contexts"])
        self.masks = torch.from_numpy(data["masks"])
        self.labels = torch.from_numpy(data["labels"])
        self.aggr = _load_aggressiveness(data)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        return (self.hands[idx], self.contexts[idx],
                self.masks[idx], self.labels[idx], self.aggr[idx])


class CardPlayDataset(Dataset):
    def __init__(self, path):
        data = np.load(path)
        self.hands = torch.from_numpy(data["hands"])
        self.play_ctxs = torch.from_numpy(data["play_ctxs"])
        self.played_vecs = torch.from_numpy(data["played_vecs"])
        self.card_feats = torch.from_numpy(data["card_feats"])
        self.labels = torch.from_numpy(data["labels"])
        self.num_legal = torch.from_numpy(data["num_legal"])
        self.aggr = _load_aggressiveness(data)

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        return (self.hands[idx], self.play_ctxs[idx], self.played_vecs[idx],
                self.card_feats[idx], self.labels[idx], self.num_legal[idx],
                self.aggr[idx])


# ---------------------------------------------------------------------------
# Loss functions
# ---------------------------------------------------------------------------

def bid_loss(logits, labels, masks):
    """CrossEntropy with illegal-action masking."""
    masked_logits = logits.masked_fill(~masks.bool(), float('-inf'))
    return F.cross_entropy(masked_logits, labels)


def discard_loss(scores, labels):
    """BCEWithLogits per card, positive class weight=5."""
    pos_weight = torch.tensor([5.0], device=scores.device)
    return F.binary_cross_entropy_with_logits(scores, labels, pos_weight=pos_weight)


def contract_loss(type_logits, trump_logits, type_labels, trump_labels):
    """CrossEntropy for type + CrossEntropy for trump (only when type=suit)."""
    type_ce = F.cross_entropy(type_logits, type_labels)

    # Trump loss only for suit contracts (type_label == 0)
    suit_mask = type_labels == 0
    if suit_mask.any():
        trump_ce = F.cross_entropy(trump_logits[suit_mask], trump_labels[suit_mask])
    else:
        trump_ce = torch.tensor(0.0, device=type_logits.device)

    return type_ce + trump_ce


def following_loss(logits, labels, masks):
    """CrossEntropy with action masking."""
    masked_logits = logits.masked_fill(~masks.bool(), float('-inf'))
    return F.cross_entropy(masked_logits, labels)


def calling_loss(logits, labels, masks):
    """CrossEntropy with action masking for calling head."""
    masked_logits = logits.masked_fill(~masks.bool(), float('-inf'))
    return F.cross_entropy(masked_logits, labels)


def countering_loss(logits, labels, masks):
    """CrossEntropy with action masking for countering head."""
    masked_logits = logits.masked_fill(~masks.bool(), float('-inf'))
    return F.cross_entropy(masked_logits, labels)


def card_play_loss(scores, labels, num_legal):
    """CrossEntropy over legal cards only."""
    batch_size = scores.shape[0]
    total_loss = 0.0
    count = 0
    for i in range(batch_size):
        n = num_legal[i].item()
        if n <= 0:
            continue
        logits_i = scores[i, :n].unsqueeze(0)
        label_i = labels[i].unsqueeze(0)
        if label_i.item() < n:
            total_loss += F.cross_entropy(logits_i, label_i)
            count += 1
    if count == 0:
        return torch.tensor(0.0, device=scores.device, requires_grad=True)
    return total_loss / count


# ---------------------------------------------------------------------------
# Accuracy metrics
# ---------------------------------------------------------------------------

def bid_accuracy(logits, labels, masks):
    masked_logits = logits.masked_fill(~masks.bool(), float('-inf'))
    preds = masked_logits.argmax(dim=-1)
    return (preds == labels).float().mean().item()


def discard_accuracy(scores, labels):
    """Exact pair match: top-2 predicted match the 2 actual discards."""
    batch_size = scores.shape[0]
    correct = 0
    for i in range(batch_size):
        pred_top2 = set(scores[i].topk(2).indices.tolist())
        true_top2 = set(labels[i].topk(2).indices.tolist())
        if pred_top2 == true_top2:
            correct += 1
    return correct / batch_size


def contract_type_accuracy(type_logits, type_labels):
    preds = type_logits.argmax(dim=-1)
    return (preds == type_labels).float().mean().item()


def following_accuracy(logits, labels, masks):
    masked_logits = logits.masked_fill(~masks.bool(), float('-inf'))
    preds = masked_logits.argmax(dim=-1)
    return (preds == labels).float().mean().item()


def calling_accuracy(logits, labels, masks):
    masked_logits = logits.masked_fill(~masks.bool(), float('-inf'))
    preds = masked_logits.argmax(dim=-1)
    return (preds == labels).float().mean().item()


def countering_accuracy(logits, labels, masks):
    masked_logits = logits.masked_fill(~masks.bool(), float('-inf'))
    preds = masked_logits.argmax(dim=-1)
    return (preds == labels).float().mean().item()


def card_play_accuracy(scores, labels, num_legal):
    batch_size = scores.shape[0]
    correct = 0
    count = 0
    for i in range(batch_size):
        n = num_legal[i].item()
        if n <= 0:
            continue
        pred = scores[i, :n].argmax().item()
        if pred == labels[i].item():
            correct += 1
        count += 1
    return correct / count if count > 0 else 0.0


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def train(data_dir, epochs, output_path, lr=1e-3, batch_size=256,
          weight_decay=1e-4, warmup_epochs=20, val_split=0.1):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Training on: {device}")

    model = PrefNet().to(device)
    print(f"Model parameters: {model.param_count():,}")

    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)

    # Load datasets
    datasets = {}
    loaders_train = {}
    loaders_val = {}

    dataset_files = {
        "bid": "bid_data.npz",
        "discard": "discard_data.npz",
        "contract": "contract_data.npz",
        "following": "following_data.npz",
        "calling": "calling_data.npz",
        "countering": "countering_data.npz",
        "card_play": "card_play_data.npz",
    }
    dataset_classes = {
        "bid": BidDataset,
        "discard": DiscardDataset,
        "contract": ContractDataset,
        "following": FollowingDataset,
        "calling": CallingDataset,
        "countering": CounteringDataset,
        "card_play": CardPlayDataset,
    }

    for name, filename in dataset_files.items():
        path = os.path.join(data_dir, filename)
        if not os.path.exists(path):
            print(f"  Warning: {path} not found, skipping {name}")
            continue

        ds = dataset_classes[name](path)
        n_val = max(1, int(len(ds) * val_split))
        n_train = len(ds) - n_val
        train_ds, val_ds = random_split(ds, [n_train, n_val])

        datasets[name] = ds
        loaders_train[name] = DataLoader(train_ds, batch_size=batch_size, shuffle=True)
        loaders_val[name] = DataLoader(val_ds, batch_size=batch_size)
        print(f"  {name}: {n_train} train, {n_val} val examples")

    if not datasets:
        print("No datasets found! Run collect.py first.")
        return

    best_val_acc = 0.0
    total_epochs = warmup_epochs + epochs
    loss_weights = {"bid": 1.0, "discard": 1.0, "contract": 1.0,
                    "following": 1.0, "calling": 1.5, "countering": 1.5,
                    "card_play": 2.0}

    for epoch in range(total_epochs):
        model.train()
        epoch_losses = {}
        phase = "warmup" if epoch < warmup_epochs else "joint"

        for name in loaders_train:
            total_loss = 0.0
            num_batches = 0

            for batch in loaders_train[name]:
                batch = [b.to(device) for b in batch]
                optimizer.zero_grad()

                if name == "bid":
                    hands, masks, labels, aggr = batch
                    logits = model.forward_bid(hands, aggr, masks)
                    loss = bid_loss(logits, labels, masks)
                elif name == "discard":
                    hands, card_feats, labels, aggr = batch
                    scores = model.forward_discard(hands, aggr, card_feats)
                    loss = discard_loss(scores, labels)
                elif name == "contract":
                    hands, contexts, type_labels, trump_labels, aggr = batch
                    type_logits, trump_logits = model.forward_contract(hands, aggr, contexts)
                    loss = contract_loss(type_logits, trump_logits,
                                         type_labels, trump_labels)
                elif name == "following":
                    hands, contexts, masks, labels, aggr = batch
                    logits = model.forward_following(hands, aggr, contexts, masks)
                    loss = following_loss(logits, labels, masks)
                elif name == "calling":
                    hands, contexts, masks, labels, aggr = batch
                    logits = model.forward_calling(hands, aggr, contexts, masks)
                    loss = calling_loss(logits, labels, masks)
                elif name == "countering":
                    hands, contexts, masks, labels, aggr = batch
                    logits = model.forward_countering(hands, aggr, contexts, masks)
                    loss = countering_loss(logits, labels, masks)
                elif name == "card_play":
                    hands, play_ctxs, played_vecs, card_feats, labels, num_legal, aggr = batch
                    scores = model.forward_card_play(
                        hands, aggr, play_ctxs, played_vecs, card_feats)
                    loss = card_play_loss(scores, labels, num_legal)

                if phase == "joint":
                    loss = loss * loss_weights.get(name, 1.0)

                loss.backward()
                optimizer.step()

                total_loss += loss.item()
                num_batches += 1

            epoch_losses[name] = total_loss / max(num_batches, 1)

        # Validation
        model.eval()
        val_accs = {}
        with torch.no_grad():
            for name in loaders_val:
                all_acc = []
                for batch in loaders_val[name]:
                    batch = [b.to(device) for b in batch]

                    if name == "bid":
                        hands, masks, labels, aggr = batch
                        logits = model.forward_bid(hands, aggr, masks)
                        all_acc.append(bid_accuracy(logits, labels, masks))
                    elif name == "discard":
                        hands, card_feats, labels, aggr = batch
                        scores = model.forward_discard(hands, aggr, card_feats)
                        all_acc.append(discard_accuracy(scores, labels))
                    elif name == "contract":
                        hands, contexts, type_labels, trump_labels, aggr = batch
                        type_logits, _ = model.forward_contract(hands, aggr, contexts)
                        all_acc.append(contract_type_accuracy(type_logits, type_labels))
                    elif name == "following":
                        hands, contexts, masks, labels, aggr = batch
                        logits = model.forward_following(hands, aggr, contexts, masks)
                        all_acc.append(following_accuracy(logits, labels, masks))
                    elif name == "calling":
                        hands, contexts, masks, labels, aggr = batch
                        logits = model.forward_calling(hands, aggr, contexts, masks)
                        all_acc.append(calling_accuracy(logits, labels, masks))
                    elif name == "countering":
                        hands, contexts, masks, labels, aggr = batch
                        logits = model.forward_countering(hands, aggr, contexts, masks)
                        all_acc.append(countering_accuracy(logits, labels, masks))
                    elif name == "card_play":
                        hands, play_ctxs, played_vecs, card_feats, labels, num_legal, aggr = batch
                        scores = model.forward_card_play(
                            hands, aggr, play_ctxs, played_vecs, card_feats)
                        all_acc.append(card_play_accuracy(scores, labels, num_legal))

                val_accs[name] = sum(all_acc) / len(all_acc) if all_acc else 0.0

        avg_val_acc = sum(val_accs.values()) / len(val_accs) if val_accs else 0.0

        # Save best model
        if avg_val_acc > best_val_acc:
            best_val_acc = avg_val_acc
            os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
            torch.save(model.state_dict(), output_path)

        # Log progress
        if (epoch + 1) % 5 == 0 or epoch == 0:
            loss_str = " ".join(f"{k}={v:.4f}" for k, v in epoch_losses.items())
            acc_str = " ".join(f"{k}={v:.3f}" for k, v in val_accs.items())
            print(f"  [{phase}] Epoch {epoch+1}/{total_epochs} "
                  f"| Loss: {loss_str} | Val acc: {acc_str} "
                  f"| Avg: {avg_val_acc:.3f} (best: {best_val_acc:.3f})")

    print(f"\nTraining complete. Best avg validation accuracy: {best_val_acc:.3f}")
    print(f"Model saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Train PrefNet via imitation learning")
    parser.add_argument("--data-dir", type=str, default="neural/data/",
                        help="Directory with .npz data files (default: neural/data/)")
    parser.add_argument("--epochs", type=int, default=50,
                        help="Joint training epochs (default: 50)")
    parser.add_argument("--warmup-epochs", type=int, default=20,
                        help="Warmup epochs per head (default: 20)")
    parser.add_argument("--output", type=str, default="neural/models/pref_net.pt",
                        help="Output model path (default: neural/models/pref_net.pt)")
    parser.add_argument("--lr", type=float, default=1e-3, help="Learning rate (default: 1e-3)")
    parser.add_argument("--batch-size", type=int, default=256, help="Batch size (default: 256)")
    args = parser.parse_args()
    train(args.data_dir, args.epochs, args.output,
          lr=args.lr, batch_size=args.batch_size,
          warmup_epochs=args.warmup_epochs)


if __name__ == "__main__":
    main()
