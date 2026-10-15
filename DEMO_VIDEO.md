# MayaCardio Demo Video Guide

**Requirement:** 5-minute demo showing resilience to network cut, process kill, and power cut.

## Video Structure

### Part 1: Setup (0:00–0:30)
- Show terminal with `python -m src.cli status` (0 samples, ready)
- Start mock service: `biosignal-mock --mode signal --seed 7 --duration 3600 --port 7100`
- Start pipeline: `python -m src.cli start` (begins collecting)

### Part 2: Normal Operation (0:30–1:30)
- Run `python -m src.cli status` every 10 seconds
  - Show: samples increasing, mode=online (if network available), backlog=0
  - Display: JSON output with `samples_saved`, `samples_in_cloud`, `mode`
- Verify data reaching cloud (show cloud sync log or mock backend logs)
- Optional: Show `pipeline results --session <id>` with 2–3 window results (means, HR, magnitude)

### Part 3: Network Cut (1:30–2:45)
**Inject failure:** Disconnect network (unplug ethernet / disable WiFi / iptables drop)
- Show mode switch from "online" → "offline" within 10 seconds
- Keep running: `watch -n 1 'python -m src.cli status'`
- Verify samples continue being saved locally (samples_saved keeps rising)
- Verify backlog accumulates (samples_in_cloud stays constant)
- Show loss log: `tail data/losses.log` (should be empty — no network loss is not data loss)
- Restore network
- Show mode switch back to "online" within 10 seconds
- Verify cloud sync catches up: backlog → 0 within 30 seconds

### Part 4: Process Kill (2:45–4:00)
**Inject failure:** Kill pipeline process (Ctrl+C or `kill -9 <pid>`)
- Show samples_saved before kill (e.g., 50000)
- Kill: `pkill -9 -f "python -m src.cli start"` (or Ctrl+C)
- Restart pipeline: `python -m src.cli start`
- Check status: `python -m src.cli status`
- Verify: samples_saved resumes from ~50000 (not 0)
- Verify: loss log records "process_kill" with recovery timestamp
- Export data: `pipeline export --session <id> --from device | head -5`
  - Show all samples from before kill are present (no gap)

### Part 5: Power Cut (4:00–4:45)
**Simulated power cut:** Graceful shutdown (sys.exit) or hard kill + restart
- Record samples_saved before (e.g., 100000)
- Kill all processes: `pkill -9 -f python`
- Simulate power-off delay: `sleep 5`
- Restart pipeline and check status
- Verify: samples_saved >= previous (WAL recovery)
- Verify: loss log shows max loss window (typically <5 seconds)
- Export and compare: `pipeline export --session <id> --from device | wc -l`
  - Confirm no silent loss

### Part 6: Summary (4:45–5:00)
- Show final stats:
  - `python -m src.cli status` (final sample count, mode, backlog)
  - `ls -lh data/` (database size, loss log, sync logs)
  - `grep -c "OFFLINE_TO_ONLINE\|PROCESS_KILL\|POWER_LOSS" data/losses.log` (failure count)
- Narrate: "Zero data loss across all three failure modes. Network mode switched in <10 seconds. Recovery from process kill and power loss automatic via WAL."

## Script Helpers

### Auto-Failure Injection (optional for scripting)
Create `tools/demo_failures.sh`:
```bash
#!/bin/bash
# Part 1: Start
python -m src.cli status
sleep 2

# Part 3: Network cut (mock with iptables if Linux)
# iptables -A OUTPUT -p tcp --dport 7100 -j DROP
# (or just press Ctrl+C on network service)

sleep 30  # Collect offline
echo "Mode should be OFFLINE now"

# Restore
# iptables -D OUTPUT -p tcp --dport 7100 -j DROP
sleep 30  # Resync

# Part 4: Process kill
pkill -9 -f "python -m src.cli start"
sleep 3
python -m src.cli start &
sleep 10
python -m src.cli status

# Part 5: Simulated hard power-off
sleep 10
pkill -9 -f python
sleep 5
python -m src.cli start &
sleep 10
python -m src.cli status
```

### Recording Setup
- **Tool:** OBS Studio (free, cross-platform) or ffmpeg
- **Codec:** H.264, 1080p, 30fps
- **Audio:** Optional narration or system audio only

### Running the Demo

**Terminal 1: Pipeline**
```bash
python -m src.cli start
```

**Terminal 2: Monitoring**
```bash
watch -n 1 'python -m src.cli status'
```

**Terminal 3: Manual Failures**
- Kill with Ctrl+C or `pkill` commands (as shown in script above)
- Or run `tools/demo_failures.sh` for automated sequence

## Verification Checklist

- [ ] Mode switches online → offline within 10 seconds on network cut
- [ ] Mode switches offline → online within 10 seconds on network restore
- [ ] Samples continue to be saved during offline period (samples_saved increases)
- [ ] Cloud backlog accumulates during offline, then clears after restore
- [ ] Process kill recovers samples from WAL (no silent loss)
- [ ] Power cut recovery via WAL (samples preserved, no gap)
- [ ] Loss log records failures with exact reasons
- [ ] `pipeline export` shows 100% of expected samples (no holes or duplicates)
- [ ] Video is ≤5 minutes

## Post-Demo Review

After recording, verify with:
```bash
# Total samples collected
python -m src.cli status | jq .samples_saved

# Check losses were recorded
cat data/losses.log | head -3

# Verify cloud sync completed
cat data/sync_outages.log | tail -3
```

---

**Note:** This demo validates criteria 5 (mode switching), 1–3 (data safety), and 6 (cloud sync recovery) from the assessment. The video provides visual proof of resilience to all three injected failures.
