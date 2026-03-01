#!/usr/bin/env bash
# evolve.sh — Continuous improvement loop for Preferans players
# Runs 100 iterations: play 10 games, then invoke Claude to tune each player separately.

set -euo pipefail
unset CLAUDECODE 2>/dev/null || true

cleanup() { kill -- -$$ 2>/dev/null; }
trap cleanup EXIT INT TERM

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GAME_SCRIPT="$SCRIPT_DIR/PrefTestSingleGame.py"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"
LOG_DIR="$SCRIPT_DIR/logs/evolution"
mkdir -p "$LOG_DIR"

ITERATIONS=100
GAMES_PER_ITER=10
NOTES_FILE="$SCRIPT_DIR/evolution_notes.md"
IMPROVEMENTS_FILE="$SCRIPT_DIR/improvements.txt"

# --- Initialize evolution notes ---
if [[ ! -f "$NOTES_FILE" ]]; then
    cat > "$NOTES_FILE" <<'EOFNOTES'
# Evolution Notes
Shared context for Claude across evolution iterations.
Updated automatically by each Claude invocation.

## Known Issues

## Strategy Observations

## Engine Fixes Applied
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

# Write the common preamble to a temp file, appending player-specific section
# Usage: build_prompt <player_name> <style_description> <analysis_questions> <output_file>
build_prompt() {
    local player_name="$1"
    local style_desc="$2"
    local analysis="$3"
    local output_file="$4"
    local compact_file="$5"
    local scores_file="$6"
    local history_file="$7"
    local iter="$8"

    cat > "$output_file" <<ENDHEADER
You are optimizing a Preferans card game player class in the file:
$GAME_SCRIPT

Read the evolution notes for context from previous iterations:
- $NOTES_FILE

Here are the COMPACT game logs from iteration $iter ($GAMES_PER_ITER games played):

ENDHEADER

    cat "$compact_file" >> "$output_file"

    cat >> "$output_file" <<ENDSCORES

Scores this iteration:
ENDSCORES

    cat "$scores_file" >> "$output_file"

    cat >> "$output_file" <<ENDMID

Score history across all iterations:
ENDMID

    cat "$history_file" >> "$output_file"

    cat >> "$output_file" <<'ENDRULES'

RULES OF THE GAME (summary):
- 3-player trick-taking card game with 32 cards (7-A in 4 suits)
- Each player gets 10 cards, 2 go to the talon
- Auction: players bid for the right to declare a contract (pass/game 2-5/in_hand/betl/sans)
- Declarer must win a certain number of tricks based on contract level
- Non-declarers can "whist" (follow) to try to defeat the declarer, or pass
- Scoring: declarer gets positive score if they make their contract, negative if they fail
- Whisting players get positive score based on tricks they take against the declarer
- Players who pass during whisting get 0 score
- Key strategies: bid conservatively to avoid declaring losing contracts; whist aggressively when you have strong cards; choose trump suits where you have the most/strongest cards

COMPACT LOG FORMAT:
- Hand is shown as [[suit1 ranks], [suit2 ranks], [suit3 ranks], [suit4 ranks]] sorted by suit strength
- Bid lines: "Name bid: hand -> bid_value" (0 = game bid, pass = pass)
- Declaration lines: "Name declaration: hand, suit_index -> call/pass" (suit_index = trump suit position in hand)
- Score lines: "Name score: N"

WHAT YOU CAN CHANGE in the player class:
- Override choose_bid() — decide when to bid, pass, or go aggressive
- Override choose_discard() — pick which cards to discard during exchange
- Override choose_contract() — pick contract type, trump suit, and level
- Override choose_whist_action() — decide whether to whist or pass against a declarer
- Override choose_card() — pick which card to play in a trick
- Add helper methods to analyze hand strength, count suits, etc.
- The player inherits from WeightedRandomPlayer which has self.rng (seeded Random instance)
- Legal bids come as list of dicts with "bid_type" and optionally "value" keys
- Legal cards come as list of Card objects with .id, .rank, .suit attributes
- Card ranks: SEVEN=1, EIGHT=2, NINE=3, TEN=4, JACK=5, QUEEN=6, KING=7, ACE=8
- Card suits: CLUBS=1, DIAMONDS=2, HEARTS=3, SPADES=4
- SUIT_NAMES and RANK_NAMES dicts map enum values to string names

IMPORTANT CONSTRAINTS:
- ONLY modify the specified player class and its helper methods
- Do NOT rename classes or change constructor signatures (each takes seed parameter)
- Do NOT modify BasePlayer, RandomMovePlayer, WeightedRandomPlayer, RandomMoveNoBetlPlayer
- Do NOT modify play_game(), main(), card_str(), hand_str(), or any other function
- Do NOT modify the other two player classes
- Do NOT add imports (all needed imports are already present)
- Keep the code working — syntax errors will crash the game
- The player must handle ALL game situations (bidding, discarding, contract, whisting, playing)
- Use self.rng for any randomness (not random module directly)
ENDRULES

    cat >> "$output_file" <<ENDPLAYER

PLAYER TO TUNE: Player${player_name}
PLAYING STYLE: ${style_desc}

${analysis}

Make targeted improvements to Player${player_name} only. Edit the file now.
After editing, output a 1-2 sentence summary of what you changed and why.
ENDPLAYER
}

echo "=== Preferans Player Evolution ==="
echo "Iterations: $ITERATIONS (${GAMES_PER_ITER} games each)"
echo "Log dir: $LOG_DIR"
echo ""

for i in $(seq 1 $ITERATIONS); do
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Iteration $i / $ITERATIONS"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # --- Play GAMES_PER_ITER games ---
    SCORES_FILE="$LOG_DIR/scores_iter${i}.txt"
    > "$SCORES_FILE"

    for g in $(seq 1 $GAMES_PER_ITER); do
        GAME_LOG="$LOG_DIR/game_iter${i}_g${g}.txt"
        COMPACT_LOG="$LOG_DIR/compact_iter${i}_g${g}.txt"
        $PYTHON "$GAME_SCRIPT" 2>/dev/null | tee "$GAME_LOG"
        echo ""
        echo "[iter $i] Game $g/$GAMES_PER_ITER saved to $GAME_LOG"

        # Capture compact log from the file the game script writes
        LATEST_COMPACT=$(ls -t "$SCRIPT_DIR"/game_compact_*.txt 2>/dev/null | head -1)
        if [[ -n "$LATEST_COMPACT" ]]; then
            cp "$LATEST_COMPACT" "$COMPACT_LOG"
            rm -f "$LATEST_COMPACT"
        fi

        alice_score=$(grep -oP 'Alice: tricks=\d+, score=\K-?\d+' "$GAME_LOG" || echo "?")
        bob_score=$(grep -oP 'Bob: tricks=\d+, score=\K-?\d+' "$GAME_LOG" || echo "?")
        carol_score=$(grep -oP 'Carol: tricks=\d+, score=\K-?\d+' "$GAME_LOG" || echo "?")
        echo "[iter $i] Game $g scores — Alice: $alice_score | Bob: $bob_score | Carol: $carol_score"

        echo "  Game $g: Alice=$alice_score Bob=$bob_score Carol=$carol_score" >> "$SCORES_FILE"
    done

    # --- SKIP irregularity inspection (kept for reference) ---
    : <<'SKIP_INSPECT'
    ... (irregularity inspection code preserved but skipped) ...
SKIP_INSPECT

    # --- Build combined compact logs file ---
    COMPACT_COMBINED="$LOG_DIR/compact_combined_iter${i}.txt"
    > "$COMPACT_COMBINED"
    for g in $(seq 1 $GAMES_PER_ITER); do
        COMPACT_LOG="$LOG_DIR/compact_iter${i}_g${g}.txt"
        echo "=== Game $g ===" >> "$COMPACT_COMBINED"
        if [[ -f "$COMPACT_LOG" ]]; then
            cat "$COMPACT_LOG" >> "$COMPACT_COMBINED"
        else
            GAME_LOG="$LOG_DIR/game_iter${i}_g${g}.txt"
            grep -v '^\[' "$GAME_LOG" >> "$COMPACT_COMBINED"
        fi
        echo "" >> "$COMPACT_COMBINED"
    done

    # --- Compute total scores for this iteration ---
    alice_total=0; bob_total=0; carol_total=0; count=0
    for g in $(seq 1 $GAMES_PER_ITER); do
        GAME_LOG="$LOG_DIR/game_iter${i}_g${g}.txt"
        a=$(grep -oP 'Alice: tricks=\d+, score=\K-?\d+' "$GAME_LOG" 2>/dev/null || echo "0")
        b=$(grep -oP 'Bob: tricks=\d+, score=\K-?\d+' "$GAME_LOG" 2>/dev/null || echo "0")
        c=$(grep -oP 'Carol: tricks=\d+, score=\K-?\d+' "$GAME_LOG" 2>/dev/null || echo "0")
        (( alice_total += a )) || true
        (( bob_total += b )) || true
        (( carol_total += c )) || true
        (( count++ )) || true
    done
    echo "[iter $i] Totals over $count games — Alice: $alice_total | Bob: $bob_total | Carol: $carol_total"

    echo "Total scores this iteration — Alice: $alice_total | Bob: $bob_total | Carol: $carol_total" >> "$SCORES_FILE"

    # --- Build score history file ---
    HISTORY_FILE="$LOG_DIR/history_iter${i}.txt"
    > "$HISTORY_FILE"
    for prev in $(seq 1 $i); do
        for pg in $(seq 1 $GAMES_PER_ITER); do
            prev_log="$LOG_DIR/game_iter${prev}_g${pg}.txt"
            if [[ -f "$prev_log" ]]; then
                a=$(grep -oP 'Alice: tricks=\d+, score=\K-?\d+' "$prev_log" 2>/dev/null || echo "?")
                b=$(grep -oP 'Bob: tricks=\d+, score=\K-?\d+' "$prev_log" 2>/dev/null || echo "?")
                c=$(grep -oP 'Carol: tricks=\d+, score=\K-?\d+' "$prev_log" 2>/dev/null || echo "?")
                echo "  Iter $prev Game $pg: Alice=$a Bob=$b Carol=$c" >> "$HISTORY_FILE"
            fi
        done
    done

    # --- Tune Alice (aggressive — optimizes for high scores) ---
    echo ""
    echo "[iter $i] Tuning Alice (aggressive)..."

    ALICE_PROMPT_FILE="$LOG_DIR/prompt_alice_iter${i}.txt"
    build_prompt "Alice" \
        "AGGRESSIVE — Alice optimizes for getting HIGH SCORES. She should bid boldly when she has strong hands, take risks that can lead to big wins, and play aggressively to maximize trick count. She'd rather win big or lose big than play it safe." \
        "Analyze Alice's performance across all $GAMES_PER_ITER games:
1. When declaring, did she win or lose? Were her bids justified by her hand strength?
2. When whisting, did she gain points? Should she whist more aggressively?
3. What card play patterns led to winning/losing tricks?
4. Is her aggressive style paying off with high scores, or is she overreaching?" \
        "$ALICE_PROMPT_FILE" \
        "$COMPACT_COMBINED" "$SCORES_FILE" "$HISTORY_FILE" "$i"

    ALICE_LOG="$LOG_DIR/claude_alice_iter${i}.txt"
    run_claude --dangerously-skip-permissions -p "$(cat "$ALICE_PROMPT_FILE")" \
        > "$ALICE_LOG" 2>&1
    echo "[iter $i] Alice tuned."

    # --- Tune Bob (cautious — optimizes for fewer failed games) ---
    echo "[iter $i] Tuning Bob (cautious)..."

    BOB_PROMPT_FILE="$LOG_DIR/prompt_bob_iter${i}.txt"
    build_prompt "Bob" \
        "CAUTIOUS — Bob optimizes for FEWER FAILED GAMES. He should bid conservatively, only declaring when he has a very strong hand, and pass when uncertain. He prefers consistent small gains over risky big wins. He'd rather score 0 than risk a negative score." \
        "Analyze Bob's performance across all $GAMES_PER_ITER games:
1. When declaring, did he succeed? Were his bids conservative enough?
2. When whisting, was he selective? Should he only whist with very strong holdings?
3. Did he avoid negative scores? That's his primary goal.
4. Is his cautious style keeping him safe, or is he missing easy opportunities?" \
        "$BOB_PROMPT_FILE" \
        "$COMPACT_COMBINED" "$SCORES_FILE" "$HISTORY_FILE" "$i"

    BOB_LOG="$LOG_DIR/claude_bob_iter${i}.txt"
    run_claude --dangerously-skip-permissions -p "$(cat "$BOB_PROMPT_FILE")" \
        > "$BOB_LOG" 2>&1
    echo "[iter $i] Bob tuned."

    # --- Tune Carol (pragmatic — takes calculated risks) ---
    echo "[iter $i] Tuning Carol (pragmatic)..."

    CAROL_PROMPT_FILE="$LOG_DIR/prompt_carol_iter${i}.txt"
    build_prompt "Carol" \
        "PRAGMATIC — Carol takes CALCULATED RISKS. She balances aggression and caution, bidding when the odds are in her favor and passing when they're not. She evaluates hand strength carefully and makes data-driven decisions. She aims for the best expected value." \
        "Analyze Carol's performance across all $GAMES_PER_ITER games:
1. When declaring, did she pick the right contracts? Were her risk assessments accurate?
2. When whisting, did she make profitable decisions?
3. What card play patterns worked well or poorly?
4. Is her balanced approach producing good expected value across games?" \
        "$CAROL_PROMPT_FILE" \
        "$COMPACT_COMBINED" "$SCORES_FILE" "$HISTORY_FILE" "$i"

    CAROL_LOG="$LOG_DIR/claude_carol_iter${i}.txt"
    run_claude --dangerously-skip-permissions -p "$(cat "$CAROL_PROMPT_FILE")" \
        > "$CAROL_LOG" 2>&1
    echo "[iter $i] Carol tuned."

    # --- Append improvement summary to improvements.txt ---
    alice_summary=$(tail -5 "$ALICE_LOG" 2>/dev/null || echo "(no summary)")
    bob_summary=$(tail -5 "$BOB_LOG" 2>/dev/null || echo "(no summary)")
    carol_summary=$(tail -5 "$CAROL_LOG" 2>/dev/null || echo "(no summary)")

    {
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "Iteration $i"
        echo "Totals: Alice=$alice_total Bob=$bob_total Carol=$carol_total"
        echo ""
        echo "Alice: $alice_summary"
        echo ""
        echo "Bob: $bob_summary"
        echo ""
        echo "Carol: $carol_summary"
        echo ""
    } >> "$IMPROVEMENTS_FILE"

    echo ""
    echo "[iter $i] All players tuned. Moving to next iteration."
    echo ""
done

echo ""
echo "=== Evolution Complete ==="
echo ""

# --- Final summary ---
echo "Score History:"
echo "Iter | Game | Alice |   Bob | Carol"
echo "-----|------|-------|-------|------"
for it in $(seq 1 $ITERATIONS); do
    for g in $(seq 1 $GAMES_PER_ITER); do
        log="$LOG_DIR/game_iter${it}_g${g}.txt"
        if [[ -f "$log" ]]; then
            a=$(grep -oP 'Alice: tricks=\d+, score=\K-?\d+' "$log" 2>/dev/null || echo "?")
            b=$(grep -oP 'Bob: tricks=\d+, score=\K-?\d+' "$log" 2>/dev/null || echo "?")
            c=$(grep -oP 'Carol: tricks=\d+, score=\K-?\d+' "$log" 2>/dev/null || echo "?")
            printf "%4d | %4d | %5s | %5s | %5s\n" "$it" "$g" "$a" "$b" "$c"
        fi
    done
done
