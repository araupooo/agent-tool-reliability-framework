"""Build propagation-map.json from the measured matrix results.

Reads outputs/run1/matrix/matrix-results.json (per-cell preserved /
errored / dropped counts) and combines them with the stage narratives
below — each narrative was written from an observed run, not from
theory — into propagation-map.json: for every fault type, component,
and injection point, what happens at each stage (issued -> in-flight
-> terminal -> collected), where a result can be lost, and where
recovery happens.

Run from the pack root: python3 scripts/build_propagation_map.py
"""

from __future__ import annotations

import json
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[1]

# Stage narratives, keyed by (component, fault). Each value maps an
# injection point to the four stage descriptions for that cell. Every
# claim here is backed by the matrix cell log named in the cell's
# evidence (see the generated JSON).
NARRATIVES = {
    ("asyncio", "timeout"): {
        "at-issue": [
            ("issued", "Deadline of 2 ticks is scheduled when the call is issued."),
            ("in-flight", "The slow call runs; the deadline event fires at tick 2, before any sibling completes."),
            ("terminal", "call_timeout for the slow call at tick 2; the call is cancelled and reported timeout."),
            ("collected", "Siblings complete on schedule; batch ends partial_failure with 2 preserved, 1 errored, 0 dropped."),
        ],
        "in-flight": [
            ("issued", "Deadline of 5 ticks is scheduled when the call is issued."),
            ("in-flight", "The slow call runs; the deadline event fires at tick 5, mid-batch."),
            ("terminal", "call_timeout for the slow call at tick 5."),
            ("collected", "Siblings complete on schedule; batch ends partial_failure with 2 preserved, 1 errored, 0 dropped."),
        ],
        "at-completion": [
            ("issued", "Deadline of 20 ticks equals the slow call's 20-tick duration."),
            ("in-flight", "The deadline event was scheduled before the call's wake event, so at tick 20 the deadline wins the tie."),
            ("terminal", "call_timeout for the slow call at tick 20, even though its work would have finished on the same tick."),
            ("collected", "Batch ends at tick 20 partial_failure with 2 preserved, 1 errored, 0 dropped. Contrast the threaded dispatcher, where the completion wins the same tie."),
        ],
    },
    ("asyncio", "cancellation"): {
        "at-issue": [
            ("issued", "Cancel is scheduled for tick 0, before any call completes."),
            ("in-flight", "All three calls are in flight when the cancel fires; every unfinished task is cancelled."),
            ("terminal", "call_cancelled for all three calls at tick 0."),
            ("collected", "Batch ends cancelled at tick 0 with 0 preserved, 3 errored, 0 dropped: every call is reported, none silently."),
        ],
        "in-flight": [
            ("issued", "Cancel is scheduled for tick 4."),
            ("in-flight", "The quick call completes at tick 3; the two long calls are still in flight at tick 4."),
            ("terminal", "call_cancelled for both long calls at tick 4."),
            ("collected", "Batch ends cancelled at tick 4 with 1 preserved, 2 errored, 0 dropped."),
        ],
        "at-completion": [
            ("issued", "Cancel is scheduled for tick 11."),
            ("in-flight", "The quick call completes at tick 3 and long-a at tick 10; only long-b is in flight at tick 11."),
            ("terminal", "call_cancelled for long-b at tick 11."),
            ("collected", "Batch ends cancelled at tick 11 with 2 preserved, 1 errored, 0 dropped."),
        ],
    },
    ("asyncio", "partial-failure"): {
        "at-issue": [
            ("issued", "The failing call is declared with a 0-tick duration, so it fails immediately."),
            ("in-flight", "The failure is raised at tick 0; per-call isolation keeps the siblings running."),
            ("terminal", "call_error with is_error: true for the failing call at tick 0."),
            ("collected", "Both siblings complete; batch ends partial_failure at tick 4 with 2 preserved, 1 errored, 0 dropped."),
        ],
        "in-flight": [
            ("issued", "The failing call is declared with a 3-tick duration."),
            ("in-flight", "The failure is raised at tick 3, mid-batch; per-call isolation keeps the siblings running."),
            ("terminal", "call_error with is_error: true for the failing call at tick 3."),
            ("collected", "Both siblings complete; batch ends partial_failure at tick 4 with 2 preserved, 1 errored, 0 dropped."),
        ],
        "at-completion": [
            ("issued", "The failing call is declared with a 4-tick duration, the same tick the last sibling completes."),
            ("in-flight", "The failure is raised at tick 4 as the last sibling completes."),
            ("terminal", "call_error with is_error: true for the failing call at tick 4."),
            ("collected", "Batch ends partial_failure at tick 4 with 2 preserved, 1 errored, 0 dropped."),
        ],
    },
    ("asyncio", "out-of-order"): {
        "at-issue": [
            ("issued", "Calls are issued in completion order (fast, mid, slow), so issue order and completion order agree."),
            ("in-flight", "Durations are 1, 3, and 5 ticks; calls complete in issue order."),
            ("terminal", "call_end events in order fast, mid, slow."),
            ("collected", "By-id and by-position collectors agree here (0 misattributed): position attribution only fails when completion order differs from issue order. 3 preserved, 0 dropped."),
        ],
        "in-flight": [
            ("issued", "Calls are issued slow, fast, mid."),
            ("in-flight", "Durations are 5, 1, and 3 ticks, so the calls complete fast, mid, slow."),
            ("terminal", "call_end events in completion order fast, mid, slow."),
            ("collected", "By-id collection attributes every result correctly (3 preserved); the position-based collector misattributes all 3."),
        ],
        "at-completion": [
            ("issued", "Calls are issued slow, fast, mid."),
            ("in-flight", "Calls complete fast, mid, slow."),
            ("terminal", "call_end events in completion order fast, mid, slow."),
            ("collected", "Attribution happens at collection time: by-id is correct (3 preserved); by-position misattributes all 3."),
        ],
    },
    ("threaded", "timeout"): {
        "at-issue": [
            ("issued", "Deadline of 2 ticks is scheduled when the slow call starts on a worker."),
            ("in-flight", "The deadline fires at tick 2 and frees the worker; the next queued call starts."),
            ("terminal", "call_timeout for the slow call at tick 2."),
            ("collected", "Siblings complete; batch ends partial_failure with 2 preserved, 1 errored, 0 dropped."),
        ],
        "in-flight": [
            ("issued", "Deadline of 5 ticks is scheduled when the slow call starts."),
            ("in-flight", "The deadline fires at tick 5, mid-batch, and frees the worker."),
            ("terminal", "call_timeout for the slow call at tick 5."),
            ("collected", "Siblings complete; batch ends partial_failure with 2 preserved, 1 errored, 0 dropped."),
        ],
        "at-completion": [
            ("issued", "Deadline of 20 ticks equals the slow call's 20-tick duration."),
            ("in-flight", "The completion event was scheduled before the deadline event, so at tick 20 the completion wins the tie."),
            ("terminal", "call_end for the slow call at tick 20: it completes instead of timing out."),
            ("collected", "Batch ends at tick 20 with status ok, 3 preserved, 0 errored, 0 dropped. Contrast the asyncio dispatcher, where the deadline wins the same tie and the call is reported timeout."),
        ],
    },
    ("threaded", "cancellation"): {
        "at-issue": [
            ("issued", "One worker, three calls: long-a starts, long-b and quick queue."),
            ("in-flight", "Cancel fires at tick 0: the two queued calls are cancelled; the running call's cancel is rejected (Future.cancel() returns False for a running call)."),
            ("terminal", "call_cancelled for long-b and quick; cancel_rejected for long-a."),
            ("collected", "long-a completes at tick 10; batch ends cancelled with 1 preserved, 2 errored, 0 dropped."),
        ],
        "in-flight": [
            ("issued", "One worker, three calls: long-a starts, long-b and quick queue."),
            ("in-flight", "Cancel fires at tick 4: the two queued calls are cancelled; the running call's cancel is rejected."),
            ("terminal", "call_cancelled for long-b and quick at tick 4; cancel_rejected for long-a."),
            ("collected", "long-a completes at tick 10; batch ends cancelled with 1 preserved, 2 errored, 0 dropped."),
        ],
        "at-completion": [
            ("issued", "One worker, three calls: long-a starts, long-b and quick queue."),
            ("in-flight", "long-a completes at tick 10 and long-b starts; cancel fires at tick 11, when long-b is running and quick is still queued."),
            ("terminal", "call_cancelled for quick at tick 11; cancel_rejected for long-b, which completes at tick 22."),
            ("collected", "Batch ends cancelled at tick 22 with 2 preserved, 1 errored, 0 dropped."),
        ],
    },
    ("threaded", "partial-failure"): {
        "at-issue": [
            ("issued", "The failing call is declared with a 0-tick duration; two workers."),
            ("in-flight", "The failure is raised at tick 0; per-future isolation keeps the siblings running."),
            ("terminal", "call_error with is_error: true for the failing call at tick 0; the worker is freed."),
            ("collected", "Both siblings complete; batch ends partial_failure at tick 4 with 2 preserved, 1 errored, 0 dropped."),
        ],
        "in-flight": [
            ("issued", "The failing call is declared with a 3-tick duration; two workers."),
            ("in-flight", "The failure is raised at tick 3, mid-batch; per-future isolation keeps the siblings running."),
            ("terminal", "call_error with is_error: true for the failing call at tick 3."),
            ("collected", "Both siblings complete; batch ends partial_failure at tick 4 with 2 preserved, 1 errored, 0 dropped."),
        ],
        "at-completion": [
            ("issued", "The failing call is declared with a 4-tick duration, the same tick the last sibling completes."),
            ("in-flight", "The failure is raised at tick 4 as the last sibling completes."),
            ("terminal", "call_error with is_error: true for the failing call at tick 4."),
            ("collected", "Batch ends partial_failure at tick 4 with 2 preserved, 1 errored, 0 dropped."),
        ],
    },
    ("threaded", "out-of-order"): {
        "at-issue": [
            ("issued", "Calls are issued in completion order (fast, mid, slow); three workers, so nothing queues."),
            ("in-flight", "Durations are 1, 3, and 5 ticks; calls complete in issue order."),
            ("terminal", "call_end events in order fast, mid, slow."),
            ("collected", "By-id and by-position collectors agree here (0 misattributed). 3 preserved, 0 dropped."),
        ],
        "in-flight": [
            ("issued", "Calls are issued slow, fast, mid; three workers."),
            ("in-flight", "Durations are 5, 1, and 3 ticks, so the calls complete fast, mid, slow."),
            ("terminal", "call_end events in completion order fast, mid, slow."),
            ("collected", "By-id collection attributes every result correctly (3 preserved); the position-based collector misattributes all 3."),
        ],
        "at-completion": [
            ("issued", "Calls are issued slow, fast, mid; three workers."),
            ("in-flight", "Calls complete fast, mid, slow."),
            ("terminal", "call_end events in completion order fast, mid, slow."),
            ("collected", "Attribution happens at collection time: by-id is correct (3 preserved); by-position misattributes all 3."),
        ],
    },
    ("shell", "timeout"): {
        "at-issue": [
            ("issued", "The slow job is wrapped in `timeout 0.2 sleep 5`."),
            ("in-flight", "The timeout kills the sleep after 0.2 s, before any sibling finishes; the job exits 124."),
            ("terminal", "call_timeout for the slow job (exit 124)."),
            ("collected", "Siblings complete; batch ends partial_failure with 2 preserved, 1 errored, 0 dropped."),
        ],
        "in-flight": [
            ("issued", "The slow job is wrapped in `timeout 0.6 sleep 5`."),
            ("in-flight", "The first sibling finishes at 0.3 s; the timeout kills the slow job at 0.6 s; the job exits 124."),
            ("terminal", "call_timeout for the slow job (exit 124)."),
            ("collected", "Batch ends partial_failure with 2 preserved, 1 errored, 0 dropped."),
        ],
        "at-completion": [
            ("issued", "The slow job is wrapped in `timeout 0.8 sleep 5`."),
            ("in-flight", "Both siblings finish (0.3 s, 0.7 s) before the timeout fires at 0.8 s; the job exits 124."),
            ("terminal", "call_timeout for the slow job (exit 124), after every other job completed."),
            ("collected", "Batch ends partial_failure with 2 preserved, 1 errored, 0 dropped."),
        ],
    },
    ("shell", "cancellation"): {
        "at-issue": [
            ("issued", "The faulted job is never started; it is recorded cancelled at issue time."),
            ("in-flight", "Only the two healthy jobs run."),
            ("terminal", "call_cancelled for the never-started job; call_end for both siblings."),
            ("collected", "Batch ends cancelled with 2 preserved, 1 errored, 0 dropped. Naive mode never records the job: dropped 1."),
        ],
        "in-flight": [
            ("issued", "Three jobs start; the faulted job sleeps 5 s."),
            ("in-flight", "The faulted job is killed with SIGTERM at 0.35 s and exits 143."),
            ("terminal", "call_cancelled for the killed job (exit 143)."),
            ("collected", "Batch ends cancelled with 2 preserved, 1 errored, 0 dropped. Naive mode never records the killed job: dropped 1."),
        ],
        "at-completion": [
            ("issued", "Three jobs start; the faulted job sleeps 0.9 s."),
            ("in-flight", "Both siblings finish (0.3 s, 0.7 s); the faulted job is killed with SIGTERM at 0.8 s, just before it would have finished, and exits 143."),
            ("terminal", "call_cancelled for the killed job (exit 143)."),
            ("collected", "Batch ends cancelled with 2 preserved, 1 errored, 0 dropped. Naive mode never records the killed job: dropped 1."),
        ],
    },
    ("shell", "partial-failure"): {
        "at-issue": [
            ("issued", "The faulted job exits 3 immediately, before any sibling finishes."),
            ("in-flight", "The failure is visible in the job's exit code as soon as it is reaped."),
            ("terminal", "call_error with the exit code for the faulted job."),
            ("collected", "Siblings complete; batch ends partial_failure with 2 preserved, 1 errored, 0 dropped. Naive mode ignores the exit code and records the failure as ok: 0 errors reported."),
        ],
        "in-flight": [
            ("issued", "The faulted job sleeps 0.5 s and then exits 3."),
            ("in-flight", "The first sibling finishes at 0.3 s; the faulted job fails at 0.5 s."),
            ("terminal", "call_error with the exit code for the faulted job."),
            ("collected", "Batch ends partial_failure with 2 preserved, 1 errored, 0 dropped. Naive mode ignores the exit code: 0 errors reported."),
        ],
        "at-completion": [
            ("issued", "The faulted job sleeps 0.7 s and then exits 3, at the same time the last sibling finishes."),
            ("in-flight", "The failure lands at completion time; it is visible only in the exit code."),
            ("terminal", "call_error with the exit code for the faulted job."),
            ("collected", "Batch ends partial_failure with 2 preserved, 1 errored, 0 dropped. Naive mode ignores the exit code: 0 errors reported."),
        ],
    },
    ("shell", "out-of-order"): {
        "at-issue": [
            ("issued", "Jobs are issued in shuffled order (gamma, alpha, beta) with sleeps 0.7, 0.5, 0.3 s."),
            ("in-flight", "Jobs complete in sleep order (beta, alpha, gamma), regardless of issue order."),
            ("terminal", "call_end events in completion order beta, alpha, gamma."),
            ("collected", "By-id collection attributes every output correctly (3 preserved); the position-based collector misattributes all 3."),
        ],
        "in-flight": [
            ("issued", "Jobs are issued alpha, beta, gamma with sleeps 0.5, 0.3, 0.7 s."),
            ("in-flight", "Jobs complete in sleep order (beta, alpha, gamma)."),
            ("terminal", "call_end events in completion order beta, alpha, gamma."),
            ("collected", "By-id collection attributes every output correctly (3 preserved); the position-based collector misattributes all 3."),
        ],
        "at-completion": [
            ("issued", "Jobs are issued alpha, beta, gamma with sleeps 0.5, 0.3, 0.7 s."),
            ("in-flight", "Jobs complete in sleep order (beta, alpha, gamma)."),
            ("terminal", "call_end events in completion order beta, alpha, gamma."),
            ("collected", "Attribution happens at collection time: by-id is correct (3 preserved); by-position misattributes all 3."),
        ],
    },
}

# Where a result can be lost, and where recovery happens, per component/fault.
LOSS_AND_RECOVERY = {
    ("asyncio", "timeout"): (
        "Without a per-call deadline the slow call holds the batch and no timeout is ever reported (naive mode: 0 errors reported, batch ends at tick 20).",
        "Schedule a per-call deadline at issue; report the timeout as a terminal record and free the batch to end on the siblings' schedule.",
    ),
    ("asyncio", "cancellation"): (
        "A cancel path that drops pending calls reports only the finished calls (naive mode: 1-3 dropped, 0 call_cancelled events).",
        "Emit one call_cancelled record per in-flight call at the cancel tick; report every issued call.",
    ),
    ("asyncio", "partial-failure"): (
        "Aborting the batch on the first error drops every call that has not finished yet (naive mode: 1-3 dropped, batch aborted).",
        "Isolate per-call errors (is_error: true) and let siblings complete; report the batch as partial_failure.",
    ),
    ("asyncio", "out-of-order"): (
        "Attributing results by completion position misattributes every result when completion order differs from issue order (naive mode: 3 misattributed at in-flight and at-completion).",
        "Match every result to its call by tool_call_id only; never zip completion-order results onto issue-order ids.",
    ),
    ("threaded", "timeout"): (
        "Without a per-call deadline the slow call holds its worker and the batch (naive mode: 0 errors reported, batch ends at tick 20).",
        "Schedule a per-call deadline when the call starts on a worker; the deadline frees the worker for the next queued call.",
    ),
    ("threaded", "cancellation"): (
        "A cancel path that drops queued calls reports only the calls that already finished (naive mode: 1-2 dropped, 0 call_cancelled events). A cancel of a running call cannot stop it at all: Future.cancel() returns False.",
        "Cancel queued calls (report each as cancelled) and let running calls finish; record the rejected cancel as cancel_rejected so the asymmetry is visible.",
    ),
    ("threaded", "partial-failure"): (
        "Aborting on the first error (shutdown with cancel_futures=True) drops queued calls and the still-running sibling (naive mode: 1-2 dropped, batch aborted).",
        "Let each future carry its own exception; siblings complete and the batch is reported partial_failure.",
    ),
    ("threaded", "out-of-order"): (
        "Attributing results by completion position misattributes every result when completion order differs from issue order (naive mode: 3 misattributed at in-flight and at-completion).",
        "Match every result to its call by tool_call_id only.",
    ),
    ("shell", "timeout"): (
        "Without the `timeout` wrapper the slow job runs to completion and no timeout is ever reported (naive mode: 0 errors reported).",
        "Wrap the job in `timeout N`; exit 124 is recorded as a call_timeout terminal record.",
    ),
    ("shell", "cancellation"): (
        "A killed job leaves only a partial output file; a collector that reads output files but not wait status never records it (naive mode: dropped 1).",
        "Record every job from its `wait -n` exit status (143 = killed), not from its output file; emit call_cancelled for the killed job.",
    ),
    ("shell", "partial-failure"): (
        "A collector that ignores exit codes records a failed job as ok (naive mode: 0 errors reported, failed output recorded as a good result).",
        "Assert the exit code for every job; a non-zero exit is a call_error record with is_error: true.",
    ),
    ("shell", "out-of-order"): (
        "Attributing outputs by completion position misattributes every output (naive mode: 3 misattributed at every point).",
        "Match every output file to its call by call id (the per-call .out file), never by completion position.",
    ),
}


def main() -> int:
    results = json.loads(
        (PACK_ROOT / "outputs" / "run1" / "matrix" / "matrix-results.json").read_text()
    )
    cells = []
    for cell in results["cells"]:
        key = (cell["component"], cell["fault"])
        stages = [
            {"stage": stage, "what_happens": text}
            for stage, text in NARRATIVES[key][cell["point"]]
        ]
        loss, recovery = LOSS_AND_RECOVERY[key]
        cells.append(
            {
                "component": cell["component"],
                "fault": cell["fault"],
                "point": cell["point"],
                "stages": stages,
                "measured_correct": cell["correct"],
                "measured_naive": cell["naive"],
                "where_results_can_be_lost": loss,
                "recovery": recovery,
                "evidence": {
                    "log": f"outputs/run1/matrix/{cell['component']}-{cell['fault']}-{cell['point']}.log",
                    "trace": f"outputs/run1/matrix/{cell['component']}-{cell['fault']}-{cell['point']}.trace.jsonl",
                    "naive_log": f"outputs/run1/matrix/{cell['component']}-{cell['fault']}-{cell['point']}-naive.log",
                },
            }
        )
    doc = {
        "description": (
            "Propagation map: for each fault type (timeout, cancellation, "
            "partial failure, out-of-order completion), each component "
            "(asyncio dispatcher, threaded dispatcher, shell runner), and "
            "each injection point (at-issue, in-flight, at-completion), what "
            "happens at each stage (issued -> in-flight -> terminal -> "
            "collected), where a result can be lost, and where recovery "
            "happens. Every stage description is drawn from the observed "
            "matrix cell run named in the cell's evidence; counts are the "
            "measured correct-mode and naive-mode results."
        ),
        "stages": ["issued", "in-flight", "terminal", "collected"],
        "components": ["asyncio", "threaded", "shell"],
        "faults": ["timeout", "cancellation", "partial-failure", "out-of-order"],
        "points": ["at-issue", "in-flight", "at-completion"],
        "timing_note": (
            "All ticks are virtual-clock ticks, not wall time, for the "
            "asyncio and threaded dispatchers. Shell times are real "
            "seconds; only completion order, exit codes, and output "
            "digests are asserted, never wall-clock thresholds."
        ),
        "deadline_tie_note": (
            "When a deadline equals a call's duration, the asyncio "
            "dispatcher reports a timeout (the deadline event is "
            "scheduled before the wake event) while the threaded "
            "dispatcher reports a completion (the completion event is "
            "scheduled before the deadline event). Both are deterministic "
            "and documented; a caller that assumes one convention for "
            "both components will misread the boundary."
        ),
        "cells": cells,
    }
    (PACK_ROOT / "propagation-map.json").write_text(
        json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"propagation-map: wrote propagation-map.json ({len(cells)} cells)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
