"""Scenario 5: out-of-order completion and result attribution (asyncio dispatcher)."""

from __future__ import annotations

import argparse
from pathlib import Path

from common import check, events, header, load_fixture, of_type, record, replay
from harness.dispatcher import collect_by_id, collect_by_position


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    header("scenario: out-of-order completion (asyncio dispatcher)")

    spec = load_fixture("batch-reorder.json")
    trace, results, summary = record(spec, out_dir / "batch-reorder.trace.jsonl")
    print(f"batch: {summary['batch_id']}")
    print(f"status: {summary['status']}")
    print(f"end_tick: {summary['end_tick']}")
    print(f"trace_sha256: {summary['trace_sha256']}")

    ev = events(trace)
    starts = [e["tool_call_id"] for e in of_type(ev, "call_start")]
    ends = [e["tool_call_id"] for e in of_type(ev, "call_end")]
    check(starts == ["call-slow-1", "call-fast-1", "call-mid-1"],
          "calls start in issue order", ",".join(starts))
    check(ends == ["call-fast-1", "call-mid-1", "call-slow-1"],
          "calls complete in duration order, not issue order", ",".join(ends))
    check(summary["completion_order"] == ends, "completion order recorded in summary")
    check(summary["status"] == "ok", "batch status is ok", summary["status"])

    # Correct collector: results matched to calls by tool_call_id.
    by_id = collect_by_id(results)
    check(by_id["call-slow-1"] == "slow-result", "by-id: slow call gets slow-result")
    check(by_id["call-fast-1"] == "fast-result", "by-id: fast call gets fast-result")
    check(by_id["call-mid-1"] == "mid-result", "by-id: mid call gets mid-result")

    # Reproduced failure: the position-based collector misattributes
    # every result (first finished result attached to first issued call).
    by_position = collect_by_position(results, summary["completion_order"],
                                      [c["tool_call_id"] for c in spec["calls"]])
    print(f"by-position mapping: {by_position}")
    check(by_position["call-slow-1"] == "fast-result",
          "by-position (buggy): slow call wrongly gets fast-result (reproduced failure)",
          by_position["call-slow-1"])
    check(by_position["call-fast-1"] == "mid-result",
          "by-position (buggy): fast call wrongly gets mid-result",
          by_position["call-fast-1"])
    check(by_position["call-mid-1"] == "slow-result",
          "by-position (buggy): mid call wrongly gets slow-result",
          by_position["call-mid-1"])
    check(by_position != by_id, "by-position mapping differs from the correct by-id mapping")

    match, _ = replay(spec, trace)
    check(match, "replay is byte-identical to the recorded trace")
    print("scenario out-of-order: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
