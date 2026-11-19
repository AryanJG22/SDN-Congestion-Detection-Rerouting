#!/bin/bash
# Usage: MODE=static|adaptive ./run_experiment.sh <background_mbps> [duration=60] [link_down_at_seconds]
# Order matters: clean up FIRST, then start Ryu, then Mininet (mn -c kills Ryu!).
cd "$(dirname "$0")" || exit 1
MODE=${MODE:-adaptive}
LOAD=${1:-8}
DURATION=${2:-60}
LINKDOWN=${3:--1}
RYU_BIN=${RYU_BIN:-$HOME/ryuenv/bin/ryu-manager}
RUN_ID="${MODE}_${LOAD}_$(date +%Y%m%d_%H%M%S)"
RUN_DIR="$PWD/results/$RUN_ID"

[ -x "$RYU_BIN" ] || { echo "ryu-manager not found at $RYU_BIN"; exit 1; }
for f in app/sender.py app/receiver.py controller/monitor.py; do
  [ -f "$f" ] || { echo "missing $f (copy your teammates' files in first)"; exit 1; }
done
mkdir -p "$RUN_DIR"

cleanup() {
  [ -n "$RYU_PID" ] && kill "$RYU_PID" 2>/dev/null
  sleep 1
  sudo mn -c >/dev/null 2>&1
  rm -f /tmp/sdn_report.sock
}
trap cleanup EXIT

echo "== run $RUN_ID (mode=$MODE load=${LOAD}Mbps duration=${DURATION}s)"
sudo mn -c >/dev/null 2>&1
pkill -f ryu-manager 2>/dev/null
rm -f /tmp/sdn_report.sock
sleep 1

MODE="$MODE" RUN_DIR="$RUN_DIR" "$RYU_BIN" controller/main.py > "$RUN_DIR/ryu.log" 2>&1 &
RYU_PID=$!
for i in $(seq 1 20); do
  ss -ltn | grep -q ':6653 ' && break
  sleep 1
done
ss -ltn | grep -q ':6653 ' || { echo "Ryu did not start, see $RUN_DIR/ryu.log"; exit 1; }

sudo python3 topology/experiment.py --run-dir "$RUN_DIR" --duration "$DURATION" \
     --load "$LOAD" --app-rate 4 --bg-start 15 --link-down-at "$LINKDOWN"
RC=$?
sudo chown -R "$USER":"$(id -gn)" "$RUN_DIR" 2>/dev/null

echo "== done (exit $RC). Files in $RUN_DIR:"
ls "$RUN_DIR"
echo "== events:"
cat "$RUN_DIR/events.csv" 2>/dev/null
exit $RC
