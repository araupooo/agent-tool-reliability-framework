"""Generate the network-scale replay corpus (deterministic, seeded).

Writes 48 batch manifests into corpus/ plus corpus/index.json:
- 24 asyncio batches (corpus-async-01..24), sizes 2..8, faults cycling
  none / timeout / cancel / error / reorder / mixed.
- 24 threaded batches (corpus-threaded-01..24), max_workers 1..3,
  same fault cycle, some with a collector wait-timeout.

Fixed seed (20261108); re-running reproduces the corpus byte-for-byte.
Run from the pack root: python3 scripts/generate_corpus.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = PACK_ROOT / "corpus"
SEED = 20261108

TOOLS = ["read_file", "compute_hash", "fetch_url", "slow_query", "long_task"]
FAULTS = ["none", "timeout", "cancel", "error", "reorder", "mixed"]


def make_calls(rng: random.Random, size: int, fault: str) -> tuple[list[dict], dict]:
    calls = []
    for i in range(size):
        tool = rng.choice(TOOLS)
        call = {
            "tool_call_id": f"call-c{i + 1}",
            "tool": tool,
            "args": {"job": f"corpus-{i + 1}", "n": i + 1},
            "behavior": "ok",
            "duration_ticks": rng.randint(1, 12),
            "result": f"{tool} result {i + 1}",
        }
        calls.append(call)
    extra: dict = {}
    if fault in ("timeout", "mixed"):
        slow = rng.randrange(size)
        calls[slow]["duration_ticks"] = rng.randint(15, 20)
        calls[slow]["timeout_ticks"] = rng.randint(4, 6)
    if fault in ("error", "mixed"):
        bad = rng.randrange(size)
        calls[bad]["behavior"] = "error"
        calls[bad]["error"] = "upstream 503: service unavailable"
        calls[bad]["result"] = ""
    if fault == "cancel":
        extra["cancel_at_tick"] = rng.randint(3, 6)
    if fault == "reorder":
        # Durations strictly decreasing in issue order, so completion
        # order is the exact reverse of issue order.
        durations = sorted((rng.randint(1, 12) for _ in range(size)), reverse=True)
        for call, duration in zip(calls, durations):
            call["duration_ticks"] = duration
    return calls, extra


def main() -> int:
    rng = random.Random(SEED)
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    index = []
    for component in ("asyncio", "threaded"):
        for n in range(1, 25):
            fault = FAULTS[(n - 1) % len(FAULTS)]
            size = [2, 3, 4, 6, 8][(n - 1) % 5]
            calls, extra = make_calls(rng, size, fault)
            batch_id = f"corpus-{component}-{n:02d}"
            spec: dict = {
                "batch_id": batch_id,
                "calls": calls,
                "cancel_at_tick": extra.get("cancel_at_tick"),
                "config": {
                    "max_identical_calls_per_turn": 3,
                    "note": f"Corpus batch: {component}, {size} calls, fault={fault}.",
                },
            }
            if component == "threaded":
                spec["config"]["max_workers"] = [1, 2, 3][(n - 1) % 3]
                if fault == "cancel":
                    # Collector wait-timeout variant on every third
                    # threaded cancel batch: the collector stops at
                    # tick 4 while work continues.
                    if n % 3 == 0:
                        spec["wait_timeout_ticks"] = 4
            manifest = CORPUS_DIR / f"{batch_id}.json"
            manifest.write_text(json.dumps(spec, sort_keys=True), encoding="utf-8")
            index.append(
                {
                    "id": batch_id,
                    "component": component,
                    "fault": fault,
                    "size": size,
                    "max_workers": spec["config"].get("max_workers"),
                    "manifest": f"corpus/{batch_id}.json",
                }
            )
    (CORPUS_DIR / "index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"corpus: wrote {len(index)} manifests to corpus/ (seed {SEED})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
