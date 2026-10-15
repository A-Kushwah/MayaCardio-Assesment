"""Pytest fixtures for MayaCardio tests."""
import pytest
import sqlite3
import tempfile
import pathlib
import shutil
import math
from typing import List, Tuple, Optional


@pytest.fixture
def tmp_db_dir():
    """Create a temporary directory for test databases."""
    tmpdir = tempfile.mkdtemp(prefix="mayacardio_test_")
    yield pathlib.Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def mock_biosignal_data():
    """Generate 1000 synthetic biosignal samples with known patterns for testing.

    Returns: List of tuples (s1_ch1, s1_ch2, s1_ch3, s2_ch1, s2_ch2)
    - s1_ch2 has periodic peaks (simulating heartbeat at ~70 BPM)
    - Other channels have noise
    - Includes edge cases: missing values (None), amplitude variations
    """
    samples = []
    sample_rate = 400  # Hz
    duration_sec = 2.5  # 1000 samples

    # Heartbeat pattern: peaks every ~1.5 seconds (simulating 40 BPM for testing)
    # At 400 Hz: 1.5 sec = 600 samples between peaks
    for i in range(1000):
        t = i / sample_rate

        # s1_ch2: ECG-like signal with periodic peaks
        # Add sine wave modulation to create peaks
        if (i % 600) < 50:  # Peak window
            s1_ch2_val = 100.0 + 50.0 * math.sin((i % 600) / 50.0 * math.pi)
        else:
            s1_ch2_val = 50.0 + 10.0 * math.sin(2 * math.pi * t * 0.5)

        # s1_ch1, s1_ch3: noise
        s1_ch1_val = 100.0 + 20.0 * math.sin(2 * math.pi * t * 1.2)
        s1_ch3_val = 80.0 + 15.0 * math.cos(2 * math.pi * t * 0.8)

        # s2_ch1, s2_ch2: accelerometer-like (magnitude ~constant)
        s2_ch1_val = 50.0 + 10.0 * math.sin(2 * math.pi * t * 0.3)
        s2_ch2_val = 40.0 + 8.0 * math.cos(2 * math.pi * t * 0.4)

        # Add some missing values in specific ranges for edge case testing
        # Missing streak: samples 200-209 (10 samples)
        if 200 <= i < 210:
            s1_ch1_val = None
            s1_ch2_val = None
            s1_ch3_val = None

        # Sparse missing in s2 channels: samples 500, 600, 700
        if i in [500, 600, 700]:
            s2_ch1_val = None
            s2_ch2_val = None

        samples.append((s1_ch1_val, s1_ch2_val, s1_ch3_val, s2_ch1_val, s2_ch2_val))

    return samples


@pytest.fixture
def edge_case_samples():
    """Fixture with edge cases: all None, single peaks, mixed validity."""
    samples = []

    # Window 0: all valid
    for i in range(400):
        samples.append((100.0, 100.0, 100.0, 50.0, 50.0))

    # Window 1: all None (no data)
    for i in range(400):
        samples.append((None, None, None, None, None))

    # Window 2: partial data (s1 valid, s2 missing)
    for i in range(400):
        samples.append((100.0, 100.0, 100.0, None, None))

    # Window 3: partial data (s2 valid, s1 missing)
    for i in range(400):
        samples.append((None, None, None, 50.0, 50.0))

    return samples


@pytest.fixture
def sample_db_connection(tmp_db_dir):
    """Create a test SQLite database with proper schema."""
    db_path = tmp_db_dir / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("CREATE TABLE IF NOT EXISTS samples (ts INTEGER PRIMARY KEY, s1_ch1 INTEGER, s1_ch2 INTEGER, s2_ch1 INTEGER)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts)")
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def loss_log_path(tmp_db_dir):
    """Create path for loss logging."""
    return tmp_db_dir / "losses.log"
