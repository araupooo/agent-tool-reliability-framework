"""Threaded scenario: basic record/replay with queueing (2 workers, 4 calls)."""
from __future__ import annotations
import argparse
from pathlib import Path
from threaded_common import check, events, header, load_fixture, of_type, record, replay

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    header("scenario: threaded basic (thread-pool dispatcher)")
    spec = load_fixture("batch-threaded-basic.json")
    trace, results, summary = record(spec, out_dir / "batch-threaded-basic.trace.jsonl")
    print(f"batch: {summary['batch_id']}")
    print(f"status: {summary['status']}")
    print(f"end_tick: {summary['end_tick']}")
    print(f"trace_sha256: {summary['trace_sha256']}")
    ev = events(trace)
    check(len(of_type(ev, "call_queued")) == 2, "two calls queue while both workers are busy", f"count={len(of_type(ev, 'call_queued'))}")
    check(summary["completion_order"] == ["call-t-2", "call-t-1", "call-t-4", "call-t-3"],
          "completion order is fast, then first, then queued calls in FIFO order",
          ",".join(summary["completion_order"]))
    check(summary["preserved"] == 4 and summary["dropped"] == 0, "all 4 results preserved, zero drops")
    check(summary["end_tick"] == 4, "batch ends at tick 4", f"end_tick={summary['end_tick']}")
    match, _ = replay(spec, trace)
    check(match, "replay is byte-identical to the recorded trace")
    print("scenario threaded basic: ALL PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
