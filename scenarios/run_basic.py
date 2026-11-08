"""Scenario 1: baseline record/replay of a parallel batch (asyncio dispatcher)."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from common import check, events, header, load_fixture, of_type, record, replay
from harness.dispatcher import canonical, sha256_hex


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    header("scenario: basic record/replay (asyncio dispatcher)")

    spec = load_fixture("batch-basic.json")
    trace, results, summary = record(spec, out_dir / "batch-basic.trace.jsonl")
    print(f"batch: {summary['batch_id']}")
    print(f"status: {summary['status']}")
    print(f"end_tick: {summary['end_tick']}")
    print(f"events: {summary['event_count']}")
    print(f"trace_sha256: {summary['trace_sha256']}")

    ev = events(trace)
    check(summary["status"] == "ok", "batch status is ok", summary["status"])
    check(summary["end_tick"] == 4, "batch ends at tick 4", f"end_tick={summary['end_tick']}")
    check(summary["completion_order"] == ["call-hash-1", "call-read-1", "call-fetch-1"],
          "completion order is hash, read, fetch (duration order, not issue order)",
          ",".join(summary["completion_order"]))
    check(len(results) == 3, "one result reported per call issued", f"{len(results)}/3")

    # Independent digest check: recompute the args digest for call-read-1.
    start = [e for e in ev if e["type"] == "call_start" and e["tool_call_id"] == "call-read-1"][0]
    expected = hashlib.sha256(canonical({"path": "src/main.rs"}).encode()).hexdigest()
    check(start["args_digest"] == expected, "args digest matches independent recomputation",
          start["args_digest"][:16])
    # Every call_end result digest matches the fixture result string.
    ends = {e["tool_call_id"]: e for e in of_type(ev, "call_end")}
    for call in spec["calls"]:
        check(ends[call["tool_call_id"]]["result_digest"] == sha256_hex(call["result"]),
              f"result digest matches fixture for {call['tool_call_id']}")

    match, _ = replay(spec, trace)
    check(match, "replay is byte-identical to the recorded trace")
    print("scenario basic: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
