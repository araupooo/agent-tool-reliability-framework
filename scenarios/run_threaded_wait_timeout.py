"""Threaded scenario: collector wait() timeout — work continues, late results arrive."""
from __future__ import annotations
import argparse
from pathlib import Path
from threaded_common import check, events, header, load_fixture, of_type, record, replay

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    header("scenario: threaded wait timeout (collector stops at tick 4)")
    spec = load_fixture("batch-threaded-wait-timeout.json")
    trace, results, summary = record(spec, out_dir / "batch-threaded-wait-timeout.trace.jsonl")
    print(f"batch: {summary['batch_id']}")
    print(f"status: {summary['status']}")
    print(f"end_tick: {summary['end_tick']}")
    print(f"trace_sha256: {summary['trace_sha256']}")
    ev = events(trace)
    timeouts = of_type(ev, "call_timeout")
    check(len(timeouts) == 2, "both running calls time out for the collector at tick 4",
          f"count={len(timeouts)}")
    check(all(e["tick"] == 4 for e in timeouts), "collector timeout fires at tick 4")
    late = of_type(ev, "call_late_result")
    check(len(late) == 2 and all(e["status"] == "ok" for e in late),
          "both calls complete late and are reconciled", f"count={len(late)}")
    check([e["tick"] for e in late] == [8, 10], "late results arrive at ticks 8 and 10")
    check(summary["late_results_reconciled"] == 2, "both late results reconciled")
    check(summary["preserved"] == 2 and summary["values_lost"] == 0,
          "both result values preserved, none lost")
    check(results["call-t-w1"]["status"] == "ok" and results["call-t-w1"].get("late") is True,
          "reconciled result is flagged late")
    match, _ = replay(spec, trace)
    check(match, "replay is byte-identical to the recorded trace")
    # Reproduced failure: a collector that discards late results loses both values.
    naive_trace, naive_results, naive_summary = record(
        spec, out_dir / "batch-threaded-wait-timeout-naive.trace.jsonl", naive_late_drop=True)
    print(f"naive (discard late results) preserved: {naive_summary['preserved']}/2")
    check(naive_summary["preserved"] == 0, "naive run: no result value is preserved")
    check(naive_summary["values_lost"] == 2, "naive run: both late result values are lost")
    check(naive_summary["status"] == "partial_failure", "naive run: batch ends partial_failure")
    check(len(of_type(events(naive_trace), "call_late_result")) == 2,
          "naive run: late completions are still observed, but discarded")
    print("scenario threaded wait timeout: ALL PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
