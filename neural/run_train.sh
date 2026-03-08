#!/bin/bash
# Train PrefNet with unbuffered output and nohup.
# Usage: ./neural/run_train.sh [num_games]
#   num_games: if provided, collect fresh data first (default: skip, use existing data)
#
# Monitor: tail -f neural/train.log
# Check:   grep 'Epoch\|complete\|parameters' neural/train.log

set -e
cd "$(dirname "$0")/.."

VENV=".venv/bin/python3"
DATA_DIR="neural/data"
MODEL_OUT="neural/models/pref_net.pt"
LOG="neural/train.log"
EPOCHS=50
WARMUP=20
BATCH=256
LR=0.001

# Optional: collect fresh data
if [ -n "$1" ]; then
    NUM_GAMES=$1
    echo "=== Collecting $NUM_GAMES games ===" | tee "$LOG"
    PYTHONUNBUFFERED=1 $VENV neural/collect.py \
        --num-games "$NUM_GAMES" \
        --output-dir "$DATA_DIR" \
        --seed 42 2>&1 | tee -a "$LOG"
    echo "" | tee -a "$LOG"
fi

echo "=== Training PrefNet ===" | tee -a "$LOG"
echo "Epochs: $WARMUP warmup + $EPOCHS joint = $(($WARMUP + $EPOCHS)) total" | tee -a "$LOG"
echo "Output: $MODEL_OUT" | tee -a "$LOG"
echo "Log: $LOG" | tee -a "$LOG"
echo "Started: $(date)" | tee -a "$LOG"
echo "" | tee -a "$LOG"

nohup env PYTHONUNBUFFERED=1 $VENV neural/train.py \
    --data-dir "$DATA_DIR" \
    --epochs "$EPOCHS" \
    --warmup-epochs "$WARMUP" \
    --output "$MODEL_OUT" \
    --batch-size "$BATCH" \
    --lr "$LR" \
    >> "$LOG" 2>&1 &

PID=$!
echo "PID: $PID" | tee -a "$LOG"
echo ""
echo "Monitor with:  tail -f $LOG"
echo "Check epoch:   grep -c 'Epoch' $LOG"
echo "Kill:          kill $PID"
