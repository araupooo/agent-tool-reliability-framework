"""Run the network-scale replay corpus: record every trace, replay it,
and assert the recovery invariants for each batch.

Usage: python3 scripts/run_corpus.py <out-dir>
Writes one trace per corpus batch under <out-dir>/corpus/ and a
summary at <out-dir>/corpus/corpus-summary.json. Prints one line per
batch plus totals. Exit code 1 on any replay mismatch or any silent
drop in correct mode.

Invariants asserted per batch (correct mode):
- every issued call has exactly one terminal result record
  (preserved + errored == issued, dropped == 0);
- every non-ok call carries a visible error/timeout/cancelled record;
- the replayed trace is byte-identical to the recorded trace.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK_ROOT))

from components.threaded_dispatcher.dispatcher import (  # noqa: E402
    ThreadedDispatcher,
    sha256_hex,
)
from harness.dispatcher import Dispatcher  # noqa: E402


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def preserved_asyncio(spec: dict, results: dict) -> int:
    by_id = {c["tool_call_id"]: c for c in spec["calls"]}
    count = 0
    for cid, result in results.items():
        if result["status"] == "ok" and result.get("result_digest") == sha256_hex(
            by_id[cid].get("result", "")
        ):
            count += 1
    return count


def main(argv=None) -> int:
    out_dir = Path(argv[1] if argv and len(argv) > 1 else "outputs/run1")
    corpus_dir = out_dir / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    index = load_json(PACK_ROOT / "corpus" / "index.json")
    print(f"== corpus: {len(index)} traces ==")
    fail = 0
    total_issued = total_preserved = total_errored = total_dropped = 0
    per_trace = []
    for entry in index:
        spec = load_json(PACK_ROOT / entry["manifest"])
        trace_path = corpus_dir / f"{entry['id']}.trace.jsonl"
        if entry["component"] == "asyncio":
            dispatcher = Dispatcher()
            trace, results, summary = asyncio.run(dispatcher.run_batch(spec))
            replay_trace, _r, _s = asyncio.run(dispatcher.run_batch(spec))
            preserved = preserved_asyncio(spec, results)
        else:
            dispatcher = ThreadedDispatcher(
                max_workers=int(spec.get("config", {}).get("max_workers", 2))
            )
            trace, results, summary = dispatcher.run_batch(spec)
            replay_trace, _r, _s = dispatcher.run_batch(spec)
            preserved = summary["preserved"]
        trace_path.write_text(trace, encoding="utf-8")
        issued = len(spec["calls"])
        reported = len(results)
        errored = sum(
            1 for r in results.values() if r["status"] in ("error", "timeout", "cancelled")
        )
        dropped = issued - reported
        replay_match = replay_trace == trace
        ok = replay_match and dropped == 0 and preserved + errored == issued
        if not ok:
            fail += 1
        total_issued += issued
        total_preserved += preserved
        total_errored += errored
        total_dropped += dropped
        print(
            f"corpus {entry['id']}: {entry['component']} fault={entry['fault']} "
            f"issued={issued} preserved={preserved} errored={errored} dropped={dropped} "
            f"replay={'MATCH' if replay_match else 'MISMATCH'} "
            f"trace_sha256={summary['trace_sha256']}"
        )
        per_trace.append(
            {
                "id": entry["id"],
                "component": entry["component"],
                "fault": entry["fault"],
                "issued": issued,
                "preserved": preserved,
                "errored": errored,
                "dropped": dropped,
                "replay_match": replay_match,
                "trace_sha256": summary["trace_sha256"],
            }
        )
    summary_doc = {
        "traces": len(index),
        "issued": total_issued,
        "preserved": total_preserved,
        "errored": total_errored,
        "dropped": total_dropped,
        "per_trace": per_trace,
    }
    (corpus_dir / "corpus-summary.json").write_text(
        json.dumps(summary_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"corpus totals: traces={len(index)} issued={total_issued} "
        f"preserved={total_preserved} errored={total_errored} dropped={total_dropped}"
    )
    print("corpus: ALL PASS" if fail == 0 else "corpus: FAILURES PRESENT")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
