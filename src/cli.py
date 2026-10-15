"""MayaCardio CLI — command interface for the cardiac pipeline.

Subcommands:
  pipeline status
  pipeline export --session ID --from device|cloud
  pipeline results --session ID
"""

import argparse
import json
import sys
from pathlib import Path

# Import our local session layer (mock persistence).
from .session import store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def json_line(obj: dict) -> None:
    print(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def _mock_results(session_id: str):
    """Mock "results" payload — one entry per "window" + mode."""
    session = store.get(session_id)
    mode = session.mode if session else "standby"
    # Mock 3 windows with synthetic section-3 outputs.
    windows = [
        {"window": 1, "mode": mode, "mean_hr": 72.0, "std_hr": 3.5, "beat_detected": 142},
        {"window": 2, "mode": mode, "mean_hr": 71.8, "std_hr": 3.2, "beat_detected": 139},
        {"window": 3, "mode": mode, "mean_hr": 73.1, "std_hr": 3.9, "beat_detected": 145},
    ]
    return windows


def _mock_export(session_id: str, source: str):
    """Mock export payload — samples (from source) then losses."""
    session = store.get(session_id)
    saved = session.samples_saved if session else 0
    cloud_count = session.samples_in_cloud if session else 0

    if source == "device":
        samples = [
            {"ts": 1697386200 + i * 10, "value": 65 + (i % 8), "source": "device"}
            for i in range(min(saved, 5))
        ]
    elif source == "cloud":
        samples = [
            {"ts": 1697386200 + i * 10, "value": 64 + (i % 7), "source": "cloud"}
            for i in range(min(cloud_count, 5))
        ]
    else:
        samples = []

    losses = session.losses if session else []
    return samples, losses


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

def cmd_status(args):
    sid = args.session or "default"
    status = store.to_status_dict(sid)
    json_line(status)


def cmd_export(args):
    sid = args.session
    if not sid:
        print(json.dumps({"error": "--session is required"}))
        sys.exit(2)
    samples, losses = _mock_export(sid, args.source or "device")
    # Emit samples first, then losses — both as JSON lines where required.
    # Requirement: "JSON lines (samples then losses)"
    for s in samples:
        json_line(s)
    # Losses line (array) — emitted as a single line.
    for loss in losses:
        json_line(loss)
    # If no losses present, emit empty array line so consumer knows end.
    if not losses:
        json_line({"losses": []})


def cmd_results(args):
    sid = args.session
    if not sid:
        print(json.dumps({"error": "--session is required"}))
        sys.exit(2)
    for window in _mock_results(sid):
        json_line(window)


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pipeline",
        description="MayaCardio cardiac pipeline CLI (ARM64 Pi, mock layer).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # status
    status_parser = subparsers.add_parser(
        "status", help="Show session status."
    )
    status_parser.add_argument(
        "--session",
        default="default",
        help="Session identifier (default: default).",
    )
    status_parser.set_defaults(func=cmd_status)

    # export
    export_parser = subparsers.add_parser(
        "export",
        help="Export samples and losses for a session.",
    )
    export_parser.add_argument(
        "--session",
        required=True,
        help="Session identifier.",
    )
    export_parser.add_argument(
        "--from",
        dest="source",
        choices=["device", "cloud"],
        default="device",
        help="Data source to export from.",
    )
    export_parser.set_defaults(func=cmd_export)

    # results
    results_parser = subparsers.add_parser(
        "results",
        help="Show per-window section-3 results for a session.",
    )
    results_parser.add_argument(
        "--session",
        required=True,
        help="Session identifier.",
    )
    results_parser.set_defaults(func=cmd_results)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())