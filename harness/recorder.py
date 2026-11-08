"""Record a batch manifest to a canonical trace file.

Usage:
    python3 -m harness.recorder <manifest.json> <output.trace.jsonl>
        [--no-timeout] [--naive-cancel] [--naive-gather] [--unguarded-nested]

The trace is canonical JSONL (sorted keys, one event per line). The
printed summary contains only deterministic values (ticks, counts,
digests) so the log itself is reproducible.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .dispatcher import Dispatcher, sha256_hex


def load_manifest(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def run(manifest_path: str, out_path: str, **flags) -> dict:
    spec = load_manifest(manifest_path)
    dispatcher = Dispatcher(
        suppress_nested_timing=not flags.get("unguarded_nested", False)
    )
    trace, results, summary = asyncio.run(
        dispatcher.run_batch(
            spec,
            enforce_timeouts=not flags.get("no_timeout", False),
            emit_cancel_results=not flags.get("naive_cancel", False),
            isolate_errors=not flags.get("naive_gather", False),
        )
    )
    Path(out_path).write_text(trace, encoding="utf-8")
    print(f"batch: {summary['batch_id']}")
    print(f"status: {summary['status']}")
    print(f"end_tick: {summary['end_tick']}")
    print(f"events: {summary['event_count']}")
    print(f"completed: {summary['completed']}")
    print(f"failed: {summary['failed']}")
    print(f"cancelled: {summary['cancelled']}")
    print(f"refused: {summary['refused']}")
    print(f"results_reported: {summary['results_reported']}/{summary['calls_issued']}")
    print(f"completion_order: {','.join(summary['completion_order'])}")
    print(f"trace_sha256: {summary['trace_sha256']}")
    print(f"trace_file: {out_path}")
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Record a batch trace.")
    parser.add_argument("manifest")
    parser.add_argument("output")
    parser.add_argument("--no-timeout", action="store_true")
    parser.add_argument("--naive-cancel", action="store_true")
    parser.add_argument("--naive-gather", action="store_true")
    parser.add_argument("--unguarded-nested", action="store_true")
    args = parser.parse_args(argv)
    run(
        args.manifest,
        args.output,
        no_timeout=args.no_timeout,
        naive_cancel=args.naive_cancel,
        naive_gather=args.naive_gather,
        unguarded_nested=args.unguarded_nested,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
