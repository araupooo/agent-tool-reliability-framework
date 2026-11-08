"""Threaded scenario: out-of-order completion — results matched by id, not position."""
from __future__ import annotations
import argparse
from pathlib import Path
from threaded_common import check, events, header, load_fixture, record, replay
from components.threaded_dispatcher.dispatcher import collect_by_id, collect_by_position

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    header("scenario: threaded out-of-order completion (thread-pool dispatcher)")
    spec = load_fixture("batch-threaded-reorder.json")
    trace, results, summary = record(spec, out_dir / "batch-threaded-reorder.trace.jsonl")
    print(f"batch: {summary['batch_id']}")
    print(f"status: {summary['status']}")
    print(f"end_tick: {summary['end_tick']}")
    print(f"trace_sha256: {summary['trace_sha256']}")
    check(summary["completion_order"] == ["call-t-fast", "call-t-mid", "call-t-slow"],
          "completion order is fast, mid, slow (issue order was slow, fast, mid)",
          ",".join(summary["completion_order"]))
    by_id = collect_by_id(results)
    check(by_id["call-t-slow"] == "slow-result" and by_id["call-t-fast"] == "fast-result"
          and by_id["call-t-mid"] == "mid-result",
          "by-id collector returns the correct result for every call")
    by_pos = collect_by_position(results, summary["completion_order"],
                                 [c["tool_call_id"] for c in spec["calls"]])
    check(by_pos["call-t-slow"] == "fast-result" and by_pos["call-t-fast"] == "mid-result"
          and by_pos["call-t-mid"] == "slow-result",
          "position-based collector misattributes all three results (reproduced failure)")
    check(summary["preserved"] == 3 and summary["dropped"] == 0, "all 3 results preserved, zero drops")
    match, _ = replay(spec, trace)
    check(match, "replay is byte-identical to the recorded trace")
    print("scenario threaded out-of-order: ALL PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
