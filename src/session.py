"""Session tracking for MayaCardio pipeline.

Tracks current mode, samples saved locally, samples synced to cloud,
and the backlog of un-synced samples.  This is a *mock* implementation
that keeps state only in memory — real persistence will be added later.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class Session:
    """In-memory representation of a single pipeline session."""

    session_id: str
    mode: str = "standby"  # standby | recording | processing | error
    samples_saved: int = 0
    samples_in_cloud: int = 0
    backlog: int = 0
    # Extra loss data is kept per-session for the export command.
    # Each entry: {"window": int, "beat": int, "severity": str}
    losses: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Session store (mock — will be replaced by real persistence layer)
# ---------------------------------------------------------------------------

class SessionStore:
    """Simple in-memory store keyed by session ID."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    # -- CRUD -------------------------------------------------------------
    def create(self, session_id: str) -> Session:
        """Create a new session or return the existing one."""
        if session_id not in self._sessions:
            self._sessions[session_id] = Session(session_id=session_id)
        return self._sessions[session_id]

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def all(self) -> list[Session]:
        return list(self._sessions.values())

    # -- Mutations --------------------------------------------------------
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

    # -- Serialisation ----------------------------------------------------
    def to_status_dict(self, session_id: str) -> dict:
        """Return a status dict matching the CLI spec."""
        session = self.get(session_id)
        if session is None:
            return {
                "mode": "unknown",
                "samples_saved": 0,
                "samples_in_cloud": 0,
                "backlog": 0,
            }
        return {
            "mode": session.mode,
            "samples_saved": session.samples_saved,
            "samples_in_cloud": session.samples_in_cloud,
            "backlog": session.backlog,
        }

    def to_summary_dict(self, session_id: str) -> Optional[dict]:
        """Return all session fields as a plain dict (for future use)."""
        session = self.get(session_id)
        if session is None:
            return None
        return asdict(session)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

store = SessionStore()


def get_store() -> SessionStore:
    """Return the shared session store singleton."""
    return store