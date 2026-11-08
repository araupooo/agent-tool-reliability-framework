# Primary sources

Every specification-derived claim in this pack is backed by one of
the sources below, each read on 2026-11-08. Measured claims (ticks,
counts, digests) are backed by the unedited logs in `outputs/`, not
by these pages.

## 1. OpenAI — Function calling

URL: https://platform.openai.com/docs/guides/function-calling

- "The model may choose to call multiple functions in a single turn.
  You can prevent this by setting `parallel_tool_calls` to `false`,
  which ensures exactly zero or one tool is called." (Parallel
  function calling)
- "On supported models beginning with GPT-5, functions can be called
  in parallel when built-in tools are also available. Built-in tools
  cannot be included in a parallel function-call batch."
- Tool call outputs "should contain a reference to a specific model
  tool call (referenced by `call_id`)".
- The five-step tool-calling flow: request with tools → receive tool
  call → execute code with the call input → return the output →
  receive the final response.

Used for: the dispatcher rule that several calls may be issued in one
turn and that each result is matched to its call by `call_id`, never
by position (finding F-01).

## 2. Anthropic — Parallel tool use

URL: https://platform.claude.com/docs/en/agents-and-tools/tool-use/parallel-tool-use

- "When Claude calls tools, the response has a `stop_reason` of
  `tool_use` and can contain several `tool_use` blocks in a single
  assistant turn. How you run those calls is your decision. The API
  doesn't prescribe an execution order: you can run the calls
  concurrently (`Promise.all`, `asyncio.gather`), sequentially in the
  order they appear, or in any combination that suits your tools."
- "Whichever strategy you use, return one `tool_result` for each
  `tool_use` block, all together in the next user message. Match each
  result to its call with `tool_use_id`."
- "If you choose not to run a particular call ... still return a
  `tool_result` for it with `is_error: true` and a brief explanation."
- "To turn it off, set `disable_parallel_tool_use: true` inside the
  `tool_choice` object."
- Computer-use and browser-use batches "run them sequentially in the
  order they appear and stop at the first failure".

Used for: the dispatcher rule that every issued call gets exactly one
reported result, including cancelled and refused calls, and that
errors are reported per call with `is_error` (findings F-02, F-03).

## 3. Python — asyncio, Coroutines and Tasks

URL: https://docs.python.org/3/library/asyncio-task.html (Python 3.15.2 docs; local runtime is Python 3.12.3, same semantics)

- "`asyncio.gather()`: If `return_exceptions` is `False` (default),
  the first raised exception is immediately propagated to the task
  that awaits on `gather()`."
- "`asyncio.TaskGroup`: The first time any of the tasks belonging to
  the group fails with an exception other than `CancelledError`, the
  remaining tasks in the group are cancelled."
- "Tasks can easily and safely be cancelled. When a task is
  cancelled, `asyncio.CancelledError` will be raised in the task at
  the next opportunity."
- "`asyncio.timeout()`: If the operation takes more than the delay,
  the context manager will cancel the current task and handle the
  resulting `CancelledError` internally, transforming it into a
  `TimeoutError`."
- "`asyncio.wait_for()`: If a timeout occurs, it cancels the awaitable
  and raises `TimeoutError`."

Used for: the naive-gather failure mode in finding F-03 (abort on
first error) and the per-call deadline mechanism in finding F-04.

## 4. CorbanuTerminal — parallel tool runtime (source inspection)

Repository: https://github.com/CorbanuCore/CorbanuTerminal
File: `codex-rs/core/src/tools/parallel.rs`
Commit inspected: `7b5b368f896081c499c94d25faf4ea4b7ec44ca0`
(local clone; the file was read directly from the checked-out tree)

- Parallel-execution gate, line 161 (`parallel_execution:
  Arc<RwLock<()>>`) and lines 328–330: parallel-capable tools take a
  shared read lock, non-parallel tools take the exclusive write lock.
- Repeat caps, lines 37–47: `MAX_IDENTICAL_TOOL_CALLS_PER_TURN: u8 =
  3`, `MAX_CONSECUTIVE_IDENTICAL_TOOL_CALLS: u32 = 3`,
  `MAX_IDENTICAL_TOOL_RESULTS: u32 = 3`,
  `STOP_TURN_AFTER_REFUSED_REPEATS: u32 = 8`.
- Cancellation, lines 381–410: `tokio::select!` on the dispatch task
  versus the `CancellationToken`; the abort path records an aborted
  output per pending call (`PendingToolCalls::take_unrecorded_outputs`).
- Nested-call timing guard, lines 558–583 (`ToolCallTimingGuard::
  capture`): "Code-mode calls are nested within a direct code-mode
  tool call whose timing already includes them. Suppress nested guards
  so consumers do not mistake overlapping events for independent
  tool-call latency."
- Tests in the same file: `tool_call_timing_guard_ignores_code_mode_source`
  (line 665) asserts nested code-mode calls create no timing guard;
  `cancellation_before_dispatch_admission_logs_dispatch_only_timing`
  (line 709) asserts a cancelled-before-dispatch call logs
  dispatch-only timing.

Used for: the repeat cap (finding F-06), the nested timing guard
(finding F-05), and the cancellation reporting rule (finding F-02).
The Rust test suite for this file was not executed: the VM has ~600 MB
free memory and building the `codex-core` test binary exhausts it
(the same build failed with out-of-memory in earlier work on this
machine). The inspection above is source-level only and is labelled
as such in `findings.md`.

## 5. GNU Bash manual — `wait -n`

URL: https://www.gnu.org/software/bash/manual/bash.html
Local runtime: GNU bash 5.2.21.

- `wait -n [-p varname] [n ...]`: waits for the next job to
  terminate and returns its exit status; with `-p`, stores the job's
  process ID in `varname`. Verified against local behaviour: the
  shell runner records completion order from `wait -n -p` and asserts
  it against the manifest (see `shell.log`).

Used for: the shell runner's completion-order recording (claim C-08).

## 6. Python — concurrent.futures (ThreadPoolExecutor, Future)

URL: https://docs.python.org/3/library/concurrent.futures.html
Local runtime: Python 3.12.3; the semantics below were also read
directly from the local CPython source
(`Lib/concurrent/futures/_base.py`, `Future.cancel`) and confirmed
by the live probe (`components/threaded_dispatcher/probe_live.py`,
`outputs/run1/threaded-live-probe.log`).

- "`Future.cancel()`: Attempt to cancel the call. If the call is
  currently being executed or finished running and cannot be
  cancelled then the method will return `False`, otherwise the call
  will be cancelled and the method will return `True`." (Local
  source: `cancel()` returns False when `self._state` is RUNNING or
  FINISHED.)
- "`Executor.shutdown(wait=True, *, cancel_futures=False)`: If
  `cancel_futures` is `True`, this method will cancel all pending
  futures that the executor has not started running. Any futures that
  are completed or running won't be cancelled, regardless of the
  value of `cancel_futures`."
- "`wait(fs, timeout=None, ...)`: ... returns after `timeout`
  seconds without cancelling anything; the futures keep running.
  (The docs describe `wait` as returning the sets of done and
  not-done futures when the timeout expires; nothing in `wait`
  cancels the underlying work.)
- Each future carries its own exception: unlike
  `asyncio.TaskGroup`, one future's failure does not cancel its
  siblings; `future.exception()` returns it per future.

Used for: the threaded dispatcher's cancel rule (finding F-07: a
running call's cancel is rejected), the abort-on-first-error naive
mode (finding F-09: `shutdown(cancel_futures=True)` drops queued
calls), and the collector wait-timeout mode (finding F-08: `wait()`
times out without cancelling the work, so late results must be
reconciled).
