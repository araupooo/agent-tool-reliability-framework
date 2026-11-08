"""Replay a threaded batch manifest and compare the trace byte-for-byte.

Usage:
    python3 -m components.threaded_dispatcher.replayer <manifest.json> <recorded.trace.jsonl>
        [--no-timeout] [--naive-cancel] [--naive-abort] [--naive-late-drop]

Exit code 0 when the replayed trace is byte-identical to the recorded
trace, 1 otherwise (with a unified diff on stdout).
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

from .dispatcher import ThreadedDispatcher
from .recorder import load_manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Replay a threaded batch trace.")
    parser.add_argument("manifest")
    parser.add_argument("recorded_trace")
    parser.add_argument("--no-timeout", action="store_true")
    parser.add_argument("--naive-cancel", action="store_true")
    parser.add_argument("--naive-abort", action="store_true")
    parser.add_argument("--naive-late-drop", action="store_true")
    args = parser.parse_args(argv)

    spec = load_manifest(args.manifest)
    dispatcher = ThreadedDispatcher(
        max_workers=int(spec.get("config", {}).get("max_workers", 2))
    )
    trace, _results, summary = dispatcher.run_batch(
        spec,
        enforce_timeouts=not args.no_timeout,
        emit_cancel_results=not args.naive_cancel,
        isolate_errors=not args.naive_abort,
        reconcile_late_results=not args.naive_late_drop,
    )
    recorded = Path(args.recorded_trace).read_text(encoding="utf-8")
    print(f"batch: {summary['batch_id']}")
    print(f"replay_status: {summary['status']}")
    print(f"replay_end_tick: {summary['end_tick']}")
    print(f"replay_events: {summary['event_count']}")
    print(f"replay_trace_sha256: {summary['trace_sha256']}")
    if trace == recorded:
        print("replay: MATCH (byte-identical)")
        return 0
    print("replay: MISMATCH")
    diff = difflib.unified_diff(
        recorded.splitlines(keepends=True),
        trace.splitlines(keepends=True),
        fromfile="recorded",
        tofile="replayed",
    )
    sys.stdout.writelines(diff)
    return 1


if __name__ == "__main__":
    sys.exit(main())
