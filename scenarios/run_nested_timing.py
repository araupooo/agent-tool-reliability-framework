"""Scenario 7: nested-call timing guard (asyncio dispatcher).

Mirrors the CorbanuTerminal overlap rule: nested code-mode calls are
excluded from timing events so consumers do not mistake overlapping
events for independent tool-call latency.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from common import check, events, header, load_fixture, of_type, record, replay


def _interval(ev: list[dict], call_id: str) -> tuple[int, int]:
    start = [e for e in ev if e["type"] == "call_start" and e["tool_call_id"] == call_id][0]
    end = [e for e in ev if e["type"] == "call_end" and e["tool_call_id"] == call_id][0]
    return start["tick"], end["tick"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    header("scenario: nested-call timing guard (asyncio dispatcher)")

    spec = load_fixture("batch-nested.json")
    trace, results, summary = record(spec, out_dir / "batch-nested.trace.jsonl")
    print(f"batch: {summary['batch_id']}")
    print(f"status: {summary['status']}")
    print(f"end_tick: {summary['end_tick']}")
    print(f"trace_sha256: {summary['trace_sha256']}")

    ev = events(trace)
    nested_events = [e for e in ev if e.get("parent_tool_call_id")]
    check(len(nested_events) == 0,
          "guarded run: no timing events for the nested sub-call",
          f"nested events={len(nested_events)}")
    outer_start, outer_end = _interval(ev, "call-outer-1")
    check((outer_start, outer_end) == (0, 6),
          "guarded run: outer call spans ticks 0..6 (2 + 3 + 1)",
          f"{outer_start}..{outer_end}")
    check(summary["status"] == "ok", "batch status is ok", summary["status"])
    check(results["call-outer-1"]["result"] == "code_mode: cell done",
          "outer call still returns its own result")

    match, _ = replay(spec, trace)
    check(match, "replay is byte-identical to the recorded trace")

    # Reproduced failure: without the guard the nested call emits its
    # own timing events, overlapping the outer call's interval; a
    # consumer summing per-call durations double-counts the nested work.
    unguarded_trace, _r, unguarded_summary = record(
        spec, out_dir / "batch-nested-unguarded.trace.jsonl", unguarded_nested=True
    )
    uev = events(unguarded_trace)
    inner_start, inner_end = _interval(uev, "call-inner-1")
    check((inner_start, inner_end) == (2, 5),
          "unguarded run: nested call spans ticks 2..5",
          f"{inner_start}..{inner_end}")
    check(outer_start < inner_start and inner_end < outer_end,
          "unguarded run: nested interval lies strictly inside the outer interval (overlap reproduced)",
          f"outer {outer_start}..{outer_end}, inner {inner_start}..{inner_end}")
    check(len([e for e in uev if e.get("parent_tool_call_id")]) == 2,
          "unguarded run: two nested timing events are emitted")
    print("scenario nested timing: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
