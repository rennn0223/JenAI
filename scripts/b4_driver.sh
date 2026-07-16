#!/bin/bash
# B4 simulated-mileage driver (V1_GATE B4): patrol laps via the real TUI.
# 前提:tmux session 裡已跑 `uv run JenAI`(ROS-sourced)且切到 auto 模式(Shift+Tab),
# 走真實批准鏈與 patrol 日報 —— 里程數據與人工操作同一條路徑,不走捷徑。
# 用法: bash scripts/b4_driver.sh [session=jenai-b4] [log=/tmp/b4_mileage.log]
SESSION=${1:-jenai-b4}
LOG=${2:-/tmp/b4_mileage.log}
LAP=0
echo "$(date -Is) B4 driver started" >> "$LOG"
while true; do
  LAP=$((LAP+1))
  tmux send-keys -t "$SESSION" "/patrol map_right_down, map_wall, dock, map_left_up x1" Enter
  START=$(date +%s)
  # each lap ~4 waypoints; give it up to 12 min, poll the pane tail
  until tmux capture-pane -t "$SESSION" -p | tail -8 | grep -qE "Patrol finished"; do
    sleep 20
    [ $(( $(date +%s) - START )) -gt 720 ] && break
  done
  RESULT=$(tmux capture-pane -t "$SESSION" -p | grep -E "Patrol finished" | tail -1 | sed 's/^ *//')
  echo "$(date -Is) lap=$LAP $(( $(date +%s) - START ))s ${RESULT:-timeout}" >> "$LOG"
  sleep 10
done
