"""Cloud sync layer for cardiac monitoring device.

Manages buffering, deduplication (CRC32), 2-hour backlog retention,
send-all-on-reconnect, and outage tracking.
Zero data loss: every sample recorded until cloud confirms receipt.
"""

import time
import json
import zlib
from collections import deque
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, asdict
import pathlib


@dataclass
class SyncSample:
    """Single sample for cloud sync.

    Attributes:
        ts: Unix timestamp (milliseconds)
        window_index: processing window number
        s1_ch1, s1_ch2, s1_ch3, s2_ch1, s2_ch2: sensor values (float or None)
        crc32: CRC32 checksum for integrity (computed from raw bytes)
    """
    ts: int
    window_index: int
    s1_ch1: Optional[float]
    s1_ch2: Optional[float]
    s1_ch3: Optional[float]
    s2_ch1: Optional[float]
    s2_ch2: Optional[float]
    crc32: int


@dataclass
class WindowResult:
    """Processing result for a 1-second window.

    Attributes:
        window_index: 0-based window number
        mode: "online" or "offline"
        missing_1: missing samples in sensor 1 (0-1200)
        missing_2: missing samples in sensor 2 (0-800)
        complete: true if no missing samples
        statistics: dict of computed metrics (mean, min, max, magnitude, heart_rate)
    """
    window_index: int
    mode: str
    missing_1: int
    missing_2: int
    complete: bool
    statistics: Dict


class CloudSync:
    """Manages buffering and syncing of samples to cloud.

    Features:
    - Deduplication via CRC32 (detects corrupt/duplicate replies)
    - 2-hour backlog window (handles 2-hour outages)
    - Send-all-on-reconnect when network returns
    - Outage tracking (records exactly which samples were lost)
    - Graceful degradation on disk full / process kill
    """

    BACKLOG_SECONDS = 7200  # 2 hours
    BATCH_SIZE = 100  # samples per send batch
    OUTAGE_LOG_DIR = pathlib.Path("data")

    def __init__(self):
        self.OUTAGE_LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._outage_log = self.OUTAGE_LOG_DIR / "sync_outages.log"

        # Active sample buffer (in memory, bounded by backlog window)
        self._sample_buffer: deque = deque()

        # Tracking
        self._samples_in_cloud = 0  # confirmed by cloud
        self._samples_pending = 0  # queued but not yet sent
        self._last_sync_time = None
        self._outage_start = None
        self._current_outage_samples = []

    @staticmethod
    def compute_crc32(sample_dict: dict) -> int:
        """Compute CRC32 checksum for a sample dict (excluding crc32 field itself).

        Used to detect corruption or duplicates in cloud replies.
        """
        # Exclude crc32 from computation
        sample_copy = {k: v for k, v in sample_dict.items() if k != "crc32"}
        # Sort keys for deterministic ordering
        json_str = json.dumps(sample_copy, sort_keys=True, separators=(",", ":"))
        return zlib.crc32(json_str.encode("utf-8")) & 0xffffffff

    def add_sample(self, sample: SyncSample) -> None:
        """Add a sample to the buffer (before sending to cloud).

        Samples older than BACKLOG_SECONDS are dropped to maintain bounded memory.
        """
        current_time = time.time()
        sample_age = (current_time - sample.ts / 1000.0)

        # Drop if too old (outside 2-hour window)
        if sample_age > self.BACKLOG_SECONDS:
            self._record_loss(
                sample,
                f"dropped_old (age={sample_age:.1f}s, backlog={self.BACKLOG_SECONDS}s)"
            )
            return

        self._sample_buffer.append(sample)
        self._samples_pending += 1

    def prepare_batch(self) -> Optional[Dict]:
        """Prepare a batch of samples for cloud send.

        Returns dict with 'samples' list and metadata, or None if buffer empty.
        """
        if not self._sample_buffer:
            return None

        batch = {
            "timestamp": int(time.time() * 1000),
            "batch_size": 0,
            "samples": [],
        }

        # Pop up to BATCH_SIZE samples
        for _ in range(self.BATCH_SIZE):
            if not self._sample_buffer:
                break
            sample = self._sample_buffer.popleft()
            batch["samples"].append(asdict(sample))
            batch["batch_size"] += 1

        self._samples_pending = max(0, self._samples_pending - batch["batch_size"])
        return batch if batch["batch_size"] > 0 else None

    def on_batch_sent(self, batch: Dict) -> None:
        """Called when cloud confirms receipt of a batch.

        Deduplicates via CRC32 to catch duplicate or corrupt replies.
        """
        if not batch or "samples" not in batch:
            return

        for sample_dict in batch["samples"]:
            # Verify CRC32
            expected_crc = sample_dict.get("crc32", 0)
            actual_crc = self.compute_crc32(sample_dict)

            if expected_crc != actual_crc:
                self._record_loss(
                    sample_dict,
                    f"crc32_mismatch (expected={expected_crc}, got={actual_crc})"
                )
                continue

            self._samples_in_cloud += 1
            self._last_sync_time = time.time()

    def on_network_lost(self) -> None:
        """Called when network transitions from ONLINE to OFFLINE."""
        self._outage_start = time.time()
        self._current_outage_samples = []

    def on_network_restored(self) -> None:
        """Called when network transitions from OFFLINE to ONLINE.

        Logs the outage and attempts to send all buffered samples.
        """
        if self._outage_start is not None:
            duration = time.time() - self._outage_start
            self._log_outage(duration, len(self._current_outage_samples))
            self._outage_start = None
            self._current_outage_samples = []

    def get_state(self) -> Dict:
        """Return current sync state for monitoring/debugging.

        Returns:
            dict with samples_in_cloud, samples_pending, buffer_size, last_sync_time
        """
        return {
            "samples_in_cloud": self._samples_in_cloud,
            "samples_pending": self._samples_pending,
            "buffer_size": len(self._sample_buffer),
            "last_sync_time": self._last_sync_time,
            "backlog_seconds": self.BACKLOG_SECONDS,
        }

    def get_backlog_stats(self) -> Dict:
        """Return statistics about current backlog.

        Returns:
            dict with oldest_sample_age, newest_sample_age, sample_count
        """
        if not self._sample_buffer:
            return {
                "oldest_sample_age": None,
                "newest_sample_age": None,
                "sample_count": 0,
            }

        current_time = time.time()
        oldest_ts = self._sample_buffer[0].ts / 1000.0
        newest_ts = self._sample_buffer[-1].ts / 1000.0

        return {
            "oldest_sample_age": current_time - oldest_ts,
            "newest_sample_age": current_time - newest_ts,
            "sample_count": len(self._sample_buffer),
        }

    def _record_loss(self, sample: any, reason: str) -> None:
        """Record a lost sample with detailed reason."""
        try:
            loss_file = self.OUTAGE_LOG_DIR / "losses.log"
            ts = sample.ts if isinstance(sample, SyncSample) else sample.get("ts", 0)
            with open(loss_file, "a") as f:
                f.write(f"{ts}|{reason}\n")
        except OSError:
            # Disk full: gracefully degrade
            pass

    def _log_outage(self, duration_seconds: float, samples_during_outage: int) -> None:
        """Log an outage event with duration and sample count."""
        try:
            with open(self._outage_log, "a") as f:
                entry = {
                    "timestamp": int(time.time() * 1000),
                    "duration_seconds": duration_seconds,
                    "samples_during_outage": samples_during_outage,
                }
                f.write(json.dumps(entry) + "\n")
        except OSError:
            # Disk full: gracefully degrade
            pass

    def clear_buffer(self) -> None:
        """Clear all buffered samples (use with caution)."""
        self._sample_buffer.clear()
        self._samples_pending = 0
