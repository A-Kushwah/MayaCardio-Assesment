# CLI + Session Module (MayaCardio)

Scope: CLI interface (`cli.py`) and session tracking (`session.py`) only.
No real networking, persistence, or processing logic — mocked.

Usage (Python):
  python -m cli status --session default
  python -m cli export --session default --from device
  python -m cli results --session default

Design tradeoffs:
- Mock session store (in-memory) for speed; real persistence deferred.
- `pipeline` command mapped to subparser `pipeline`; `status/export/results` are sub-subcommands for simplicity.
- JSON-line output for streaming compatibility.
