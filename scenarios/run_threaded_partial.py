"""Threaded scenario: partial failure — one call's error is isolated."""
from __future__ import annotations
import argparse
from pathlib import Path
from threaded_common import check, events, header, load_fixture, of_type, record, replay

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    header("scenario: threaded partial failure (thread-pool dispatcher)")
    spec = load_fixture("batch-threaded-partial.json")
    trace, results, summary = record(spec, out_dir / "batch-threaded-partial.trace.jsonl")
    print(f"batch: {summary['batch_id']}")
    print(f"status: {summary['status']}")
    print(f"end_tick: {summary['end_tick']}")
    print(f"trace_sha256: {summary['trace_sha256']}")
    ev = events(trace)
    errors = of_type(ev, "call_error")
    check(len(errors) == 1 and errors[0]["tool_call_id"] == "call-t-fail-b",
          "exactly one error, for the failing call", f"count={len(errors)}")
    check(errors[0]["is_error"] is True, "error event carries is_error: true")
    check(summary["status"] == "partial_failure", "batch status is partial_failure", summary["status"])
    check(results["call-t-ok-a"]["status"] == "ok", "sibling ok-a completes")
    check(results["call-t-ok-c"]["status"] == "ok", "sibling ok-c completes")
    check(len(results) == 3, "one result reported per call issued", f"{len(results)}/3")
    match, _ = replay(spec, trace)
    check(match, "replay is byte-identical to the recorded trace")
    # Reproduced failure: abort-on-first-error drops the still-running sibling.
    naive_trace, naive_results, naive_summary = record(
        spec, out_dir / "batch-threaded-partial-naive.trace.jsonl", naive_abort=True)
    print(f"naive (abort on first error) status: {naive_summary['status']}")
    check(naive_summary["status"] == "aborted", "naive run: batch aborts on the first error")
    check(naive_summary["dropped"] == 1, "naive run: the still-running sibling is dropped",
          f"dropped={naive_summary['dropped']}")
    check("call-t-ok-c" not in naive_results, "naive run: the running sibling never reports")
    print("scenario threaded partial failure: ALL PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
