"""Resource metrics collection for cardiac pipeline during online mode.

Samples CPU, memory, disk, and network every second during pipeline execution.
Exports aggregated metrics to CSV and JSON formats.
"""

import os
import sys
import time
import json
import csv
import psutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime


class MetricsCollector:
    """Collects and aggregates system resource metrics.

    Attributes:
        samples: List of metric samples (timestamp, cpu%, mem_mb, disk_mb, net_mb)
        start_time: Unix timestamp when collection started
        end_time: Unix timestamp when collection ended
        duration_sec: Collection duration in seconds
    """

    def __init__(self, interval_sec: float = 1.0):
        """Initialize metrics collector.

        Args:
            interval_sec: Sampling interval in seconds (default 1.0)
        """
        self.interval_sec = interval_sec
        self.samples: List[Dict] = []
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        self.duration_sec: float = 0.0

        # Track disk writes and network bytes sent across samples
        self._prev_disk_write_bytes: Optional[int] = None
        self._prev_net_sent_bytes: Optional[int] = None
        self._process: Optional[psutil.Process] = None

    def start(self) -> None:
        """Start metric collection."""
        self.start_time = time.time()
        self.samples = []
        self._process = psutil.Process(os.getpid())

        # Get initial disk and network state
        self._update_disk_baseline()
        self._update_network_baseline()

    def stop(self) -> None:
        """Stop metric collection and compute duration."""
        self.end_time = time.time()
        if self.start_time:
            self.duration_sec = self.end_time - self.start_time

    def _update_disk_baseline(self) -> None:
        """Capture initial disk write bytes."""
        try:
            # Try to read from /proc/diskstats (Linux)
            if sys.platform.startswith('linux'):
                total_write_bytes = self._read_disk_write_bytes_linux()
                self._prev_disk_write_bytes = total_write_bytes
            else:
                # Windows: use process-level I/O stats
                if self._process:
                    io_counters = self._process.io_counters()
                    self._prev_disk_write_bytes = io_counters.write_bytes
        except Exception:
            self._prev_disk_write_bytes = 0

    def _update_network_baseline(self) -> None:
        """Capture initial network sent bytes."""
        try:
            net_io = psutil.net_io_counters()
            self._prev_net_sent_bytes = net_io.bytes_sent
        except Exception:
            self._prev_net_sent_bytes = 0

    def _read_disk_write_bytes_linux(self) -> int:
        """Read total disk write bytes from /proc/diskstats (Linux only)."""
        total_writes = 0
        try:
            with open('/proc/diskstats', 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 10:
                        # Column 9 (0-indexed 8) is writes completed
                        # Column 10 (0-indexed 9) is sectors written
                        # We'll use sectors * 512 bytes per sector
                        sectors_written = int(parts[9])
                        total_writes += sectors_written * 512
        except Exception:
            pass
        return total_writes

    def _get_disk_written_mb(self) -> float:
        """Get incremental disk written since last sample (MB)."""
        try:
            if sys.platform.startswith('linux'):
                current_bytes = self._read_disk_write_bytes_linux()
            else:
                # Windows: use process I/O stats
                if self._process:
                    io_counters = self._process.io_counters()
                    current_bytes = io_counters.write_bytes
                else:
                    current_bytes = 0

            if self._prev_disk_write_bytes is not None:
                delta_bytes = max(0, current_bytes - self._prev_disk_write_bytes)
                self._prev_disk_write_bytes = current_bytes
                return delta_bytes / (1024 * 1024)  # Convert to MB
        except Exception:
            pass
        return 0.0

    def _get_network_sent_mb(self) -> float:
        """Get incremental network sent since last sample (MB)."""
        try:
            net_io = psutil.net_io_counters()
            current_bytes = net_io.bytes_sent

            if self._prev_net_sent_bytes is not None:
                delta_bytes = max(0, current_bytes - self._prev_net_sent_bytes)
                self._prev_net_sent_bytes = current_bytes
                return delta_bytes / (1024 * 1024)  # Convert to MB
        except Exception:
            pass
        return 0.0

    def sample(self) -> Dict:
        """Collect one metric sample and return it.

        Returns:
            Dictionary with keys: timestamp, cpu_percent, memory_mb,
                                  disk_written_mb, network_sent_mb
        """
        try:
            timestamp = time.time()

            # CPU usage (percent, averaged over interval)
            cpu_percent = psutil.cpu_percent(interval=None)  # non-blocking

            # Memory usage
            try:
                if self._process:
                    mem_info = self._process.memory_info()
                    memory_mb = mem_info.rss / (1024 * 1024)
                else:
                    vm = psutil.virtual_memory()
                    memory_mb = vm.used / (1024 * 1024)
            except Exception:
                memory_mb = 0.0

            # Disk written (incremental since last sample)
            disk_mb = self._get_disk_written_mb()

            # Network sent (incremental since last sample)
            network_mb = self._get_network_sent_mb()

            sample_dict = {
                'timestamp': timestamp,
                'cpu_percent': round(cpu_percent, 2),
                'memory_mb': round(memory_mb, 2),
                'disk_written_mb': round(disk_mb, 4),
                'network_sent_mb': round(network_mb, 4),
            }

            self.samples.append(sample_dict)
            return sample_dict
        except Exception as e:
            print(f"Error sampling metrics: {e}", file=sys.stderr)
            return {
                'timestamp': time.time(),
                'cpu_percent': 0.0,
                'memory_mb': 0.0,
                'disk_written_mb': 0.0,
                'network_sent_mb': 0.0,
            }

    def get_aggregates(self) -> Dict:
        """Compute aggregated metrics from all samples.

        Returns:
            Dictionary with: cpu_avg, cpu_peak, memory_peak,
                           total_disk_written, total_network_sent
        """
        if not self.samples:
            return {
                'cpu_avg': 0.0,
                'cpu_peak': 0.0,
                'memory_peak': 0.0,
                'total_disk_written': 0.0,
                'total_network_sent': 0.0,
            }

        cpu_values = [s['cpu_percent'] for s in self.samples]
        memory_values = [s['memory_mb'] for s in self.samples]
        disk_values = [s['disk_written_mb'] for s in self.samples]
        network_values = [s['network_sent_mb'] for s in self.samples]

        return {
            'cpu_avg': round(sum(cpu_values) / len(cpu_values), 2),
            'cpu_peak': round(max(cpu_values), 2),
            'memory_peak': round(max(memory_values), 2),
            'total_disk_written': round(sum(disk_values), 2),
            'total_network_sent': round(sum(network_values), 4),
        }

    def export_csv(self, filepath: Path) -> None:
        """Export samples to CSV file.

        Args:
            filepath: Output CSV file path
        """
        if not self.samples:
            print("No samples to export", file=sys.stderr)
            return

        fieldnames = ['timestamp', 'cpu_percent', 'memory_mb',
                      'disk_written_mb', 'network_sent_mb']

        with open(filepath, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.samples)

    def export_json(self, filepath: Path) -> None:
        """Export aggregates and samples to JSON file.

        Args:
            filepath: Output JSON file path
        """
        aggregates = self.get_aggregates()
        output = {
            'collection_start': self.start_time,
            'collection_end': self.end_time,
            'duration_seconds': self.duration_sec,
            'aggregates': aggregates,
            'samples_count': len(self.samples),
            'samples': self.samples,
        }

        with open(filepath, 'w') as f:
            json.dump(output, f, indent=2)

    def print_summary(self) -> None:
        """Print aggregated metrics to stdout."""
        agg = self.get_aggregates()
        print("\n" + "=" * 60)
        print("METRICS COLLECTION SUMMARY")
        print("=" * 60)
        print(f"Duration: {self.duration_sec:.1f} seconds")
        print(f"Samples collected: {len(self.samples)}")
        print()
        print(f"CPU (average): {agg['cpu_avg']:.2f}%")
        print(f"CPU (peak): {agg['cpu_peak']:.2f}%")
        print(f"Memory (peak): {agg['memory_peak']:.2f} MB")
        print(f"Disk written (total): {agg['total_disk_written']:.2f} MB")
        print(f"Network sent (total): {agg['total_network_sent']:.4f} MB")
        print("=" * 60 + "\n")
