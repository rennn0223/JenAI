#!/bin/bash
# B4 Isaac Sim endurance driver: patrol through the real JenAI TUI.
# Usage: bash scripts/b4_driver.sh [session=jenai-b4] [log=/tmp/b4_mileage.log]
# Defaults are deliberately bounded. Override with B4_MAX_LAPS,
# B4_MAX_SECONDS, B4_LAP_TIMEOUT_SECONDS, or B4_PATROL_ROUTE for a declared run.
set -u

SESSION=${1:-jenai-b4}
LOG=${2:-/tmp/b4_mileage.log}
MAX_LAPS=${B4_MAX_LAPS:-102}
MAX_SECONDS=${B4_MAX_SECONDS:-72000}
LAP_TIMEOUT_SECONDS=${B4_LAP_TIMEOUT_SECONDS:-720}
PATROL_ROUTE=${B4_PATROL_ROUTE:-map_right_down, map_right_up, map_left_up, map_left_down}
IFS=',' read -r -a ROUTE_POINTS <<< "$PATROL_ROUTE"
EXPECTED_WAYPOINTS=${#ROUTE_POINTS[@]}
RUN_ID=${JENAI_RUN_ID:-b4-$(date +%Y%m%dT%H%M%S)-$$}
LOCK=${LOG}.lock
LAP=0
STARTED_AT=$(date +%s)
EXIT_REASON=completed

for value in "$MAX_LAPS" "$MAX_SECONDS" "$LAP_TIMEOUT_SECONDS"; do
  case "$value" in
    ''|*[!0-9]*|0)
      echo "B4 limits must be positive integers" >&2
      exit 2
      ;;
  esac
done

if ! tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session '$SESSION' does not exist" >&2
  exit 1
fi

exec 9>>"$LOCK"
if ! flock -n 9; then
  echo "another B4 driver already owns $LOCK" >&2
  exit 1
fi

cleanup() {
  code=$?
  stopped_at=$(date +%s)
  stop_sent=false
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux send-keys -t "$SESSION" "/stop" Enter
    stop_sent=true
  fi
  echo "$(date -Is) run_id=$RUN_ID event=stopped reason=$EXIT_REASON exit_code=$code laps=$LAP elapsed_s=$((stopped_at - STARTED_AT)) stop_sent=$stop_sent" >> "$LOG"
}
trap 'EXIT_REASON=signal; exit 130' HUP INT TERM
trap cleanup EXIT

echo "$(date -Is) run_id=$RUN_ID event=started session=$SESSION pid=$$ max_laps=$MAX_LAPS max_seconds=$MAX_SECONDS lap_timeout_s=$LAP_TIMEOUT_SECONDS route=$PATROL_ROUTE" >> "$LOG"

while [ "$LAP" -lt "$MAX_LAPS" ]; do
  NOW=$(date +%s)
  if [ $((NOW - STARTED_AT)) -ge "$MAX_SECONDS" ]; then
    EXIT_REASON=max_seconds
    break
  fi
  if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    EXIT_REASON=session_missing
    break
  fi

  LAP=$((LAP + 1))
  BEFORE=$(tmux capture-pane -t "$SESSION" -S - -p | grep -cE "Patrol finished" || true)
  tmux send-keys -t "$SESSION" "/patrol $PATROL_ROUTE x1" Enter
  START=$(date +%s)
  CURRENT=$BEFORE

  # A stale completion from an earlier lap must not finish the current lap.
  while true; do
    CURRENT=$(tmux capture-pane -t "$SESSION" -S - -p | grep -cE "Patrol finished" || true)
    [ "$CURRENT" -gt "$BEFORE" ] && break
    [ $(( $(date +%s) - START )) -ge "$LAP_TIMEOUT_SECONDS" ] && break
    sleep 20
  done

  RESULT=$(tmux capture-pane -t "$SESSION" -S - -p | grep -E "Patrol finished" | tail -1 | sed 's/^ *//' || true)
  ELAPSED=$(( $(date +%s) - START ))
  if [ "$CURRENT" -gt "$BEFORE" ]; then
    if [[ "$RESULT" == *"Patrol finished: $EXPECTED_WAYPOINTS/$EXPECTED_WAYPOINTS waypoints reached."* ]]; then
      STATUS=completed
    else
      STATUS=partial
    fi
  else
    STATUS=timeout
    RESULT=""
  fi
  echo "$(date -Is) run_id=$RUN_ID lap=$LAP elapsed_s=$ELAPSED status=$STATUS result=${RESULT:-none}" >> "$LOG"
  sleep 10
done

if [ "$LAP" -ge "$MAX_LAPS" ]; then
  EXIT_REASON=max_laps
fi
