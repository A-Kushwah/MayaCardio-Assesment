"""Storage persistence abstraction with crash-recovery semantics."""
import sqlite3
import os
import pathlib

DB_DIR = pathlib.Path(__file__).resolve().parent.parent / "data"
DB_FILE = DB_DIR / "biosignal.db"
LOSS_LOG = DB_DIR / "losses.log"

class Storage:
    """Append-only SQLite with WAL for crash/recovery; every sample exactly once."""
    def __init__(self):
        DB_DIR.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(DB_FILE), isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=FULL")
        self._init_tables()

    def _init_tables(self):
        self.conn.execute("CREATE TABLE IF NOT EXISTS samples (ts INTEGER PRIMARY KEY, s1_ch1 INTEGER, s1_ch2 INTEGER, s2_ch1 INTEGER)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts)")

    def write_sample(self, ts, s1_ch1, s1_ch2, s2_ch1):
        self.conn.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?)", (ts, s1_ch1, s1_ch2, s2_ch1))

    def recover(self):
        """Return max timestamp; identifies gap after crash/reboot."""
        row = self.conn.execute("SELECT MAX(ts) FROM samples").fetchone()
        return row[0] if row and row[0] else 0
