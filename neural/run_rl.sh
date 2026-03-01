#!/bin/bash
# REINFORCE self-play training for PrefNet.
# Starts from IL checkpoint and trains via self-play.
#
# Usage: ./neural/run_rl.sh
# Monitor: tail -f neural/rl_train.log
# Check:   grep 'Episode\|Eval\|NEW BEST' neural/rl_train.log

set -e
cd "$(dirname "$0")/.."

VENV=".venv/bin/python3"
LOG="neural/rl_train.log"

EPISODES=50000
LR=1e-4
TEMPERATURE=0.5
TEMP_END=0.1
ENTROPY_COEFF=0.01
EVAL_EVERY=500
EVAL_GAMES=100
SAVE_EVERY=1000

echo "=== REINFORCE Self-Play Training ===" | tee "$LOG"
echo "Episodes: $EPISODES" | tee -a "$LOG"
echo "LR: $LR, Temp: $TEMPERATURE → $TEMP_END" | tee -a "$LOG"
echo "Entropy coeff: $ENTROPY_COEFF" | tee -a "$LOG"
echo "Started: $(date)" | tee -a "$LOG"
echo "" | tee -a "$LOG"

nohup env PYTHONUNBUFFERED=1 $VENV neural/self_play.py \
    --episodes "$EPISODES" \
    --lr "$LR" \
    --temperature "$TEMPERATURE" \
    --temp-end "$TEMP_END" \
    --entropy-coeff "$ENTROPY_COEFF" \
    --eval-every "$EVAL_EVERY" \
    --eval-games "$EVAL_GAMES" \
    --save-every "$SAVE_EVERY" \
    >> "$LOG" 2>&1 &

PID=$!
echo "PID: $PID" | tee -a "$LOG"
echo ""
echo "Monitor with:  tail -f $LOG"
echo "Check eval:    grep 'Eval\|NEW BEST' $LOG"
echo "Kill:          kill $PID"
