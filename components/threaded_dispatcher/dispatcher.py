"""Thread-pool tool dispatcher with deterministic virtual-clock replay.

Models the semantics of Python's ``concurrent.futures.ThreadPoolExecutor``
on a virtual clock, so every run is reproducible tick-for-tick. No
wall-clock time is read. The modelled semantics were verified against
the primary sources cited in citations.md and against the local CPython
3.12.3 source (``Lib/concurrent/futures/_base.py``,
``Future.cancel``) and a live probe (``probe_live.py``) using a real
``ThreadPoolExecutor``:

- ``Executor.submit`` schedules a callable and returns a ``Future``.
  With ``max_workers`` workers, calls beyond the pool wait in a FIFO
  queue; they have not started and can still be cancelled.
- ``Future.cancel()``: "Returns True if the future was cancelled,
  False otherwise. A future cannot be cancelled if it is running or
  has already completed." A cancel issued while a call is running
  returns False and the call runs to completion.
- ``Executor.shutdown(wait=True, cancel_futures=True)``: "cancel all
  pending futures that the executor has not started running. Any
  futures that are completed or running won't be cancelled."
- ``concurrent.futures.wait(..., timeout=...)`` returns after the
  timeout without cancelling anything: the underlying work keeps
  running and may complete late. A collector that stops at the timeout
  and discards late results loses real results (see the
  ``wait_timeout`` mode below).
- Unlike ``asyncio.TaskGroup``, one future's exception does not affect
  its siblings: each future carries its own exception. The
  ``abort_on_error`` naive mode reproduces a caller that reacts to the
  first error by shutting the executor down with
  ``cancel_futures=True``, which drops every still-queued call.

Trace schema: the same canonical JSONL schema as the asyncio
dispatcher (see TRACE_SCHEMA.md), plus two event types:

- ``call_queued`` — a call submitted while all workers are busy.
- ``cancel_rejected`` — a cancel that could not stop a running call
  (``Future.cancel()`` returned False).
- ``call_late_result`` — a call that completed after the collector's
  timeout closed the batch (``wait_timeout`` mode only).

Every call receives exactly one terminal result record in correct
mode: ok, error, timeout, cancelled, or refused. Naive modes
reproduce the classic failure modes so each can be observed and
asserted against.
"""

from __future__ import annotations

import hashlib
import heapq
import itertools
import json


def canonical(obj) -> str:
    """Canonical JSON: sorted keys, no insignificant whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ToolError(Exception):
    """A declared tool failure (fixture behaviour, not a harness bug)."""


class Emitter:
    """Appends canonical trace events with monotonically increasing seq."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.now: int = 0

    def emit(self, event: dict) -> dict:
        event = dict(event)
        event["seq"] = len(self.events)
        event["tick"] = self.now
        self.events.append(event)
        return event

    def text(self) -> str:
        return "".join(canonical(e) + "\n" for e in self.events)


class ThreadedDispatcher:
    """Deterministic simulation of a ThreadPoolExecutor-style dispatcher.

    ``max_workers`` bounds the number of concurrently running calls;
    further calls wait in a FIFO queue. Time is virtual: the clock
    advances only to the next scheduled event tick.
    """

    def __init__(self, max_workers: int = 2) -> None:
        self.max_workers = max_workers

    def run_batch(
        self,
        spec: dict,
        *,
        enforce_timeouts: bool = True,
        emit_cancel_results: bool = True,
        isolate_errors: bool = True,
        reconcile_late_results: bool = True,
    ):
        """Run one batch. Returns (trace_text, results_by_id, summary).

        ``results_by_id`` maps tool_call_id -> result dict. Completion
        order is returned inside ``summary["completion_order"]``.

        ``wait_timeout_ticks`` (in the spec, optional): the collector
        stops waiting at this tick, mirroring
        ``concurrent.futures.wait(..., timeout=...)``. Running calls
        are recorded as ``timeout`` at that tick, but the work keeps
        running; when a call later completes, a ``call_late_result``
        event is emitted. With ``reconcile_late_results=True`` the
        late result is recorded (the value is preserved, flagged
        ``late: true``); with False it is discarded (the value is
        lost — see ``summary["values_lost"]``).
        """
        emitter = Emitter()
        calls = spec["calls"]
        max_workers = int(spec.get("config", {}).get("max_workers", self.max_workers))
        cancel_at = spec.get("cancel_at_tick")
        wait_timeout = spec.get("wait_timeout_ticks")

        emitter.emit(
            {
                "type": "batch_start",
                "batch_id": spec["batch_id"],
                "call_ids": [c["tool_call_id"] for c in calls],
                "max_workers": max_workers,
            }
        )

        # Event heap: (tick, seq, kind, call_id). Ties resolve in
        # scheduling order, so every run is deterministic.
        heap: list = []
        seq = itertools.count()

        def schedule(tick: int, kind: str, cid):
            heapq.heappush(heap, (tick, next(seq), kind, cid))

        by_id = {c["tool_call_id"]: c for c in calls}
        state: dict[str, str] = {}  # queued | running | done
        results: dict[str, dict] = {}
        completion_order: list[str] = []
        values_lost = 0
        late_reconciled = 0
        collection_closed_tick = None
        running: set[str] = set()
        queue: list[str] = []

        def start_call(cid: str, tick: int) -> None:
            call = by_id[cid]
            state[cid] = "running"
            running.add(cid)
            emitter.emit(
                {
                    "type": "call_start",
                    "tool_call_id": cid,
                    "tool": call["tool"],
                    "args_digest": sha256_hex(canonical(call.get("args", {}))),
                    "parent_tool_call_id": call.get("parent_tool_call_id"),
                    "worker_slot": len(running) - 1,
                }
            )
            duration = int(call.get("duration_ticks", 0))
            schedule(tick + duration, "complete", cid)
            if enforce_timeouts and call.get("timeout_ticks") is not None:
                # A deadline equal to the duration does not time the
                # call out: the completion event is scheduled first
                # and wins the tie, so the call completes.
                schedule(tick + int(call["timeout_ticks"]), "deadline", cid)

        def finish_call(cid: str, tick: int, *, late: bool = False) -> None:
            """Record a call's terminal outcome. Returns nothing."""
            call = by_id[cid]
            running.discard(cid)
            state[cid] = "done"
            if late:
                emitter.emit(
                    {
                        "type": "call_late_result",
                        "tool_call_id": cid,
                        "status": "ok",
                        "result_digest": sha256_hex(call.get("result", "")),
                        "collection_closed_tick": collection_closed_tick,
                    }
                )
            if call.get("behavior") == "error":
                emitter.emit(
                    {
                        "type": "call_error",
                        "tool_call_id": cid,
                        "status": "error",
                        "error": call.get("error", "declared tool failure"),
                        "is_error": True,
                    }
                )
                results[cid] = {
                    "tool_call_id": cid,
                    "status": "error",
                    "error": call.get("error", "declared tool failure"),
                    "is_error": True,
                }
            else:
                result = call.get("result", "")
                emitter.emit(
                    {
                        "type": "call_end",
                        "tool_call_id": cid,
                        "status": "ok",
                        "result_digest": sha256_hex(result),
                        "parent_tool_call_id": call.get("parent_tool_call_id"),
                    }
                )
                results[cid] = {
                    "tool_call_id": cid,
                    "status": "ok",
                    "result": result,
                    "result_digest": sha256_hex(result),
                    "late": late,
                }
            completion_order.append(cid)

        def pump_queue(tick: int) -> None:
            while queue and len(running) < max_workers:
                start_call(queue.pop(0), tick)

        # Issue: start the first max_workers calls, queue the rest.
        for call in calls:
            cid = call["tool_call_id"]
            if len(running) < max_workers:
                start_call(cid, 0)
            else:
                state[cid] = "queued"
                queue.append(cid)
                emitter.emit(
                    {
                        "type": "call_queued",
                        "tool_call_id": cid,
                        "tool": call["tool"],
                        "queue_position": len(queue) - 1,
                    }
                )

        if cancel_at is not None:
            schedule(int(cancel_at), "cancel_all", None)
        if wait_timeout is not None:
            schedule(int(wait_timeout), "wait_timeout", None)

        aborted = False
        while heap:
            tick, _, kind, cid = heapq.heappop(heap)
            # Stale events (a completion or deadline for a call that
            # already timed out, was cancelled, or finished) are
            # discarded WITHOUT advancing the clock: the clock only
            # moves to ticks where real work happens.
            if kind in ("complete", "deadline") and state.get(cid) != "running":
                continue
            emitter.now = tick
            if kind == "complete":
                call = by_id[cid]
                if collection_closed_tick is not None:
                    # The collector already stopped waiting: this is
                    # a late result.
                    if reconcile_late_results:
                        finish_call(cid, tick, late=True)
                        late_reconciled += 1
                    else:
                        running.discard(cid)
                        state[cid] = "done"
                        values_lost += 1
                        emitter.emit(
                            {
                                "type": "call_late_result",
                                "tool_call_id": cid,
                                "status": "discarded",
                                "result_digest": sha256_hex(call.get("result", "")),
                                "collection_closed_tick": collection_closed_tick,
                            }
                        )
                    pump_queue(tick)
                    continue
                finish_call(cid, tick)
                if not isolate_errors and results[cid]["status"] == "error":
                    # Naive abort-on-first-error: shut down with
                    # cancel_futures=True — queued calls are cancelled
                    # and (naive) never reported.
                    aborted = True
                    for qcid in list(queue):
                        state[qcid] = "done"
                        queue.remove(qcid)
                        emitter.emit(
                            {
                                "type": "call_cancelled",
                                "tool_call_id": qcid,
                                "status": "cancelled",
                                "reason": "aborted: queued call dropped on first error",
                            }
                        )
                    break
                pump_queue(tick)
            elif kind == "deadline":
                running.discard(cid)
                state[cid] = "done"
                call = by_id[cid]
                emitter.emit(
                    {
                        "type": "call_timeout",
                        "tool_call_id": cid,
                        "status": "timeout",
                        "deadline_ticks": call.get("timeout_ticks"),
                    }
                )
                results[cid] = {
                    "tool_call_id": cid,
                    "status": "timeout",
                    "deadline_ticks": call.get("timeout_ticks"),
                }
                completion_order.append(cid)
                pump_queue(tick)
            elif kind == "cancel_all":
                for qcid in list(queue):
                    state[qcid] = "done"
                    queue.remove(qcid)
                    if emit_cancel_results:
                        emitter.emit(
                            {
                                "type": "call_cancelled",
                                "tool_call_id": qcid,
                                "status": "cancelled",
                            }
                        )
                        results[qcid] = {
                            "tool_call_id": qcid,
                            "status": "cancelled",
                        }
                    else:
                        # Naive path: the cancelled call reports
                        # nothing and is silently dropped.
                        pass
                for rcid in sorted(running):
                    # Future.cancel() returns False for a running
                    # call: it cannot be stopped.
                    emitter.emit(
                        {
                            "type": "cancel_rejected",
                            "tool_call_id": rcid,
                            "status": "running",
                            "reason": "Future.cancel() returns False for a running call",
                        }
                    )
            elif kind == "wait_timeout":
                collection_closed_tick = tick
                for rcid in sorted(running):
                    results[rcid] = {
                        "tool_call_id": rcid,
                        "status": "timeout",
                        "reason": "collector wait() timed out; work continues",
                    }
                    emitter.emit(
                        {
                            "type": "call_timeout",
                            "tool_call_id": rcid,
                            "status": "timeout",
                            "reason": "collector wait() timed out; work continues",
                        }
                    )
                # Queued calls are still waiting; they run on when a
                # worker frees up, and their results are late too.
                for qcid in list(queue):
                    state[qcid] = "done"
                    queue.remove(qcid)
                    results[qcid] = {
                        "tool_call_id": qcid,
                        "status": "cancelled",
                        "reason": "collector stopped waiting before the call started",
                    }
                    emitter.emit(
                        {
                            "type": "call_cancelled",
                            "tool_call_id": qcid,
                            "status": "cancelled",
                            "reason": "collector stopped waiting before the call started",
                        }
                    )

        counts = {"ok": 0, "error": 0, "timeout": 0, "cancelled": 0, "refused": 0}
        for result in results.values():
            counts[result["status"]] = counts.get(result["status"], 0) + 1
        preserved = sum(
            1
            for r in results.values()
            if r["status"] == "ok"
            and r.get("result_digest")
            == sha256_hex(by_id[r["tool_call_id"]].get("result", ""))
        )
        dropped = len(calls) - len(results)
        if aborted:
            status = "aborted"
        elif counts["cancelled"]:
            status = "cancelled"
        elif counts["error"] or counts["timeout"]:
            status = "partial_failure"
        else:
            status = "ok"
        emitter.emit(
            {
                "type": "batch_end",
                "batch_id": spec["batch_id"],
                "status": status,
                "completed": counts["ok"],
                "failed": counts["error"] + counts["timeout"],
                "cancelled": counts["cancelled"],
                "refused": counts["refused"],
                "collection_closed_tick": collection_closed_tick,
                "late_results_reconciled": late_reconciled,
                "values_lost": values_lost,
            }
        )
        summary = {
            "batch_id": spec["batch_id"],
            "status": status,
            "end_tick": emitter.now,
            "event_count": len(emitter.events),
            "trace_sha256": sha256_hex(emitter.text()),
            "completed": counts["ok"],
            "failed": counts["error"] + counts["timeout"],
            "cancelled": counts["cancelled"],
            "refused": counts["refused"],
            "results_reported": len(results),
            "calls_issued": len(calls),
            "preserved": preserved,
            "dropped": dropped,
            "values_lost": values_lost,
            "late_results_reconciled": late_reconciled,
            "collection_closed_tick": collection_closed_tick,
            "completion_order": completion_order,
        }
        return emitter.text(), results, summary


def collect_by_id(results: dict) -> dict:
    """Correct collector: results keyed by tool_call_id."""
    return {cid: r.get("result") for cid, r in results.items()}


def collect_by_position(results: dict, completion_order: list, call_ids: list) -> dict:
    """Buggy collector: completion-order results zipped onto issue-order ids.

    Reproduces the classic misattribution when completions arrive out
    of order: the first result to finish is attached to the first call
    issued, regardless of which call produced it.
    """
    ordered = [results[cid] for cid in completion_order if cid in results]
    return {
        cid: ordered[i].get("result") if i < len(ordered) else None
        for i, cid in enumerate(call_ids)
    }
