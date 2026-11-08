"""Threaded scenario: cancellation — queued calls cancel, running calls do not."""
from __future__ import annotations
import argparse
from pathlib import Path
from threaded_common import check, events, header, load_fixture, of_type, record, replay

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    header("scenario: threaded cancellation (1 worker, cancel at tick 4)")
    spec = load_fixture("batch-threaded-cancel.json")
    trace, results, summary = record(spec, out_dir / "batch-threaded-cancel.trace.jsonl")
    print(f"batch: {summary['batch_id']}")
    print(f"status: {summary['status']}")
    print(f"end_tick: {summary['end_tick']}")
    print(f"trace_sha256: {summary['trace_sha256']}")
    ev = events(trace)
    cancelled = of_type(ev, "call_cancelled")
    check(len(cancelled) == 2, "two queued calls are cancelled", f"count={len(cancelled)}")
    check({e["tool_call_id"] for e in cancelled} == {"call-t-long-b", "call-t-quick"},
          "the cancelled calls are exactly the two queued ones")
    rejected = of_type(ev, "cancel_rejected")
    check(len(rejected) == 1 and rejected[0]["tool_call_id"] == "call-t-long-a",
          "cancel of the running call is rejected (Future.cancel() returns False)")
    check(results["call-t-long-a"]["status"] == "ok", "running call completes normally at tick 10")
    check(summary["status"] == "cancelled", "batch status is cancelled", summary["status"])
    check(summary["end_tick"] == 10, "batch ends at tick 10, when the running call finishes")
    check(len(results) == 3, "one result reported per call issued", f"{len(results)}/3")
    match, _ = replay(spec, trace)
    check(match, "replay is byte-identical to the recorded trace")
    # Reproduced failure: a cancel path that drops pending calls reports 1/3.
    naive_trace, naive_results, naive_summary = record(
        spec, out_dir / "batch-threaded-cancel-naive.trace.jsonl", naive_cancel=True)
    print(f"naive (drop on cancel) results_reported: {naive_summary['results_reported']}/3")
    check(naive_summary["results_reported"] == 1, "naive run: only the finished call is reported")
    check(naive_summary["dropped"] == 2, "naive run: both cancelled calls are silently dropped")
    check(len(of_type(events(naive_trace), "call_cancelled")) == 0, "naive run: no call_cancelled event")
    print("scenario threaded cancellation: ALL PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
