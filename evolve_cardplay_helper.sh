#!/usr/bin/env bash
# evolve_cardplay_helper.sh — Focused evolution of shared card play logic
# Runs 21 iterations: play 10 games each, analyze losing games trick-by-trick,
# then invoke Claude to improve ONLY the shared card play functions.
#
# Called by evolve_cardplay.sh via nohup. Do not run directly.

set -euo pipefail
unset CLAUDECODE 2>/dev/null || true

err_handler() { echo "[FATAL] Error on line $1, exit code $2"; }
trap 'err_handler $LINENO $?' ERR

cleanup() { kill -- -$$ 2>/dev/null; }
trap cleanup EXIT INT TERM

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GAME_SCRIPT="$SCRIPT_DIR/PrefTestSingleGame.py"
PYTHON="$SCRIPT_DIR/.venv/bin/python3"
LOG_DIR="$SCRIPT_DIR/logs/evolution_cardplay"
mkdir -p "$LOG_DIR"

ITERATIONS=21
GAMES_PER_ITER=10
NOTES_FILE="$LOG_DIR/cardplay_notes.md"
IMPROVEMENTS_FILE="$LOG_DIR/cardplay_improvements.txt"

# --- Initialize notes ---
if [[ ! -f "$NOTES_FILE" ]]; then
    cat > "$NOTES_FILE" <<'EOFNOTES'
# Card Play Evolution Notes
Shared context for Claude across card-play evolution iterations.

## Card Play Insights

## Rules Added

## Patterns Observed
EOFNOTES
fi

# Run claude with retry on failure
run_claude() {
    local max_retries=50
    local wait_secs=300
    local attempt=1
    while true; do
        if claude "$@" < /dev/null; then
            return 0
        fi
        if (( attempt >= max_retries )); then
            echo "[cardplay-evolve] Claude failed after $max_retries attempts. Exiting."
            exit 1
        fi
        echo "[cardplay-evolve] Claude failed (attempt $attempt/$max_retries). Waiting ${wait_secs}s before retry..."
        sleep "$wait_secs"
        (( attempt++ ))
    done
}

# Extract losing games analysis from full game logs
# A "losing" game for a player means they were declarer and lost, or they were a
# whister and the declarer made their contract against them.
extract_losing_analysis() {
    local game_log="$1"
    local output="$2"

    # Check if declarer lost
    local declarer_lost
    declarer_lost=$(grep -c "LOST$" "$game_log" 2>/dev/null || true)

    if (( declarer_lost > 0 )); then
        echo "=== DECLARER LOST ===" >> "$output"
        # Extract the full trick-by-trick play
        sed -n '/--- Dealt Hands ---/,/--- Scoring ---/p' "$game_log" >> "$output"
        # Extract scoring
        grep -A5 -- "--- Scoring ---" "$game_log" >> "$output"
        echo "" >> "$output"
        return 0
    fi

    # Check if declarer won (meaning whisters lost)
    local declarer_won
    declarer_won=$(grep -c "WON$" "$game_log" 2>/dev/null || true)

    if (( declarer_won > 0 )); then
        # Check if any whister got negative or 0 score
        local whister_bad
        whister_bad=$(grep -P 'tricks=\d+, score=(-\d+|0)' "$game_log" 2>/dev/null | grep -v "Declarer" || echo "")
        if [[ -n "$whister_bad" ]]; then
            echo "=== WHISTERS COULD HAVE DONE BETTER ===" >> "$output"
            sed -n '/--- Dealt Hands ---/,/--- Scoring ---/p' "$game_log" >> "$output"
            grep -A5 -- "--- Scoring ---" "$game_log" >> "$output"
            echo "" >> "$output"
            return 0
        fi
    fi

    # Also include close games (declarer barely won/lost)
    return 1
}

echo "=== Preferans Card Play Evolution ==="
echo "Iterations: $ITERATIONS ($GAMES_PER_ITER games each)"
echo "Players: alice, bob, carol (all 3 every game)"
echo "Focus: shared card play functions only"
echo "Log dir: $LOG_DIR"
echo ""

for i in $(seq 1 $ITERATIONS); do
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Iteration $i / $ITERATIONS — Card Play Analysis"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # --- Play GAMES_PER_ITER games with all 3 heuristic players ---
    SCORES_FILE="$LOG_DIR/scores_iter${i}.txt"
    > "$SCORES_FILE"

    LOSING_ANALYSIS="$LOG_DIR/losing_analysis_iter${i}.txt"
    > "$LOSING_ANALYSIS"

    FULL_TRICKS="$LOG_DIR/full_tricks_iter${i}.txt"
    > "$FULL_TRICKS"

    for g in $(seq 1 $GAMES_PER_ITER); do
        GAME_LOG="$LOG_DIR/game_iter${i}_g${g}.txt"
        COMPACT_LOG="$LOG_DIR/compact_iter${i}_g${g}.txt"

        STDERR_LOG="$LOG_DIR/stderr_iter${i}_g${g}.txt"
        if ! $PYTHON "$GAME_SCRIPT" --players "alice,bob,carol" 2>"$STDERR_LOG" | tee "$GAME_LOG"; then
            echo "[iter $i] Game $g CRASHED. Stderr:"
            cat "$STDERR_LOG"
            echo ""
            echo "[iter $i] Skipping game $g, continuing..."
            continue
        fi
        rm -f "$STDERR_LOG"
        echo ""
        echo "[iter $i] Game $g/$GAMES_PER_ITER saved to $GAME_LOG"

        # Capture compact log
        LATEST_COMPACT=$(ls -t "$SCRIPT_DIR"/game_compact_*.txt 2>/dev/null | head -1)
        if [[ -n "$LATEST_COMPACT" ]]; then
            cp "$LATEST_COMPACT" "$COMPACT_LOG"
            rm -f "$LATEST_COMPACT"
        fi

        # Clean up full game output file too
        LATEST_OUTPUT=$(ls -t "$SCRIPT_DIR"/game_output_*.txt 2>/dev/null | head -1)
        if [[ -n "$LATEST_OUTPUT" ]]; then
            rm -f "$LATEST_OUTPUT"
        fi

        # Extract scores
        for pname in alice bob carol; do
            cap_name=$(echo "$pname" | sed 's/./\U&/')
            sc=$(grep -oP "${cap_name}: tricks=\d+, score=\K-?\d+" "$GAME_LOG" 2>/dev/null || echo "")
            if [[ -n "$sc" ]]; then
                echo "  Game $g: ${cap_name}=$sc" >> "$SCORES_FILE"
            fi
        done

        # Always include full trick-by-trick in the tricks file
        echo "=== Game $g ===" >> "$FULL_TRICKS"
        sed -n '/--- Dealt Hands ---/,/^$/p' "$GAME_LOG" >> "$FULL_TRICKS"
        sed -n '/--- Playing/,/--- Scoring ---/p' "$GAME_LOG" >> "$FULL_TRICKS"
        grep -A10 -- "--- Scoring ---" "$GAME_LOG" >> "$FULL_TRICKS" || true
        echo "" >> "$FULL_TRICKS"

        # Extract losing games for focused analysis
        extract_losing_analysis "$GAME_LOG" "$LOSING_ANALYSIS" || true
    done

    # --- Compute totals ---
    alice_total=0; bob_total=0; carol_total=0
    for g in $(seq 1 $GAMES_PER_ITER); do
        GAME_LOG="$LOG_DIR/game_iter${i}_g${g}.txt"
        a=$(grep -oP 'Alice: tricks=\d+, score=\K-?\d+' "$GAME_LOG" 2>/dev/null || echo "")
        b=$(grep -oP 'Bob: tricks=\d+, score=\K-?\d+' "$GAME_LOG" 2>/dev/null || echo "")
        c=$(grep -oP 'Carol: tricks=\d+, score=\K-?\d+' "$GAME_LOG" 2>/dev/null || echo "")
        if [[ -n "$a" ]]; then (( alice_total += a )) || true; fi
        if [[ -n "$b" ]]; then (( bob_total += b )) || true; fi
        if [[ -n "$c" ]]; then (( carol_total += c )) || true; fi
    done
    echo "[iter $i] Totals — Alice: $alice_total | Bob: $bob_total | Carol: $carol_total"
    echo "Total scores — Alice: $alice_total | Bob: $bob_total | Carol: $carol_total" >> "$SCORES_FILE"

    # Count losing games
    losing_count=$(grep -c "=== DECLARER LOST ===" "$LOSING_ANALYSIS" 2>/dev/null || true)
    whister_bad_count=$(grep -c "=== WHISTERS COULD HAVE DONE BETTER ===" "$LOSING_ANALYSIS" 2>/dev/null || true)
    echo "[iter $i] Losing games: $losing_count declarer losses, $whister_bad_count whister suboptimal"

    # --- Build score history ---
    HISTORY_FILE="$LOG_DIR/history_iter${i}.txt"
    > "$HISTORY_FILE"
    for prev in $(seq 1 $i); do
        pa=0; pb=0; pc=0
        for pg in $(seq 1 $GAMES_PER_ITER); do
            prev_log="$LOG_DIR/game_iter${prev}_g${pg}.txt"
            if [[ -f "$prev_log" ]]; then
                a=$(grep -oP 'Alice: tricks=\d+, score=\K-?\d+' "$prev_log" 2>/dev/null || echo "")
                b=$(grep -oP 'Bob: tricks=\d+, score=\K-?\d+' "$prev_log" 2>/dev/null || echo "")
                c=$(grep -oP 'Carol: tricks=\d+, score=\K-?\d+' "$prev_log" 2>/dev/null || echo "")
                if [[ -n "$a" ]]; then (( pa += a )) || true; fi
                if [[ -n "$b" ]]; then (( pb += b )) || true; fi
                if [[ -n "$c" ]]; then (( pc += c )) || true; fi
            fi
        done
        echo "  Iter $prev: Alice=$pa Bob=$pb Carol=$pc" >> "$HISTORY_FILE"
    done

    # --- Build prompt for Claude ---
    echo ""
    echo "[iter $i] Invoking Claude to analyze and improve card play logic..."

    PROMPT_FILE="$LOG_DIR/prompt_cardplay_iter${i}.txt"
    cat > "$PROMPT_FILE" <<ENDHEADER
You are optimizing the SHARED card play logic for a Preferans card game in the file:
$GAME_SCRIPT

Read the card play evolution notes from previous iterations:
- $NOTES_FILE

GAME RULES (summary):
- 3-player trick-taking card game with 32 cards (7-A in 4 suits)
- Each player gets 10 cards, 2 go to the talon
- Declarer must win tricks based on contract level (level 2 needs 6 tricks, etc.)
- Followers try to prevent declarer from winning
- Trump suit beats all non-trump cards
- Must follow suit if possible
- Card ranks: SEVEN=1, EIGHT=2, NINE=3, TEN=4, JACK=5, QUEEN=6, KING=7, ACE=8
- Card suits: CLUBS=1, DIAMONDS=2, HEARTS=3, SPADES=4
- Counter-clockwise play order: 1→3→2→1

FULL TRICK-BY-TRICK LOGS FROM THIS ITERATION ($GAMES_PER_ITER games):

ENDHEADER

    cat "$FULL_TRICKS" >> "$PROMPT_FILE"

    cat >> "$PROMPT_FILE" <<ENDLOSING

LOSING GAMES ANALYSIS (games where declarer lost or whisters performed poorly):

ENDLOSING

    if [[ -s "$LOSING_ANALYSIS" ]]; then
        cat "$LOSING_ANALYSIS" >> "$PROMPT_FILE"
    else
        echo "(No losing games this iteration — all declarers won)" >> "$PROMPT_FILE"
    fi

    cat >> "$PROMPT_FILE" <<ENDSCORES

SCORES THIS ITERATION:
ENDSCORES

    cat "$SCORES_FILE" >> "$PROMPT_FILE"

    cat >> "$PROMPT_FILE" <<ENDHISTORY

SCORE HISTORY ACROSS ALL ITERATIONS:
ENDHISTORY

    cat "$HISTORY_FILE" >> "$PROMPT_FILE"

    cat >> "$PROMPT_FILE" <<'ENDRULES'

YOUR TASK — CARD PLAY ANALYSIS AND IMPROVEMENT:

You must ONLY modify the shared card play functions. These are the functions you can change:
- _ctx_trick_winner(ctx) — determine who's winning the current trick
- _ctx_is_trick_winnable(legal_cards, ctx) — can we beat the current trick winner?
- _ctx_other_follower_winning(ctx) — is the other follower currently winning?
- _ctx_is_through_declarer(ctx) — am I leading with declarer playing next?
- _ctx_is_unsupported_king(card, hand) — is this a singleton king?
- _ctx_suit_remaining(suit, ctx) — how many cards of this suit are unaccounted for?
- _shared_whister_lead(legal_cards, ctx, trump_val) — follower's lead card selection
- _shared_declarer_lead(legal_cards, ctx, trump_val, trump_leads_counter) — declarer's lead card
- _shared_must_follow(legal_cards, ctx, played, is_declarer, trump_val, params) — following suit
- _shared_cant_follow(legal_cards, ctx, is_declarer, trump_val, params) — can't follow suit
- _shared_betl_defender_lead(legal_cards, hand) — betl defender leading

You may also ADD new shared helper functions (prefix them with _ctx_ or _shared_).

DO NOT MODIFY:
- Any player class (PlayerAlice, PlayerBob, PlayerCarol, NeuralPlayer)
- Any player's choose_card, choose_bid, choose_discard, choose_contract, choose_whist_action
- BasePlayer, RandomMovePlayer, WeightedRandomPlayer, RandomMoveNoBetlPlayer
- play_game(), main(), or the CardPlayContext dataclass
- Do NOT add imports

ANALYSIS APPROACH — follow these steps:

1. STUDY THE TRICK LOGS carefully. For each losing game:
   a. Look at the dealt hands — what cards did each player have?
   b. Look at the trick-by-trick play sequence
   c. Identify specific tricks where a DIFFERENT card choice would have won more tricks
   d. Example insight: "In Game 3, Alice (declarer) played Q♣ on trick 3 but should have
      played J♠ (trump) first to draw out opponent trumps before cashing side-suit winners"

2. IDENTIFY PATTERNS across multiple games:
   - Are declarers losing because they cash winners too early before drawing trumps?
   - Are declarers losing because they lead trumps too aggressively?
   - Are followers failing to coordinate (e.g., both playing high when one should duck)?
   - Are followers wasting trumps on tricks the other follower could win?
   - Are there suit-length issues (e.g., not leading from longest suit)?
   - Is the timing of trump plays suboptimal?

3. CONVERT INSIGHTS INTO RULES. For each pattern you identify:
   - Formulate it as a concrete rule with conditions and actions
   - Example: "If declarer has 4+ trumps, lead trumps first to draw 2 rounds before
     cashing side-suit aces"
   - Example: "If following suit and partner is already winning, play lowest to save
     high cards for later"
   - Example: "When leading as whister, if declarer plays after me (through position),
     lead from my weakest suit to give partner information"

4. IMPLEMENT the rules in the shared functions. Add the logic with clear comments
   explaining the reasoning. Use card tracking (ctx.played_cards) to make informed
   decisions about what opponents might still hold.

5. UPDATE the evolution notes file ($NOTES_FILE) with:
   - New insights discovered this iteration
   - Rules added and their rationale
   - Any patterns that need more data to confirm

After analysis, edit the shared functions in the game file AND update the notes file.
If no improvements are warranted, output:
"SKIP: No improvements found this iteration."
Otherwise output a summary of changes made.
ENDRULES

    TUNE_LOG="$LOG_DIR/claude_cardplay_iter${i}.txt"
    run_claude --dangerously-skip-permissions -p "$(cat "$PROMPT_FILE")" \
        > "$TUNE_LOG" 2>&1
    echo "[iter $i] Card play logic updated."

    # --- Append summary to improvements file ---
    tune_summary=$(tail -5 "$TUNE_LOG" 2>/dev/null || echo "(no summary)")

    {
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "Iteration $i — $(date '+%Y-%m-%d %H:%M') — Card Play"
        echo "Totals: Alice=$alice_total Bob=$bob_total Carol=$carol_total"
        echo "Losing: $losing_count declarer, $whister_bad_count whister"
        echo ""
        echo "Changes: $tune_summary"
        echo ""
    } >> "$IMPROVEMENTS_FILE"

    echo ""
    echo "[iter $i] Done. Moving to next iteration."
    echo ""
done

echo ""
echo "=== Card Play Evolution Complete ==="
echo ""

# --- Final summary ---
echo "Score History (totals per iteration):"
echo "Iter |  Alice |    Bob |  Carol"
echo "-----|--------|--------|-------"
for it in $(seq 1 $ITERATIONS); do
    ta=0; tb=0; tc=0
    for g in $(seq 1 $GAMES_PER_ITER); do
        log="$LOG_DIR/game_iter${it}_g${g}.txt"
        if [[ -f "$log" ]]; then
            a=$(grep -oP 'Alice: tricks=\d+, score=\K-?\d+' "$log" 2>/dev/null || echo "")
            b=$(grep -oP 'Bob: tricks=\d+, score=\K-?\d+' "$log" 2>/dev/null || echo "")
            c=$(grep -oP 'Carol: tricks=\d+, score=\K-?\d+' "$log" 2>/dev/null || echo "")
            if [[ -n "$a" ]]; then (( ta += a )) || true; fi
            if [[ -n "$b" ]]; then (( tb += b )) || true; fi
            if [[ -n "$c" ]]; then (( tc += c )) || true; fi
        fi
    done
    printf "%4d | %6d | %6d | %6d\n" "$it" "$ta" "$tb" "$tc"
done
