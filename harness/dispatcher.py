"""Asyncio parallel tool dispatcher with deterministic virtual-clock replay.

Semantics modelled on the primary sources cited in citations.md:

- OpenAI function calling: a model may return several tool calls in one
  turn; each call is identified by its ``call_id`` and each result must
  reference that ``call_id``. ``parallel_tool_calls: false`` limits a turn
  to zero or one call.
- Anthropic parallel tool use: the API does not prescribe an execution
  order (``asyncio.gather`` is a valid choice); the runtime must return
  one ``tool_result`` per ``tool_use`` block in a single user message,
  matched by ``tool_use_id``; a call that was not run is still reported
  with ``is_error: true``.
- CorbanuTerminal ``codex-rs/core/src/tools/parallel.rs`` (commit
  7b5b368f896081c499c94d25faf4ea4b7ec44ca0): parallel-capable tools share a
  read lock on the parallel-execution gate while non-parallel tools take
  it exclusively; cancellation is driven by a ``CancellationToken`` and
  the abort path still records an output per pending call; nested
  code-mode calls are excluded from timing events so overlapping events
  are not mistaken for independent tool-call latency; identical calls
  are capped at 3 per turn.

This dispatcher implements the same rules on a virtual clock so that
every run is reproducible tick-for-tick. No wall-clock time is read.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

from .vclock import VirtualClock


def canonical(obj) -> str:
    """Canonical JSON: sorted keys, no insignificant whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def call_signature(call: dict) -> str:
    """Identity of a call for repeat detection: tool name + args digest.

    Mirrors the SHA-256 tool-call signature in CorbanuTerminal's
    ``tool_call_signature`` (tool name, NUL, payload digest).
    """
    digest = sha256_hex(canonical(call.get("args", {})))
    return f"{call['tool']}:{digest[:16]}"


class ToolError(Exception):
    """A declared tool failure (fixture behaviour, not a harness bug)."""


class Emitter:
    """Appends canonical trace events with monotonically increasing seq."""

    def __init__(self, clock: VirtualClock) -> None:
        self.clock = clock
        self.events: list[dict] = []

    def emit(self, event: dict) -> dict:
        event = dict(event)
        event["seq"] = len(self.events)
        event["tick"] = self.clock.now
        self.events.append(event)
        return event

    def text(self) -> str:
        return "".join(canonical(e) + "\n" for e in self.events)


class Dispatcher:
    """Runs a batch of parallel tool calls on the virtual clock.

    Correct behaviour (the default) enforces per-call timeouts, isolates
    per-call errors, and reports a result for every call, including
    cancelled and refused ones. The ``naive_*`` flags reproduce the
    classic failure modes so each can be observed and asserted against:
    ``enforce_timeouts=False`` lets a slow call hold the batch;
    ``emit_cancel_results=False`` drops pending calls from the result
    set on cancellation; ``isolate_errors=False`` lets one call's error
    abort the whole batch (``asyncio.gather`` without isolation).
    """

    def __init__(
        self,
        max_identical_calls_per_turn: int = 3,
        suppress_nested_timing: bool = True,
    ) -> None:
        self.max_identical_calls_per_turn = max_identical_calls_per_turn
        self.suppress_nested_timing = suppress_nested_timing

    async def run_batch(
        self,
        spec: dict,
        *,
        enforce_timeouts: bool = True,
        emit_cancel_results: bool = True,
        isolate_errors: bool = True,
    ):
        """Run one batch. Returns (trace_text, results_by_id, summary).

        ``results_by_id`` maps tool_call_id -> result dict. Completion
        order is returned inside ``summary["completion_order"]``.
        """
        clock = VirtualClock()
        emitter = Emitter(clock)
        calls = spec["calls"]
        emitter.emit(
            {
                "type": "batch_start",
                "batch_id": spec["batch_id"],
                "call_ids": [c["tool_call_id"] for c in calls],
            }
        )

        # Repeat cap: identical (tool, args) calls beyond the cap are
        # refused without running, mirroring MAX_IDENTICAL_TOOL_CALLS_PER_TURN.
        seen: dict[str, int] = {}
        refused: set[str] = set()
        for call in calls:
            sig = call_signature(call)
            seen[sig] = seen.get(sig, 0) + 1
            if seen[sig] > self.max_identical_calls_per_turn:
                refused.add(call["tool_call_id"])
                emitter.emit(
                    {
                        "type": "call_refused",
                        "tool_call_id": call["tool_call_id"],
                        "tool": call["tool"],
                        "args_digest": sha256_hex(canonical(call.get("args", {}))),
                        "status": "refused",
                        "reason": (
                            f"identical tool call refused after "
                            f"{self.max_identical_calls_per_turn} identical "
                            f"calls in one turn"
                        ),
                    }
                )

        cancel_reason: dict[str, str] = {}
        completion_order: list[str] = []
        if spec.get("cancel_at_tick") is not None:
            clock.schedule(int(spec["cancel_at_tick"]), "cancel_all", None)

        tasks: dict[str, asyncio.Task] = {}
        for call in calls:
            cid = call["tool_call_id"]
            if cid in refused:
                continue
            tasks[cid] = asyncio.create_task(
                self._run_call(
                    call,
                    clock,
                    emitter,
                    cancel_reason,
                    completion_order,
                    emit_cancel_results=emit_cancel_results,
                    isolate_errors=isolate_errors,
                )
            )
            if enforce_timeouts and call.get("timeout_ticks") is not None:
                clock.schedule(
                    clock.now + int(call["timeout_ticks"]), "deadline", cid
                )

        await asyncio.sleep(0)  # startup: every task registers its first sleep
        aborted = await self._maybe_abort_on_error(
            tasks, cancel_reason, emitter, spec, isolate_errors
        )
        while aborted is None:
            pending = [t for t in tasks.values() if not t.done()]
            if not pending:
                break
            # Discard stale wake events (their future is already done)
            # without advancing the clock.
            while (
                clock.peek() is not None
                and clock.peek()[2] == "wake"
                and clock.peek()[3].done()
            ):
                clock.pop()
            if len(clock):
                tick = clock.peek()[0]
                clock.now = tick
                while clock.peek() is not None and clock.peek()[0] == tick:
                    _, _, kind, payload = clock.pop()
                    if kind == "wake":
                        fut = payload
                        if not fut.done():
                            fut.set_result(None)
                    elif kind == "deadline":
                        task = tasks.get(payload)
                        if task is not None and not task.done():
                            cancel_reason[payload] = "timeout"
                            task.cancel()
                    elif kind == "cancel_all":
                        for cid, task in tasks.items():
                            if not task.done():
                                cancel_reason[cid] = "cancelled"
                                task.cancel()
                await asyncio.sleep(0)  # let woken tasks run to their next await
                aborted = await self._maybe_abort_on_error(
                    tasks, cancel_reason, emitter, spec, isolate_errors
                )
            else:
                await asyncio.sleep(0)
                if not len(clock) and any(
                    not t.done() for t in tasks.values()
                ):
                    raise RuntimeError(
                        "deadlock: tasks pending with no scheduled "
                        "virtual-clock events"
                    )

        results: dict[str, dict] = {}
        for call in calls:
            cid = call["tool_call_id"]
            if cid in refused:
                results[cid] = {
                    "tool_call_id": cid,
                    "status": "refused",
                    "reason": "identical tool call refused",
                }
                continue
            task = tasks[cid]
            if task.cancelled():
                continue
            if task.exception() is not None:
                continue
            result = task.result()
            if result is not None:
                results[cid] = result

        counts = {"ok": 0, "error": 0, "timeout": 0, "cancelled": 0, "refused": 0}
        for result in results.values():
            counts[result["status"]] = counts.get(result["status"], 0) + 1
        if aborted is not None:
            status = "aborted"
        elif counts["cancelled"]:
            status = "cancelled"
        elif counts["error"] or counts["timeout"] or counts["refused"]:
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
            }
        )
        summary = {
            "batch_id": spec["batch_id"],
            "status": status,
            "end_tick": clock.now,
            "event_count": len(emitter.events),
            "trace_sha256": sha256_hex(emitter.text()),
            "completed": counts["ok"],
            "failed": counts["error"] + counts["timeout"],
            "cancelled": counts["cancelled"],
            "refused": counts["refused"],
            "results_reported": len(results),
            "calls_issued": len(calls),
            "completion_order": completion_order,
        }
        return emitter.text(), results, summary

    async def _maybe_abort_on_error(
        self, tasks, cancel_reason, emitter, spec, isolate_errors
    ):
        """Naive gather behaviour: the first uncaught error aborts the batch.

        Returns an ``aborted`` record, or None when nothing aborted.
        Only active when ``isolate_errors`` is False.
        """
        if isolate_errors:
            return None
        for cid, task in tasks.items():
            if task.done() and not task.cancelled() and task.exception() is not None:
                for cid2, task2 in tasks.items():
                    if not task2.done():
                        cancel_reason[cid2] = "aborted"
                        task2.cancel()
                emitter.emit(
                    {
                        "type": "batch_aborted",
                        "batch_id": spec["batch_id"],
                        "status": "aborted",
                        "failed_tool_call_id": cid,
                        "error": str(task.exception()),
                    }
                )
                await asyncio.sleep(0)  # let the cancellations land
                return {"failed_tool_call_id": cid, "error": str(task.exception())}
        return None

    async def _run_call(
        self,
        call: dict,
        clock: VirtualClock,
        emitter: Emitter,
        cancel_reason: dict,
        completion_order: list,
        *,
        emit_cancel_results: bool,
        isolate_errors: bool,
    ):
        """Top-level per-call wrapper: emits terminal events, returns a result dict (or None when unreported)."""
        cid = call["tool_call_id"]

        def finish(outcome):
            if outcome is not None:
                completion_order.append(cid)
            return outcome

        try:
            result = await self._execute(
                call, clock, emitter, cancel_reason, traced=True
            )
        except asyncio.CancelledError:
            reason = cancel_reason.get(cid, "cancelled")
            if reason == "timeout":
                emitter.emit(
                    {
                        "type": "call_timeout",
                        "tool_call_id": cid,
                        "status": "timeout",
                        "deadline_ticks": call.get("timeout_ticks"),
                    }
                )
                return finish(
                    {
                        "tool_call_id": cid,
                        "status": "timeout",
                        "deadline_ticks": call.get("timeout_ticks"),
                    }
                )
            if reason == "aborted" or not emit_cancel_results:
                # Naive paths: the cancelled call reports nothing.
                return finish(None)
            emitter.emit(
                {
                    "type": "call_cancelled",
                    "tool_call_id": cid,
                    "status": "cancelled",
                }
            )
            return finish({"tool_call_id": cid, "status": "cancelled"})
        except ToolError as exc:
            emitter.emit(
                {
                    "type": "call_error",
                    "tool_call_id": cid,
                    "status": "error",
                    "error": str(exc),
                    "is_error": True,
                }
            )
            if isolate_errors:
                return finish(
                    {
                        "tool_call_id": cid,
                        "status": "error",
                        "error": str(exc),
                        "is_error": True,
                    }
                )
            raise
        return finish(
            {
                "tool_call_id": cid,
                "status": "ok",
                "result": result,
                "result_digest": sha256_hex(result),
            }
        )

    async def _execute(
        self,
        call: dict,
        clock: VirtualClock,
        emitter: Emitter,
        cancel_reason: dict,
        *,
        traced: bool,
    ) -> str:
        """Performs the work. Emits start/end events only when traced."""
        cid = call["tool_call_id"]
        if traced:
            emitter.emit(
                {
                    "type": "call_start",
                    "tool_call_id": cid,
                    "tool": call["tool"],
                    "args_digest": sha256_hex(canonical(call.get("args", {}))),
                    "parent_tool_call_id": call.get("parent_tool_call_id"),
                }
            )
        # Cancellation propagates to the top-level wrapper (_run_call),
        # which emits the single terminal event for the call; _execute
        # never emits terminal events itself, so no event is duplicated.
        if call.get("behavior") == "nested":
            await clock.sleep(int(call.get("first_ticks", 1)))
            sub = dict(call["sub"])
            sub["parent_tool_call_id"] = cid
            if self.suppress_nested_timing:
                # Guarded: the nested call runs but emits no timing
                # events (ToolCallTimingGuard behaviour).
                await self._execute(
                    sub, clock, emitter, cancel_reason, traced=False
                )
            else:
                await self._execute(
                    sub, clock, emitter, cancel_reason, traced=True
                )
            await clock.sleep(int(call.get("second_ticks", 1)))
        else:
            await clock.sleep(int(call.get("duration_ticks", 0)))
        if call.get("behavior") == "error":
            raise ToolError(call.get("error", "declared tool failure"))
        result = call.get("result", "")
        if traced:
            emitter.emit(
                {
                    "type": "call_end",
                    "tool_call_id": cid,
                    "status": "ok",
                    "result_digest": sha256_hex(result),
                    "parent_tool_call_id": call.get("parent_tool_call_id"),
                }
            )
        return result


def collect_by_id(results: dict) -> dict:
    """Correct collector: results keyed by tool_call_id."""
    return {cid: r.get("result") for cid, r in results.items()}


def collect_by_position(results: dict, completion_order: list, call_ids: list) -> dict:
    """Buggy collector: completion-order results zipped onto issue-order ids.

    Reproduces the classic misattribution when completions arrive out of
    order: the first result to finish is attached to the first call
    issued, regardless of which call produced it.
    """
    ordered = [results[cid] for cid in completion_order if cid in results]
    return {
        cid: ordered[i].get("result") if i < len(ordered) else None
        for i, cid in enumerate(call_ids)
    }
