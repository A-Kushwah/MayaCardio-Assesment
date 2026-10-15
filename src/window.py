"""Window data structure for 1-second cardiac windows (400 samples / sensor)."""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class Window:
    """One 1-second window of synchronized sensor data."""
    index: int                    # window number w (0-based)
    mode: str                     # "online" or "offline"
    sensor1: List[Optional[float]] = field(default_factory=list)  # 400 samples, 3 ch (stored flat or per-ch?)
    sensor2: List[Optional[float]] = field(default_factory=list)  # 400 samples, 2 ch

    # For our engine we treat inputs as per-channel arrays of length 400
    # We'll expose helper properties instead of enforcing flat structure.

    def __post_init__(self):
        if not self.sensor1:
            self.sensor1 = [None] * 400
        if not self.sensor2:
            self.sensor2 = [None] * 400


@dataclass
class WindowResult:
    """Computed output per window (section 3 logic)."""
    window_index: int
    mode: str
    missing_1: int
    missing_2: int
    complete: bool
    mean_ch1_s1: Optional[float]     # sensor 1 ch1
    mean_ch2_s1: Optional[float]     # sensor 1 ch2 (HR source)
    mean_ch3_s1: Optional[float]     # sensor 1 ch3
    min_ch1_s1: Optional[float]
    min_ch2_s1: Optional[float]
    min_ch3_s1: Optional[float]
    max_ch1_s1: Optional[float]
    max_ch2_s1: Optional[float]
    max_ch3_s1: Optional[float]
    magnitude: Optional[float]
    heart_rate: Optional[float]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "window_index": self.window_index,
            "mode": self.mode,
            "missing_1": self.missing_1,
            "missing_2": self.missing_2,
            "complete": self.complete,
            "mean_ch1_s1": round(self.mean_ch1_s1, 1) if self.mean_ch1_s1 is not None else None,
            "mean_ch2_s1": round(self.mean_ch2_s1, 1) if self.mean_ch2_s1 is not None else None,
            "mean_ch3_s1": round(self.mean_ch3_s1, 1) if self.mean_ch3_s1 is not None else None,
            "min_ch1_s1": round(self.min_ch1_s1, 1) if self.min_ch1_s1 is not None else None,
            "min_ch2_s1": round(self.min_ch2_s1, 1) if self.min_ch2_s1 is not None else None,
            "min_ch3_s1": round(self.min_ch3_s1, 1) if self.min_ch3_s1 is not None else None,
            "max_ch1_s1": round(self.max_ch1_s1, 1) if self.max_ch1_s1 is not None else None,
            "max_ch2_s1": round(self.max_ch2_s1, 1) if self.max_ch2_s1 is not None else None,
            "max_ch3_s1": round(self.max_ch3_s1, 1) if self.max_ch3_s1 is not None else None,
            "magnitude": round(self.magnitude, 1) if self.magnitude is not None else None,
            "heart_rate": round(self.heart_rate, 1) if self.heart_rate is not None else None,
        }
