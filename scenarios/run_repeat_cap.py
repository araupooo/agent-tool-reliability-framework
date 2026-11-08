"""Scenario 6: identical-call repeat cap (asyncio dispatcher)."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import check, events, header, load_fixture, of_type, record, replay


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    header("scenario: identical-call repeat cap (asyncio dispatcher)")

    spec = load_fixture("batch-repeat.json")
    trace, results, summary = record(spec, out_dir / "batch-repeat.trace.jsonl")
    print(f"batch: {summary['batch_id']}")
    print(f"status: {summary['status']}")
    print(f"end_tick: {summary['end_tick']}")
    print(f"trace_sha256: {summary['trace_sha256']}")

    ev = events(trace)
    refused = of_type(ev, "call_refused")
    check(len(refused) == 1 and refused[0]["tool_call_id"] == "call-dup-4",
          "exactly one refusal, for the fourth identical call", f"count={len(refused)}")
    check("after 3 identical calls" in refused[0]["reason"],
          "refusal reason names the cap", refused[0]["reason"])
    check(len(of_type(ev, "call_end")) == 3, "exactly three calls run to completion")
    check(len([e for e in of_type(ev, "call_start")
               if e["tool_call_id"] == "call-dup-4"]) == 0,
          "the refused call never starts")
    check(summary["status"] == "partial_failure", "batch status is partial_failure",
          summary["status"])
    check(results["call-dup-4"]["status"] == "refused", "refused call is still reported")
    check(len(results) == 4, "one result reported per call issued", f"{len(results)}/4")

    match, _ = replay(spec, trace)
    check(match, "replay is byte-identical to the recorded trace")
    print("scenario repeat cap: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
