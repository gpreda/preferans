#!/usr/bin/env bash
# evolve_helper.sh — Evolution loop focused on improving Alice
# Each iteration: play 50 games, then Claude analyzes and improves Alice's strategy.
#
# Called by evolve.sh via nohup. Do not run directly.

set -euo pipefail
unset CLAUDECODE 2>/dev/null || true

cleanup() { kill -- -$$ 2>/dev/null; }
trap cleanup EXIT INT TERM

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GAME_SCRIPT="$SCRIPT_DIR/PrefTestSingleGame.py"
BENCHMARK_SCRIPT="$SCRIPT_DIR/benchmark_players.py"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"
LOG_DIR="$SCRIPT_DIR/logs/evolution"
mkdir -p "$LOG_DIR"

ITERATIONS=20
GAMES_PER_ITER=50
IMPROVEMENTS_FILE="$SCRIPT_DIR/improvements.txt"
NOTES_FILE="$SCRIPT_DIR/evolution_notes.md"

# Opponents pool
OPPONENT_POOL=("alice" "bob" "carol" "neural-aggressive" "neural-pragmatic")

# --- Initialize evolution notes ---
if [[ ! -f "$NOTES_FILE" ]]; then
    cat > "$NOTES_FILE" <<'EOFNOTES'
# Evolution Notes — Alice Focus
Shared context for Claude across evolution iterations.
Focus: improving Alice's heuristics by identifying missing stats and conditions.

## Known Issues

## Strategy Observations

## Improvements Applied
EOFNOTES
fi

# Run claude with retry on failure (e.g. usage limit)
run_claude() {
    local max_retries=50
    local wait_secs=300  # 5 minutes
    local attempt=1
    while true; do
        if claude "$@" < /dev/null; then
            return 0
        fi
        if (( attempt >= max_retries )); then
            echo "[evolve] Claude failed after $max_retries attempts. Exiting."
            exit 1
        fi
        echo "[evolve] Claude failed (attempt $attempt/$max_retries). Waiting ${wait_secs}s before retry..."
        sleep "$wait_secs"
        (( attempt++ ))
    done
}

# Run benchmark and save output
run_benchmark() {
    local label="$1"
    local bench_log="$LOG_DIR/benchmark_${label}.txt"
    echo "[evolve] Running benchmark: $label ..."
    $PYTHON "$BENCHMARK_SCRIPT" > "$bench_log" 2>&1 || true
    echo "[evolve] Benchmark saved to $bench_log"
    echo ""
    echo "=== Benchmark: $label ==="
    tail -30 "$bench_log"
    echo ""
}

# Pick 2 random opponents from OPPONENT_POOL
pick_opponents() {
    local seed="$1"
    $PYTHON -c "
import random
rng = random.Random($seed)
pool = ['alice', 'bob', 'carol', 'neural-aggressive', 'neural-pragmatic']
chosen = rng.sample(pool, 2)
print(','.join(chosen))
"
}

echo "=== Preferans Alice Evolution ==="
echo "Iterations: $ITERATIONS ($GAMES_PER_ITER games each)"
echo "Opponent pool: ${OPPONENT_POOL[*]}"
echo "Log dir: $LOG_DIR"
echo ""

# ================================================================
# Step 1: Baseline benchmark
# ================================================================
run_benchmark "baseline"
BASELINE_FILE="$LOG_DIR/benchmark_baseline.txt"

{
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "BASELINE BENCHMARK — $(date '+%Y-%m-%d %H:%M')"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    tail -30 "$BASELINE_FILE"
    echo ""
} >> "$IMPROVEMENTS_FILE"

# ================================================================
# Step 2: Evolution loop
# ================================================================
for i in $(seq 1 $ITERATIONS); do
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Iteration $i / $ITERATIONS — Improving Alice ($GAMES_PER_ITER games)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    ITER_LOG_DIR="$LOG_DIR/iter${i}"
    mkdir -p "$ITER_LOG_DIR"

    SCORES_FILE="$ITER_LOG_DIR/all_scores.txt"
    COMPACT_ALL="$ITER_LOG_DIR/all_compacts.txt"
    > "$SCORES_FILE"
    > "$COMPACT_ALL"

    TOTAL_ALICE=0
    GAME_COUNT=0

    for g in $(seq 1 $GAMES_PER_ITER); do
        GAME_SEED=$(( RANDOM * 32768 + RANDOM ))
        OPPONENTS=$(pick_opponents "$GAME_SEED")
        OPP1=$(echo "$OPPONENTS" | cut -d',' -f1)
        OPP2=$(echo "$OPPONENTS" | cut -d',' -f2)

        GAME_LOG="$ITER_LOG_DIR/game_${g}.txt"
        STDERR_LOG="$ITER_LOG_DIR/stderr_${g}.txt"

        echo "[iter $i game $g/$GAMES_PER_ITER] alice,$OPP1,$OPP2 seed=$GAME_SEED"
        if ! $PYTHON "$GAME_SCRIPT" "$GAME_SEED" --players "alice,$OPP1,$OPP2" \
                > "$GAME_LOG" 2>"$STDERR_LOG"; then
            echo "[iter $i game $g] CRASHED. Skipping."
            continue
        fi
        rm -f "$STDERR_LOG"

        # Collect compact log
        LATEST_COMPACT=$(ls -t "$SCRIPT_DIR"/game_compact_*.txt 2>/dev/null | head -1)
        if [[ -n "$LATEST_COMPACT" ]]; then
            echo "=== Game $g: alice,$OPP1,$OPP2 (seed=$GAME_SEED) ===" >> "$COMPACT_ALL"
            cat "$LATEST_COMPACT" >> "$COMPACT_ALL"
            echo "" >> "$COMPACT_ALL"
            rm -f "$LATEST_COMPACT"
        fi
        LATEST_OUTPUT=$(ls -t "$SCRIPT_DIR"/game_output_*.txt 2>/dev/null | head -1)
        [[ -n "$LATEST_OUTPUT" ]] && rm -f "$LATEST_OUTPUT"

        # Extract scores (float → truncate to int for bash arithmetic)
        alice_score_raw=$(grep -oP 'P1 Alice: tricks=\d+, score=\K-?[0-9.]+' "$GAME_LOG" 2>/dev/null | head -1 || echo "")
        if [[ -n "$alice_score_raw" ]]; then
            alice_score=$(echo "$alice_score_raw" | awk '{printf "%d", $1}')
            echo "Game $g (seed=$GAME_SEED): alice,$OPP1,$OPP2 → Alice=$alice_score" >> "$SCORES_FILE"
            TOTAL_ALICE=$(( TOTAL_ALICE + alice_score ))
            (( GAME_COUNT++ )) || true
        fi
    done

    if (( GAME_COUNT > 0 )); then
        AVG_ALICE=$(echo "scale=1; $TOTAL_ALICE / $GAME_COUNT" | bc)
    else
        AVG_ALICE="n/a"
    fi
    echo "[iter $i] Alice total=$TOTAL_ALICE across $GAME_COUNT games (avg=$AVG_ALICE)"

    # --- Build score history ---
    HISTORY_FILE="$ITER_LOG_DIR/history.txt"
    > "$HISTORY_FILE"
    for prev in $(seq 1 $i); do
        prev_scores="$LOG_DIR/iter${prev}/all_scores.txt"
        if [[ -f "$prev_scores" ]]; then
            prev_total=$(awk -F'Alice=' '{s+=$2} END{print s+0}' "$prev_scores")
            prev_count=$(wc -l < "$prev_scores")
            if (( prev_count > 0 )); then
                prev_avg=$(echo "scale=1; $prev_total / $prev_count" | bc)
            else
                prev_avg="n/a"
            fi
            echo "  Iter $prev: total=$prev_total games=$prev_count avg=$prev_avg" >> "$HISTORY_FILE"
        fi
    done

    # --- Select interesting games for Claude (worst 10 + best 5) ---
    INTERESTING="$ITER_LOG_DIR/interesting_games.txt"
    > "$INTERESTING"

    # Sort by Alice score, pick worst 10 and best 5
    sort -t'=' -k3 -n "$SCORES_FILE" | head -10 > "$ITER_LOG_DIR/_worst.txt"
    sort -t'=' -k3 -rn "$SCORES_FILE" | head -5 > "$ITER_LOG_DIR/_best.txt"

    echo "=== WORST 10 GAMES (biggest losses) ===" >> "$INTERESTING"
    while IFS= read -r line; do
        game_num=$(echo "$line" | grep -oP 'Game \K\d+')
        echo "$line" >> "$INTERESTING"
        game_log="$ITER_LOG_DIR/game_${game_num}.txt"
        if [[ -f "$game_log" ]]; then
            echo "--- Hands + Tricks ---" >> "$INTERESTING"
            sed -n '/--- Dealt Hands ---/,/^$/p' "$game_log" >> "$INTERESTING"
            sed -n '/--- Playing/,/--- Scoring ---/p' "$game_log" >> "$INTERESTING"
            grep -A10 -- "--- Scoring ---" "$game_log" >> "$INTERESTING" || true
            echo "" >> "$INTERESTING"
        fi
    done < "$ITER_LOG_DIR/_worst.txt"

    echo "=== BEST 5 GAMES (biggest wins) ===" >> "$INTERESTING"
    while IFS= read -r line; do
        game_num=$(echo "$line" | grep -oP 'Game \K\d+')
        echo "$line" >> "$INTERESTING"
        game_log="$ITER_LOG_DIR/game_${game_num}.txt"
        if [[ -f "$game_log" ]]; then
            echo "--- Hands + Tricks ---" >> "$INTERESTING"
            sed -n '/--- Dealt Hands ---/,/^$/p' "$game_log" >> "$INTERESTING"
            sed -n '/--- Playing/,/--- Scoring ---/p' "$game_log" >> "$INTERESTING"
            grep -A10 -- "--- Scoring ---" "$game_log" >> "$INTERESTING" || true
            echo "" >> "$INTERESTING"
        fi
    done < "$ITER_LOG_DIR/_best.txt"
    rm -f "$ITER_LOG_DIR/_worst.txt" "$ITER_LOG_DIR/_best.txt"

    # --- Build Claude prompt ---
    echo ""
    echo "[iter $i] Invoking Claude to analyze and improve Alice..."

    PROMPT_FILE="$ITER_LOG_DIR/prompt.txt"
    cat > "$PROMPT_FILE" <<ENDHEADER
You are optimizing PlayerAlice in the file:
$GAME_SCRIPT

Read the evolution notes for context from previous iterations:
- $NOTES_FILE

EXPERIMENT RESULTS — Iteration $i:
Alice played $GAME_COUNT games against random opponent pairs.
Total score: $TOTAL_ALICE | Average: $AVG_ALICE per game

ALL GAME SCORES:

ENDHEADER

    cat "$SCORES_FILE" >> "$PROMPT_FILE"

    cat >> "$PROMPT_FILE" <<ENDHISTORY

SCORE HISTORY ACROSS ALL ITERATIONS:

ENDHISTORY

    cat "$HISTORY_FILE" >> "$PROMPT_FILE"

    cat >> "$PROMPT_FILE" <<ENDINTERESTING

DETAILED LOGS FOR INTERESTING GAMES (worst losses + best wins):

ENDINTERESTING

    cat "$INTERESTING" >> "$PROMPT_FILE"

    cat >> "$PROMPT_FILE" <<'ENDRULES'

RULES OF THE GAME (summary):
- 3-player trick-taking card game with 32 cards (7-A in 4 suits)
- Each player gets 10 cards, 2 go to the talon
- Auction: players bid for the right to declare a contract (pass/game 2-5/in_hand/betl/sans)
- Declarer must win a certain number of tricks based on contract level
- Non-declarers can "whist" (follow) to try to defeat the declarer, or pass
- Scoring: declarer gets positive score if they make their contract, negative if they fail
- Whisting players get positive score based on tricks they take against the declarer
- Players who pass during whisting get 0 score
- Neural players are references — you cannot modify them

COMPACT LOG FORMAT:
- Hand is shown as [[suit1 ranks], [suit2 ranks], [suit3 ranks], [suit4 ranks]] sorted by suit strength
- Bid lines: "Name bid: hand -> bid_value" (0 = game bid, pass = pass)
- Declaration lines: "Name declaration: hand, suit_index -> call/pass" (suit_index = trump suit position in hand)
- Score lines: "Name score: N"

PLAYER CLASS ARCHITECTURE — READ THIS CAREFULLY:

PlayerAlice inherits: BasePlayer -> RandomMovePlayer -> WeightedRandomPlayer -> PlayerAlice

There are TWO layers of methods:
1. LOW-LEVEL "choose_*" methods — called by the game engine directly
2. HIGH-LEVEL "decision" methods — called BY the choose_* methods, with FULL HAND ACCESS

The choose_* methods are DISPATCHERS. They retrieve self._hand and call the decision methods.
For example, choose_whist_action() does:
    hand = getattr(self, '_hand', [])
    decision = self.following_decision(hand, contract_type, trump_suit, legal_actions)
And choose_bid() does:
    hand = getattr(self, '_hand', [])
    decision = self.bid_intent(hand, legal_bids)

DO NOT override choose_bid() or choose_whist_action() — they are dispatchers.
INSTEAD override the DECISION methods that receive the hand:

DECISION METHODS YOU SHOULD OVERRIDE (all receive the hand!):
- bid_intent(self, hand, legal_bids) — decide when to bid/pass. hand = list of Card objects
- discard_decision(self, hand_card_ids, talon_card_ids) — pick discards
- bid_decision(self, hand, legal_levels, winner_bid) — pick contract type, trump, level
- following_decision(self, hand, contract_type, trump_suit, legal_actions) — follow/pass
- decide_to_call(self, hand, contract_type, trump_suit, legal_actions) — call when 1st defender passed
- decide_to_counter(self, hand, contract_type, trump_suit, legal_actions) — counter decision
- choose_card(self, legal_cards) — pick card to play (legal_cards = list of Card objects)

HELPER METHOD AVAILABLE IN BASE CLASS:
- _should_follow_heuristic(self, hand, trump_suit) -> (should_follow: bool, n_trump_tricks: int, sum_reasons: float)
  Uses trump trick counting (AK=2, AD=2, AJx=2, KJx=2, etc.) and side-suit reason analysis
  Any player should follow if: trump_tricks>=2 OR sum_reasons>=3 OR (trump_tricks>=1 AND sum_reasons>=2)

KEY FACTS:
- self._hand is ALWAYS available — it contains the player's current hand as list of Card objects
- self.rng is a seeded Random instance for reproducible randomness
- Card objects have .id (string like "A_spades"), .rank (enum), .suit (enum)
- Card ranks: SEVEN=1, EIGHT=2, NINE=3, TEN=4, JACK=5, QUEEN=6, KING=7, ACE=8
- Card suits: CLUBS=1, DIAMONDS=2, HEARTS=3, SPADES=4
- SUIT_NAMES and RANK_NAMES dicts map enum values to string names
- Rank and Suit are imported enums you can use directly (e.g. Rank.ACE, Suit.SPADES)

IMPORTANT CONSTRAINTS:
- ONLY modify PlayerAlice and its helper methods
- Do NOT rename classes or change constructor signatures (each takes seed parameter)
- Do NOT modify BasePlayer, RandomMovePlayer, WeightedRandomPlayer, RandomMoveNoBetlPlayer
- Do NOT modify NeuralPlayer, PlayerBob, PlayerCarol
- Do NOT modify play_game(), main(), card_str(), hand_str(), or any other function
- Do NOT add imports (all needed imports are already present)
- Keep the code working — syntax errors will crash the game
- The player must handle ALL game situations (bidding, discarding, contract, whisting, playing)
- Use self.rng for any randomness (not random module directly)

ANALYSIS PHILOSOPHY — FOCUS ON MISSING STATS AND CONDITIONS:

Your goal is NOT to tweak existing probability numbers (changing 0.65 to 0.70, etc.).
Instead, look for MISSING heuristics, helper stats, and conditions that would let
Alice make smarter decisions. Examples of what to look for:

1. MISSING HAND PATTERN RECOGNITION:
   - Are there card combinations Alice doesn't detect? (e.g. "void in a suit",
     "AK in same suit as guaranteed 2 tricks", "running sequence like AKQJ")
   - Does Alice recognize when she has a "sure trick" vs a "speculative trick"?
   - Can Alice detect dangerous distributions (e.g. 5-card suit in opponent's trump)?

2. MISSING CONTEXTUAL STATS:
   - Does Alice track what the auction tells her about opponents' hands?
   - Does Alice consider the talon cards she saw when making whisting decisions?
   - Does Alice count how many cards remain in each suit during play?
   - Does Alice track which high cards have been played?

3. MISSING CARD PLAY LOGIC:
   - Does Alice know when to lead trump vs side suits?
   - Does Alice recognize when to hold back an ace vs play it immediately?
   - Does Alice consider the play order (leading vs following)?
   - As a whister, does Alice have a plan (e.g. "take my sure tricks first,
     then lead partner's strong suit")?

4. MISSING DEFENSIVE PATTERNS:
   - Does Alice recognize when to signal partner by playing high/low?
   - Does Alice count declarer's likely trump length?
   - Does Alice know when to force declarer to trump?

For each improvement, create a NEW helper stat or condition rather than adjusting
an existing threshold. For example:
  BAD:  "Changed whist rate from 0.65 to 0.75 for 1-ace hands"
  GOOD: "Added _has_running_sequence() helper that detects AKQ+ in a suit.
         When Alice has a running sequence as whister, she always follows
         because those tricks are guaranteed."

After editing (or skipping), output a 1-2 sentence summary of what you changed and why.
Also update the evolution notes file with insights from this iteration.
ENDRULES

    TUNE_LOG="$ITER_LOG_DIR/claude_output.txt"
    run_claude --dangerously-skip-permissions -p "$(cat "$PROMPT_FILE")" \
        > "$TUNE_LOG" 2>&1
    echo "[iter $i] Alice analysis complete."

    # --- Append improvement summary to improvements.txt ---
    tune_summary=$(tail -5 "$TUNE_LOG" 2>/dev/null || echo "(no summary)")

    {
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "Iteration $i — $(date '+%Y-%m-%d %H:%M')"
        echo "Games: $GAME_COUNT | Alice total: $TOTAL_ALICE | Avg: $AVG_ALICE"
        echo ""
        echo "Summary: $tune_summary"
        echo ""
    } >> "$IMPROVEMENTS_FILE"

    # --- Benchmark every 5 iterations ---
    if (( i % 5 == 0 )); then
        run_benchmark "iter${i}"
        BENCH_FILE="$LOG_DIR/benchmark_iter${i}.txt"
        {
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            echo "BENCHMARK after iteration $i — $(date '+%Y-%m-%d %H:%M')"
            echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
            tail -30 "$BENCH_FILE"
            echo ""
        } >> "$IMPROVEMENTS_FILE"
    fi

    echo ""
    echo "[iter $i] Done. Moving to next iteration."
    echo ""
done

echo ""
echo "=== Alice Evolution Complete ==="
echo ""

# --- Final benchmark ---
run_benchmark "final"
FINAL_BENCH="$LOG_DIR/benchmark_final.txt"
{
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "FINAL BENCHMARK — $(date '+%Y-%m-%d %H:%M')"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    tail -30 "$FINAL_BENCH"
    echo ""
} >> "$IMPROVEMENTS_FILE"
