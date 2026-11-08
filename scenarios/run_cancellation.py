"""Scenario 3: batch cancellation (asyncio dispatcher)."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import check, events, header, load_fixture, of_type, record, replay


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    header("scenario: cancellation (asyncio dispatcher)")

    spec = load_fixture("batch-cancel.json")
    trace, results, summary = record(spec, out_dir / "batch-cancel.trace.jsonl")
    print(f"batch: {summary['batch_id']}")
    print(f"status: {summary['status']}")
    print(f"end_tick: {summary['end_tick']}")
    print(f"trace_sha256: {summary['trace_sha256']}")

    ev = events(trace)
    cancelled = of_type(ev, "call_cancelled")
    check(len(cancelled) == 2, "two in-flight calls are cancelled",
          f"count={len(cancelled)}")
    check({e["tool_call_id"] for e in cancelled} == {"call-long-a", "call-long-b"},
          "the cancelled calls are exactly the two long ones")
    check(all(e["tick"] == 4 for e in cancelled), "cancellations fire at tick 4")
    check(results["call-quick-1"]["status"] == "ok", "quick call completed before cancel")
    check(summary["status"] == "cancelled", "batch status is cancelled", summary["status"])
    check(summary["end_tick"] == 4, "batch ends at the cancel tick", f"end_tick={summary['end_tick']}")
    check(len(results) == 3, "one result reported per call issued, including cancelled calls",
          f"{len(results)}/3")

    match, _ = replay(spec, trace)
    check(match, "replay is byte-identical to the recorded trace")

    # Reproduced failure: a cancel path that drops pending calls reports
    # only 1 of 3 results, violating one-result-per-call.
    naive_trace, naive_results, naive_summary = record(
        spec, out_dir / "batch-cancel-naive.trace.jsonl", naive_cancel=True
    )
    print(f"naive (drop on cancel) results_reported: {naive_summary['results_reported']}/3")
    check(naive_summary["results_reported"] == 1,
          "naive run: only the finished call is reported (reproduced failure)",
          f"reported={naive_summary['results_reported']}")
    check("call-long-a" not in naive_results and "call-long-b" not in naive_results,
          "naive run: both cancelled calls are missing from the result set")
    check(len(of_type(events(naive_trace), "call_cancelled")) == 0,
          "naive run: no call_cancelled event is emitted")
    print("scenario cancellation: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
