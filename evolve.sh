#!/usr/bin/env bash
# evolve.sh — Launch evolution loop in background via nohup

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/logs/evolution/evolve.log"
mkdir -p "$(dirname "$LOG_FILE")"

nohup bash "$SCRIPT_DIR/evolve_helper.sh" > "$LOG_FILE" 2>&1 &
echo "Evolution started (PID $!) — output: $LOG_FILE"
echo "  tail -f $LOG_FILE"
