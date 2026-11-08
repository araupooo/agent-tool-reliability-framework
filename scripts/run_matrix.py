"""Run the fault-injection matrix (matrix/matrix.json, 36 cells).

Usage: python3 scripts/run_matrix.py <out-dir>
For each cell (component x fault x injection point) runs the correct
mode and the matching naive mode, writes per-cell logs and traces
under <out-dir>/matrix/, and writes <out-dir>/matrix/matrix-results.json.
Asserts the correct-mode expectations from matrix.json (one terminal
record per issued call, dropped == 0). Exit code 1 on any mismatch.

Cell specs (asyncio / threaded share the same shapes; the threaded
dispatcher uses max_workers 2 / 1 / 2 / 3 for timeout / cancel /
partial / reorder):
- timeout: slow call (20 ticks) with deadline T; T = 2 (at-issue),
  5 (in-flight), 20 (at-completion, deadline == duration).
- cancellation: long calls (10, 12 ticks) + quick call (3 ticks);
  cancel at tick 0 (at-issue), 4 (in-flight), 11 (at-completion).
- partial-failure: ok (2 ticks), failing call (error), ok (4 ticks);
  failing call duration 0 (at-issue), 3 (in-flight), 4 (at-completion).
- out-of-order: slow (5), fast (1), mid (3) ticks; at-issue issues
  them in completion order [fast, mid, slow]; the other points use
  the standard issue order. Attribution is checked with the by-id
  collector (correct) and the position-based collector (naive).

Shell cells are run through components/shell_runner/run_matrix_cell.sh.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK_ROOT))

from components.threaded_dispatcher.dispatcher import (  # noqa: E402
    ThreadedDispatcher,
    collect_by_id,
    collect_by_position,
    sha256_hex,
)
from harness.dispatcher import Dispatcher  # noqa: E402
from harness.dispatcher import collect_by_id as a_collect_by_id  # noqa: E402
from harness.dispatcher import collect_by_position as a_collect_by_position  # noqa: E402


def call(cid, tool, dur, result="", behavior="ok", error=None, timeout=None):
    c = {
        "tool_call_id": cid,
        "tool": tool,
        "args": {"job": cid},
        "behavior": behavior,
        "duration_ticks": dur,
        "result": result,
    }
    if error:
        c["error"] = error
    if timeout is not None:
        c["timeout_ticks"] = timeout
    return c


def build_spec(component: str, fault: str, point: str) -> dict:
    workers = {"timeout": 2, "cancellation": 1, "partial-failure": 2, "out-of-order": 3}
    if fault == "timeout":
        timeout = {"at-issue": 2, "in-flight": 5, "at-completion": 20}[point]
        calls = [
            call("call-m-slow", "slow_query", 20, "slow_query: 1000 rows", timeout=timeout),
            call("call-m-read", "read_file", 3, "read_file: 42 lines"),
            call("call-m-hash", "compute_hash", 6, "compute_hash: 2cf24dba"),
        ]
        return {"batch_id": f"matrix-{component}-{fault}-{point}", "calls": calls,
                "cancel_at_tick": None, "config": {"max_workers": workers[fault]}}
    if fault == "cancellation":
        cancel_at = {"at-issue": 0, "in-flight": 4, "at-completion": 11}[point]
        calls = [
            call("call-m-long-a", "long_task", 10, "long_task a: done"),
            call("call-m-long-b", "long_task", 12, "long_task b: done"),
            call("call-m-quick", "read_file", 3, "read_file: 42 lines"),
        ]
        return {"batch_id": f"matrix-{component}-{fault}-{point}", "calls": calls,
                "cancel_at_tick": cancel_at, "config": {"max_workers": workers[fault]}}
    if fault == "partial-failure":
        duration = {"at-issue": 0, "in-flight": 3, "at-completion": 4}[point]
        calls = [
            call("call-m-ok-a", "read_file", 2, "read_file a: 10 lines"),
            call("call-m-fail-b", "fetch_url", duration, "", behavior="error",
                 error="upstream 503: service unavailable"),
            call("call-m-ok-c", "compute_hash", 4, "compute_hash: 486ea462"),
        ]
        return {"batch_id": f"matrix-{component}-{fault}-{point}", "calls": calls,
                "cancel_at_tick": None, "config": {"max_workers": workers[fault]}}
    # out-of-order
    calls = [
        call("call-m-slow", "slow_query", 5, "slow-result"),
        call("call-m-fast", "compute_hash", 1, "fast-result"),
        call("call-m-mid", "read_file", 3, "mid-result"),
    ]
    if point == "at-issue":
        calls = [calls[1], calls[2], calls[0]]  # issued fast, mid, slow
    return {"batch_id": f"matrix-{component}-{fault}-{point}", "calls": calls,
            "cancel_at_tick": None, "config": {"max_workers": workers[fault]}}


def metrics(spec: dict, results: dict, summary: dict) -> dict:
    by_id = {c["tool_call_id"]: c for c in spec["calls"]}
    preserved = sum(
        1
        for cid, r in results.items()
        if r["status"] == "ok"
        and r.get("result_digest") == sha256_hex(by_id[cid].get("result", ""))
    )
    errored = sum(
        1 for r in results.values() if r["status"] in ("error", "timeout", "cancelled")
    )
    return {
        "issued": len(spec["calls"]),
        "preserved": preserved,
        "errored": errored,
        "dropped": len(spec["calls"]) - len(results),
        "values_lost": summary.get("values_lost", 0),
        "status": summary["status"],
        "end_tick": summary["end_tick"],
        "trace_sha256": summary["trace_sha256"],
    }


def run_python_cell(component, fault, point, out_dir, naive):
    spec = build_spec(component, fault, point)
    flags = {}
    if naive:
        if fault == "timeout":
            flags["no_timeout"] = True
        elif fault == "cancellation":
            flags["naive_cancel"] = True
        elif fault == "partial-failure":
            flags["naive_abort"] = True
    if component == "asyncio":
        dispatcher = Dispatcher()
        trace, results, summary = asyncio.run(
            dispatcher.run_batch(
                spec,
                enforce_timeouts=not flags.get("no_timeout", False),
                emit_cancel_results=not flags.get("naive_cancel", False),
                isolate_errors=not flags.get("naive_abort", False),
            )
        )
        by_id_fn, by_pos_fn = a_collect_by_id, a_collect_by_position
    else:
        dispatcher = ThreadedDispatcher(
            max_workers=int(spec["config"]["max_workers"])
        )
        trace, results, summary = dispatcher.run_batch(
            spec,
            enforce_timeouts=not flags.get("no_timeout", False),
            emit_cancel_results=not flags.get("naive_cancel", False),
            isolate_errors=not flags.get("naive_abort", False),
        )
        by_id_fn, by_pos_fn = collect_by_id, collect_by_position
    m = metrics(spec, results, summary)
    if fault == "out-of-order":
        call_ids = [c["tool_call_id"] for c in spec["calls"]]
        by_id = by_id_fn(results)
        by_pos = by_pos_fn(results, summary["completion_order"], call_ids)
        m["position_misattributed"] = sum(
            1 for cid in call_ids if by_pos.get(cid) != by_id.get(cid)
        )
    else:
        m["position_misattributed"] = 0
    return spec, trace, results, summary, m


def run_shell_cell(fault, point, out_dir, naive):
    cell_dir = out_dir / (f"shell-{fault}-{point}" + ("-naive" if naive else ""))
    cmd = [
        "bash", "components/shell_runner/run_matrix_cell.sh",
        fault, point, str(cell_dir),
    ]
    if naive:
        cmd.append("--naive")
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=PACK_ROOT)
    log_text = proc.stdout + proc.stderr
    m = {"issued": 3, "preserved": 0, "errored": 0, "dropped": 0,
         "values_lost": 0, "status": "", "end_tick": None, "trace_sha256": ""}
    for key in ("preserved", "errored", "dropped", "values_lost", "position_misattributed"):
        match = re.search(rf"^{key}: (\d+)$", log_text, re.MULTILINE)
        if match:
            m[key] = int(match.group(1))
    match = re.search(r"^status: (\S+)$", log_text, re.MULTILINE)
    if match:
        m["status"] = match.group(1)
    match = re.search(r"^trace_sha256: ([0-9a-f]{64})$", log_text, re.MULTILINE)
    if match:
        m["trace_sha256"] = match.group(1)
    return log_text, m, proc.returncode


def main(argv=None) -> int:
    out_dir = Path(argv[1] if argv and len(argv) > 1 else "outputs/run1")
    matrix_dir = out_dir / "matrix"
    matrix_dir.mkdir(parents=True, exist_ok=True)
    definition = json.loads((PACK_ROOT / "matrix" / "matrix.json").read_text())
    print(f"== matrix: {len(definition['cells'])} cells ==")
    fail = 0
    results_doc = {"cells": []}
    for cell in definition["cells"]:
        comp, fault, point = cell["component"], cell["fault"], cell["point"]
        base = f"{comp}-{fault}-{point}"
        # --- correct mode ---
        if comp == "shell":
            log_text, m, rc = run_shell_cell(fault, point, matrix_dir, naive=False)
            trace_name = None
            (matrix_dir / f"{base}.log").write_text(log_text, encoding="utf-8")
            if rc != 0:
                fail += 1
                print(f"matrix {base}: FAIL (cell script exit {rc})")
                continue
        else:
            spec, trace, _r, summary, m = run_python_cell(comp, fault, point, matrix_dir, naive=False)
            lines = [
                f"== matrix cell: {comp}/{fault}/{point} ==",
                f"batch: {summary['batch_id']}",
                f"status: {summary['status']}",
                f"end_tick: {summary['end_tick']}",
                f"events: {summary['event_count']}",
                f"issued: {m['issued']}",
                f"preserved: {m['preserved']}",
                f"errored: {m['errored']}",
                f"dropped: {m['dropped']}",
                f"values_lost: {m['values_lost']}",
                f"position_misattributed: {m['position_misattributed']}",
                f"completion_order: {','.join(summary['completion_order'])}",
                f"trace_sha256: {summary['trace_sha256']}",
            ]
            (matrix_dir / f"{base}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
            (matrix_dir / f"{base}.trace.jsonl").write_text(trace, encoding="utf-8")
        expected = cell["expected"]
        ok = (m["preserved"] == expected["preserved"] and m["errored"] == expected["errored"]
              and m["dropped"] == expected["dropped"])
        # --- naive mode (recorded, not asserted) ---
        if comp == "shell":
            naive_log, nm, _rc = run_shell_cell(fault, point, matrix_dir, naive=True)
            (matrix_dir / f"{base}-naive.log").write_text(naive_log, encoding="utf-8")
        else:
            _spec, n_trace, _r, n_summary, nm = run_python_cell(comp, fault, point, matrix_dir, naive=True)
            nlines = [
                f"== matrix cell (naive): {comp}/{fault}/{point} ==",
                f"batch: {n_summary['batch_id']}",
                f"status: {n_summary['status']}",
                f"end_tick: {n_summary['end_tick']}",
                f"events: {n_summary['event_count']}",
                f"issued: {nm['issued']}",
                f"preserved: {nm['preserved']}",
                f"errored: {nm['errored']}",
                f"dropped: {nm['dropped']}",
                f"values_lost: {nm['values_lost']}",
                f"position_misattributed: {nm['position_misattributed']}",
                f"completion_order: {','.join(n_summary['completion_order'])}",
                f"trace_sha256: {n_summary['trace_sha256']}",
            ]
            (matrix_dir / f"{base}-naive.log").write_text("\n".join(nlines) + "\n", encoding="utf-8")
            (matrix_dir / f"{base}-naive.trace.jsonl").write_text(n_trace, encoding="utf-8")
        status = "PASS" if ok else "FAIL"
        if not ok:
            fail += 1
        print(
            f"matrix {base}: {status} preserved={m['preserved']} errored={m['errored']} "
            f"dropped={m['dropped']} (expected {expected['preserved']}/"
            f"{expected['errored']}/{expected['dropped']}) naive: "
            f"preserved={nm['preserved']} errored={nm['errored']} dropped={nm['dropped']} "
            f"values_lost={nm['values_lost']} misattributed={nm['position_misattributed']}"
        )
        results_doc["cells"].append({
            "component": comp, "fault": fault, "point": point,
            "expected": expected, "correct": m, "naive": nm,
            "correct_pass": ok,
            "log": f"matrix/{base}.log",
            "naive_log": f"matrix/{base}-naive.log",
        })
    correct_pass = sum(1 for c in results_doc["cells"] if c["correct_pass"])
    results_doc["totals"] = {
        "cells": len(definition["cells"]),
        "correct_pass": correct_pass,
        "correct_dropped_total": sum(c["correct"]["dropped"] for c in results_doc["cells"]),
        "naive_dropped_total": sum(c["naive"]["dropped"] for c in results_doc["cells"]),
        "naive_values_lost_total": sum(c["naive"]["values_lost"] for c in results_doc["cells"]),
    }
    (matrix_dir / "matrix-results.json").write_text(
        json.dumps(results_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"matrix totals: {correct_pass}/{len(definition['cells'])} correct cells pass; "
          f"correct-mode dropped total: {results_doc['totals']['correct_dropped_total']}")
    print("matrix: ALL PASS" if fail == 0 else "matrix: FAILURES PRESENT")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
