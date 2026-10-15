# Resource Metrics Report

**Deliverable 4: Resource metrics collected during 1-hour online mode execution**

## Collection Environment

- **Device:** [e.g., Raspberry Pi 4, 4GB RAM]
- **OS:** [e.g., Raspberry Pi OS, Linux kernel version]
- **Python Version:** [e.g., 3.8.10]
- **Collection Date:** [YYYY-MM-DD HH:MM UTC]
- **Duration:** 1 hour (3600 seconds)
- **Pipeline Mode:** Online (with cloud sync)
- **Network Conditions:** [e.g., stable, simulated outages]

## Aggregated Metrics

### CPU Usage
- **Average:** [XX.X]%
- **Peak:** [XX.X]%

### Memory Usage
- **Peak:** [XXXX.XX] MB

### Disk I/O
- **Total Written:** [XXX.XX] MB
- **Average per second:** [X.XX] MB/s

### Network I/O
- **Total Sent:** [XXX.XXXX] MB
- **Average per second:** [X.XXXX] MB/s

## Analysis

### Resource Constraints Met

- **ARM64 Limits:** 4 cores, 2 GB RAM
  - Memory usage well below 2 GB: ✓/✗
  - CPU utilization reasonable: ✓/✗

### Disk Performance
- [Comment on write patterns, I/O efficiency]

### Network Performance
- [Comment on cloud sync efficiency, bandwidth utilization]

## Data Collection Files

- **CSV Export:** `metrics_<timestamp>.csv`
  - Columns: timestamp, cpu_percent, memory_mb, disk_written_mb, network_sent_mb
  - Rows: 3600 (one per second)

- **JSON Export:** `metrics_<timestamp>.json`
  - Includes: aggregates, collection duration, sample count, raw samples

## How to Run

```bash
# From repository root:
pip install psutil

# Run 1-hour collection:
python tools/run_metrics.py

# Run custom duration and output:
python tools/run_metrics.py --duration 300 --output ./my_results
```

## Output Example

```
Starting metrics collection for 3600 seconds (1.0 hours)...
Output directory: /path/to/output

  [60 samples, 60.0s elapsed, 3540.0s remaining]
  [120 samples, 120.0s elapsed, 3480.0s remaining]
  ...

============================================================
METRICS COLLECTION SUMMARY
============================================================
Duration: 3600.0 seconds
Samples collected: 3600

CPU (average): 12.34%
CPU (peak): 45.67%
Memory (peak): 1024.56 MB
Disk written (total): 2048.90 MB
Network sent (total): 512.1234 MB
============================================================
```

## Notes

- Metrics are sampled every second for 3600 samples
- CPU usage includes all processes on the system (not just pipeline)
- Memory reported is resident set size (RSS) of the Python process
- Disk writes include all I/O (persistence layer + temporary files)
- Network sent includes all IP traffic on the device (not pipeline-specific)
- For production, instrument individual pipeline components for detailed breakdown
