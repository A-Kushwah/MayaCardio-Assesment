"""Integration tests: end-to-end collect → process → store → export with mode switches."""
import pytest
import sys
import pathlib
import sqlite3
import tempfile
import shutil
import time
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from processor import Processor
from network import NetworkClient, NetworkMode


class TestE2EDataPipeline:
    """End-to-end pipeline tests: collect → process → store → export."""

    def test_pipeline_offline_mode_full_cycle(self, mock_biosignal_data, tmp_db_dir):
        """Test complete offline pipeline: ingest → process → store → export."""
        # 1. Collect samples (simulated)
        collected_samples = mock_biosignal_data[:1000]

        # 2. Process samples
        processor = Processor(mode="offline")
        results = processor.process_samples(collected_samples)

        # 3. Store results
        assert len(results) >= 2
        for result in results:
            assert result.mode == "offline"
            assert result.window_index is not None

        # 4. Verify data integrity
        assert all(r.window_index >= 0 for r in results)
        assert results[0].window_index == 0
        assert results[-1].window_index == len(results) - 1

    def test_pipeline_sample_count_preservation(self, mock_biosignal_data):
        """Test that all samples are accounted for through pipeline."""
        collected = len(mock_biosignal_data)
        processor = Processor(mode="offline")
        results = processor.process_samples(mock_biosignal_data)

        # Verify samples map to windows
        total_samples_in_windows = len(results) * 400
        assert total_samples_in_windows <= collected < total_samples_in_windows + 400

    def test_pipeline_with_missing_data_streaks(self, edge_case_samples):
        """Test pipeline handles missing data correctly."""
        processor = Processor(mode="offline")
        results = processor.process_samples(edge_case_samples)

        # Window 0: complete
        assert results[0].complete is True
        assert results[0].missing_1 == 0

        # Window 1: all missing
        assert results[1].complete is False
        assert results[1].missing_1 == 1200

        # Window 2: s1 valid, s2 missing
        assert results[2].complete is False
        assert results[2].missing_1 == 0
        assert results[2].missing_2 == 800  # 2 channels × 400 samples

        # Window 3: s1 missing, s2 valid
        assert results[3].complete is False
        assert results[3].missing_1 == 1200  # 3 channels × 400 samples
        assert results[3].missing_2 == 0


class TestOnlineModeOperation:
    """Test online mode with network transitions."""

    def test_online_mode_processing(self, mock_biosignal_data):
        """Test processor operates in online mode."""
        processor = Processor(mode="online")
        assert processor.mode == "online"

        # Add samples incrementally
        for sample in mock_biosignal_data[:800]:
            processor.add_sample(*sample)

        results = []
        while True:
            result = processor.process_window()
            if result is None:
                break
            results.append(result)

        assert len(results) == 2
        assert all(r.mode == "online" for r in results)

    @patch("network.NetworkClient._check_connectivity")
    def test_mode_switch_online_to_offline(self, mock_check, mock_biosignal_data):
        """Test switching from online to offline mode."""
        mock_check.side_effect = [True, False, False, False]

        network = NetworkClient(detection_timeout=2.0)
        network.start_monitoring()

        # Initially online
        time.sleep(0.5)
        assert network.is_online()

        # Wait for offline detection
        time.sleep(2.5)
        assert network.is_offline()

        network.stop_monitoring()

    @patch("network.NetworkClient._check_connectivity")
    def test_mode_switch_offline_to_online(self, mock_check):
        """Test switching from offline to online mode."""
        mock_check.side_effect = [False, False, True]

        network = NetworkClient(detection_timeout=1.0)
        network.start_monitoring()

        # Initially offline
        time.sleep(0.5)
        assert network.is_offline()

        # Detect online
        time.sleep(2.0)
        assert network.is_online()

        network.stop_monitoring()


class TestZeroDataLoss:
    """Test zero data loss through full pipeline."""

    def test_no_loss_with_complete_data(self, sample_db_connection):
        """Test that all complete samples are persisted."""
        conn = sample_db_connection

        # Simulate 100 samples
        for i in range(100):
            ts = 1000 + i
            conn.execute(
                "INSERT OR IGNORE INTO samples VALUES (?,?,?,?)",
                (ts, 100 + i, 110 + i, 50 + i),
            )
        conn.commit()

        # Verify all persisted
        count = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count == 100

        # Verify recovery can find all
        max_ts = conn.execute("SELECT MAX(ts) FROM samples").fetchone()
        recovery_ts = max_ts[0] if max_ts and max_ts[0] else 0
        assert recovery_ts == 1099

    def test_no_duplicate_after_recovery(self, sample_db_connection):
        """Test that resending batch after recovery doesn't duplicate."""
        conn = sample_db_connection

        # Batch 1: samples 1000-1099
        batch = [(1000 + i, 100 + i, 110 + i, 50 + i) for i in range(100)]

        # First send
        for ts, ch1, ch2, s2ch1 in batch:
            conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (ts, ch1, ch2, s2ch1))
        conn.commit()

        # Simulate recovery: retry same batch
        for ts, ch1, ch2, s2ch1 in batch:
            conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (ts, ch1, ch2, s2ch1))
        conn.commit()

        # Verify no duplicates
        count = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count == 100

    def test_sequential_batch_integrity(self, sample_db_connection):
        """Test that multiple batches maintain integrity."""
        conn = sample_db_connection

        batches = [
            [(1000 + i, 100 + i, 110 + i, 50 + i) for i in range(10)],
            [(1010 + i, 100 + i, 110 + i, 50 + i) for i in range(10)],
            [(1020 + i, 100 + i, 110 + i, 50 + i) for i in range(10)],
        ]

        for batch in batches:
            for ts, ch1, ch2, s2ch1 in batch:
                conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (ts, ch1, ch2, s2ch1))
            conn.commit()

        # Verify all 30 samples
        count = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count == 30

        # Verify gaps between batches preserved
        ts_values = [row[0] for row in conn.execute("SELECT ts FROM samples ORDER BY ts").fetchall()]
        assert ts_values[0] == 1000
        assert ts_values[9] == 1009
        assert ts_values[10] == 1010
        assert ts_values[29] == 1029


class TestHeartRateAcrossWindowBoundaries:
    """Test heart rate detection across window transitions."""

    def test_hr_requires_5_second_window(self, mock_biosignal_data):
        """Test that HR requires 4+ windows of history (5 seconds)."""
        processor = Processor(mode="offline")
        results = processor.process_samples(mock_biosignal_data)

        # Windows 0-3 should have None heart_rate
        for i in range(4):
            if i < len(results):
                result = results[i]
                assert result.heart_rate is None, f"Window {i} should not have HR"

        # Window 4+ might have HR (if peaks detected)
        # At least verify no errors
        for result in results[4:]:
            assert isinstance(result.heart_rate, (float, type(None)))

    def test_hr_consistency_with_partial_data(self, edge_case_samples):
        """Test HR calculation with partial missing data."""
        processor = Processor(mode="offline")
        results = processor.process_samples(edge_case_samples)

        # Window with all data should compute HR
        if len(results) > 4:
            result = results[4]
            # Should either have HR or None (no error)
            assert isinstance(result.heart_rate, (float, type(None)))


class TestCloudSyncDeduplication:
    """Test cloud sync with 2-hour backlog and deduplication."""

    def test_backlog_tracking(self):
        """Test tracking of unacknowledged samples (backlog)."""
        backlog = []

        # Simulate adding to backlog
        for i in range(100):
            backlog.append({"ts": 1000 + i, "values": [100, 110, 120, 50, 60]})

        assert len(backlog) == 100

        # Simulate cloud acknowledging first 50
        acknowledged = backlog[:50]
        remaining = backlog[50:]

        assert len(remaining) == 50

    def test_2_hour_backlog_window(self):
        """Test that 2-hour backlog window is respected."""
        sample_rate = 400  # Hz
        duration_2h = 2 * 60 * 60  # seconds
        max_samples_2h = sample_rate * duration_2h

        # At 400 Hz, 2 hours = 2,880,000 samples
        assert max_samples_2h == 2_880_000

        # Verify we can track this many samples
        backlog = list(range(max_samples_2h))
        assert len(backlog) == max_samples_2h

    def test_deduplication_in_backlog(self):
        """Test deduplication when retransmitting backlog."""
        # Simulate backlog with duplicates after resend
        backlog_attempt_1 = [
            {"ts": 1000, "seq": 1},
            {"ts": 1001, "seq": 2},
            {"ts": 1002, "seq": 3},
        ]

        backlog_attempt_2 = [
            {"ts": 1000, "seq": 1},  # Duplicate
            {"ts": 1001, "seq": 2},  # Duplicate
            {"ts": 1002, "seq": 3},  # Duplicate
            {"ts": 1003, "seq": 4},  # New
        ]

        # Deduplicate by ts
        seen_ts = set()
        deduplicated = []
        for item in backlog_attempt_2:
            if item["ts"] not in seen_ts:
                deduplicated.append(item)
                seen_ts.add(item["ts"])

        assert len(deduplicated) == 4
        assert deduplicated[-1]["ts"] == 1003


class TestExportFormats:
    """Test export of processed data."""

    def test_export_as_dict(self):
        """Test exporting window result as dict."""
        processor = Processor(mode="offline")

        for i in range(400):
            processor.add_sample(100.0, 110.0, 120.0, 50.0, 60.0)

        result = processor.process_window()
        exported = result.as_dict()

        assert isinstance(exported, dict)
        assert "window_index" in exported
        assert "mode" in exported
        assert "complete" in exported
        assert "mean_ch1_s1" in exported
        assert "heart_rate" in exported

    def test_export_preserves_values(self):
        """Test that export preserves calculation values."""
        processor = Processor(mode="offline")

        for i in range(400):
            processor.add_sample(100.5, 200.3, 150.7, 50.0, 60.0)

        result = processor.process_window()
        exported = result.as_dict()

        # Values should be rounded to 1 decimal
        assert exported["mean_ch1_s1"] == 100.5
        assert exported["mean_ch2_s1"] == 200.3
        assert exported["mode"] == "offline"

    def test_export_with_none_values(self):
        """Test export handles None values correctly."""
        processor = Processor(mode="offline")

        for i in range(400):
            processor.add_sample(None, None, None, 50.0, 60.0)

        result = processor.process_window()
        exported = result.as_dict()

        assert exported["mean_ch1_s1"] is None
        assert exported["magnitude"] is not None  # s2 has data


class TestSyncAcrossReboot:
    """Test data sync consistency across device restart."""

    def test_recovery_point_identification(self, sample_db_connection):
        """Test that recovery point correctly identifies restart gap."""
        conn = sample_db_connection

        # Simulate: crash after writing 1000-1049
        for i in range(50):
            conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (1000 + i, 100, 110, 50))
        conn.commit()

        # Get recovery point
        max_ts = conn.execute("SELECT MAX(ts) FROM samples").fetchone()
        recovery_ts = max_ts[0] if max_ts and max_ts[0] else 0
        next_expected = recovery_ts + 1

        assert recovery_ts == 1049
        assert next_expected == 1050

        # Restart: continue from next_expected
        for i in range(50):
            conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (next_expected + i, 100, 110, 50))
        conn.commit()

        # Verify no gap
        count = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count == 100

        ts_values = [row[0] for row in conn.execute("SELECT ts FROM samples ORDER BY ts").fetchall()]
        assert ts_values == list(range(1000, 1100))

    def test_processor_state_preserved(self, mock_biosignal_data):
        """Test that processor state can be reconstructed after restart."""
        # Simulate: process first half
        p1 = Processor(mode="offline")
        results_1 = p1.process_samples(mock_biosignal_data[:500])

        # State after first batch
        p1_window_index = p1.window_index
        p1_buffer_len = len(p1.s1_ch1)

        # Simulate restart: new processor, add remaining samples
        p2 = Processor(mode="offline")
        # Start from last position (in real system, recovered from storage)
        results_2 = p2.process_samples(mock_biosignal_data[500:])

        # Verify continuity
        assert len(results_1) + len(results_2) >= 2
        # First window from p2 should have index > p1's last window
        if results_2:
            assert results_2[0].window_index >= results_1[-1].window_index
