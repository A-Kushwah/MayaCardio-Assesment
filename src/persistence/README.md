# MayaCardio Persistence Module (persistence)
# Source: collector.py + storage.py
# Design: append-only SQLite WAL + flat-file loss log for crash/reboot safety.
# Tradeoffs: SQLite WAL (durability) vs flat-file simplicity; 2GB RAM => no in-memory buffers > 10s.
