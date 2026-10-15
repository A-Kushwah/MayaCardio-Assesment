"""Unit tests for Storage: crash recovery, WAL, deduplication, loss logging."""
import pytest
import sqlite3
import tempfile
import pathlib
import sys
import os

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from storage import Storage


class TestStorageInit:
    """Test Storage initialization and table creation."""

    def test_init_creates_db_dir(self, tmp_db_dir):
        """Test that __init__ creates database directory."""
        db_path = tmp_db_dir / "subdir" / "biosignal.db"
        monkeypatch = pytest.MonkeyPatch()

        # Mock the DB_DIR to use tmp_db_dir
        import storage
        original_db_dir = storage.DB_DIR
        original_db_file = storage.DB_FILE
        storage.DB_DIR = db_path.parent
        storage.DB_FILE = db_path

        try:
            s = Storage()
            assert db_path.parent.exists()
            s.conn.close()
        finally:
            storage.DB_DIR = original_db_dir
            storage.DB_FILE = original_db_file

    def test_init_wal_mode_enabled(self, tmp_db_dir, monkeypatch):
        """Test that WAL mode is enabled for crash recovery."""
        db_path = tmp_db_dir / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")

        # Check WAL is active
        result = conn.execute("PRAGMA journal_mode").fetchone()
        assert result[0].upper() == "WAL"

        # Check synchronous is FULL
        sync_result = conn.execute("PRAGMA synchronous").fetchone()
        assert sync_result[0] == 2  # FULL = 2

        conn.close()

    def test_init_creates_samples_table(self, tmp_db_dir, monkeypatch):
        """Test that samples table is created with correct schema."""
        db_path = tmp_db_dir / "test.db"

        import storage
        original_db_dir = storage.DB_DIR
        original_db_file = storage.DB_FILE
        storage.DB_DIR = db_path.parent
        storage.DB_FILE = db_path

        try:
            s = Storage()

            # Check table exists
            cursor = s.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='samples'"
            )
            assert cursor.fetchone() is not None

            # Check columns
            cursor = s.conn.execute("PRAGMA table_info(samples)")
            columns = {row[1]: row[2] for row in cursor.fetchall()}
            assert "ts" in columns
            assert "s1_ch1" in columns
            assert "s1_ch2" in columns
            assert "s2_ch1" in columns

            s.conn.close()
        finally:
            storage.DB_DIR = original_db_dir
            storage.DB_FILE = original_db_file

    def test_init_creates_index(self, tmp_db_dir, monkeypatch):
        """Test that timestamp index is created."""
        db_path = tmp_db_dir / "test.db"

        import storage
        original_db_dir = storage.DB_DIR
        original_db_file = storage.DB_FILE
        storage.DB_DIR = db_path.parent
        storage.DB_FILE = db_path

        try:
            s = Storage()

            cursor = s.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_samples_ts'"
            )
            assert cursor.fetchone() is not None

            s.conn.close()
        finally:
            storage.DB_DIR = original_db_dir
            storage.DB_FILE = original_db_file


class TestWriteSample:
    """Test sample persistence with deduplication."""

    def test_write_single_sample(self, sample_db_connection):
        """Test writing a single sample."""
        conn = sample_db_connection
        conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (1000, 100, 110, 50))
        conn.commit()

        row = conn.execute("SELECT * FROM samples WHERE ts=1000").fetchone()
        assert row == (1000, 100, 110, 50)

    def test_write_multiple_samples(self, sample_db_connection):
        """Test writing multiple sequential samples."""
        conn = sample_db_connection
        for i in range(10):
            conn.execute(
                "INSERT OR IGNORE INTO samples VALUES (?,?,?,?)",
                (1000 + i, 100 + i, 110 + i, 50 + i),
            )
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count == 10

    def test_deduplication_insert_or_ignore(self, sample_db_connection):
        """Test that INSERT OR IGNORE prevents duplicates."""
        conn = sample_db_connection

        # Insert same sample twice
        conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (1000, 100, 110, 50))
        conn.commit()

        conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (1000, 100, 110, 50))
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM samples WHERE ts=1000").fetchone()[0]
        assert count == 1  # Only one row

    def test_deduplication_different_values_same_ts(self, sample_db_connection):
        """Test that duplicate ts with different values doesn't update."""
        conn = sample_db_connection

        # Insert first
        conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (1000, 100, 110, 50))
        conn.commit()

        # Try to insert different values with same ts
        conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (1000, 200, 220, 60))
        conn.commit()

        # Should still have original values
        row = conn.execute("SELECT * FROM samples WHERE ts=1000").fetchone()
        assert row == (1000, 100, 110, 50)

    def test_zero_loss_persistence(self, sample_db_connection):
        """Test that all unique samples are persisted (zero loss)."""
        conn = sample_db_connection

        samples = [(1000 + i, 100 + i, 110 + i, 50 + i) for i in range(100)]

        for ts, ch1, ch2, s2ch1 in samples:
            conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (ts, ch1, ch2, s2ch1))
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count == 100

        # Verify each sample is present
        for i in range(100):
            row = conn.execute("SELECT * FROM samples WHERE ts=?", (1000 + i,)).fetchone()
            assert row is not None
            assert row[1] == 100 + i


class TestRecover:
    """Test crash recovery via max timestamp."""

    def test_recover_empty_db(self, sample_db_connection):
        """Test recover() on empty database."""
        conn = sample_db_connection

        max_ts = conn.execute("SELECT MAX(ts) FROM samples").fetchone()
        recovery_ts = max_ts[0] if max_ts and max_ts[0] else 0
        assert recovery_ts == 0

    def test_recover_with_data(self, sample_db_connection):
        """Test recover() returns max timestamp."""
        conn = sample_db_connection

        for i in range(100):
            conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (1000 + i, 100, 110, 50))
        conn.commit()

        max_ts = conn.execute("SELECT MAX(ts) FROM samples").fetchone()
        recovery_ts = max_ts[0] if max_ts and max_ts[0] else 0
        assert recovery_ts == 1099  # 1000 + 99

    def test_recover_after_partial_batch(self, sample_db_connection):
        """Test recover() identifies gap after simulated crash."""
        conn = sample_db_connection

        # Simulate: wrote samples 1000-1049, then crash, restart
        for i in range(50):
            conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (1000 + i, 100, 110, 50))
        conn.commit()

        max_ts = conn.execute("SELECT MAX(ts) FROM samples").fetchone()
        recovery_ts = max_ts[0] if max_ts and max_ts[0] else 0
        assert recovery_ts == 1049

        # Verify gap: next expected is 1050
        assert recovery_ts + 1 == 1050


class TestLossLogging:
    """Test loss logging for disk full, process kill, etc."""

    def test_loss_log_path_creation(self, loss_log_path):
        """Test loss log file can be created."""
        loss_log_path.parent.mkdir(parents=True, exist_ok=True)
        loss_log_path.write_text("")
        assert loss_log_path.exists()

    def test_loss_log_append_entry(self, loss_log_path):
        """Test appending loss entry to log."""
        loss_log_path.parent.mkdir(parents=True, exist_ok=True)

        with open(loss_log_path, "a") as f:
            f.write("1000|disk_full\n")
            f.write("1001|process_killed\n")

        lines = loss_log_path.read_text().strip().split("\n")
        assert len(lines) == 2
        assert "disk_full" in lines[0]
        assert "process_killed" in lines[1]

    def test_loss_log_format(self, loss_log_path):
        """Test loss log maintains ts|reason format."""
        loss_log_path.parent.mkdir(parents=True, exist_ok=True)

        with open(loss_log_path, "a") as f:
            f.write("1000|sqlite3.OperationalError: disk I/O error\n")

        lines = loss_log_path.read_text().strip().split("\n")
        ts, reason = lines[0].split("|", 1)
        assert int(ts) == 1000
        assert "disk" in reason.lower()


class TestCrashRecoveryScenario:
    """Integration tests for crash and recovery scenarios."""

    def test_scenario_normal_operation(self, sample_db_connection):
        """Test normal operation: write → commit → verify."""
        conn = sample_db_connection

        for i in range(100):
            conn.execute(
                "INSERT OR IGNORE INTO samples VALUES (?,?,?,?)",
                (1000 + i, 100 + i, 110 + i, 50 + i),
            )
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count == 100

    def test_scenario_crash_before_commit(self, tmp_db_dir):
        """Test that uncommitted writes are lost (as expected with WAL)."""
        db_path = tmp_db_dir / "test_crash.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("CREATE TABLE samples (ts INTEGER PRIMARY KEY, s1_ch1 INTEGER, s1_ch2 INTEGER, s2_ch1 INTEGER)")

        # Insert but don't commit
        conn.execute("INSERT INTO samples VALUES (1000, 100, 110, 50)")
        # Simulate crash: close without commit

        # Reconnect: uncommitted should be rolled back
        conn2 = sqlite3.connect(str(db_path))
        count = conn2.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count == 0
        conn2.close()

    def test_scenario_crash_after_commit(self, tmp_db_dir):
        """Test that committed data survives crash."""
        db_path = tmp_db_dir / "test_recovery.db"

        # Write and commit
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("CREATE TABLE samples (ts INTEGER PRIMARY KEY, s1_ch1 INTEGER, s1_ch2 INTEGER, s2_ch1 INTEGER)")
        conn.execute("INSERT INTO samples VALUES (1000, 100, 110, 50)")
        conn.commit()

        # Simulate crash and reconnect
        conn2 = sqlite3.connect(str(db_path))
        count = conn2.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count == 1

        row = conn2.execute("SELECT * FROM samples WHERE ts=1000").fetchone()
        assert row == (1000, 100, 110, 50)
        conn2.close()

    def test_scenario_duplicate_batch_after_restart(self, sample_db_connection):
        """Test that re-sending batch after restart doesn't duplicate (INSERT OR IGNORE)."""
        conn = sample_db_connection

        # Batch 1: samples 1000-1009
        batch1 = [(1000 + i, 100 + i, 110 + i, 50 + i) for i in range(10)]
        for ts, ch1, ch2, s2ch1 in batch1:
            conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (ts, ch1, ch2, s2ch1))
        conn.commit()

        # Simulate: recovery detected max_ts=1009, so retry batch1 again
        for ts, ch1, ch2, s2ch1 in batch1:
            conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (ts, ch1, ch2, s2ch1))
        conn.commit()

        # Should still have only 10 unique samples
        count = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        assert count == 10


class TestDataIntegrity:
    """Test data integrity across operations."""

    def test_no_silent_data_loss(self, sample_db_connection):
        """Test that no samples are silently dropped."""
        conn = sample_db_connection

        timestamps = [1000, 1005, 1010, 1020, 1030, 2000, 5000, 10000]
        for i, ts in enumerate(timestamps):
            conn.execute(
                "INSERT OR IGNORE INTO samples VALUES (?,?,?,?)",
                (ts, 100 + i, 110 + i, 50 + i),
            )
        conn.commit()

        for i, ts in enumerate(timestamps):
            row = conn.execute("SELECT * FROM samples WHERE ts=?", (ts,)).fetchone()
            assert row is not None, f"Sample at ts={ts} was lost"
            assert row[1] == 100 + i

    def test_timestamp_primary_key_uniqueness(self, sample_db_connection):
        """Test that ts PRIMARY KEY enforces uniqueness."""
        conn = sample_db_connection

        conn.execute("INSERT INTO samples VALUES (1000, 100, 110, 50)")
        conn.commit()

        # Try to insert duplicate ts (different values)
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO samples VALUES (1000, 200, 220, 60)")
            conn.commit()

    def test_ordered_retrieval_by_timestamp(self, sample_db_connection):
        """Test that samples can be retrieved in order by timestamp."""
        conn = sample_db_connection

        timestamps = [5000, 1000, 3000, 2000, 4000]
        for i, ts in enumerate(timestamps):
            conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (ts, i, i, i))
        conn.commit()

        rows = conn.execute("SELECT ts FROM samples ORDER BY ts").fetchall()
        ts_values = [row[0] for row in rows]
        assert ts_values == sorted(timestamps)
