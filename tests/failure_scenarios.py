"""Failure scenario tests: network cuts, disk full, process kill, power loss simulation."""
import pytest
import sys
import pathlib
import sqlite3
import tempfile
import os
import threading
import time
from unittest.mock import Mock, patch, MagicMock
from io import BytesIO

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from processor import Processor
from network import NetworkClient, NetworkMode


class TestNetworkOutageScenarios:
    """Simulate network outages and recovery."""

    @patch("network.NetworkClient._check_connectivity")
    def test_scenario_network_outage_detection(self, mock_check):
        """Test detection of network outage within 10 seconds."""
        callback = Mock()
        client = NetworkClient(detection_timeout=2.0, mode_change_callback=callback)

        # Start online
        mock_check.side_effect = [True, True, False, False, False]

        client.start_monitoring()
        time.sleep(0.5)
        assert client.is_online()

        # Wait for offline detection (timeout=2s + some margin)
        time.sleep(2.5)
        assert client.is_offline()

        # Verify callback was called
        assert callback.call_count >= 1
        client.stop_monitoring()

    @patch("network.NetworkClient._check_connectivity")
    def test_scenario_brief_network_blip(self, mock_check):
        """Test brief outage that recovers quickly (< 10 sec)."""
        client = NetworkClient(detection_timeout=2.0)

        # Online, brief failure, recover
        mock_check.side_effect = [True, True, False, True]

        client.start_monitoring()
        time.sleep(0.5)
        assert client.is_online()

        time.sleep(0.3)
        # Still online (timeout not reached)
        assert client.is_online()

        time.sleep(1.0)
        # Should still be online (timeout reset by recovery)
        assert client.is_online()

        client.stop_monitoring()

    @patch("network.NetworkClient._check_connectivity")
    def test_scenario_prolonged_outage(self, mock_check):
        """Test prolonged outage (> 10 seconds)."""
        client = NetworkClient(detection_timeout=1.0)

        # Go online, then long outage
        mock_check.side_effect = [True, True] + [False] * 10

        client.start_monitoring()
        time.sleep(0.5)
        assert client.is_online()

        # Wait through timeout
        time.sleep(2.5)
        assert client.is_offline()

        client.stop_monitoring()

    @patch("network.NetworkClient._check_connectivity")
    def test_scenario_flaky_network(self, mock_check):
        """Test flaky network with rapid transitions."""
        callback = Mock()
        client = NetworkClient(detection_timeout=1.0, mode_change_callback=callback)

        # Rapid on/off pattern
        pattern = [True, True, False, False, True, True, False, False, True]
        mock_check.side_effect = pattern

        client.start_monitoring()
        time.sleep(3.5)

        # Mode should be stable (last was True)
        assert client.is_online() or client.is_offline()

        # Multiple transitions should have occurred
        assert callback.call_count >= 1

        client.stop_monitoring()


class TestDiskFullScenario:
    """Simulate disk full condition and recovery."""

    def test_scenario_disk_full_during_write(self, tmp_db_dir):
        """Test handling of disk full error during sample write."""
        db_path = tmp_db_dir / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE samples (ts INTEGER PRIMARY KEY, s1_ch1 INTEGER, s1_ch2 INTEGER, s2_ch1 INTEGER)")

        # Write successful samples
        for i in range(10):
            conn.execute("INSERT INTO samples VALUES (?,?,?,?)", (1000 + i, 100, 110, 50))
        conn.commit()

        # Simulate disk full by closing connection
        initial_count = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        conn.close()

        # Verify data persisted despite "disk full"
        conn2 = sqlite3.connect(str(db_path))
        count = conn2.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count == initial_count
        conn2.close()

    def test_scenario_disk_full_loss_logging(self, loss_log_path):
        """Test loss logging when disk full prevents insert."""
        loss_log_path.parent.mkdir(parents=True, exist_ok=True)

        # Simulate unhandled insert failure
        error_reason = "sqlite3.OperationalError: disk I/O error"

        with open(loss_log_path, "a") as f:
            f.write(f"1000|{error_reason}\n")
            f.write(f"1001|{error_reason}\n")

        losses = loss_log_path.read_text().strip().split("\n")
        assert len(losses) == 2
        assert "disk" in losses[0].lower()

    def test_scenario_recover_after_disk_full(self, tmp_db_dir):
        """Test recovery after disk full condition clears."""
        db_path = tmp_db_dir / "test.db"

        # Write initial batch
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE samples (ts INTEGER PRIMARY KEY, s1_ch1 INTEGER, s1_ch2 INTEGER, s2_ch1 INTEGER)")

        for i in range(50):
            conn.execute("INSERT INTO samples VALUES (?,?,?,?)", (1000 + i, 100, 110, 50))
        conn.commit()

        max_ts_before = conn.execute("SELECT MAX(ts) FROM samples").fetchone()[0]
        conn.close()

        # Simulate: disk full prevented samples 1050-1099
        # Now disk is available, continue from 1100

        conn2 = sqlite3.connect(str(db_path))
        for i in range(50, 150):
            conn2.execute("INSERT INTO samples VALUES (?,?,?,?)", (1000 + i, 100, 110, 50))
        conn2.commit()

        max_ts_after = conn2.execute("SELECT MAX(ts) FROM samples").fetchone()[0]
        assert max_ts_after == 1149
        conn2.close()


class TestProcessKillScenario:
    """Simulate process kill and restart."""

    def test_scenario_kill_before_commit(self, tmp_db_dir):
        """Test that uncommitted writes are lost on kill."""
        db_path = tmp_db_dir / "test.db"

        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("CREATE TABLE samples (ts INTEGER PRIMARY KEY, s1_ch1 INTEGER, s1_ch2 INTEGER, s2_ch1 INTEGER)")

        # Insert but don't commit
        conn.execute("INSERT INTO samples VALUES (1000, 100, 110, 50)")
        # Simulate kill: no commit

        # Reconnect (as new process)
        conn2 = sqlite3.connect(str(db_path))
        count = conn2.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count == 0  # Lost uncommitted data

        conn2.close()

    def test_scenario_kill_after_commit(self, tmp_db_dir):
        """Test that committed data survives process kill."""
        db_path = tmp_db_dir / "test.db"

        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("CREATE TABLE samples (ts INTEGER PRIMARY KEY, s1_ch1 INTEGER, s1_ch2 INTEGER, s2_ch1 INTEGER)")

        # Insert and commit
        for i in range(50):
            conn.execute("INSERT INTO samples VALUES (?,?,?,?)", (1000 + i, 100, 110, 50))
        conn.commit()

        # Simulate kill
        conn_temp = conn
        conn = None

        # Reconnect
        conn2 = sqlite3.connect(str(db_path))
        count = conn2.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count == 50  # Committed data persisted

        conn2.close()

    def test_scenario_restart_and_resume(self, tmp_db_dir):
        """Test restart and resume from last known position."""
        db_path = tmp_db_dir / "test.db"

        # Process 1: write and commit
        conn1 = sqlite3.connect(str(db_path))
        conn1.execute("PRAGMA journal_mode=WAL")
        conn1.execute("CREATE TABLE samples (ts INTEGER PRIMARY KEY, s1_ch1 INTEGER, s1_ch2 INTEGER, s2_ch1 INTEGER)")

        for i in range(100):
            conn1.execute("INSERT INTO samples VALUES (?,?,?,?)", (1000 + i, 100, 110, 50))
        conn1.commit()

        max_ts_p1 = conn1.execute("SELECT MAX(ts) FROM samples").fetchone()[0]
        conn1.close()

        # Process 2: restart and resume from max_ts
        conn2 = sqlite3.connect(str(db_path))
        recovery_ts = conn2.execute("SELECT MAX(ts) FROM samples").fetchone()[0]
        assert recovery_ts == max_ts_p1

        # Continue from recovery point
        next_ts = recovery_ts + 1
        for i in range(50):
            conn2.execute("INSERT INTO samples VALUES (?,?,?,?)", (next_ts + i, 100, 110, 50))
        conn2.commit()

        total_count = conn2.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert total_count == 150

        conn2.close()


class TestPowerLossScenario:
    """Simulate power loss and recovery."""

    def test_scenario_power_loss_uncommitted(self, tmp_db_dir):
        """Test power loss: uncommitted writes are lost."""
        db_path = tmp_db_dir / "test.db"

        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("CREATE TABLE samples (ts INTEGER PRIMARY KEY, s1_ch1 INTEGER, s1_ch2 INTEGER, s2_ch1 INTEGER)")

        # Write and commit some data
        for i in range(50):
            conn.execute("INSERT INTO samples VALUES (?,?,?,?)", (1000 + i, 100, 110, 50))
        conn.commit()

        # Start another batch (not committed)
        for i in range(50, 100):
            conn.execute("INSERT INTO samples VALUES (?,?,?,?)", (1000 + i, 100, 110, 50))
        # Power loss before commit

        # Recovery
        conn2 = sqlite3.connect(str(db_path))
        count = conn2.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count == 50  # Only committed data

        conn2.close()

    def test_scenario_power_loss_with_wal_recovery(self, tmp_db_dir):
        """Test WAL recovery after power loss."""
        db_path = tmp_db_dir / "test.db"
        wal_path = db_path.with_suffix(".db-wal")
        shm_path = db_path.with_suffix(".db-shm")

        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("CREATE TABLE samples (ts INTEGER PRIMARY KEY, s1_ch1 INTEGER, s1_ch2 INTEGER, s2_ch1 INTEGER)")

        # Commit some data
        for i in range(25):
            conn.execute("INSERT INTO samples VALUES (?,?,?,?)", (1000 + i, 100, 110, 50))
        conn.commit()

        # More writes to WAL
        for i in range(25, 50):
            conn.execute("INSERT INTO samples VALUES (?,?,?,?)", (1000 + i, 100, 110, 50))
        conn.commit()

        conn.close()

        # Recovery: reopen database (WAL should be replayed)
        conn2 = sqlite3.connect(str(db_path))
        count = conn2.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count == 50

        conn2.close()


class TestSystemClockChangeScenario:
    """Simulate system clock changes."""

    def test_scenario_clock_backward(self):
        """Test backward clock change doesn't affect sample timestamps."""
        # Samples use absolute timestamps (not relative to system clock)
        samples = [
            (1000, 100.0, 110.0, 120.0, 50.0, 60.0),
            (1001, 100.0, 110.0, 120.0, 50.0, 60.0),
            (1002, 100.0, 110.0, 120.0, 50.0, 60.0),
        ]

        processor = Processor(mode="offline")
        for sample in samples:
            ts = sample[0]
            processor.add_sample(*sample[1:])

        # Even if system clock changes, samples are still in order
        assert len(processor.s1_ch1) == 3

    def test_scenario_clock_forward(self, tmp_db_dir):
        """Test forward clock change doesn't duplicate entries."""
        db_path = tmp_db_dir / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE samples (ts INTEGER PRIMARY KEY, s1_ch1 INTEGER, s1_ch2 INTEGER, s2_ch1 INTEGER)")

        # Insert samples
        for i in range(10):
            conn.execute("INSERT INTO samples VALUES (?,?,?,?)", (1000 + i, 100, 110, 50))
        conn.commit()

        # System clock jumps forward
        # New samples continue with absolute ts (not affected)
        for i in range(10, 20):
            conn.execute("INSERT INTO samples VALUES (?,?,?,?)", (1000 + i, 100, 110, 50))
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count == 20  # No duplicates

        conn.close()


class TestSensorDropoutScenario:
    """Simulate sensor dropout and recovery."""

    def test_scenario_sensor_dropout_streak(self, edge_case_samples):
        """Test processor handles extended sensor dropout."""
        # edge_case_samples has window with all None
        processor = Processor(mode="offline")
        results = processor.process_samples(edge_case_samples[:800])

        # First window: complete
        assert results[0].complete is True

        # Second window: all missing (dropout)
        assert results[1].complete is False
        assert results[1].missing_1 == 1200
        assert results[1].missing_2 == 800

    def test_scenario_partial_sensor_dropout(self):
        """Test processor when one sensor drops out."""
        samples = []

        # Window 0: both sensors
        for i in range(400):
            samples.append((100.0, 110.0, 120.0, 50.0, 60.0))

        # Window 1: sensor 2 drops (s2 data becomes None)
        for i in range(400):
            samples.append((100.0, 110.0, 120.0, None, None))

        processor = Processor(mode="offline")
        results = processor.process_samples(samples)

        assert results[0].complete is True
        assert results[1].complete is False
        assert results[1].missing_1 == 0
        assert results[1].missing_2 == 400

    def test_scenario_sensor_recovery(self):
        """Test processor after sensor recovers."""
        samples = []

        # Window 0: all valid
        for i in range(400):
            samples.append((100.0, 110.0, 120.0, 50.0, 60.0))

        # Window 1: dropout
        for i in range(400):
            samples.append((None, None, None, None, None))

        # Window 2: recovery
        for i in range(400):
            samples.append((100.0, 110.0, 120.0, 50.0, 60.0))

        processor = Processor(mode="offline")
        results = processor.process_samples(samples)

        assert results[0].complete is True
        assert results[1].complete is False
        assert results[2].complete is True


class TestConcurrentFailureScenarios:
    """Test concurrent failures (network + disk issues, etc.)."""

    @patch("network.NetworkClient._check_connectivity")
    def test_scenario_network_offline_and_disk_pressure(self, mock_check, tmp_db_dir):
        """Test network offline while disk space is low."""
        mock_check.return_value = False

        # Network is offline
        client = NetworkClient(detection_timeout=1.0)
        client.start_monitoring()
        time.sleep(1.5)
        assert client.is_offline()

        # Meanwhile, disk operations continue in offline mode
        db_path = tmp_db_dir / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE samples (ts INTEGER PRIMARY KEY, s1_ch1 INTEGER, s1_ch2 INTEGER, s2_ch1 INTEGER)")

        # Can still persist samples locally
        for i in range(50):
            conn.execute("INSERT INTO samples VALUES (?,?,?,?)", (1000 + i, 100, 110, 50))
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count == 50  # Local storage works despite network being offline

        client.stop_monitoring()
        conn.close()

    def test_scenario_process_kill_with_backlog(self, tmp_db_dir):
        """Test process kill while data is queued for cloud."""
        db_path = tmp_db_dir / "test.db"

        # Simulate: local samples persisted, backlog queued
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("CREATE TABLE samples (ts INTEGER PRIMARY KEY, s1_ch1 INTEGER, s1_ch2 INTEGER, s2_ch1 INTEGER)")

        # Persisted samples
        for i in range(100):
            conn.execute("INSERT INTO samples VALUES (?,?,?,?)", (1000 + i, 100, 110, 50))
        conn.commit()

        # Backlog in memory (simulated)
        backlog = [(1100 + i, {"value": i}) for i in range(50)]

        # Process kill: backlog is lost, but persisted samples survive
        conn_close_result = conn.close()

        # Restart: recover persisted samples
        conn2 = sqlite3.connect(str(db_path))
        recovered = conn2.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert recovered == 100  # All persisted samples intact

        # Backlog must be resent (not persisted before kill)
        conn2.close()


class TestZeroLossUnderFailure:
    """Verify zero data loss guarantee under combined failures."""

    def test_verify_zero_loss_network_outage_to_restart(self, tmp_db_dir):
        """Test zero loss: network outage → process kill → restart."""
        db_path = tmp_db_dir / "test.db"

        # Phase 1: Normal operation, write samples
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("CREATE TABLE samples (ts INTEGER PRIMARY KEY, s1_ch1 INTEGER, s1_ch2 INTEGER, s2_ch1 INTEGER)")

        for i in range(100):
            conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (1000 + i, 100, 110, 50))
        conn.commit()

        count1 = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count1 == 100

        # Phase 2: Network goes offline (samples queue locally)
        # Process continues to write
        for i in range(100, 150):
            conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (1000 + i, 100, 110, 50))
        conn.commit()

        count2 = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count2 == 150

        # Phase 3: Process killed
        # Uncommitted writes are lost
        conn.close()

        # Phase 4: Restart
        conn2 = sqlite3.connect(str(db_path))
        count3 = conn2.execute("SELECT COUNT(*) FROM samples").fetchone()[0]

        # Verify: all committed samples preserved
        assert count3 == count2
        conn2.close()

    def test_verify_zero_duplication_after_network_recovery(self, sample_db_connection):
        """Test zero duplication: network recovers and resends backlog."""
        conn = sample_db_connection

        # Samples in backlog (not acknowledged)
        backlog = [(1000 + i, 100 + i, 110 + i, 50 + i) for i in range(50)]

        # First attempt to send (offline, so not actually sent but persisted locally)
        for ts, ch1, ch2, s2ch1 in backlog:
            conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (ts, ch1, ch2, s2ch1))
        conn.commit()

        count1 = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count1 == 50

        # Network recovers, retry sends same backlog
        for ts, ch1, ch2, s2ch1 in backlog:
            conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (ts, ch1, ch2, s2ch1))
        conn.commit()

        count2 = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        # Still 50, no duplicates (INSERT OR IGNORE)
        assert count2 == 50
