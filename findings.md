# Findings

Severity-ranked findings from the validation runs. Every finding was
reproduced by a runnable script; the "Trigger" line is the exact
command (run from the pack root), and "Evidence" names the unedited
log and trace files. All ticks are virtual-clock ticks, not wall time.

## F-01 — High — Position-based result attribution misattributes outputs on out-of-order completion

**Trigger:** `python3 scenarios/run_out_of_order.py --out-dir outputs/run1`

**Observed:** Three calls are issued in order slow (5 ticks), fast
(1 tick), mid (3 ticks) and complete in order fast, mid, slow. A
collector that pairs completion-order results with issue-order call
ids — the position-based collector in `harness/dispatcher.py`
(`collect_by_position`) — assigns `fast-result` to the slow call,
`mid-result` to the fast call, and `slow-result` to the mid call: all
three results are misattributed.

**Expected:** Results are matched to calls by `tool_call_id` only
(OpenAI: each output references its `call_id`; Anthropic: each result
is matched by `tool_use_id`). The by-id collector
(`collect_by_id`) returns the correct mapping for all three calls.

**Evidence:** `outputs/run1/out-of-order.log`,
`outputs/run1/batch-reorder.trace.jsonl`
(`trace_sha256` in the log). Replay: byte-identical
(`outputs/run1/replay-batch-reorder.log`).

## F-02 — High — Cancellation that drops pending calls leaves issued calls without a result

**Trigger:** `python3 scenarios/run_cancellation.py --out-dir outputs/run1`

**Observed:** Batch cancelled at tick 4 with two calls in flight. The
naive cancel path (`--naive-cancel`) reports only 1 of 3 results: the
two cancelled calls are missing from the result set entirely, and no
`call_cancelled` event is emitted.

**Expected:** One result per issued call (Anthropic: "return one
`tool_result` for each `tool_use` block ... If you choose not to run a
particular call ... still return a `tool_result` for it with
`is_error: true`"). The correct path emits `call_cancelled` for both
in-flight calls at tick 4, reports 3/3 results, and ends the batch at
tick 4 with status `cancelled`. This matches CorbanuTerminal's abort
path, which records an aborted output per pending call
(`PendingToolCalls::take_unrecorded_outputs`).

**Evidence:** `outputs/run1/cancellation.log`,
`outputs/run1/batch-cancel.trace.jsonl`,
`outputs/run1/batch-cancel-naive.trace.jsonl`. Replay:
byte-identical (`outputs/run1/replay-batch-cancel.log`,
`outputs/run1/replay-cancel-naive.log`).

## F-03 — Medium — Aborting the batch on the first error loses later siblings' results

**Trigger:** `python3 scenarios/run_partial_failure.py --out-dir outputs/run1`

**Observed:** The middle call fails at tick 3 with a declared
`upstream 503` error. The naive gather path (`--naive-gather`,
abort-on-first-error, the `asyncio.TaskGroup` pattern: "the first
time any of the tasks belonging to the group fails ... the remaining
tasks in the group are cancelled") aborts the batch: status `aborted`,
`batch_aborted` emitted, and only 1 of 3 results reported — the later
sibling (4 ticks) never reports.

**Expected:** Per-call error isolation: the failing call is reported
with `is_error: true`, both siblings complete, batch status
`partial_failure`, 3/3 results reported, batch ends at tick 4.

**Evidence:** `outputs/run1/partial-failure.log`,
`outputs/run1/batch-partial.trace.jsonl`,
`outputs/run1/batch-partial-naive.trace.jsonl`. Replay:
byte-identical (`outputs/run1/replay-batch-partial.log`,
`outputs/run1/replay-partial-naive.log`).

## F-04 — Medium — No per-call deadline lets a slow call hold the whole batch

**Trigger:** `python3 scenarios/run_timeout.py --out-dir outputs/run1`

**Observed:** One call has a 20-tick duration. Without a per-call
deadline (`--no-timeout`) the batch ends at tick 20, held by the
slowest call, and no timeout is reported.

**Expected:** With a 5-tick deadline the slow call is cut at tick 5
(`call_timeout` carrying `deadline_ticks: 5`), both siblings finish on
schedule, and the batch ends at tick 6 with status `partial_failure`.

**Evidence:** `outputs/run1/timeout.log`,
`outputs/run1/batch-timeout.trace.jsonl`,
`outputs/run1/batch-timeout-naive.trace.jsonl`. Replay:
byte-identical (`outputs/run1/replay-batch-timeout.log`,
`outputs/run1/replay-timeout-naive.log`).

## F-05 — Low — Unguarded nested-call timing events overlap the outer call and double-count latency

**Trigger:** `python3 scenarios/run_nested_timing.py --out-dir outputs/run1`

**Observed:** An outer call (ticks 0–6) runs a nested sub-call
(ticks 2–5). Without the timing guard (`--unguarded-nested`) the
nested call emits its own `call_start`/`call_end` events strictly
inside the outer interval; a consumer summing per-call durations
counts the nested 3 ticks twice.

**Expected:** Nested calls are excluded from timing events
(CorbanuTerminal `ToolCallTimingGuard::capture`: "Suppress nested
guards so consumers do not mistake overlapping events for independent
tool-call latency"; test `tool_call_timing_guard_ignores_code_mode_source`).
The guarded trace contains zero events with `parent_tool_call_id`
set, while the outer call still returns its own result.

**Evidence:** `outputs/run1/nested-timing.log`,
`outputs/run1/batch-nested.trace.jsonl`,
`outputs/run1/batch-nested-unguarded.trace.jsonl`. Replay:
byte-identical (`outputs/run1/replay-batch-nested.log`,
`outputs/run1/replay-nested-unguarded.log`).

## F-06 — Low — Unbounded identical-call repetition loops without a per-turn cap

**Trigger:** `python3 scenarios/run_repeat_cap.py --out-dir outputs/run1`

**Observed:** Four identical calls (same tool, same arguments) are
issued. The fourth is refused without running: a `call_refused`
event names the cap ("identical tool call refused after 3 identical
calls in one turn"), the refused call never starts, and it is still
reported in the result set (4/4 reported, batch status
`partial_failure`).

**Expected:** The cap mirrors CorbanuTerminal's
`MAX_IDENTICAL_TOOL_CALLS_PER_TURN: u8 = 3` (`parallel.rs` line 37):
repeating an identical call cannot produce new information, so the
dispatcher refuses it instead of looping.

**Evidence:** `outputs/run1/repeat-cap.log`,
`outputs/run1/batch-repeat.trace.jsonl`. Replay: byte-identical
(`outputs/run1/replay-batch-repeat.log`).

## Reference implementation notes (source inspection, not executed)

CorbanuTerminal `codex-rs/core/src/tools/parallel.rs`, commit
`7b5b368f896081c499c94d25faf4ea4b7ec44ca0`:

- Parallel-execution gate (lines 161, 328–330): parallel-capable
  tools share a read lock; non-parallel tools take the exclusive write
  lock. The harness models this as: every call in a batch runs
  concurrently unless the batch is declared sequential.
- Cancellation (lines 381–410): `tokio::select!` between the dispatch
  task and the `CancellationToken`; the abort path still records an
  output per pending call. The harness models this in F-02.
- Repeat caps (lines 37–47): 3 identical calls / 3 consecutive
  identical calls / 3 identical results per turn; the turn stops
  after 8 refused repeats. The harness models the 3-call cap in F-06.
- Nested timing guard (lines 558–583) and its tests (lines 665, 709).
  The harness models the guard in F-05.

The Rust unit tests in that file were **not** executed: the VM has
~600 MB free memory and building the `codex-core` test binary
exhausts it (the same build failed out-of-memory in earlier work on
this machine). This is a stated environment limitation, not a passing
result. The shell runner (`components/shell_runner/`) is the second
runnable validation component instead: four real concurrent processes
complete in the manifest's declared order with matching output
digests (see `outputs/run1/shell.log`).

## F-07 — High — Threaded cancel cannot stop a running call: only queued calls are cancelled

**Trigger:** `python3 scenarios/run_threaded_cancel.py --out-dir outputs/run1`

**Observed:** One worker, three calls, cancel at tick 4. The running
call's cancel is rejected — a `cancel_rejected` event records
"`Future.cancel()` returns False for a running call" — and the call
completes normally at tick 10. The two queued calls are cancelled
and reported (`call_cancelled`, tick 4). Correct mode reports 3/3
results, 0 dropped. The live probe confirms the same behaviour
against a real `ThreadPoolExecutor`: `cancel()` on a queued future
returns True, on a running future returns False
(`outputs/run1/threaded-live-probe.log`).

**Expected:** Per the `concurrent.futures` docs, "`Future.cancel()`:
If the call is currently being executed or finished running and
cannot be cancelled then the method will return `False`". A caller
that assumes a batch cancel stops in-flight threaded work will be
wrong: the work finishes and its result arrives after the batch was
reported cancelled.

**Evidence:** `outputs/run1/threaded-cancel.log`,
`outputs/run1/batch-threaded-cancel.trace.jsonl`,
`outputs/run1/batch-threaded-cancel-naive.trace.jsonl` (naive mode
drops both cancelled calls: 1/3 reported). Replay: byte-identical
(`outputs/run1/replay-batch-threaded-cancel.log`,
`outputs/run1/replay-threaded-cancel-naive.log`). Matrix cells
`threaded/cancellation/*` (`outputs/run1/matrix/`): correct mode
0 dropped in all three points; naive mode drops 2/2/1.

## F-08 — Medium — A collector `wait()` timeout does not cancel the work: late results are lost if discarded

**Trigger:** `python3 scenarios/run_threaded_wait_timeout.py --out-dir outputs/run1`

**Observed:** Two calls (8 and 10 ticks), collector stops waiting at
tick 4. Both calls are recorded `timeout` at tick 4, keep running,
and complete late at ticks 8 and 10 (`call_late_result` events).
Correct mode reconciles both late results (flagged `late: true`):
2/2 preserved, 0 values lost, batch ends at tick 10. The naive
collector discards late results: 0/2 preserved, 2 values lost, batch
ends `partial_failure` — the results existed and were observed, but
never recorded.

**Expected:** `concurrent.futures.wait(..., timeout=...)` returns
after the timeout without cancelling anything. A collector that
stops at the timeout must reconcile results that arrive afterwards,
or it silently loses real results. This is a values-lost failure,
not a dropped-call failure: the calls were reported (as timeouts)
but their values were not preserved.

**Evidence:** `outputs/run1/threaded-wait-timeout.log`,
`outputs/run1/batch-threaded-wait-timeout.trace.jsonl`,
`outputs/run1/batch-threaded-wait-timeout-naive.trace.jsonl`.
Replay: byte-identical
(`outputs/run1/replay-batch-threaded-wait-timeout.log`,
`outputs/run1/replay-threaded-wait-timeout-naive.log`).

## F-09 — Medium — Aborting on the first error drops queued and running threaded calls

**Trigger:** `python3 scenarios/run_threaded_partial.py --out-dir outputs/run1`

**Observed:** The middle call fails at tick 3 with a declared
`upstream 503` error. The naive abort path (first error shuts the
executor down with `cancel_futures=True`, the `asyncio.TaskGroup`
pattern applied to a thread pool) aborts the batch: status
`aborted`, only 2 of 3 results reported — the still-running sibling
(4 ticks) never reports, even though it had already started.

**Expected:** Per-call error isolation, as in the asyncio
dispatcher: the failing call is reported with `is_error: true`,
both siblings complete, batch status `partial_failure`, 3/3
results reported, batch ends at tick 4. Note the difference from
F-03: in the threaded dispatcher the abort also drops a call that
was already running, because the abort stops the batch before the
running call's result is collected.

**Evidence:** `outputs/run1/threaded-partial.log`,
`outputs/run1/batch-threaded-partial.trace.jsonl`,
`outputs/run1/batch-threaded-partial-naive.trace.jsonl`. Replay:
byte-identical (`outputs/run1/replay-batch-threaded-partial.log`,
`outputs/run1/replay-threaded-partial-naive.log`). Matrix cells
`threaded/partial-failure/*`: naive mode drops 2/1/1 across the
three points.

## F-10 — Medium — A killed shell job leaves only a partial output: without wait-status handling it is silently dropped

**Trigger:** `bash components/shell_runner/run_matrix_cell.sh cancellation in-flight <out-dir>`

**Observed:** The faulted job sleeps 5 s and is killed with SIGTERM
at 0.35 s; it exits 143 and leaves a partial (empty) output file.
Correct mode records it from the `wait -n` exit status as
`call_cancelled` (exit 143): 2 preserved, 1 errored, 0 dropped. The
naive collector, which reads output files but not wait status, never
records the killed job: dropped 1. The same holds at the other two
injection points (never-started job, job killed just before it
would have finished): naive mode drops 1 in all three shell
cancellation cells.

**Expected:** Every job is recorded from its `wait -n` exit status,
not from its output file: 124 is a timeout, 143 is a cancellation,
any other non-zero exit is an error. An output file alone cannot
distinguish "killed" from "produced no output".

**Evidence:** `outputs/run1/matrix/shell-cancellation-in-flight.log`
and `outputs/run1/matrix/shell-cancellation-in-flight-naive.log`
(naive: dropped 1),
`outputs/run1/matrix/shell-cancellation-in-flight/cell.trace.jsonl`.

## F-11 — Low — Position-based attribution misattributes outputs in the threaded dispatcher too

**Trigger:** `python3 scenarios/run_threaded_out_of_order.py --out-dir outputs/run1`

**Observed:** Three calls are issued slow (5 ticks), fast (1 tick),
mid (3 ticks) with three workers and complete fast, mid, slow. The
by-id collector returns the correct result for every call; the
position-based collector assigns `fast-result` to the slow call,
`mid-result` to the fast call, and `slow-result` to the mid call:
all three misattributed. The failure is not specific to asyncio or
to any queueing behaviour — it follows from completion order
differing from issue order, which the thread pool also produces.

**Expected:** Results are matched to calls by `tool_call_id` only.
The same misattribution appears in the matrix cells
`threaded/out-of-order/in-flight` and
`threaded/out-of-order/at-completion` (3 misattributed each) and in
all three shell out-of-order cells; it is invisible in the at-issue
cells, where issue order happens to equal completion order.

**Evidence:** `outputs/run1/threaded-out-of-order.log`,
`outputs/run1/batch-threaded-reorder.trace.jsonl`. Replay:
byte-identical (`outputs/run1/replay-batch-threaded-reorder.log`).

## F-12 — Low — Deadline == duration: asyncio reports a timeout, the threaded dispatcher reports a completion

**Trigger:** `python3 scripts/run_matrix.py outputs/run1 (cells asyncio/timeout/at-completion and threaded/timeout/at-completion)`

**Observed:** A call with a 20-tick duration and a 20-tick deadline.
The asyncio dispatcher reports it `timeout` at tick 20 (the
deadline event is scheduled before the call's wake event, so the
deadline wins the tie); the batch ends `partial_failure` at tick 20.
The threaded dispatcher reports it `ok` at tick 20 (the completion
event is scheduled before the deadline event, so the completion wins
the tie); the batch ends `ok` at tick 20. Both runs are
deterministic and replay byte-identically; nothing is dropped in
either (2 preserved / 1 errored / 0 dropped vs 3 preserved /
0 errored / 0 dropped).

**Expected:** Neither behaviour is wrong on its own — each follows
from its event-scheduling order — but a caller that assumes one
convention for both components will misread the boundary: the same
manifest yields a timeout in one component and a completion in the
other. The boundary must be documented per component (see the
`deadline_tie_note` in `propagation-map.json`) and tested explicitly
rather than assumed.

**Evidence:** `outputs/run1/matrix/asyncio-timeout-at-completion.log`
(`trace_sha256`
`78abdd38c7e64be6ea8318d7540bd0f2f986c9bc1029509836177fb09bb0591e`)
and `outputs/run1/matrix/threaded-timeout-at-completion.log`
(`trace_sha256`
`f0453064fb4b538aa4e1e4f9aa00610aa114ce5024d01953928ac40f61ed18a7`).

## Validation summary

- 7 asyncio batches recorded and replayed byte-identically, plus 4
  naive-variant traces replayed byte-identically
  (`outputs/run1/replay-*.log`).
- 7 threaded batches recorded and replayed byte-identically, plus 4
  naive-variant traces replayed byte-identically
  (`outputs/run1/replay-batch-threaded-*.log`,
  `outputs/run1/replay-threaded-*-naive.log`).
- Live probe: a real `ThreadPoolExecutor` confirms the modelled
  cancel/wait/shutdown semantics (boolean outcomes only, no
  wall-clock thresholds) (`outputs/run1/threaded-live-probe.log`).
- Shell runner: 4 real parallel processes, completion order matches
  the manifest, exit codes and output digests match, trace replayed
  byte-identically (`outputs/run1/shell.log`,
  `outputs/run1/replay-shell.log`).
- Network-scale corpus: 48 traces (24 asyncio, 24 threaded), 214
  issued calls, 163 preserved, 51 errored, 0 dropped; every trace
  replays byte-identically (`outputs/run1/corpus/corpus-summary.json`).
- Fault-injection matrix: 36 cells (4 faults x 3 components x 3
  injection points), correct mode 36/36 pass with 0 silent drops;
  naive modes drop 24 calls in 15 cells, report 0 errors in 23
  cells where correct mode reports at least one, and misattribute
  results in 7 cells
  (`outputs/run1/matrix/matrix-results.json`).
- Correct-mode behaviour is clean in every component: no silent
  drops were found in correct mode. The findings document the
  naive-mode gaps and two cross-component boundary differences
  (deadline tie, F-12; cancel reach, F-02/F-07/F-10).
- Clean-environment reproduction from a fresh copy:
  `outputs/run2-clean/` is byte-identical to `outputs/run1/`
  (`outputs/run2-clean/clean-reproduce.log`).
- Digests: `SHA256SUMS` (verify with `bash scripts/verify_digests.sh`).
