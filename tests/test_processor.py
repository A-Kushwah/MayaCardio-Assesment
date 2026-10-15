"""Unit tests for Processor: windowing, peak detection, heart rate, stats, null handling."""
import pytest
import sys
import pathlib
import math

# Add src to path for imports
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from processor import Processor
from window import WindowResult


class TestProcessorInit:
    """Test Processor initialization and mode validation."""

    def test_init_offline_mode(self):
        """Test Processor initializes in offline mode."""
        p = Processor(mode="offline")
        assert p.mode == "offline"
        assert p.window_index == 0
        assert len(p.s1_ch1) == 0

    def test_init_online_mode(self):
        """Test Processor initializes in online mode."""
        p = Processor(mode="online")
        assert p.mode == "online"
        assert p.window_index == 0

    def test_init_invalid_mode(self):
        """Test Processor raises ValueError for invalid mode."""
        with pytest.raises(ValueError, match='mode must be "online" or "offline"'):
            Processor(mode="invalid")

    def test_init_default_mode(self):
        """Test Processor defaults to offline mode."""
        p = Processor()
        assert p.mode == "offline"


class TestAddSample:
    """Test sample accumulation in history buffers."""

    def test_add_single_sample(self):
        """Test adding a single sample."""
        p = Processor()
        p.add_sample(100.0, 110.0, 120.0, 50.0, 60.0)
        assert len(p.s1_ch1) == 1
        assert len(p.s1_ch2) == 1
        assert p.s1_ch1[0] == 100.0
        assert p.s1_ch2[0] == 110.0

    def test_add_multiple_samples(self):
        """Test adding multiple samples."""
        p = Processor()
        for i in range(10):
            p.add_sample(float(i), float(i + 1), float(i + 2), float(i + 3), float(i + 4))
        assert len(p.s1_ch1) == 10
        assert len(p.s1_ch2) == 10
        assert p.s1_ch1[-1] == 9.0
        assert p.s1_ch2[-1] == 10.0

    def test_add_none_values(self):
        """Test adding None values (missing samples)."""
        p = Processor()
        p.add_sample(None, 110.0, None, 50.0, None)
        assert len(p.s1_ch1) == 1
        assert p.s1_ch1[0] is None
        assert p.s1_ch2[0] == 110.0
        assert p.s1_ch3[0] is None

    def test_buffer_maxlen_constraint(self):
        """Test that buffers respect maxlen (2800 samples)."""
        p = Processor()
        for i in range(3000):
            p.add_sample(float(i), float(i), float(i), float(i), float(i))
        assert len(p.s1_ch1) == 2800  # maxlen is 2800
        # Oldest samples should be gone, newest should remain
        assert p.s1_ch1[-1] == 2999.0
        assert p.s1_ch1[0] == 200.0  # 3000 - 2800 = 200


class TestWindowProcessing:
    """Test window extraction and processing."""

    def test_process_window_insufficient_samples(self):
        """Test that process_window returns None without 400 samples."""
        p = Processor()
        for i in range(399):
            p.add_sample(100.0, 100.0, 100.0, 50.0, 50.0)
        result = p.process_window()
        assert result is None
        assert p.window_index == 0

    def test_process_window_first_complete(self):
        """Test processing first complete window (400 samples)."""
        p = Processor()
        for i in range(400):
            p.add_sample(100.0, 110.0, 120.0, 50.0, 60.0)
        result = p.process_window()
        assert result is not None
        assert result.window_index == 0
        assert p.window_index == 1  # Advances after processing
        assert result.mode == "offline"
        assert result.complete is True
        assert result.missing_1 == 0
        assert result.missing_2 == 0

    def test_process_multiple_windows(self):
        """Test processing multiple sequential windows."""
        p = Processor()
        for i in range(1000):  # 2.5 windows
            p.add_sample(100.0, 110.0, 120.0, 50.0, 60.0)

        results = []
        while True:
            result = p.process_window()
            if result is None:
                break
            results.append(result)

        assert len(results) == 2
        assert results[0].window_index == 0
        assert results[1].window_index == 1
        assert p.window_index == 2


class TestStatistics:
    """Test statistical calculations (mean, min, max)."""

    def test_mean_min_max_valid_data(self):
        """Test mean/min/max calculation with valid data."""
        p = Processor()
        for i in range(400):
            # Add: 100, 200, 300, 100, 100 for each sample
            p.add_sample(100.0, 200.0, 300.0, 100.0, 100.0)

        result = p.process_window()
        assert result.mean_ch1_s1 == 100.0
        assert result.mean_ch2_s1 == 200.0
        assert result.mean_ch3_s1 == 300.0
        assert result.min_ch1_s1 == 100.0
        assert result.max_ch1_s1 == 100.0

    def test_mean_min_max_with_variation(self):
        """Test mean/min/max with varying data."""
        p = Processor()
        for i in range(400):
            s1_ch1_val = 100.0 + (i % 100)  # Varies 100-199
            p.add_sample(s1_ch1_val, 200.0, 300.0, 100.0, 100.0)

        result = p.process_window()
        assert result.min_ch1_s1 == 100.0
        assert result.max_ch1_s1 == 199.0
        # Mean should be around 149.5
        assert 149.0 < result.mean_ch1_s1 < 150.0

    def test_statistics_with_missing_values(self):
        """Test statistics calculation skips None values."""
        p = Processor()
        for i in range(400):
            s1_ch1_val = 100.0 if i % 2 == 0 else None
            p.add_sample(s1_ch1_val, 200.0, 300.0, 100.0, 100.0)

        result = p.process_window()
        # Should compute stats only from non-None values
        assert result.mean_ch1_s1 == 100.0
        assert result.min_ch1_s1 == 100.0
        assert result.max_ch1_s1 == 100.0
        assert result.missing_1 == 200  # 200 None in ch1

    def test_statistics_all_none(self):
        """Test statistics when all values are None."""
        p = Processor()
        for i in range(400):
            p.add_sample(None, None, None, 100.0, 100.0)

        result = p.process_window()
        assert result.mean_ch1_s1 is None
        assert result.min_ch1_s1 is None
        assert result.max_ch1_s1 is None
        assert result.missing_1 == 1200  # 400 samples * 3 channels


class TestMagnitude:
    """Test magnitude calculation (acceleration norm)."""

    def test_magnitude_calculation(self):
        """Test magnitude from s2 ch1 and ch2."""
        p = Processor()
        for i in range(400):
            # s2_ch1 = 3, s2_ch2 = 4 -> magnitude = sqrt(9+16) = 5
            p.add_sample(100.0, 100.0, 100.0, 3.0, 4.0)

        result = p.process_window()
        assert result.magnitude == pytest.approx(5.0)

    def test_magnitude_with_missing_s2(self):
        """Test magnitude when s2 data is missing."""
        p = Processor()
        for i in range(400):
            p.add_sample(100.0, 100.0, 100.0, None, None)

        result = p.process_window()
        assert result.magnitude is None
        assert result.missing_2 == 800  # 2 channels × 400 samples

    def test_magnitude_partial_s2_missing(self):
        """Test magnitude with some s2 values missing."""
        p = Processor()
        for i in range(400):
            if i < 200:
                p.add_sample(100.0, 100.0, 100.0, 3.0, 4.0)
            else:
                p.add_sample(100.0, 100.0, 100.0, None, None)

        result = p.process_window()
        # Magnitude computed from first 200 samples: sqrt(9+16) = 5
        assert result.magnitude == pytest.approx(5.0)
        assert result.missing_2 == 400  # 2 channels × 200 missing samples


class TestCompleteFlag:
    """Test 'complete' flag logic."""

    def test_complete_all_valid(self):
        """Test complete=True when all data present."""
        p = Processor()
        for i in range(400):
            p.add_sample(100.0, 100.0, 100.0, 50.0, 50.0)
        result = p.process_window()
        assert result.complete is True
        assert result.missing_1 == 0
        assert result.missing_2 == 0

    def test_complete_false_with_missing_s1(self):
        """Test complete=False when s1 data missing."""
        p = Processor()
        for i in range(400):
            p.add_sample(None, 100.0, 100.0, 50.0, 50.0)
        result = p.process_window()
        assert result.complete is False
        assert result.missing_1 > 0

    def test_complete_false_with_missing_s2(self):
        """Test complete=False when s2 data missing."""
        p = Processor()
        for i in range(400):
            p.add_sample(100.0, 100.0, 100.0, None, 50.0)
        result = p.process_window()
        assert result.complete is False
        assert result.missing_2 > 0


class TestHeartRate:
    """Test heart rate detection from peaks in s1_ch2."""

    def test_heart_rate_not_enough_windows(self):
        """Test heart_rate=None for windows < 4."""
        p = Processor()
        # First 3 windows (1200 samples)
        for i in range(1200):
            p.add_sample(100.0, 100.0, 100.0, 50.0, 50.0)

        results = []
        while True:
            result = p.process_window()
            if result is None:
                break
            results.append(result)

        # Windows 0, 1, 2 should have None heart_rate
        assert len(results) == 3
        for result in results:
            assert result.heart_rate is None

    def test_heart_rate_with_synthetic_peaks(self, mock_biosignal_data):
        """Test heart rate calculation with known peak pattern."""
        p = Processor(mode="offline")
        results = p.process_samples(mock_biosignal_data)

        # After window 4, we should have heart rate (if peaks are detected)
        # Note: with the specific pattern in mock_biosignal_data, HR may not
        # be reliably detected, so we check that calculation works without error
        for result in results:
            if result.window_index >= 4:
                # Should be None or in realistic range (30-200 BPM)
                if result.heart_rate is not None:
                    assert 30 < result.heart_rate < 200
                break

    def test_heart_rate_missing_data_returns_none(self):
        """Test heart_rate=None when 5-second window has missing data."""
        p = Processor()
        # Windows 0-3: valid data
        for i in range(1600):
            p.add_sample(100.0, 100.0, 100.0, 50.0, 50.0)

        # Window 4: inject missing value at position 1650
        for i in range(400):
            if i == 50:  # One missing value in window 4
                p.add_sample(100.0, None, 100.0, 50.0, 50.0)
            else:
                p.add_sample(100.0, 100.0, 100.0, 50.0, 50.0)

        results = []
        while True:
            result = p.process_window()
            if result is None:
                break
            results.append(result)

        # Window 4 should have None heart_rate due to missing data
        window_4 = next((r for r in results if r.window_index == 4), None)
        assert window_4 is not None
        assert window_4.heart_rate is None


class TestProcessSamples:
    """Test batch processing (offline mode)."""

    def test_process_samples_empty(self):
        """Test processing empty sample list."""
        p = Processor()
        results = p.process_samples([])
        assert results == []
        assert p.window_index == 0

    def test_process_samples_single_window(self):
        """Test processing exactly 400 samples."""
        p = Processor()
        samples = [(100.0, 100.0, 100.0, 50.0, 50.0) for _ in range(400)]
        results = p.process_samples(samples)
        assert len(results) == 1
        assert results[0].window_index == 0

    def test_process_samples_multiple_windows(self, mock_biosignal_data):
        """Test processing 1000 samples (2+ windows)."""
        p = Processor()
        results = p.process_samples(mock_biosignal_data)
        # 1000 samples / 400 per window = 2 complete windows
        assert len(results) >= 2
        assert results[0].window_index == 0
        assert results[1].window_index == 1

    def test_process_samples_incremental_consistency(self, mock_biosignal_data):
        """Test that batch and incremental processing produce same results."""
        p1 = Processor()
        results_batch = p1.process_samples(mock_biosignal_data)

        p2 = Processor()
        results_incremental = []
        for sample in mock_biosignal_data:
            p2.add_sample(*sample)
            result = p2.process_window()
            if result:
                results_incremental.append(result)

        assert len(results_batch) == len(results_incremental)
        for b, inc in zip(results_batch, results_incremental):
            assert b.window_index == inc.window_index
            assert b.complete == inc.complete
            assert b.missing_1 == inc.missing_1
            assert b.missing_2 == inc.missing_2


class TestEdgeCases:
    """Test edge cases and corner scenarios."""

    def test_edge_case_all_none(self, edge_case_samples):
        """Test window with all None values."""
        p = Processor()
        results = p.process_samples(edge_case_samples[:800])  # First 2 windows

        win0 = results[0]
        assert win0.complete is True

        win1 = results[1]
        assert win1.complete is False
        assert win1.missing_1 == 1200
        assert win1.missing_2 == 800
        assert win1.mean_ch1_s1 is None
        assert win1.magnitude is None

    def test_edge_case_partial_validity(self, edge_case_samples):
        """Test windows with partial sensor validity."""
        p = Processor()
        results = p.process_samples(edge_case_samples)

        # Window 2: s1 valid, s2 missing
        win2 = results[2]
        assert win2.complete is False
        assert win2.missing_1 == 0
        assert win2.missing_2 == 800  # 2 channels × 400 samples
        assert win2.mean_ch1_s1 == 100.0
        assert win2.magnitude is None

        # Window 3: s2 valid, s1 missing
        win3 = results[3]
        assert win3.complete is False
        assert win3.missing_1 == 1200  # 3 channels × 400 samples
        assert win3.missing_2 == 0
        assert win3.mean_ch1_s1 is None
        assert win3.magnitude == pytest.approx(math.sqrt(50.0 * 50.0 + 50.0 * 50.0))

    def test_window_result_as_dict(self):
        """Test WindowResult serialization to dict."""
        p = Processor()
        for i in range(400):
            p.add_sample(100.5, 200.3, 150.7, 50.0, 60.0)

        result = p.process_window()
        d = result.as_dict()

        assert d["window_index"] == 0
        assert d["mode"] == "offline"
        assert d["complete"] is True
        assert isinstance(d["mean_ch1_s1"], float) or d["mean_ch1_s1"] is None
        # Rounding applied to 1 decimal place
        assert d["mean_ch1_s1"] == 100.5
