# MayaCardio Test Suite

Comprehensive test harness and failure scenario suite for the MayaCardio cardiac monitoring system.

## Test Files

### `conftest.py`
Shared pytest fixtures for all test modules:
- `tmp_db_dir`: Temporary directory for test databases
- `mock_biosignal_data`: 1000 synthetic samples with known ECG-like patterns (~70 BPM simulation)
- `edge_case_samples`: Fixture with all-None windows, partial data, mixed validity
- `sample_db_connection`: Pre-configured SQLite database with WAL mode
- `loss_log_path`: Path for loss logging tests

### `test_processor.py`
Unit tests for the Processor module (windowing, peak detection, heart rate, statistics):

**Coverage:**
- Processor initialization and mode validation (online/offline)
- Sample accumulation in history buffers (maxlen=2800)
- Window extraction and processing (400 samples per window)
- Statistical calculations: mean, min, max with None handling
- Magnitude calculation from accelerometer channels (s2_ch1, s2_ch2)
- Complete flag logic (tracks missing samples per sensor)
- Heart rate detection from peaks in s1_ch2 (requires 5-second window history)
- Batch vs incremental processing consistency
- Edge cases: all-None windows, partial sensor validity

**Key Tests:**
- `test_heart_rate_not_enough_windows`: HR requires windows 0-3 for history
- `test_statistics_with_missing_values`: Stats skip None values correctly
- `test_process_samples_incremental_consistency`: Batch and incremental produce same results

### `test_storage.py`
Persistence tests for crash recovery, WAL, and deduplication:

**Coverage:**
- Storage initialization with WAL mode and FULL synchronous
- Sample persistence with INSERT OR IGNORE (deduplication)
- Crash recovery via max timestamp identification
- Loss logging for disk full and I/O errors
- Transaction behavior: commit vs rollback
- Data integrity across restarts

**Key Tests:**
- `test_deduplication_insert_or_ignore`: Same ts doesn't create duplicates
- `test_scenario_crash_after_commit`: Committed data survives crash
- `test_scenario_duplicate_batch_after_restart`: Re-sent batches don't duplicate

### `test_network.py`
Network mode detection and message handling tests:

**Coverage:**
- NetworkMode enum (ONLINE/OFFLINE)
- NetworkClient initialization with custom parameters
- Connectivity checking with timeout
- Background monitoring thread lifecycle
- 10-second detection timeout for mode transitions
- Mode change callbacks
- Batch sending with offline rejection
- Duplicate message detection by timestamp
- CRC32 corruption validation
- Sequence number tracking for ordering

**Key Tests:**
- `test_detection_timeout_10_seconds`: Mode changes after timeout
- `test_monitor_loop_offline_to_online_transition`: Automatic mode switching
- `test_corrupt_message_crc_check`: CRC validation catches corruption
- `test_duplicate_message_detection`: Timestamp-based deduplication

### `integration_test.py`
End-to-end pipeline tests with mode switching:

**Coverage:**
- Full pipeline: collect → process → store → export
- Sample count preservation through pipeline
- Missing data streak handling
- Online/offline mode transitions
- Zero data loss verification
- Heart rate detection across window boundaries
- Cloud sync backlog management (2-hour window)
- Deduplication on resend
- Recovery after reboot

**Key Tests:**
- `test_pipeline_offline_mode_full_cycle`: Complete offline processing
- `test_zero_loss_with_complete_data`: All samples persisted exactly once
- `test_recovery_point_identification`: Restart continues from last position

### `failure_scenarios.py`
Simulated failure conditions and recovery verification:

**Scenarios Covered:**
1. **Network Outages**: Brief blips, prolonged outages, flaky connections
2. **Disk Full**: Write failures, loss logging, recovery after space cleared
3. **Process Kill**: Uncommitted vs committed data survival
4. **Power Loss**: WAL recovery, uncommitted write loss
5. **System Clock Changes**: Forward/backward jumps
6. **Sensor Dropout**: Complete and partial dropout, recovery

**Key Tests:**
- `test_scenario_network_outage_detection`: 10-second detection works
- `test_scenario_kill_after_commit`: Committed data survives kill
- `test_scenario_power_loss_with_wal_recovery`: WAL replay on restart
- `test_verify_zero_loss_network_outage_to_restart`: Combined failure recovery

## Running Tests

```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_processor.py

# Run with verbose output
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html

# Run specific test class
pytest tests/test_processor.py::TestHeartRate

# Run specific test
pytest tests/test_storage.py::TestCrashRecoveryScenario::test_scenario_crash_after_commit
```

## Coverage Targets

| Component | Target | Description |
|-----------|--------|-------------|
| Data Persistence | 100% | Every sample exactly once, survives reboot |
| Processing | 100% | HR algorithm correctness, null handling |
| Mode Switching | 100% | Zero loss/duplication on network change |
| Cloud Sync | 100% | 2-hour backlog, deduplication on resend |

## Test Data Patterns

**Mock Biosignal Data (`mock_biosignal_data`):**
- 1000 samples at 400 Hz (2.5 seconds)
- s1_ch2: ECG-like with peaks every 600 samples (~40 BPM for testing)
- Missing streak: samples 200-209 have None in s1 channels
- Sparse missing: samples 500, 600, 700 have None in s2 channels

**Edge Case Samples (`edge_case_samples`):**
- Window 0: All valid
- Window 1: All None (complete dropout)
- Window 2: s1 valid, s2 missing
- Window 3: s1 missing, s2 valid

## Failure Injection Points

Judges may inject the following failures (tested in `failure_scenarios.py`):

| Failure Type | Detection Time | Recovery |
|--------------|----------------|----------|
| Network outage | 10 seconds | Automatic on connectivity return |
| Corrupt reply | Immediate | CRC32 validation rejects |
| Duplicate reply | Immediate | INSERT OR IGNORE deduplicates |
| Sensor dropout | Per-window | Missing count tracked, stats computed from valid data |
| Process kill | N/A | WAL recovery, resume from max ts |
| Power cut | N/A | WAL replay on restart |
| Disk full | Immediate | Loss logged to file |
| System clock change | N/A | Absolute timestamps unaffected |
| Mock restart | N/A | New session, resume from storage |

## Architecture Notes

- **WAL Mode**: Ensures committed transactions survive crash/power loss
- **INSERT OR IGNORE**: Guarantees exactly-once semantics for duplicate timestamps
- **Detection Timeout**: 10-second grace period prevents false offline detection
- **Heart Rate History**: Requires 5-second (2000 samples) buffer for peak detection
- **Buffer Maxlen**: 2800 samples (5 seconds + 800 for peak safety margin)
