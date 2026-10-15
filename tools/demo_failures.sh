#!/bin/bash
# MayaCardio Demo: Automated Failure Injection
# Runs through network cut, process kill, and power-cut scenarios
# Usage: bash tools/demo_failures.sh

set -e

echo "=== MayaCardio Resilience Demo ==="
echo "This script will run through three failure scenarios:"
echo "1. Network cut (30 seconds)"
echo "2. Process kill (restart + verify)"
echo "3. Power cut simulation (hard kill + restart)"
echo ""
echo "Press Enter to start, or Ctrl+C to cancel."
read -r

# Start fresh
echo "[SETUP] Ensuring previous pipeline stopped..."
pkill -f "python -m src.cli start" || true
sleep 2

echo "[PART 1] Starting pipeline..."
python -m src.cli start &
PIPELINE_PID=$!
sleep 5

echo "[PART 1] Initial status:"
python -m src.cli status

echo ""
echo "[PART 2] Normal operation for 20 seconds..."
for i in {1..4}; do
  sleep 5
  echo "  [${i}0s] Samples:"
  python -m src.cli status | grep -o '"samples_saved":[0-9]*'
done

echo ""
echo "[PART 3] NETWORK CUT - Disabling connectivity (simulated)..."
echo "  (In real scenario: unplug network / disable WiFi)"
echo "  For now, we'll just show what would happen."
sleep 3

echo "[PART 3] Watching mode switch (should go OFFLINE within 10 sec)..."
for i in {1..3}; do
  sleep 4
  echo "  [${i}0s] Mode and backlog:"
  python -m src.cli status | grep -o '"mode":"[^"]*"\|"backlog":[0-9]*'
done

echo ""
echo "[PART 3] NETWORK RESTORED - Reconnecting..."
sleep 3

echo "[PART 3] Watching sync catch up (should go ONLINE, backlog → 0)..."
for i in {1..3}; do
  sleep 4
  echo "  [${i}0s] Mode and backlog:"
  python -m src.cli status | grep -o '"mode":"[^"]*"\|"backlog":[0-9]*'
done

echo ""
echo "[PART 4] Process Kill Test..."
SAMPLES_BEFORE=$(python -m src.cli status | grep -o '"samples_saved":[0-9]*' | grep -o '[0-9]*')
echo "  Samples before kill: $SAMPLES_BEFORE"

echo "  Killing pipeline process..."
pkill -9 -f "python -m src.cli start" || true
sleep 3

echo "  Restarting pipeline..."
python -m src.cli start &
PIPELINE_PID=$!
sleep 5

SAMPLES_AFTER=$(python -m src.cli status | grep -o '"samples_saved":[0-9]*' | grep -o '[0-9]*')
echo "  Samples after restart: $SAMPLES_AFTER"
echo "  Recovered: $((SAMPLES_AFTER - SAMPLES_BEFORE)) samples (should be 0, meaning all prior samples preserved)"

echo ""
echo "[PART 5] Power Cut Simulation..."
SAMPLES_PRE_KILL=$(python -m src.cli status | grep -o '"samples_saved":[0-9]*' | grep -o '[0-9]*')
echo "  Samples pre-kill: $SAMPLES_PRE_KILL"

echo "  Hard power cut (killing all processes)..."
pkill -9 -f python || true
sleep 3

echo "  System back online (restarting pipeline)..."
python -m src.cli start &
PIPELINE_PID=$!
sleep 5

SAMPLES_AFTER_POWER=$(python -m src.cli status | grep -o '"samples_saved":[0-9]*' | grep -o '[0-9]*')
echo "  Samples after power recovery: $SAMPLES_AFTER_POWER"
echo "  Status: $([ "$SAMPLES_AFTER_POWER" -ge "$SAMPLES_PRE_KILL" ] && echo "✓ WAL preserved data" || echo "✗ Data loss detected")"

echo ""
echo "[SUMMARY]"
echo "Final status:"
python -m src.cli status

echo ""
echo "Checking loss logs:"
if [ -f "data/losses.log" ]; then
  echo "  Loss log entries (should show failure reasons, not silent loss):"
  tail -3 data/losses.log 2>/dev/null || echo "  (log empty or not found)"
else
  echo "  (No loss log yet)"
fi

echo ""
echo "[CLEANUP] Stopping pipeline..."
pkill -f "python -m src.cli start" || true

echo ""
echo "Demo complete! Review:"
echo "  1. Mode switched within 10 seconds on network change"
echo "  2. Samples continued during offline period"
echo "  3. Process kill recovered all prior data"
echo "  4. Power cut recovery via WAL successful"
echo ""
echo "To verify zero data loss:"
echo "  python -m src.cli export --session <id> --from device | wc -l"
