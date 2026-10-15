"""Session tracking for MayaCardio pipeline.

Tracks current mode, samples saved locally, samples synced to cloud,
and the backlog of un-synced samples. For demo purposes, generates
realistic data based on elapsed time.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional
import time


@dataclass
class Session:
    """In-memory representation of a single pipeline session."""

    session_id: str
    mode: str = "online"  # online | offline
    samples_saved: int = 0
    samples_in_cloud: int = 0
    backlog: int = 0
    losses: list = field(default_factory=list)
    start_time: float = field(default_factory=time.time)


class SessionStore:
    """Simple in-memory store keyed by session ID."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self, session_id: str) -> Session:
        """Create a new session or return the existing one."""
        if session_id not in self._sessions:
            self._sessions[session_id] = Session(session_id=session_id, start_time=time.time())
        return self._sessions[session_id]

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def all(self) -> list[Session]:
        return list(self._sessions.values())

    def update_mode(self, session_id: str, mode: str) -> None:
        session = self.create(session_id)
        session.mode = mode

    def record_samples(self, session_id: str, saved: int = 0,
                       cloud: int = 0, backlog: int = 0) -> None:
        """Increment sample counters for a session."""
        session = self.create(session_id)
        session.samples_saved += saved
        session.samples_in_cloud += cloud
        session.backlog += backlog

    def to_status_dict(self, session_id: str) -> dict:
        """Return a status dict matching the CLI spec.

        For demo: generate realistic data based on elapsed time.
        400 samples/sec = ~4000 samples per 10 seconds.
        """
        session = self.get(session_id)
        if session is None:
            session = self.create(session_id)

        # Calculate elapsed time since session start
        elapsed = time.time() - session.start_time

        # Simulate collection: 400 samples/sec
        simulated_samples = int(elapsed * 400)

        # Update session
        session.samples_saved = simulated_samples

        # Simulate cloud sync: assume 95% synced with some backlog
        session.samples_in_cloud = int(simulated_samples * 0.95)
        session.backlog = simulated_samples - session.samples_in_cloud

        return {
            "mode": session.mode,
            "samples_saved": session.samples_saved,
            "samples_in_cloud": session.samples_in_cloud,
            "backlog": session.backlog,
        }

    def to_summary_dict(self, session_id: str) -> Optional[dict]:
        """Return all session fields as a plain dict."""
        session = self.get(session_id)
        if session is None:
            return None
        return asdict(session)


# Module-level singleton
store = SessionStore()


def get_store() -> SessionStore:
    """Return the shared session store singleton."""
    return store
