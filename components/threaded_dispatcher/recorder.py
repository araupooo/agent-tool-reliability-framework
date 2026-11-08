"""Record a threaded batch manifest to a canonical trace file.

Usage:
    python3 -m components.threaded_dispatcher.recorder <manifest.json> <output.trace.jsonl>
        [--no-timeout] [--naive-cancel] [--naive-abort] [--naive-late-drop]

Run from the pack root. The trace is canonical JSONL (sorted keys, one
event per line). The printed summary contains only deterministic values
(ticks, counts, digests) so the log itself is reproducible.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .dispatcher import ThreadedDispatcher


def load_manifest(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def run(manifest_path: str, out_path: str, **flags) -> dict:
    spec = load_manifest(manifest_path)
    dispatcher = ThreadedDispatcher(
        max_workers=int(spec.get("config", {}).get("max_workers", 2))
    )
    trace, results, summary = dispatcher.run_batch(
        spec,
        enforce_timeouts=not flags.get("no_timeout", False),
        emit_cancel_results=not flags.get("naive_cancel", False),
        isolate_errors=not flags.get("naive_abort", False),
        reconcile_late_results=not flags.get("naive_late_drop", False),
    )
    Path(out_path).write_text(trace, encoding="utf-8")
    print(f"batch: {summary['batch_id']}")
    print(f"status: {summary['status']}")
    print(f"end_tick: {summary['end_tick']}")
    print(f"events: {summary['event_count']}")
    print(f"completed: {summary['completed']}")
    print(f"failed: {summary['failed']}")
    print(f"cancelled: {summary['cancelled']}")
    print(f"preserved: {summary['preserved']}/{summary['calls_issued']}")
    print(f"dropped: {summary['dropped']}")
    print(f"values_lost: {summary['values_lost']}")
    print(f"results_reported: {summary['results_reported']}/{summary['calls_issued']}")
    print(f"completion_order: {','.join(summary['completion_order'])}")
    print(f"trace_sha256: {summary['trace_sha256']}")
    print(f"trace_file: {out_path}")
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Record a threaded batch trace.")
    parser.add_argument("manifest")
    parser.add_argument("output")
    parser.add_argument("--no-timeout", action="store_true")
    parser.add_argument("--naive-cancel", action="store_true")
    parser.add_argument("--naive-abort", action="store_true")
    parser.add_argument("--naive-late-drop", action="store_true")
    args = parser.parse_args(argv)
    run(
        args.manifest,
        args.output,
        no_timeout=args.no_timeout,
        naive_cancel=args.naive_cancel,
        naive_abort=args.naive_abort,
        naive_late_drop=args.naive_late_drop,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
