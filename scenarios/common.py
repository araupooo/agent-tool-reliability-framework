"""Shared helpers for the scenario scripts (record, replay, assert)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACK_ROOT))

from harness.dispatcher import Dispatcher  # noqa: E402


def load_fixture(name: str) -> dict:
    with open(PACK_ROOT / "fixtures" / name, "r", encoding="utf-8") as fh:
        return json.load(fh)


def record(spec: dict, out_path: Path, **flags) -> tuple[str, dict, dict]:
    """Run a batch and write the canonical trace. Returns (trace, results, summary)."""
    dispatcher = Dispatcher(
        suppress_nested_timing=not flags.get("unguarded_nested", False)
    )
    trace, results, summary = asyncio.run(
        dispatcher.run_batch(
            spec,
            enforce_timeouts=not flags.get("no_timeout", False),
            emit_cancel_results=not flags.get("naive_cancel", False),
            isolate_errors=not flags.get("naive_gather", False),
        )
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(trace, encoding="utf-8")
    return trace, results, summary


def replay(spec: dict, recorded_text: str, **flags) -> tuple[bool, dict]:
    """Re-run a batch and compare with the recorded trace."""
    dispatcher = Dispatcher(
        suppress_nested_timing=not flags.get("unguarded_nested", False)
    )
    trace, _results, summary = asyncio.run(
        dispatcher.run_batch(
            spec,
            enforce_timeouts=not flags.get("no_timeout", False),
            emit_cancel_results=not flags.get("naive_cancel", False),
            isolate_errors=not flags.get("naive_gather", False),
        )
    )
    return trace == recorded_text, summary


def events(trace: str) -> list[dict]:
    return [json.loads(line) for line in trace.splitlines() if line.strip()]


def of_type(trace_events: list[dict], event_type: str) -> list[dict]:
    return [e for e in trace_events if e["type"] == event_type]


def check(condition: bool, label: str, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"{status}: {label}" + (f" -- {detail}" if detail else ""))
    if not condition:
        raise SystemExit(1)


def header(title: str) -> None:
    print(f"== {title} ==")
