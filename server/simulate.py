"""Game simulation generator for Preferans.

Each simulation is driven by random target bids assigned to players.
All pre-play phases (auction, exchange, whisting) are driven entirely
through the GameSession state machine — no direct engine bidding calls.
"""
import random
import os

from models import (
    Game, Player, Alice, Bob, Carol,
    Card, Suit, Rank, RoundPhase, AuctionPhase,
    ContractType, BidType, PlayerType,
    SUIT_NAMES, RANK_NAMES,
)
from engine import GameEngine
from game_engine_service import GameSession


# Unicode suit symbols
SUIT_SYMBOL = {
    Suit.SPADES: "\u2660",
    Suit.DIAMONDS: "\u2666",
    Suit.HEARTS: "\u2665",
    Suit.CLUBS: "\u2663",
}

LEVEL_TO_TRUMP = {2: "spades", 3: "diamonds", 4: "hearts", 5: "clubs"}

# Target bid probability table: (target_type, target_value) -> weight
TARGET_PROBS = [
    (("pass",       0), 34),
    (("game",       2), 10), (("game",       3), 10),
    (("game",       4), 10), (("game",       5), 10),
    (("in_hand",    2),  5), (("in_hand",    3),  5),
    (("in_hand",    4),  5), (("in_hand",    5),  5),
    (("betl",       0),  2), (("in_hand_betl", 0), 1),
    (("sans",       0),  2), (("in_hand_sans", 0), 1),
]

# Suit-only variant: no in_hand / betl / sans targets
TARGET_PROBS_SUIT_ONLY = [
    (("pass", 0), 34),
    (("game", 2), 10), (("game", 3), 10),
    (("game", 4), 10), (("game", 5), 10),
]

PROB_FOLLOW         = 0.50
PROB_COUNTER        = 0.10
PROB_DOUBLE_COUNTER = 0.10
PROB_CALL           = 0.15


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def card_str(card: Card) -> str:
    return f"{RANK_NAMES[card.rank]}{SUIT_SYMBOL[card.suit]}"


def hand_str(cards: list[Card]) -> str:
    return " ".join(card_str(c) for c in cards)


def target_label(target: tuple) -> str:
    t, v = target
    if t == "game":     return f"game_{v}"
    if t == "in_hand":  return f"in_hand_{v}"
    return t  # pass, betl, sans, in_hand_betl, in_hand_sans


def format_step(step_num: int, game: Game, engine: GameEngine,
                commands: list[str], chosen: str,
                initial_talon: list[Card]) -> str:
    """Render one step as the text format."""
    rnd = game.current_round
    lines = [f"--- step {step_num} ---"]

    # Game header line
    talon_s = hand_str(initial_talon)
    contract_s = "-"
    if rnd.contract:
        c = rnd.contract
        if c.type == ContractType.SUIT:
            contract_s = f"{c.bid_value}{SUIT_SYMBOL[c.trump_suit]}"
        elif c.type == ContractType.BETL:
            contract_s = "betl"
        else:
            contract_s = "sans"
        if c.is_in_hand:
            contract_s += "/ih"
    discarded_s = hand_str(rnd.discarded) if rnd.discarded else "-"
    trick_s = "-"
    if rnd.current_trick and rnd.current_trick.cards:
        trick_s = " ".join(f"P{pid}:{card_str(c)}" for pid, c in rnd.current_trick.cards)
    lines.append(
        f"game: talon={talon_s} | contract={contract_s}"
        f" | discarded={discarded_s} | trick={trick_s}"
    )

    # Player lines
    for p in sorted(game.players, key=lambda x: x.id):
        lines.append(f"P{p.id}: hand={hand_str(p.hand)} | tricks={p.tricks_won}")

    lines.append(f"commands: {' '.join(commands)}")
    lines.append(f"> {chosen}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Target-based choice from SM commands
# ---------------------------------------------------------------------------

# SM command label -> (type, effective_value)
_CMD_MAP = {
    'Pass':    ('pass', 0),
    '2':       ('game', 2),  '3':       ('game', 3),
    '4':       ('game', 4),  '5':       ('game', 5),
    'Hand':    ('in_hand', 0),
    'Betl':    ('betl', 6),
    'Sans':    ('sans', 7),
}

# Contract command -> level
_CONTRACT_CMD_MAP = {
    'Spades': 2, 'Diamonds': 3, 'Hearts': 4, 'Clubs': 5,
    'Betl': 6, 'Sans': 7,
}


def assign_targets(rng, suit_only: bool = False) -> list[tuple]:
    pool = TARGET_PROBS_SUIT_ONLY if suit_only else TARGET_PROBS
    bids, weights = zip(*pool)
    return [rng.choices(bids, weights=weights, k=1)[0] for _ in range(3)]


def choose_auction_cmd(target: tuple, commands: list[str]) -> int:
    """Return 1-based index of the command to execute during auction."""
    target_type, target_value = target
    cmd_set = set(commands)

    if target_type == "pass":
        return commands.index("Pass") + 1

    # In-hand targets
    if target_type in ("in_hand", "in_hand_betl", "in_hand_sans"):
        if "Hand" in cmd_set:
            return commands.index("Hand") + 1
        # In-hand deciding: try specific bids
        if target_type == "in_hand_sans" and "Sans" in cmd_set:
            return commands.index("Sans") + 1
        if target_type in ("in_hand_betl", "in_hand_sans") and "Betl" in cmd_set:
            return commands.index("Betl") + 1
        # In-hand declaring: pick highest in_hand_N <= target
        in_hand_cmds = [c for c in commands if c.startswith("in_hand ")]
        if in_hand_cmds:
            if target_type == "in_hand":
                valid = [c for c in in_hand_cmds if int(c.split()[-1]) <= target_value]
                if valid:
                    return commands.index(max(valid, key=lambda c: int(c.split()[-1]))) + 1
            else:
                return commands.index(in_hand_cmds[-1]) + 1
        return commands.index("Pass") + 1

    # Game / betl / sans targets
    target_eff = (target_value if target_type == "game"
                  else 6 if target_type == "betl" else 7)

    # Pick highest game-level bid <= target
    best_cmd = None
    best_eff = -1
    for cmd in commands:
        info = _CMD_MAP.get(cmd)
        if not info:
            continue
        ctype, cval = info
        if ctype == 'pass':
            continue
        if ctype == 'in_hand':
            continue
        eff = cval
        if eff <= target_eff and eff > best_eff:
            best_eff = eff
            best_cmd = cmd

    if best_cmd:
        return commands.index(best_cmd) + 1
    return commands.index("Pass") + 1


def choose_contract_cmd(target: tuple, commands: list[str], suit_only: bool = False) -> int:
    """Return 1-based index for contract selection (exchanging phase)."""
    t, v = target
    target_level = (v if t in ("game", "in_hand")
                    else 6 if t in ("betl", "in_hand_betl")
                    else 7 if t in ("sans", "in_hand_sans")
                    else 2)
    if suit_only:
        target_level = min(target_level, 5)

    # Find matching or closest command
    best_cmd = None
    best_diff = 999
    for cmd in commands:
        lvl = _CONTRACT_CMD_MAP.get(cmd)
        if lvl is None:
            continue
        if suit_only and lvl > 5:
            continue
        diff = abs(lvl - target_level)
        if diff < best_diff:
            best_diff = diff
            best_cmd = cmd
    if best_cmd:
        return commands.index(best_cmd) + 1
    return 1


def _cards_by_suit(hand_cards) -> dict:
    """Group cards into {Suit: [Card, ...]}."""
    suits = {}
    for card in hand_cards:
        suits.setdefault(card.suit, []).append(card)
    return suits


def _count_trump_tricks(trump_cards) -> int:
    """Count guaranteed trump tricks.

    1-trick: A, Kx, Dxx (10xx), Jxxx
    2-trick: AK, AD (A+10), AJxe (AJ+2x), KJx, DJxx (10+J+xx)
    """
    ranks = {c.rank for c in trump_cards}
    count = len(trump_cards)
    has_A = Rank.ACE in ranks
    has_K = Rank.KING in ranks
    has_D = Rank.TEN in ranks
    has_J = Rank.JACK in ranks

    # 2-trick combinations
    if has_A and has_K:
        return 2
    if has_A and has_D:
        return 2
    if has_A and has_J and count >= 4:
        return 2
    if has_K and has_J and count >= 3:
        return 2
    if has_D and has_J and count >= 4:
        return 2

    # 1-trick combinations
    if has_A:
        return 1
    if has_K and count >= 2:
        return 1
    if has_D and count >= 3:
        return 1
    if has_J and count >= 4:
        return 1

    return 0


def _suit_reason(cards) -> float:
    """Compute reason strength for a non-trump suit.

    Strong (1.0): A with <5 cards, Kx with <4 cards
    Medium (0.5): A with <6, Kx with <5, Dxx (10xx) with <4
    Weak (0.25): A/Kx/Dxx any count
    """
    ranks = {c.rank for c in cards}
    count = len(cards)
    has_A = Rank.ACE in ranks
    has_K = Rank.KING in ranks
    has_D = Rank.TEN in ranks

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


def _compute_follow_stats(hand_cards, trump_suit) -> tuple:
    """Compute (num_trump_tricks, sum_reasons)."""
    by_suit = _cards_by_suit(hand_cards)

    trump_cards = by_suit.get(trump_suit, [])
    num_trump_tricks = _count_trump_tricks(trump_cards)

    sum_reasons = 0.0
    for suit, cards in by_suit.items():
        if suit == trump_suit:
            continue
        sum_reasons += _suit_reason(cards)

    # Weak reason: 3+ trump cards without a safe trick
    if len(trump_cards) >= 3 and num_trump_tricks == 0:
        sum_reasons += 0.25

    return num_trump_tricks, sum_reasons


def _boost_for_talon(hand_cards, talon, trump_suit) -> float:
    """For each revealed talon card where the player has a reason in that suit,
    increase by 0.25. Only considers non-trump suits."""
    by_suit = _cards_by_suit(hand_cards)
    boost = 0.0
    for card in talon:
        if card.suit == trump_suit:
            continue
        suit_cards = by_suit.get(card.suit, [])
        if suit_cards and _suit_reason(suit_cards) > 0:
            boost += 0.25
    return boost


def _should_follow(hand_cards, trump_suit, talon=None,
                   is_aggressive=False, first_defender_passed=False) -> bool:
    """Determine if the player should follow based on hand analysis."""
    num_trump_tricks, sum_reasons = _compute_follow_stats(hand_cards, trump_suit)

    if talon:
        sum_reasons += _boost_for_talon(hand_cards, talon, trump_suit)

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


def choose_whist_cmd(rng, commands: list[str], hand_cards=None, trump_suit=None,
                     talon=None, is_aggressive=False,
                     first_defender_passed=False) -> int:
    """Return 1-based index for whisting commands with hand-based heuristics."""
    cmd_set = set(commands)

    # Declarer responding to counter
    if "Double counter" in cmd_set:
        if rng.random() < PROB_DOUBLE_COUNTER:
            return commands.index("Double counter") + 1
        return commands.index("Start game") + 1

    # Counter sub-phase (follower): start_game / call / counter
    if "Start game" in cmd_set:
        r = rng.random()
        if "Call" in cmd_set and r < PROB_CALL:
            return commands.index("Call") + 1
        if "Counter" in cmd_set and r < PROB_CALL + PROB_COUNTER:
            return commands.index("Counter") + 1
        return commands.index("Start game") + 1

    # Heuristic-based follow decision
    if hand_cards and trump_suit and "Follow" in cmd_set:
        if _should_follow(hand_cards, trump_suit, talon,
                          is_aggressive, first_defender_passed):
            return commands.index("Follow") + 1

    # Declaration phase: Pass / Follow (+ possibly Call / Counter)
    r = rng.random()
    if r < PROB_FOLLOW:
        return commands.index("Follow") + 1
    if "Counter" in cmd_set and r < PROB_FOLLOW + PROB_COUNTER:
        return commands.index("Counter") + 1
    if "Call" in cmd_set and r < PROB_FOLLOW + PROB_COUNTER + PROB_CALL:
        return commands.index("Call") + 1
    return commands.index("Pass") + 1


# ---------------------------------------------------------------------------
# Round simulation
# ---------------------------------------------------------------------------

def simulate_round(seed: int, suit_only: bool = False) -> list[str]:
    """Simulate one complete round via the GameSession SM."""
    rng = random.Random(seed)

    old_state = random.getstate()
    random.setstate(rng.getstate())

    session = GameSession(["Alice", "Bob", "Carol"])
    engine = session.engine
    game = engine.game

    random.setstate(old_state)

    # Assign target bids
    targets = assign_targets(rng, suit_only)
    players_sorted = sorted(game.players, key=lambda p: p.id)
    player_targets = {p.position: t for p, t in zip(players_sorted, targets)}

    # Parameters block
    param_lines = ["--- parameters ---"]
    for p in players_sorted:
        param_lines.append(f"P{p.id}: target={target_label(player_targets[p.position])}")
    blocks = ["\n".join(param_lines)]

    initial_talon = list(game.current_round.talon)
    step_num = 1

    # --- SM-driven phases (auction, exchange, whisting) ---
    max_steps = 60
    while session.sm_active and max_steps > 0:
        max_steps -= 1
        commands, player_pos = session.get_commands()
        if not commands:
            break

        phase = session.get_phase()

        if phase == 'auction':
            target = player_targets[player_pos]
            cmd_id = choose_auction_cmd(target, commands)
        elif phase == 'exchanging':
            if commands[0].isdigit():
                # Discard: pick randomly
                cmd_id = rng.randint(1, len(commands))
            else:
                # Contract selection
                target = player_targets[player_pos]
                cmd_id = choose_contract_cmd(target, commands, suit_only)
        elif phase == 'whisting':
            rnd = game.current_round
            contract = rnd.contract if rnd else None
            trump_s = contract.trump_suit if contract else None
            player_obj = next((p for p in game.players if p.position == player_pos), None)
            hand_c = list(player_obj.hand) if player_obj else None
            orig_talon = list(rnd.original_talon) if rnd else None
            is_aggr = player_obj and player_obj.playing_style == PlayingStyle.AGGRESSIVE
            first_passed = any(v == "pass" for v in rnd.whist_declarations.values()) if rnd else False
            cmd_id = choose_whist_cmd(rng, commands, hand_cards=hand_c, trump_suit=trump_s,
                                      talon=orig_talon, is_aggressive=is_aggr,
                                      first_defender_passed=first_passed)
        elif phase == 'playing':
            # In-hand undeclared: contract selection
            target = player_targets[player_pos]
            cmd_id = choose_contract_cmd(target, commands, suit_only)
        else:
            break

        cmd_labels = [f"{i+1}:{c.lower()}" for i, c in enumerate(commands)]
        chosen = f"{cmd_id}:{commands[cmd_id-1].lower()} (P{player_pos})"
        blocks.append(format_step(step_num, game, engine, cmd_labels, chosen, initial_talon))
        step_num += 1

        session.execute(cmd_id)

    # --- Trick play (not SM-driven) ---
    max_steps = 50
    while game.current_round.phase == RoundPhase.PLAYING and max_steps > 0:
        max_steps -= 1
        commands, player_pos = session.get_commands()
        if not commands:
            break

        card_labels = [f"{i+1}:{c.lower()}" for i, c in enumerate(commands)]
        cmd_id = rng.randint(1, len(commands))
        chosen = f"{cmd_id}:{commands[cmd_id-1].lower()} (P{player_pos})"
        blocks.append(format_step(step_num, game, engine, card_labels, chosen, initial_talon))
        step_num += 1

        session.execute(cmd_id)

    # --- Scoring ---
    rnd = game.current_round
    score_lines = [f"--- step {step_num} ---", "phase: scoring"]
    if rnd.contract:
        c = rnd.contract
        if c.type == ContractType.SUIT:
            score_lines.append(f"contract: {c.bid_value}{SUIT_SYMBOL[c.trump_suit]}"
                               f"{'/ih' if c.is_in_hand else ''}")
        elif c.type == ContractType.BETL:
            score_lines.append(f"contract: betl{'/ih' if c.is_in_hand else ''}")
        else:
            score_lines.append(f"contract: sans{'/ih' if c.is_in_hand else ''}")
    for p in sorted(game.players, key=lambda x: x.id):
        score_lines.append(f"P{p.id}: tricks={p.tricks_won} score={p.score}")
    blocks.append("\n".join(score_lines))

    return blocks


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    sim_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "simulations",
    )
    os.makedirs(sim_dir, exist_ok=True)

    # Suppress engine debug prints during generation
    import builtins
    original_print = builtins.print
    builtins.print = lambda *a, **k: None

    try:
        for i in range(1, 11):
            blocks = simulate_round(seed=i)
            path = os.path.join(sim_dir, f"game_{i:03d}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n\n".join(blocks) + "\n")
            builtins.print = original_print
            print(f"  wrote {path} ({len(blocks) - 1} steps)")
            builtins.print = lambda *a, **k: None

        for i in range(11, 21):
            blocks = simulate_round(seed=i, suit_only=True)
            path = os.path.join(sim_dir, f"game_{i:03d}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n\n".join(blocks) + "\n")
            builtins.print = original_print
            print(f"  wrote {path} ({len(blocks) - 1} steps)")
            builtins.print = lambda *a, **k: None
    finally:
        builtins.print = original_print

    print(f"Done. 20 simulations in {sim_dir}")


if __name__ == "__main__":
    main()
