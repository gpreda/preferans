#!/usr/bin/env bash
# evolve_cardplay.sh — Launch card-play evolution loop in background via nohup

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/logs/evolution_cardplay/evolve_cardplay.log"
mkdir -p "$(dirname "$LOG_FILE")"

nohup bash "$SCRIPT_DIR/evolve_cardplay_helper.sh" > "$LOG_FILE" 2>&1 &
echo "Card-play evolution started (PID $!) — output: $LOG_FILE"
echo "  tail -f $LOG_FILE"
