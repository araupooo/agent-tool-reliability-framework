"""Threaded scenario: queue saturation — cancel reaches queued calls only."""
from __future__ import annotations
import argparse
from pathlib import Path
from threaded_common import check, events, header, load_fixture, of_type, record, replay

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    header("scenario: threaded queue saturation (1 worker, 4 calls, cancel at tick 3)")
    spec = load_fixture("batch-threaded-queue.json")
    trace, results, summary = record(spec, out_dir / "batch-threaded-queue.trace.jsonl")
    print(f"batch: {summary['batch_id']}")
    print(f"status: {summary['status']}")
    print(f"end_tick: {summary['end_tick']}")
    print(f"trace_sha256: {summary['trace_sha256']}")
    ev = events(trace)
    check(len(of_type(ev, "call_queued")) == 3, "three calls queue behind the single worker")
    cancelled = of_type(ev, "call_cancelled")
    check({e["tool_call_id"] for e in cancelled} == {"call-t-q3", "call-t-q4"},
          "exactly the two still-queued calls are cancelled at tick 3")
    rejected = of_type(ev, "cancel_rejected")
    check(len(rejected) == 1 and rejected[0]["tool_call_id"] == "call-t-q2",
          "the running call's cancel is rejected; it completes at tick 4")
    check(results["call-t-q1"]["status"] == "ok" and results["call-t-q2"]["status"] == "ok",
          "both started calls complete")
    check(summary["preserved"] == 2 and summary["dropped"] == 0,
          "started results preserved, cancelled calls reported, zero drops")
    check(summary["end_tick"] == 4, "batch ends at tick 4", f"end_tick={summary['end_tick']}")
    match, _ = replay(spec, trace)
    check(match, "replay is byte-identical to the recorded trace")
    print("scenario threaded queue saturation: ALL PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
