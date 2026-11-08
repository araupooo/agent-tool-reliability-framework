# Propagation map

How timeout, cancellation, partial failure, and out-of-order
completion propagate through three agent-tooling components — the
asyncio parallel tool dispatcher (`harness/`), the threaded
thread-pool dispatcher (`components/threaded_dispatcher/`), and the
shell parallel runner (`components/shell_runner/`).

The machine-readable version is `propagation-map.json` (one entry per
matrix cell, with measured counts and evidence paths). This document
summarises it. Every stage description below is drawn from an
observed matrix cell run (`scripts/run_matrix.py`, 36 cells,
`outputs/run1/matrix/`); nothing here is derived from theory alone.
All ticks are virtual-clock ticks, not wall time, for the asyncio and
threaded dispatchers. Shell times are real seconds; only completion
order, exit codes, and output digests are asserted, never
wall-clock thresholds.

## Stages

Each call moves through four stages:

1. **issued** — the call is submitted (and, for the threaded
   dispatcher, either starts on a worker or waits in the FIFO queue;
   for the shell runner, starts as a background job).
2. **in-flight** — the call is running; faults land here.
3. **terminal** — exactly one terminal record per issued call in
   correct mode: `ok`, `error`, `timeout`, `cancelled`, or `refused`.
4. **collected** — the batch result set is assembled from the terminal
   records, matched by `tool_call_id`.

## Timeout

| Component | at-issue | in-flight | at-completion |
|---|---|---|---|
| asyncio | Deadline 2 ticks; slow call cut at tick 2, siblings on schedule. Batch `partial_failure`, 2 preserved / 1 errored / 0 dropped. | Deadline 5 ticks; cut mid-batch at tick 5. Same counts. | Deadline 20 ticks = duration: the deadline event is scheduled before the wake, so the timeout wins the tie. Batch ends at tick 20, `partial_failure`, same counts. |
| threaded | Deadline 2 ticks; cut at tick 2 and the worker is freed for the next queued call. Same counts. | Deadline 5 ticks; cut mid-batch, worker freed. Same counts. | Deadline 20 ticks = duration: the completion event is scheduled before the deadline event, so the call **completes** instead of timing out. Batch ends at tick 20, status `ok`, 3 preserved / 0 errored / 0 dropped. |
| shell | `timeout 0.2 sleep 5`; killed before any sibling finishes, exit 124. Same counts. | `timeout 0.6 sleep 5`; first sibling finishes at 0.3 s, job killed at 0.6 s, exit 124. Same counts. | `timeout 0.8 sleep 5`; both siblings finish first, job killed at 0.8 s, exit 124. Same counts. |

**Where results are lost:** without a per-call deadline (or without
the `timeout` wrapper) the slow call holds the batch and no timeout
is ever reported — in every naive timeout cell across all three
components, 0 errors are reported and the slow call is recorded as
`ok`.

**Recovery:** schedule a per-call deadline at issue (asyncio /
threaded) or wrap the job in `timeout N` (shell); record the timeout
as a terminal record; the batch ends on the siblings' schedule.

## Cancellation

| Component | at-issue | in-flight | at-completion |
|---|---|---|---|
| asyncio | Cancel at tick 0: all 3 calls in flight, all cancelled. 0 preserved / 3 errored / 0 dropped. | Cancel at tick 4: quick call done at tick 3, both long calls cancelled. 1 / 2 / 0. | Cancel at tick 11: quick done at 3, long-a done at 10, long-b cancelled. 2 / 1 / 0. |
| threaded | Cancel at tick 0 (1 worker): long-a running, long-b + quick queued → cancelled; running call's cancel is **rejected** (`Future.cancel()` returns False). long-a completes at tick 10. 1 / 2 / 0. | Cancel at tick 4: same as at-issue — the queued calls are still queued. 1 / 2 / 0. | Cancel at tick 11: long-a done at 10, long-b running → rejected, completes at tick 22; quick still queued → cancelled. 2 / 1 / 0. |
| shell | Faulted job never started; recorded cancelled. 2 / 1 / 0. | Job killed with SIGTERM at 0.35 s, exit 143. 2 / 1 / 0. | Job killed with SIGTERM at 0.8 s, just before it would have finished at 0.9 s, exit 143. 2 / 1 / 0. |

**Where results are lost:** a cancel path that drops pending calls
reports only the finished calls — naive cancellation drops 1–3 calls
per cell in every component (asyncio 3/2/1, threaded 2/2/1, shell
1/1/1 across the three points). Separately, a cancel of a *running*
threaded call cannot stop it at all: it completes, and its result
arrives after the batch was reported cancelled unless the collector
reconciles late results (see the wait-timeout scenario, F-08).

**Recovery:** emit one `call_cancelled` record per cancelled call;
for the threaded dispatcher, record the rejected cancel of a running
call as `cancel_rejected` so the asymmetry is visible; report every
issued call.

## Partial failure

| Component | at-issue | in-flight | at-completion |
|---|---|---|---|
| asyncio | Failing call errors at tick 0; siblings complete. 2 / 1 / 0. | Failing call errors at tick 3, mid-batch. 2 / 1 / 0. | Failing call errors at tick 4, as the last sibling completes. 2 / 1 / 0. |
| threaded | Failing call errors at tick 0; worker freed; siblings complete. 2 / 1 / 0. | Failing call errors at tick 3. 2 / 1 / 0. | Failing call errors at tick 4. 2 / 1 / 0. |
| shell | Job exits 3 immediately. 2 / 1 / 0. | Job sleeps 0.5 s, then exits 3. 2 / 1 / 0. | Job sleeps 0.7 s, then exits 3, as the last sibling finishes. 2 / 1 / 0. |

**Where results are lost:** aborting the batch on the first error
drops every call that has not finished yet — naive abort drops 1–3
calls per cell in the asyncio and threaded dispatchers (asyncio
3/2/1, threaded 2/1/1). In the shell runner the equivalent failure
is ignoring exit codes: the failed job is recorded as `ok` and 0
errors are reported in every naive partial-failure cell.

**Recovery:** isolate per-call errors (`is_error: true`) and let
siblings complete; in the shell runner, assert the exit code of
every job — a non-zero exit is a `call_error` record.

## Out-of-order completion

| Component | at-issue | in-flight | at-completion |
|---|---|---|---|
| asyncio | Issued fast, mid, slow, so issue order equals completion order; by-id and by-position agree (0 misattributed). | Issued slow, fast, mid; complete fast, mid, slow. By-id correct (3 preserved); by-position misattributes all 3. | Same as in-flight; attribution happens at collection time. |
| threaded | Same as asyncio (3 workers, nothing queues). | Same as asyncio. | Same as asyncio. |
| shell | Issued in shuffled order (gamma, alpha, beta); complete beta, alpha, gamma. By-id correct; by-position misattributes all 3. | Issued alpha, beta, gamma; complete beta, alpha, gamma. Same. | Same as in-flight. |

**Where results are lost:** nothing is dropped — the failure is
misattribution, not loss. A position-based collector assigns the
first result to finish to the first call issued: 3 of 3 results
misattributed in every naive out-of-order cell except the asyncio /
threaded at-issue cells, where issue order happens to equal
completion order and the bug is invisible.

**Recovery:** match every result to its call by `tool_call_id`
only; in the shell runner, match each output file to its call by
call id, never by completion position.

## Cross-component differences worth knowing

1. **Deadline == duration tie.** The asyncio dispatcher reports a
   timeout; the threaded dispatcher reports a completion. Both are
   deterministic and follow from event-scheduling order (see the
   `deadline_tie_note` in `propagation-map.json`). A caller that
   assumes one convention for both components will misread the
   boundary. (Finding F-12.)
2. **Cancellation reach.** The asyncio dispatcher can cancel any
   in-flight call; the threaded dispatcher can cancel only queued
   calls — a running call's `Future.cancel()` returns False and the
   call runs to completion; the shell runner can kill a running job,
   but only with SIGTERM, which leaves a partial output file.
   (Findings F-02, F-07, F-10.)
3. **Collector timeout.** Only the threaded dispatcher models a
   collector that stops waiting (`wait_timeout_ticks`): `wait()` times
   out without cancelling the work, so results arrive late and must
   be reconciled — discarding them loses real results (F-08).
4. **Error isolation.** The asyncio and threaded dispatchers isolate
   per-call errors by default; the naive abort mode (TaskGroup /
   `shutdown(cancel_futures=True)`) drops unfinished calls in both.
   The shell runner has no equivalent: a failed job is visible only
   through its exit code. (Findings F-03, F-09.)

## Correct-mode totals (matrix, 36 cells)

36/36 cells pass their expected counts; correct-mode dropped total
is 0 across all 108 issued calls. Naive modes drop 24 calls in 15
cells, report 0 errors in 23 cells where the correct mode reports at
least one, and misattribute results in 7 cells. The corpus
(48 traces, 214 issued calls) replays byte-identically with 163
preserved, 51 errored, 0 dropped.
