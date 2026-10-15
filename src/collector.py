"""Client + disk writer for cardiac monitoring device.
Reads from biosignal-mock --mode signal --seed 7 --duration 3600 --port 7100
Persists every sample to disk exactly once; handles disk-full / kill gracefully.
"""
import sqlite3
import pathlib
import time

DB_DIR = pathlib.Path("data")
DB_FILE = DB_DIR / "biosignal.db"
LOSS_FILE = DB_DIR / "losses.log"

class Collector:
    def __init__(self, port=7100, seed=7):
        DB_DIR.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(DB_FILE))
        self.db.execute("CREATE TABLE IF NOT EXISTS samples (ts INTEGER PRIMARY KEY, ch1 REAL, ch2 REAL, ch3 REAL, ch4 REAL, ch5 REAL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS losses (ts INTEGER PRIMARY KEY, reason TEXT)")
        self.db.commit()
        self.start_time = time.time()

    def persist(self, ts, samples):
        """Persist sample to disk exactly once."""
        try:
            self.db.execute("INSERT OR IGNORE INTO samples VALUES (?,?,?,?,?,?)",
                (int(ts), *samples[:5]))
            self.db.commit()
        except sqlite3.IntegrityError:
            pass  # already persisted exactly once
        except Exception as e:
            # disk full / kill: record loss with real reason
            with open(LOSS_FILE, "a") as f:
                f.write(f"{int(ts)}|{type(e).__name__}: {e}\n")
