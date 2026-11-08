"""Threaded scenario: per-call timeout (deadline frees the worker)."""
from __future__ import annotations
import argparse
from pathlib import Path
from threaded_common import check, events, header, load_fixture, of_type, record, replay

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    header("scenario: threaded timeout (thread-pool dispatcher)")
    spec = load_fixture("batch-threaded-timeout.json")
    trace, results, summary = record(spec, out_dir / "batch-threaded-timeout.trace.jsonl")
    print(f"batch: {summary['batch_id']}")
    print(f"status: {summary['status']}")
    print(f"end_tick: {summary['end_tick']}")
    print(f"trace_sha256: {summary['trace_sha256']}")
    ev = events(trace)
    timeouts = of_type(ev, "call_timeout")
    check(len(timeouts) == 1 and timeouts[0]["tool_call_id"] == "call-t-slow",
          "exactly one timeout, for the slow call", f"count={len(timeouts)}")
    check(timeouts[0]["tick"] == 5, "timeout fires at the 5-tick deadline", f"tick={timeouts[0]['tick']}")
    check(summary["status"] == "partial_failure", "batch status is partial_failure", summary["status"])
    check(summary["end_tick"] == 9, "batch ends at tick 9, on the siblings' schedule", f"end_tick={summary['end_tick']}")
    check(results["call-t-read"]["status"] == "ok", "sibling read_file completes")
    check(results["call-t-hash"]["status"] == "ok", "sibling compute_hash completes")
    check(results["call-t-slow"]["status"] == "timeout", "slow call reported as timeout")
    check(len(results) == 3, "one result reported per call issued", f"{len(results)}/3")
    match, _ = replay(spec, trace)
    check(match, "replay is byte-identical to the recorded trace")
    naive_trace, naive_results, naive_summary = record(
        spec, out_dir / "batch-threaded-timeout-naive.trace.jsonl", no_timeout=True)
    print(f"naive (no deadline) end_tick: {naive_summary['end_tick']}")
    check(naive_summary["end_tick"] == 20, "naive run: batch held to tick 20 by the slow call")
    check(naive_results["call-t-slow"]["status"] == "ok", "naive run: slow call runs to completion")
    check(len(of_type(events(naive_trace), "call_timeout")) == 0, "naive run: no timeout event")
    print("scenario threaded timeout: ALL PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
