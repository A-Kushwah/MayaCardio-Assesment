"""Processing engine for cardiac monitoring device.

Implements windowing, heart rate algorithm, and statistics as per spec.
Same logic for online and offline processing.
"""

from __future__ import annotations
from collections import deque
from typing import List, Optional, Tuple, Deque
import math
try:
    from .window import Window, WindowResult
except ImportError:
    from window import Window, WindowResult


class Processor:
    """Processes synchronized sensor samples into 1-second windows.

    Attributes:
        mode: "online" or "offline"
        window_index: current window number (0-based)
        history_len: number of samples to keep for peak detection and HR
        buffers: per-channel sample histories (most recent at the end)
    """

    def __init__(self, mode: str = "offline"):
        if mode not in ("online", "offline"):
            raise ValueError('mode must be "online" or "offline"')
        self.mode = mode
        self.window_index = 0
        # 5 seconds (2000 samples) + 800 samples for peak detection safety
        self.history_len = 2800
        # Each buffer holds tuples (sample_value, is_available) or just value with None for missing
        # We'll store raw values (float or None) and manage missing via None.
        self.s1_ch1: Deque[Optional[float]] = deque(maxlen=self.history_len)
        self.s1_ch2: Deque[Optional[float]] = deque(maxlen=self.history_len)
        self.s1_ch3: Deque[Optional[float]] = deque(maxlen=self.history_len)
        self.s2_ch1: Deque[Optional[float]] = deque(maxlen=self.history_len)
        self.s2_ch2: Deque[Optional[float]] = deque(maxlen=self.history_len)

    def add_sample(
        self,
        s1_ch1: Optional[float],
        s1_ch2: Optional[float],
        s1_ch3: Optional[float],
        s2_ch1: Optional[float],
        s2_ch2: Optional[float],
    ) -> None:
        """Add one synchronized sample (5 values) to the history buffers."""
        self.s1_ch1.append(s1_ch1)
        self.s1_ch2.append(s1_ch2)
        self.s1_ch3.append(s1_ch3)
        self.s2_ch1.append(s2_ch1)
        self.s2_ch2.append(s2_ch2)

    def _get_window_samples(self, w: int) -> Tuple[
        List[Optional[float]], List[Optional[float]], List[Optional[float]],
        List[Optional[float]], List[Optional[float]]
    ]:
        """Extract the 400 samples for window w from the buffers.
        Assumes we have at least (w+1)*400 samples in history.
        Returns lists for each channel (length 400) with None for missing.
        """
        start = w * 400
        end = start + 400
        # Since we store in deques with maxlen, we need to index from left.
        # Convert to list for slicing (deque does not support slicing).
        s1_ch1_list = list(self.s1_ch1)
        s1_ch2_list = list(self.s1_ch2)
        s1_ch3_list = list(self.s1_ch3)
        s2_ch1_list = list(self.s2_ch1)
        s2_ch2_list = list(self.s2_ch2)
        return (
            s1_ch1_list[start:end],
            s1_ch2_list[start:end],
            s1_ch3_list[start:end],
            s2_ch1_list[start:end],
            s2_ch2_list[start:end],
        )

    def _count_missing(self, samples: List[Optional[float]]) -> int:
        return sum(1 for v in samples if v is None)

    def _mean_min_max(self, samples: List[Optional[float]]) -> Tuple[
        Optional[float], Optional[float], Optional[float]
    ]:
        valid = [v for v in samples if v is not None]
        if not valid:
            return None, None, None
        return (
            sum(valid) / len(valid),
            min(valid),
            max(valid),
        )

    def _magnitude(
        self, s2_ch1: List[Optional[float]], s2_ch2: List[Optional[float]]
    ) -> Optional[float]:
        sumsq = 0.0
        count = 0
        for a, b in zip(s2_ch1, s2_ch2):
            if a is not None and b is not None:
                sumsq += a * a + b * b
                count += 1
        if count == 0:
            return None
        return math.sqrt(sumsq / count)

    def _is_peak(
        self, channel: List[Optional[float]], idx: int, prev_peak_index: Optional[int]
    ) -> bool:
        # Need 800 samples before and 1 after available
        if idx < 800 or idx + 1 >= len(channel):
            return False
        if channel[idx] is None or channel[idx - 1] is None or channel[idx + 1] is None:
            return False
        # Larger than both neighbours
        if not (channel[idx] > channel[idx - 1] and channel[idx] > channel[idx + 1]):
            return False
        # At least 100 samples after previous peak
        if prev_peak_index is not None and (idx - prev_peak_index) < 100:
            return False
        # Larger than half the max of 800 samples before
        prev_800 = channel[idx - 800:idx]  # 800 samples ending at idx-1
        valid_prev = [v for v in prev_800 if v is not None]
        if not valid_prev:
            return False
        half_max = max(valid_prev) / 2.0
        if not (channel[idx] > half_max):
            return False
        return True

    def _detect_peaks(
        self, channel: List[Optional[float]], start_idx: int, end_idx: int
    ) -> List[int]:
        """Detect peaks with 100-sample separation rule."""
        peaks: List[int] = []
        prev_peak: Optional[int] = None
        for i in range(start_idx, end_idx):  # need idx+1 for neighbour check
            if self._is_peak(channel, i, prev_peak):
                peaks.append(i)
                prev_peak = i
        return peaks

    def _heart_rate(
        self,
        s1_ch2: List[Optional[float]],
        window_index: int,
        total_samples_so_far: int,
    ) -> Optional[float]:
        """Compute heart rate for window_index using last 5 seconds of s1_ch2.
        Returns BPM or None.
        """
        # No rate if w < 4
        if window_index < 4:
            return None
        # Define 5-second window in samples: from 400*(w-4) to 400*(w+1)-1 inclusive
        hist_start = (window_index - 4) * 400
        hist_end = (window_index + 1) * 400 - 1  # inclusive
        # Check we have enough history
        if hist_end >= len(s1_ch2):
            # Not enough samples yet (should not happen if called after window filled)
            return None
        # Extract the 5-second slice
        slice_ = s1_ch2[hist_start:hist_end + 1]
        # If any sample missing in this slice, no rate
        if any(v is None for v in slice_):
            return None
        # Detect peaks in this slice (note: we need to adjust indices for _detect_peaks)
        # We'll pass the absolute start and end indices to _detect_peaks
        peaks = self._detect_peaks(s1_ch2, hist_start, hist_end)
        # Need at least 2 peaks
        if len(peaks) < 2:
            return None
        # Compute gaps between consecutive peaks in samples
        gaps_samples = [peaks[i + 1] - peaks[i] for i in range(len(peaks) - 1)]
        # Convert to seconds (divide by sample rate 400 Hz)
        gaps_sec = [g / 400.0 for g in gaps_samples]
        mean_gap_sec = sum(gaps_sec) / len(gaps_sec)
        # Heart rate = 60 / mean_gap_sec
        if mean_gap_sec <= 0:
            return None
        return 60.0 / mean_gap_sec

    def process_window(self) -> Optional[WindowResult]:
        """Process the most recent full window (if available) and return its result.
        Returns None if we don't yet have 400 samples for a window.
        Advances window_index after processing.
        """
        total_samples = len(self.s1_ch1)
        if total_samples < (self.window_index + 1) * 400:
            return None  # not enough for this window yet

        # Get samples for this window
        s1_ch1_win, s1_ch2_win, s1_ch3_win, s2_ch1_win, s2_ch2_win = self._get_window_samples(
            self.window_index
        )

        # Missing counts
        missing_1 = self._count_missing(s1_ch1_win) + self._count_missing(s1_ch2_win) + self._count_missing(s1_ch3_win)
        missing_2 = self._count_missing(s2_ch1_win) + self._count_missing(s2_ch2_win)
        complete = (missing_1 == 0) and (missing_2 == 0)

        # Per-channel statistics for sensor 1
        mean_ch1_s1, min_ch1_s1, max_ch1_s1 = self._mean_min_max(s1_ch1_win)
        mean_ch2_s1, min_ch2_s1, max_ch2_s1 = self._mean_min_max(s1_ch2_win)
        mean_ch3_s1, min_ch3_s1, max_ch3_s1 = self._mean_min_max(s1_ch3_win)

        # Magnitude from sensor 2
        magnitude = self._magnitude(s2_ch1_win, s2_ch2_win)

        # Heart rate from sensor 1 channel 2
        heart_rate = self._heart_rate(
            list(self.s1_ch2),  # full history so far
            self.window_index,
            total_samples,
        )

        result = WindowResult(
            window_index=self.window_index,
            mode=self.mode,
            missing_1=missing_1,
            missing_2=missing_2,
            complete=complete,
            mean_ch1_s1=mean_ch1_s1,
            mean_ch2_s1=mean_ch2_s1,
            mean_ch3_s1=mean_ch3_s1,
            min_ch1_s1=min_ch1_s1,
            min_ch2_s1=min_ch2_s1,
            min_ch3_s1=min_ch3_s1,
            max_ch1_s1=max_ch1_s1,
            max_ch2_s1=max_ch2_s1,
            max_ch3_s1=max_ch3_s1,
            magnitude=magnitude,
            heart_rate=heart_rate,
        )

        self.window_index += 1
        return result

    def process_samples(
        self, samples: List[Tuple[Optional[float], ...]]
    ) -> List[WindowResult]:
        """Process a list of samples (offline) and return all window results.
        Each sample is a tuple (s1_ch1, s1_ch2, s1_ch3, s2_ch1, s2_ch2).
        """
        results: List[WindowResult] = []
        for sample in samples:
            self.add_sample(*sample)
            # Process as many windows as possible
            while True:
                win_result = self.process_window()
                if win_result is None:
                    break
                results.append(win_result)
        return results
