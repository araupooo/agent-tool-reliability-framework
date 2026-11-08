"""Scenario 4: partial failure (asyncio dispatcher)."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import check, events, header, load_fixture, of_type, record, replay


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    header("scenario: partial failure (asyncio dispatcher)")

    spec = load_fixture("batch-partial.json")
    trace, results, summary = record(spec, out_dir / "batch-partial.trace.jsonl")
    print(f"batch: {summary['batch_id']}")
    print(f"status: {summary['status']}")
    print(f"end_tick: {summary['end_tick']}")
    print(f"trace_sha256: {summary['trace_sha256']}")

    ev = events(trace)
    errors = of_type(ev, "call_error")
    check(len(errors) == 1 and errors[0]["tool_call_id"] == "call-fail-b",
          "exactly one error, for the failing call", f"count={len(errors)}")
    check(errors[0]["is_error"] is True, "error event is marked is_error")
    check(errors[0]["error"] == "upstream 503: service unavailable",
          "error event carries the declared failure")
    check(summary["status"] == "partial_failure", "batch status is partial_failure",
          summary["status"])
    check(summary["end_tick"] == 4, "batch ends at tick 4, when the last sibling finishes",
          f"end_tick={summary['end_tick']}")
    check(results["call-ok-a"]["status"] == "ok", "sibling A completes")
    check(results["call-ok-c"]["status"] == "ok", "sibling C completes")
    check(results["call-fail-b"]["status"] == "error", "failing call reported as error")
    check(len(results) == 3, "one result reported per call issued", f"{len(results)}/3")

    match, _ = replay(spec, trace)
    check(match, "replay is byte-identical to the recorded trace")

    # Reproduced failure: asyncio.gather without per-call isolation lets
    # the first error abort the batch; the later sibling never reports.
    naive_trace, naive_results, naive_summary = record(
        spec, out_dir / "batch-partial-naive.trace.jsonl", naive_gather=True
    )
    print(f"naive (gather, no isolation) status: {naive_summary['status']}")
    print(f"naive (gather, no isolation) results_reported: {naive_summary['results_reported']}/3")
    check(naive_summary["status"] == "aborted",
          "naive run: batch aborts on the first error (reproduced failure)",
          naive_summary["status"])
    check(len(of_type(events(naive_trace), "batch_aborted")) == 1,
          "naive run: batch_aborted event is emitted")
    check("call-ok-c" not in naive_results,
          "naive run: the later sibling reports nothing")
    check(len(naive_results) == 1, "naive run: only the already-finished call reports",
          f"reported={len(naive_results)}")
    print("scenario partial failure: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
