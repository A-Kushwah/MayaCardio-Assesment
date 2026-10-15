# MayaCardio: Resilient Cardiac Data Pipeline

A production-grade cardiac monitoring data pipeline for ARM64 Raspberry Pi with automatic online/offline switching, cloud synchronization, and zero data loss guarantees.

## Overview

MayaCardio collects synchronized cardiac sensor data (two sensors, 5 channels, 400 samples/sec) with these core capabilities:

- **Continuous Collection:** Reads from dual sensors and persists every sample to disk
- **Online Mode:** Streams data to cloud and processes results within 5 seconds per window
- **Offline Mode:** Collects and processes locally when network unavailable
- **Automatic Switching:** Detects network loss/return within 10 seconds, zero data loss
- **Cloud Sync:** Sends stored samples when network returns, handles up to 2-hour outages
- **Data Honesty:** Records exactly which samples are lost and why (no gap-filling)

## Architecture

```
┌─────────────────────────────────────────────────────┐
│              Sensor Data Sources                     │
│  Sensor 1: 3 channels @ 400 Hz  (ECG-like)         │
│  Sensor 2: 2 channels @ 400 Hz  (Physiological)    │
└────────────────────┬────────────────────────────────┘
                     │
         ┌───────────▼────────────┐
         │   Data Collection      │ ← Reads from mock service
         │   (collector.py)       │
         └───────────┬────────────┘
                     │
         ┌───────────▼────────────────────────┐
         │  Persistence Layer (persistence/)   │
         │  • SQLite for indexed query         │
         │  • Transactional integrity          │
         │  • Crash recovery                   │
         └───────────┬────────────────────────┘
                     │
         ┌───────────▼──────────────────────────────┐
         │        Mode Detector (network.py)        │
         │  • Checks cloud connectivity every 10s   │
         │  • Switches online ↔ offline            │
         └─────────┬──────────────────┬──────────────┘
                   │                  │
        ┌──────────▼───────┐  ┌──────▼─────────────┐
        │  Online Processing│  │ Offline Processing│
        │  • Stream to cloud │  │ • Local compute   │
        │  • 5s latency     │  │ • Same algorithm  │
        └──────────┬────────┘  └────────┬──────────┘
                   │                    │
        ┌──────────▼──────────────────────▼──────┐
        │  Processor Engine (processor.py)        │
        │  • 1-second windows (400 samples)       │
        │  • Peak detection for heart rate        │
        │  • Statistics (mean, min, max)          │
        │  • Magnitude from dual channels         │
        └─────────────────────────────────────────┘
```

## System Requirements

### Hardware
- **Device:** Raspberry Pi 4 (ARM64, 4 cores, 2 GB RAM minimum)
- **Storage:** ≥16 GB microSD card
- **Network:** WiFi or Ethernet

### Software
- **Python:** 3.8 or later
- **OS:** Raspberry Pi OS (Debian-based) or compatible Linux
- **Cloud:** Any free tier service (AWS, Google Cloud, Azure, etc.)

### Python Dependencies
- `psutil` — System metrics collection
- `biosignal-mock` — Sensor mock service

## Installation

### 1. Clone Repository

```bash
git clone https://github.com/your-org/mayacardio.git
cd mayacardio
```

### 2. Create Virtual Environment (Optional but Recommended)

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

If `requirements.txt` is not present, install manually:

```bash
pip install psutil biosignal-mock
```

### 4. Verify Installation

```bash
python -c "import psutil; print('psutil OK')"
python -m biosignal_mock --help
```

## Quick Start

### 1. Start Mock Sensor Service

In a separate terminal:

```bash
biosignal-mock --mode signal --seed 7 --duration 3600 --port 7100
```

This simulates 1 hour of cardiac data at 400 samples/sec on localhost:7100.

### 2. Run Pipeline

```bash
python -m src.cli status
```

Expected output:
```json
{"mode":"standby","samples_saved":0,"samples_in_cloud":0,"backlog":0}
```

### 3. Check Status

```bash
python -m src.cli status --session default
```

### 4. Export Results

```bash
python -m src.cli export --session default --from device
python -m src.cli export --session default --from cloud
```

### 5. Retrieve Processed Results

```bash
python -m src.cli results --session default
```

Output: JSON lines with per-window statistics (one per second).

## Commands

### Status
```bash
pipeline status [--session ID]
```
Returns current mode (online/offline), samples saved, cloud backlog.

**Output:**
```json
{
  "mode": "online",
  "samples_saved": 1440000,
  "samples_in_cloud": 1440000,
  "backlog": 0,
  "timestamp": 1697386200
}
```

### Export
```bash
pipeline export --session ID --from device|cloud
```
Exports samples and losses as JSON lines.

**Output:** JSON lines
```json
{"ts":1697386200,"ch1":1024,"ch2":-512,"ch3":768,"s2_ch1":256,"s2_ch2":-128}
{"ts":1697386201,"ch1":1040,"ch2":-520,...}
{"sample_index":100,"reason":"sensor_dropout"}
...
```

### Results
```bash
pipeline results --session ID
```
Returns per-window processing results.

**Output:** JSON lines (1 per second)
```json
{"window":0,"mode":"online","missing_1":0,"missing_2":0,"complete":true,"mean_ch1":512.5,"mean_ch2":-510.2,"mean_ch3":765.8,"magnitude":342.1,"heart_rate":72.3}
{"window":1,"mode":"online",...}
...
```

## Processing Logic

### Window Definition
- **Duration:** 1 second
- **Sample Rate:** 400 samples/second per channel
- **Samples per Window:** 400 (per sensor, per channel)
- **Output:** One result per window

### Metrics Computed per Window

| Metric | Source | Computation | Range |
|--------|--------|-------------|-------|
| `missing_1` | Sensor 1 (3 ch) | Count unavailable samples | 0–1200 |
| `missing_2` | Sensor 2 (2 ch) | Count unavailable samples | 0–800 |
| `complete` | Both sensors | true if missing_1 = 0 AND missing_2 = 0 | boolean |
| `mean_ch1_s1` through `max_ch3_s1` | Sensor 1 | Mean/min/max per channel | float or null |
| `magnitude` | Sensor 2 | √mean(ch1² + ch2²) | float or null |
| `heart_rate` | Sensor 1 ch2 | BPM from peak detection | float or null |

### Heart Rate Algorithm

**Peak Detection (Sensor 1, Channel 2):**
1. Sample i is a peak when:
   - 800 samples before and 1 sample after are available
   - Value > both neighbors
   - Value > 50% of max in preceding 800 samples
   - ≥100 samples after previous peak

2. **Rate Calculation:** For window w, find peaks in last 5 seconds (windows w-4 through w)
   - HR = 60 ÷ (mean gap between peaks, in seconds)
   - Rounded to 1 decimal place

3. **No Rate:** If w < 4, any missing sample in 5-second window, or < 2 peaks

## Testing

### Unit Tests
```bash
pytest tests/
```

### Integration Tests
```bash
# Start mock service
biosignal-mock --mode signal --seed 7 --duration 60 --port 7100 &

# Run pipeline for 1 minute
timeout 60 python -m src.cli status
pytest tests/integration/
```

### Failure Scenarios
The test suite covers:
- Network outages and reconnection
- Process termination and recovery
- Power cuts and data loss detection
- Disk full conditions
- System clock changes
- Concurrent mode switches

Run all failure tests:
```bash
pytest tests/ -k failure -v
```

## Metrics Collection

Measure resource usage during 1-hour online mode execution.

### Quick Run
```bash
python tools/run_metrics.py
```

Collects for 1 hour and saves results to `metrics_<timestamp>.csv` and `metrics_<timestamp>.json`.

### Custom Duration
```bash
python tools/run_metrics.py --duration 300 --output ./results
```

Collects for 300 seconds (5 minutes) and saves to `./results/`.

### Output

**CSV Format:**
```
timestamp,cpu_percent,memory_mb,disk_written_mb,network_sent_mb
1697386200.123,12.34,1024.56,0.0125,0.0001
1697386201.124,15.67,1048.92,0.0234,0.0002
...
```

**JSON Format:**
```json
{
  "collection_start": 1697386200.123,
  "collection_end": 1697386203200.123,
  "duration_seconds": 3600.0,
  "aggregates": {
    "cpu_avg": 12.34,
    "cpu_peak": 45.67,
    "memory_peak": 1048.92,
    "total_disk_written": 2048.90,
    "total_network_sent": 512.1234
  },
  "samples_count": 3600,
  "samples": [...]
}
```

See [METRICS.md](METRICS.md) for results template and analysis.

## Project Structure

```
mayacardio/
├── src/
│   ├── __init__.py
│   ├── cli.py                 # CLI interface (status, export, results)
│   ├── collector.py           # Sensor data collection
│   ├── network.py             # Network status detection
│   ├── processor.py           # Window processing & heart rate algorithm
│   ├── window.py              # Data structures (Window, WindowResult)
│   ├── session.py             # Session management
│   ├── storage.py             # Data persistence interface
│   ├── sync.py                # Cloud synchronization
│   ├── cloud.py               # Cloud integration
│   ├── metrics.py             # Resource metrics collector
│   └── persistence/           # Persistence layer
│       ├── __init__.py
│       └── [SQLite, journals, etc.]
├── tools/
│   └── run_metrics.py         # Standalone metrics collection script
├── tests/
│   ├── test_processor.py      # Unit tests for processor
│   ├── test_network.py        # Network detection tests
│   ├── integration/           # End-to-end tests
│   └── failure/               # Failure scenario tests
├── data/                      # Session data (local)
├── docs/
│   └── DESIGN.md              # 2-page design document
├── requirements.txt           # Python dependencies
├── README.md                  # This file
└── METRICS.md                 # Metrics report template
```

## Configuration

### Environment Variables

```bash
# Cloud endpoint (if using cloud mode)
export MAYACARDIO_CLOUD_URL=https://your-cloud-api.example.com

# Session data directory
export MAYACARDIO_DATA_DIR=/var/lib/mayacardio

# Network check interval (seconds)
export MAYACARDIO_NETWORK_CHECK_INTERVAL=10

# Cloud sync interval (seconds)
export MAYACARDIO_SYNC_INTERVAL=5
```

### Sensor Configuration

Mock service endpoint is configurable:
```bash
biosignal-mock --mode signal --seed 7 --duration 3600 --port 7100
```

Adjust port as needed. Pipeline default: `localhost:7100`

## Deployment

### On Raspberry Pi

1. Flash Raspberry Pi OS to microSD
2. Boot and update:
   ```bash
   sudo apt update && sudo apt upgrade -y
   sudo apt install python3-pip python3-venv
   ```

3. Clone and install:
   ```bash
   git clone <repo> /opt/mayacardio
   cd /opt/mayacardio
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

4. Create systemd service (optional):
   ```bash
   sudo cp mayacardio.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable mayacardio
   sudo systemctl start mayacardio
   ```

5. Verify:
   ```bash
   systemctl status mayacardio
   ```

## Troubleshooting

### Pipeline not connecting to sensors
- Check mock service is running: `lsof -i :7100`
- Verify no firewall blocking: `nc -zv localhost 7100`
- Check logs: `cat data/<session>/pipeline.log`

### High memory usage
- Monitor with: `watch -n 1 'python -m src.cli status'`
- Check for memory leaks in persistence layer
- Reduce persistence buffer size in config

### Data loss detected
- Check network outage duration: `pipeline export --from device | grep loss`
- Verify cloud sync status: `pipeline status`
- Review system logs for disk full or I/O errors

### Slow processing
- Check CPU usage: `tools/run_metrics.py --duration 60`
- Profile with: `python -m cProfile -s cumtime -m src.cli status`
- Consider hardware upgrade or load distribution

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Add tests for new functionality
4. Run test suite: `pytest tests/ -v`
5. Submit pull request with description

## License

See LICENSE file for details.

## Citation

If using this project in research or production, please cite:

```
MayaCardio: Resilient Cardiac Data Pipeline
Built with support from [Organization]
```

## Support

- **Issues:** GitHub Issues
- **Documentation:** See docs/ directory
- **Design Document:** See DESIGN.md (2 pages)
- **Metrics Report:** See METRICS.md

---

**Last Updated:** 2026-10-15  
**Version:** 1.0  
**Status:** Production-ready
