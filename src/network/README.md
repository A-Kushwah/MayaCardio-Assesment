# Network & Cloud Sync Module

## Overview

This module implements the network and cloud synchronization layer for the MayaCardio cardiac monitoring device. It handles:

- **Automatic mode switching**: Detects network loss/return within 10 seconds with zero data loss/duplication
- **Online mode**: Stream data to cloud continuously, process results within 5 seconds per window
- **Offline mode**: Collect and process locally with identical logic
- **Cloud sync**: Send all stored samples when network returns, handle 2-hour outages, complete sync within 15 minutes
- **Data loss tracking**: Record exactly which samples are lost and why, never invent values

## Architecture

### Three Core Modules

#### `network.py` — NetworkClient
Manages connectivity detection and cloud communication.

**Key classes:**
- `NetworkMode`: Enum (ONLINE / OFFLINE)
- `NetworkClient`: Background thread monitors connectivity every 1 second
  - Detects ONLINE → OFFLINE transition after 10 seconds of failed pings
  - Detects OFFLINE → ONLINE transition on first successful ping
  - Calls optional callback on mode changes
  - Provides `send_batch()` method (returns False if offline)

**Design tradeoffs:**
- **1-second check interval**: Balances responsiveness (detects loss quickly) vs. CPU/battery cost
- **10-second timeout**: Allows for transient network blips without false positives
- **TCP socket connect as health check**: Simple, requires no additional service; works behind most firewalls

**Graceful degradation:**
- Monitor thread catches all exceptions and retries
- Failed connectivity checks never crash the device
- Device continues processing locally even if monitor thread dies

#### `sync.py` — CloudSync
Manages buffering, deduplication, and outage tracking.

**Key classes:**
- `SyncSample`: Dataclass for samples with CRC32 checksum
- `WindowResult`: Processing result from processor
- `CloudSync`: Buffer management and deduplication
  - In-memory deque bounded by 2-hour backlog window
  - Automatic cleanup of samples older than `BACKLOG_SECONDS` (7200s)
  - CRC32-based deduplication (detects corrupt/duplicate cloud replies)
  - Batch preparation (configurable `BATCH_SIZE`, default 100 samples)
  - Outage logging to `data/sync_outages.log`
  - Loss logging to `data/losses.log`

**Design tradeoffs:**
- **In-memory buffer**: Bounded by time (2 hours) rather than disk; fast access but limited by RAM
  - Alternative: Sqlite3 queue (what `collector.py` uses) — more durable but slower
  - Choice: RAM for streaming performance; oldest samples auto-dropped after 2 hours
- **CRC32 deduplication**: Lightweight (~30 bytes per sample), catches bit flips and duplicates
  - Not cryptographic (use TLS for security, not CRC32)
- **Graceful disk-full handling**: All file writes wrapped in try/except
  - Device continues processing; data not lost to cloud, just not logged to disk
- **Sample age computed at insertion time**: Samples added to buffer only if within backlog window
  - Prevents unbounded memory growth; old samples never enter buffer

#### `cloud.py` — CloudBackend + CloudClient
Abstraction for free cloud services with unified interface.

**Key classes:**
- `CloudBackend`: Abstract base class for provider implementations
- `FirebaseBackend`: Firebase Realtime Database (free: 100 concurrent connections, 1 GB)
- `SupabaseBackend`: Supabase PostgreSQL (free: 500 MB storage, 2 GB bandwidth/month)
- `MockBackend`: In-memory mock for testing (with optional latency/failure injection)
- `CloudClient`: Unified async client with retry logic

**Design tradeoffs:**
- **Firebase Realtime Database**:
  - Pros: Zero-config, simple REST API, real-time updates
  - Cons: No SQL, not suited for analytics; limited to 1 GB free tier
  - Use case: Real-time dashboard, status sync
- **Supabase PostgreSQL**:
  - Pros: Full SQL, 500 MB free storage, better for analytics
  - Cons: Need to define schema, slightly higher latency than Firebase
  - Use case: Historical data analysis, long-term retention
- **Mock backend**:
  - Configurable latency and failure rates
  - Perfect for local development and resilience testing
  - Default for tests (no credentials required)
- **Async/await design**: Allows concurrent batch sends and result retrieval
  - Requires `asyncio` event loop in main app
  - Alternative: Synchronous blocking calls (simpler, but slower for multiple batches)

**Retry logic:**
- Exponential backoff: `delay = base_delay * 2^attempt`
- Max 3 retries by default (configurable)
- Failed sends don't crash app; sync state tracked separately

## Integration with Processor & Collector

### Data Flow

```
Collector (sensor data) → Processor (windowing) → SyncSample (CRC32) → CloudSync (buffer)
                                                                             ↓
                                                                        NetworkClient
                                                                         (online?)
                                                                             ↓
                                                                        CloudBackend
                                                                        (retry logic)
                                                                             ↓
                                                                        Cloud (Firebase/Supabase)
```

### Example Usage

```python
from src.network import NetworkClient, NetworkMode
from src.sync import CloudSync, SyncSample
from src.cloud import CloudClient, CloudProvider
import time

# Initialize
network = NetworkClient(cloud_host="api.firebase.google.com", detection_timeout=10.0)
sync = CloudSync()
cloud = CloudClient(provider=CloudProvider.MOCK)

# Start monitoring
network.start_monitoring()

# Add samples (from processor)
sample = SyncSample(
    ts=int(time.time() * 1000),
    window_index=42,
    s1_ch1=100.5, s1_ch2=101.2, s1_ch3=99.8,
    s2_ch1=50.1, s2_ch2=50.3,
    crc32=0
)
sample.crc32 = sync.compute_crc32(sample.__dict__)
sync.add_sample(sample)

# If online, send batch
if network.is_online():
    batch = sync.prepare_batch()
    if batch:
        success = await cloud.send_batch_with_retry(batch)
        if success:
            sync.on_batch_sent(batch)
else:
    sync.on_network_lost()

# Monitor state
state = sync.get_state()
print(f"Synced: {state['samples_in_cloud']}, Pending: {state['samples_pending']}")
```

## Resilience Guarantees

### Zero Data Loss
- **On offline**: Samples retained in memory buffer (2-hour window)
- **On network return**: All buffered samples sent via `prepare_batch()` + `send_batch_with_retry()`
- **On disk full**: Loss logged to `losses.log` with exact reason; processing continues
- **On process kill**: Collector has sqlite3 for durability; sync buffer is ephemeral by design
  - Tradeoff: Real-time responsiveness vs. crash durability

### Duplicate Prevention
- CRC32 checksum on every sample
- Cloud replies checked against expected CRC
- Corrupted/duplicate samples recorded in `losses.log`, not re-sent

### Outage Tracking
- `on_network_lost()` / `on_network_restored()` log to `data/sync_outages.log`
- Format: `{"timestamp": ms, "duration_seconds": float, "samples_during_outage": int}`
- Enables root-cause analysis: which outages caused data loss?

## Testing

Mock backend suitable for:
- Unit tests (no credentials, instant returns)
- Latency injection (test 5-second window timeout)
- Failure injection (test retry logic)
- Concurrent batch testing

```python
from src.cloud import CloudClient, CloudProvider
import asyncio

cloud = CloudClient(
    provider=CloudProvider.MOCK,
    latency_ms=100,  # simulate 100ms cloud latency
)

batch = {"samples": [...]}
success = await cloud.send_batch_with_retry(batch)
```

## Configuration

| Parameter | Default | Notes |
|-----------|---------|-------|
| `cloud_host` | `cloud.mayacardio.local` | Target hostname for connectivity check |
| `cloud_port` | `443` | HTTPS |
| `detection_timeout` | `10.0` | Seconds before declaring network lost |
| `BACKLOG_SECONDS` | `7200` | 2-hour sample retention window |
| `BATCH_SIZE` | `100` | Samples per batch send |
| `max_retries` | `3` | Cloud send retries |
| `retry_delay_ms` | `1000.0` | Initial backoff delay |

All configurable via `__init__` parameters.

## Files Created

- `/a/Projects/MayaCardio/src/network.py` — NetworkClient (150 lines)
- `/a/Projects/MayaCardio/src/sync.py` — CloudSync (280 lines)
- `/a/Projects/MayaCardio/src/cloud.py` — Cloud backends (380 lines)
- `/a/Projects/MayaCardio/src/network/README.md` — This document
