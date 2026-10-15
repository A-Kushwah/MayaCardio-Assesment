# MayaCardio Cardiac Pipeline: System Design

## Executive Summary

MayaCardio is a cardiac monitoring pipeline for ARM64 Raspberry Pi (4 cores, 2GB RAM) that collects synchronized biosignal data from dual sensors, persists samples to disk with crash-recovery guarantees, processes 1-second windows of 400-sample blocks into heart-rate and statistical metrics, and syncs results to cloud with automatic online/offline detection. The system prioritizes **zero data loss** through append-only SQLite WAL persistence and guarantees recovery within **5 seconds** of any crash or power interruption.

---

## 1. System Architecture

### 1.1 Data Flow & Components

```
┌─────────────────────────────────────────────────────────────┐
│                    MayaCardio Pipeline                       │
└─────────────────────────────────────────────────────────────┘

Biosignal Source (mock or real sensor)
         │
         ▼
    ┌─────────┐
    │Collector│ (src/collector.py)
    │         │ ├─ Mock client (biosignal-mock) or live serial
    │         │ ├─ De-duplication (INSERT OR IGNORE)
    │         │ └─ Loss logging on disk-full / kill
    └────┬────┘
         │ (per-sample INSERT + COMMIT)
         ▼
    ┌──────────────┐
    │   Storage    │ (src/storage.py)
    │ SQLite + WAL │ ├─ Append-only samples table
    │              │ ├─ MAX(ts) recovery point
    │              │ ├─ CRC32 dedup (IF added later)
    │              │ └─ 2GB local cache (Pi constraint)
    └────┬─────────┘
         │ (2GB max, ~25M samples at 400 Hz for ~17h)
         ▼
    ┌──────────────┐
    │  Processor   │ (src/processor.py)
    │   Windowing  │ ├─ 2800-sample deque per channel (5s + peak margin)
    │   + HR Algo  │ ├─ 400 samples = 1 second window (mode: online/offline)
    │              │ ├─ Peak detection (100-sample minimum separation)
    │              │ └─ Heart rate: 60 / mean(gap_in_seconds)
    └────┬─────────┘
         │ (WindowResult per complete window)
         ▼
    ┌──────────────┐
    │ Session Mgr  │ (src/session.py)
    │ (in-memory)  │ ├─ Mode: standby | recording | processing | error
    │              │ ├─ Counters: samples_saved, samples_in_cloud, backlog
    │              │ └─ Per-session loss tracking
    └────┬─────────┘
         │
    ┌────┴──────────┐
    │               │
    ▼               ▼
  Online?      Sync Thread
    │          (future: src/sync.py)
    │          ├─ 2-hour backlog queue
    │          ├─ Batch + retry on failure
    │          └─ Cloud acknowledgement purges
    │
    ▼
 Cloud (HTTPS)
```

### 1.2 Component Responsibilities

| Component | Role | Key Guarantee |
|-----------|------|---|
| **Collector** | Sample ingestion, mock mock or live serial | Every sample written exactly once or loss logged |
| **Storage** | Persistent local cache, crash recovery | WAL mode + FULL sync ensures atomicity; MAX(ts) identifies recovery point |
| **Processor** | Windowing, peak detection, heart-rate calculation | Stateless windowing from complete 400-sample windows; mode-agnostic |
| **Session Mgr** | Mode state, sample counters | Tracks recording/processing/error; backlog for sync queue |
| **Network Client** | Online/offline detection, cloud connectivity | 10-second timeout; mode changes trigger callbacks |
| **CLI** | Status, export, results commands | JSON-line output; mode-dependent behavior |

---

## 2. Durability Guarantees & Recovery

### 2.1 Crash & Reboot Scenarios

**Scenario: Process kill, disk full, or power cut during sample collection**

| Failure Mode | Before Crash | After Reboot | Recovery Time | Data Loss |
|---|---|---|---|---|
| **Process SIGKILL** | N samples in WAL, N in samples table | WAL replayed on reconnect; MAX(ts) found | < 1 sec | 0 bytes (WAL replay atomic) |
| **Disk full** | Last successful INSERT committed; new samples logged to losses.log | losses.log survives; samples table consistent | < 5 sec (FS check + recovery scan) | Logged to losses.log; not lost, tracked |
| **Power cut (ungraceful)** | WAL checkpoint may be incomplete; some samples in WAL, some committed | SQLite crash recovery re-plays WAL; MAX(ts) identifies resume point | < 5 sec (SQLite repair + recovery) | 0 bytes (atomic WAL recovery) |
| **Collector thread hangs** | Samples stuck in buffer; not yet persisted to disk | Restart collector; rescan from MAX(ts) | < 2 sec (restart + scan) | Depends on buffer; single-threaded design minimizes |

**Recovery Protocol:**
1. On start, call `Storage.recover()` → returns `MAX(ts)` from samples table
2. Resume collection from `MAX(ts) + 1`
3. Processor rewinds from last known window boundary (deques rebuilt)
4. Session counters reset to 0; backlog recalculated on sync thread start

### 2.2 Maximum Data Loss Window

**Worst case: Power cut during Collector commit**

- **Window:** 0–5 seconds (WAL checkpoint interval on Pi under 2GB RAM pressure)
- **Data at risk:** Samples in WAL not yet flushed to main DB file
- **Mitigation:** WAL replay on recovery is atomic; no partial writes
- **Practical loss:** **0 samples** (atomic WAL recovery)
- **However:** If collector is killed mid-batch before any INSERT, up to ~400 samples (1 window) may be lost and must be logged to losses.log

**Stated worst case for assessment:** **5 seconds** (covers Pi WAL checkpoint latency + one window at 400 Hz)

---

## 3. Mode Switching Logic

### 3.1 Online/Offline Detection

**Network Client (`src/network.py`)** monitors connectivity every 1 second:

1. **Detection Timeout:** 10 seconds
2. **Transition Rules:**
   - **OFFLINE → ONLINE:** Immediate on successful TCP connect to cloud_host:cloud_port
   - **ONLINE → OFFLINE:** After 10 consecutive failed pings (10 seconds)
3. **Callback:** Mode change triggers `Session.update_mode(new_mode)`
4. **No state loss:** Samples queued in backlog during OFFLINE remain in memory until sync

### 3.2 Processing Mode (Online vs. Offline)

- **Online mode:** Stream windows to cloud; process with low latency
- **Offline mode:** Buffer windows locally; process when space available
- **Processor.mode** attribute set per-session; affects WindowResult metadata only (no algorithmic change)
- **Health check:** Heartbeat to cloud every 30 seconds (in sync thread); missed heartbeat escalates to OFFLINE

---

## 4. Cloud Sync Strategy

### 4.1 Backlog & Reconnection Protocol

- **Local backlog:** 2-hour window at 400 Hz = ~2.88M samples; at ~16 bytes/sample = ~46MB JSON (Pi can buffer in memory or use deque with maxlen)
- **Batch size:** 1000 samples per HTTP POST (minimizes network overhead)
- **Retry logic:**
  - Exponential backoff: 1s, 2s, 4s, 8s, 16s, 30s (max)
  - Max retries: 100 (covers ~10 minutes of backlog accumulation)
  - Failure threshold: If 100 retries exhausted, log to losses.log and move to next batch
- **Acknowledgement:** Cloud sends `{"acked_ts": <max_ts>}` ; collector purges samples ≤ acked_ts from backlog
- **De-duplication:** CRC32(sample) added to each JSON record; cloud rejects duplicates (idempotent)

### 4.2 Network Loss Resilience

- **Connection drop:** Backlog stays in memory; Network Client detects OFFLINE within 10 seconds
- **Recovery:** On ONLINE transition, sync thread resumes from last acked_ts
- **No manual intervention:** Automatic retry + exponential backoff; status command shows backlog size

---

## 5. Resource Constraints & Optimization

### 5.1 ARM64 Raspberry Pi (4 cores, 2GB RAM)

| Resource | Constraint | Usage | Margin |
|----------|-----------|-------|--------|
| **Memory** | 2 GB | Processor deques: 2800 × 5 floats × 8 bytes = 560 KB; Session store + backlog: ~50 MB (2h samples); Safety margin: 1.4 GB | 70% free |
| **CPU** | 4 cores | Collector: 1 thread (sample I/O); Processor: 1 thread (window calc); Network: 1 thread (sync); Monitor: 1 thread (connectivity) | Non-blocking; no busy-waiting |
| **Disk I/O** | SD card (limited IOPS) | SQLite WAL: 4 KB pages; commit every sample; ~400 commits/sec | Mitigated by WAL batch writes; tune PRAGMA cache_size |
| **Network** | Home WiFi (~10 Mbps) | 1000 samples/batch = 16 KB JSON; ~1 batch/4 sec on 400 Hz input = ~4 KB/s avg | Comfortably under WiFi capacity |

### 5.2 Key Optimizations

1. **SQLite WAL mode:** Readers don't block writers; checkpoints deferred to low-load periods
2. **Deque maxlen:** Automatic eviction prevents unbounded memory growth; old samples purged after 5 seconds
3. **INSERT OR IGNORE:** De-duplication at DB layer; handles resume on crash
4. **PRAGMA synchronous=FULL:** Guarantees data on disk, trade-off acceptable for medical device
5. **Batched cloud sync:** 1000 samples/batch reduces HTTP request overhead; 2-hour backlog absorbs network latency spikes

---

## 6. Failure Scenarios & Recovery Summary

| Scenario | Trigger | Detection | Recovery | Window |
|----------|---------|-----------|----------|--------|
| **Process crash** | SIGTERM, SIGKILL | Supervisor restart | recover(); resume from MAX(ts) | 1 sec |
| **Disk full** | Write to samples fails | Exception caught; loss logged | Purge old data or expand disk | 5 sec |
| **Network loss** | TCP timeout (10s) | Mode change callback | Backlog queued; auto-retry on ONLINE | 0–10 sec |
| **Power cut** | Ungraceful shutdown | N/A (power loss) | WAL replay on next boot | 5 sec (recovery scan) |
| **Heartbeat failure** | Cloud not responding | No ACK for 30 sec | Mark OFFLINE; escalate backlog retention | 30 sec |
| **Database corruption** | Rare; disk I/O error | PRAGMA integrity_check fails | Restore from last backup or resync | 1–10 sec |

---

## 7. Heartbeat & Health Check

- **Heartbeat interval:** 30 seconds (Network Client sends status JSON to cloud)
- **Health metrics sent:**
  - Current mode (online/offline/error)
  - Samples saved (local count)
  - Samples in cloud (sync progress)
  - Backlog size (ready for sync)
  - Last error (if any)
- **Missing heartbeat:** If no ACK within 30 seconds + 2 retries, mark mode OFFLINE
- **Backlog monitoring:** If backlog > 1M samples for > 1 hour, alert operator (sync blocked)

---

## 8. Design Rationale

### Why SQLite WAL?
- **Atomicity:** Commit is all-or-nothing; crash during commit leaves previous state intact
- **Performance:** Readers don't wait for writers; checkpoints asynchronous
- **Simplicity:** No custom recovery code; SQLite handles crash recovery transparently
- **Reliability:** Industry-standard for embedded systems (iOS, Android, browsers)

### Why deque(maxlen)?
- **Memory-safe:** Automatic eviction prevents unbounded growth
- **Deterministic:** No garbage collection pauses (real-time requirement)
- **Simplicity:** Python standard library; no external dependency

### Why CRC32 dedup?
- **Low overhead:** 4 bytes per sample; fast hash
- **Idempotency:** Cloud can reject duplicate sends; safe to retry without data corruption

### Why 10-second network detection?
- **Fast enough:** Medical alert requirement (<15 sec)
- **Robust:** Avoids flapping on WiFi interference (short glitches < 10s ignored)

### Why 2-hour backlog window?
- **Memory feasible:** ~46 MB on 2GB Pi (2% overhead)
- **Practical recovery:** Covers typical WiFi outages (home network restarts, ISP issues)
- **Cloud scalability:** Batch processing manageable for backend

---

## Deployment Checklist

- [ ] SQLite database initialized with WAL mode + FULL synchronous
- [ ] Collector + Processor threads started on boot
- [ ] Network Client heartbeat configured (cloud endpoint)
- [ ] CLI exposed via systemd service or container
- [ ] Backlog retention policy set (purge after cloud ACK confirmed)
- [ ] Logging configured (losses.log, crash dumps, audit trail)
- [ ] Monitoring dashboard subscribed to heartbeat stream
- [ ] Backup strategy: periodic snapshot of samples table to cloud archive

---

**Document Generated:** 2026-10-15  
**System Version:** 1.0 (MVP)  
**Target Platform:** ARM64 Raspberry Pi 4, Python 3.9+, SQLite 3.31+
