"""PyTorch model: shared encoder + 7 decision heads for Preferans.

Architecture:
  PrefEncoder (shared): 56 → 128 → 64
  BidHead:        65 → 32 → 5  (masked logits)
  DiscardHead:    79 → 32 → 1  (per-card scorer, shared weights)
  ContractHead:   69 → 32 → 7  (type[3] + trump[4])
  FollowingHead:  73 → 32 → 2  (pass, follow)
  CallingHead:    77 → 32 → 4  (pass, follow, call, counter)
  CounteringHead: 77 → 32 → 3  (start_game, counter, double_counter)
  CardPlayHead:  127 → 64 → 1  (per-card scorer, shared weights)

All heads receive a 1-dim aggressiveness conditioning input.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F


class PrefEncoder(nn.Module):
    """Shared hand encoder: 56-dim hand features → 64-dim embedding."""

    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(56, 128)
        self.fc2 = nn.Linear(128, 64)

    def forward(self, x):
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        return x


class BidHead(nn.Module):
    """Bidding head: (64+1) → 32 → 5 logits [pass, game, in_hand, betl, sans]."""

    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(65, 32)
        self.fc2 = nn.Linear(32, 5)

    def forward(self, embedding, aggressiveness, mask=None):
        x = torch.cat([embedding, aggressiveness], dim=-1)
        x = F.relu(self.fc1(x))
        logits = self.fc2(x)
        if mask is not None:
            logits = logits.masked_fill(~mask.bool(), float('-inf'))
        return logits


class DiscardHead(nn.Module):
    """Discard head: scores each card. Input = embedding(64) + aggr(1) + card_features(14) = 79."""

    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(79, 32)
        self.fc2 = nn.Linear(32, 1)

    def forward(self, embedding, aggressiveness, card_features):
        """Score each card for discarding.

        Args:
            embedding: (batch, 64) hand embedding
            aggressiveness: (batch, 1) aggressiveness score
            card_features: (batch, num_cards, 14) per-card features

        Returns:
            scores: (batch, num_cards) discard scores
        """
        batch_size, num_cards, _ = card_features.shape
        emb_aggr = torch.cat([embedding, aggressiveness], dim=-1)  # (batch, 65)
        emb_expanded = emb_aggr.unsqueeze(1).expand(-1, num_cards, -1)  # (batch, num_cards, 65)
        combined = torch.cat([emb_expanded, card_features], dim=-1)  # (batch, num_cards, 79)
        x = F.relu(self.fc1(combined))
        scores = self.fc2(x).squeeze(-1)  # (batch, num_cards)
        return scores


class ContractHead(nn.Module):
    """Contract head: embedding(64) + context(4) + aggr(1) = 69 → 7 [type(3) + trump(4)]."""

    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(69, 32)
        self.fc2 = nn.Linear(32, 7)

    def forward(self, embedding, aggressiveness, context):
        combined = torch.cat([embedding, context, aggressiveness], dim=-1)
        x = F.relu(self.fc1(combined))
        out = self.fc2(x)
        type_logits = out[:, :3]   # suit, betl, sans
        trump_logits = out[:, 3:]  # clubs, diamonds, hearts, spades
        return type_logits, trump_logits


class FollowingHead(nn.Module):
    """Following head: embedding(64) + context(8) + aggr(1) = 73 → 2 logits.

    Actions: [pass, follow]
    """

    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(73, 32)
        self.fc2 = nn.Linear(32, 2)

    def forward(self, embedding, aggressiveness, context, mask=None):
        combined = torch.cat([embedding, context, aggressiveness], dim=-1)
        x = F.relu(self.fc1(combined))
        logits = self.fc2(x)
        if mask is not None:
            logits = logits.masked_fill(~mask.bool(), float('-inf'))
        return logits


class CallingHead(nn.Module):
    """Calling head: embedding(64) + context(12) + aggr(1) = 77 → 4 logits.

    Actions: [pass, follow, call, counter]
    """

    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(77, 32)
        self.fc2 = nn.Linear(32, 4)

    def forward(self, embedding, aggressiveness, context, mask=None):
        combined = torch.cat([embedding, context, aggressiveness], dim=-1)
        x = F.relu(self.fc1(combined))
        logits = self.fc2(x)
        if mask is not None:
            logits = logits.masked_fill(~mask.bool(), float('-inf'))
        return logits


class CounteringHead(nn.Module):
    """Countering head: embedding(64) + context(12) + aggr(1) = 77 → 3 logits.

    Actions: [start_game, counter, double_counter]
    """

    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(77, 32)
        self.fc2 = nn.Linear(32, 3)

    def forward(self, embedding, aggressiveness, context, mask=None):
        combined = torch.cat([embedding, context, aggressiveness], dim=-1)
        x = F.relu(self.fc1(combined))
        logits = self.fc2(x)
        if mask is not None:
            logits = logits.masked_fill(~mask.bool(), float('-inf'))
        return logits


class CardPlayHead(nn.Module):
    """Card play head: scores each legal card.

    Input per card: embedding(64) + aggr(1) + play_context(16) + cards_played(32) + card_features(14) = 127.
    """

    def __init__(self):
        super().__init__()
        self.fc1 = nn.Linear(127, 64)
        self.fc2 = nn.Linear(64, 1)

    def forward(self, embedding, aggressiveness, play_context, cards_played, card_features):
        """Score each legal card for playing.

        Args:
            embedding: (batch, 64)
            aggressiveness: (batch, 1)
            play_context: (batch, 16)
            cards_played: (batch, 32)
            card_features: (batch, num_cards, 14)

        Returns:
            scores: (batch, num_cards) raw scores
        """
        batch_size, num_cards, _ = card_features.shape
        shared = torch.cat([embedding, aggressiveness, play_context, cards_played], dim=-1)  # (batch, 113)
        shared_expanded = shared.unsqueeze(1).expand(-1, num_cards, -1)  # (batch, num_cards, 113)
        combined = torch.cat([shared_expanded, card_features], dim=-1)  # (batch, num_cards, 127)
        x = F.relu(self.fc1(combined))
        scores = self.fc2(x).squeeze(-1)  # (batch, num_cards)
        return scores


class PrefNet(nn.Module):
    """Combined Preferans neural network with shared encoder and 7 decision heads."""

    def __init__(self):
        super().__init__()
        self.encoder = PrefEncoder()
        self.bid_head = BidHead()
        self.discard_head = DiscardHead()
        self.contract_head = ContractHead()
        self.following_head = FollowingHead()
        self.calling_head = CallingHead()
        self.countering_head = CounteringHead()
        self.card_play_head = CardPlayHead()

    def forward_bid(self, hand_features, aggressiveness, mask=None):
        emb = self.encoder(hand_features)
        return self.bid_head(emb, aggressiveness, mask)

    def forward_discard(self, hand_features, aggressiveness, card_features):
        emb = self.encoder(hand_features)
        return self.discard_head(emb, aggressiveness, card_features)

    def forward_contract(self, hand_features, aggressiveness, context):
        emb = self.encoder(hand_features)
        return self.contract_head(emb, aggressiveness, context)

    def forward_following(self, hand_features, aggressiveness, context, mask=None):
        emb = self.encoder(hand_features)
        return self.following_head(emb, aggressiveness, context, mask)

    def forward_calling(self, hand_features, aggressiveness, context, mask=None):
        emb = self.encoder(hand_features)
        return self.calling_head(emb, aggressiveness, context, mask)

    def forward_countering(self, hand_features, aggressiveness, context, mask=None):
        emb = self.encoder(hand_features)
        return self.countering_head(emb, aggressiveness, context, mask)

    def forward_card_play(self, hand_features, aggressiveness, play_context, cards_played, card_features):
        emb = self.encoder(hand_features)
        return self.card_play_head(emb, aggressiveness, play_context, cards_played, card_features)

    def param_count(self):
        return sum(p.numel() for p in self.parameters())
