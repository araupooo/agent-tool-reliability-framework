# Agent Tool Reliability Framework

Map timeout, cancellation, and partial-failure propagation across
three agent-tooling components, replay a network-scale corpus
deterministically, and measure recovery with deterministic
assertions for preserved results, reported errors, and zero silent
drops. Every assertion is deterministic: a virtual clock replaces
wall-clock time, so a replayed trace is byte-identical to the
recorded one.

## What is reused, what is new, and what was assessed

**Reused verbatim from the published parallel-tool-replay-harness**
(https://github.com/araupooo/parallel-tool-replay-harness) — the
record/replay core this framework extends rather than recreates:

- `harness/` — virtual clock, asyncio dispatcher, recorder,
  replayer (unchanged).
- `scenarios/run_basic.py`, `run_timeout.py`, `run_cancellation.py`,
  `run_partial_failure.py`, `run_out_of_order.py`,
  `run_repeat_cap.py`, `run_nested_timing.py` (unchanged) and the
  `fixtures/batch-*.json` manifests they consume (unchanged).
- `components/shell_runner/run_parallel.sh`,
  `manifest_to_tsv.py`, `manifest.json` (unchanged).
- The trace schema, the digest scripts, and findings F-01–F-06 from
  that pack (re-verified here by re-running the same scenarios; see
  `findings.md`).

**New in this framework:**

- `components/threaded_dispatcher/` — a third inspected component:
  a thread-pool dispatcher modelling Python's
  `concurrent.futures.ThreadPoolExecutor` on the same virtual clock
  (worker slots, FIFO queue, `Future.cancel()` semantics,
  collector `wait()` timeout with late-result reconciliation), plus a
  live probe (`probe_live.py`) that confirms the modelled semantics
  against a real `ThreadPoolExecutor`.
- `corpus/` — a network-scale replay corpus: 48 generated batch
  manifests (24 asyncio, 24 threaded; 2–8 calls each; faults none /
  timeout / cancel / error / reorder / mixed), 214 issued calls in
  total, every trace replayable deterministically.
- `matrix/` + `scripts/run_matrix.py` — a fault-injection matrix:
  4 fault types × 3 components × 3 injection points = 36 cells,
  each run in correct mode and in the matching naive mode, with
  per-cell preserved / errored / dropped counts.
- `propagation-map.md` + `propagation-map.json` — what happens at
  each stage (issued → in-flight → terminal → collected) for every
  fault, component, and injection point, derived from the observed
  matrix runs.
- `scripts/run_corpus.py`, `scripts/build_propagation_map.py` —
  corpus and propagation-map runners.
- Findings F-07–F-12 (threaded cancel/timeout behaviour, the
  deadline-tie difference, shell kill handling, and the
  position-attribution failure in the threaded dispatcher).

**Assessed, not built:** CorbanuTerminal's parallel tool runtime
(`codex-rs/core/src/tools/parallel.rs`, commit
`7b5b368f896081c499c94d25faf4ea4b7ec44ca0`) — the
parallel-execution gate, `CancellationToken` abort path, nested-call
timing guard, and the 3-per-turn identical-call cap — by source
inspection only; its Rust test suite was not executed (see
`findings.md` for the environment limitation).

## Components validated

1. **Asyncio parallel tool dispatcher** (`harness/`) — a Python
   `asyncio` dispatcher that runs batches of parallel tool calls on a
   virtual clock, records a canonical JSONL trace, and replays it.
   Semantics follow the OpenAI function-calling guide (multiple calls
   per turn, matched by `call_id`) and the Anthropic parallel-tool-use
   guide (one `tool_result` per `tool_use` block, matched by
   `tool_use_id`, `is_error` for calls that were not run).
2. **Shell parallel runner** (`components/shell_runner/`) — the same
   trace schema applied to real concurrent shell processes
   (background jobs, `wait -n` completion order, exit codes, output
   digests).
3. **Threaded thread-pool dispatcher**
   (`components/threaded_dispatcher/`) — models
   `concurrent.futures.ThreadPoolExecutor`: at most `max_workers`
   calls run at once, further calls wait in a FIFO queue, a queued
   call can be cancelled (`Future.cancel()` returns True) while a
   running call cannot (returns False), a collector `wait()` timeout
   does not stop the work (results arrive late), and
   `shutdown(cancel_futures=True)` cancels only pending futures.
   The modelled semantics were verified against the CPython 3.12.3
   source (`Lib/concurrent/futures/_base.py`, `Future.cancel`) and a
   live probe using a real `ThreadPoolExecutor`
   (`components/threaded_dispatcher/probe_live.py`,
   `outputs/run1/threaded-live-probe.log`).

## Layout

- `harness/` — virtual clock, asyncio dispatcher, recorder, replayer (reused).
- `scenarios/` — fourteen runnable scenarios: the seven reused
  asyncio scenarios plus seven threaded scenarios (basic, timeout,
  cancellation, partial failure, out-of-order completion, collector
  wait timeout, queue saturation).
- `fixtures/` — batch manifests (tool calls, durations in ticks,
  faults) consumed by the scenarios, including the seven new
  `batch-threaded-*.json` manifests.
- `components/shell_runner/` — shell parallel runner, manifest, and
  the fault-injection matrix cell driver (`run_matrix_cell.sh`).
- `components/threaded_dispatcher/` — thread-pool dispatcher,
  recorder, replayer, live probe.
- `corpus/` — 48 generated batch manifests + `index.json`
  (deterministic seed 20261108; regenerate with
  `python3 scripts/generate_corpus.py`).
- `matrix/matrix.json` — the 36-cell fault-injection matrix
  definition with expected correct-mode counts.
- `propagation-map.md`, `propagation-map.json` — the propagation
  map (human-readable and machine-readable).
- `scripts/run_all.sh` — runs every scenario, the corpus, the
  matrix, and every replay check.
- `scripts/run_corpus.py` — records and replays the 48-trace corpus.
- `scripts/run_matrix.py` — runs the 36-cell matrix (correct +
  naive modes).
- `scripts/build_propagation_map.py` — builds
  `propagation-map.json` from the measured matrix results.
- `scripts/clean_reproduce.sh` — clean-environment reproduction check.
- `outputs/` — unedited logs and traces from the two recorded runs
  (`run1`, `run2-clean`). Never edit these; verify with `SHA256SUMS`.
- `findings.md` — severity-ranked findings, each with a reproducible trigger.
- `citations.md` — primary sources for every specification-derived claim.
- `claims.md` — claim-to-evidence index (each claim → script + output + digest).
- `docs/index.html` — rendered findings page (self-contained; GitHub Pages reads `docs/`).

## Measured results

- Matrix, correct mode: 36/36 cells pass; 0 silent drops across
  108 issued calls. Naive modes drop 24 calls in 15 cells, report
  0 errors in 23 cells where the correct mode reports at least one,
  and misattribute results in 7 cells.
- Corpus: 48 traces, 214 issued calls, 163 preserved, 51 errored,
  0 dropped; every trace replays byte-identically.
- Scenarios: 7 asyncio + 7 threaded, all passing, all traces
  replayed byte-identically (including the naive variants).
- Findings: 12 severity-ranked (3 High, 5 Medium, 4 Low) — F-01–F-06
  reused and re-verified, F-07–F-12 new. Correct-mode behaviour is
  clean in every component: no silent drops were found in correct
  mode, and the findings document the naive-mode gaps and two
  cross-component boundary differences (deadline tie, cancel reach).

## Reproduce

```sh
bash scripts/run_all.sh outputs/run1
bash scripts/clean_reproduce.sh   # second run from a fresh copy; must match run1 byte-for-byte
bash scripts/verify_digests.sh    # verifies SHA256SUMS
```

Requirements: Python 3.11+ (standard library only), GNU bash 5.1+
(`wait -n -p`), `sha256sum`, GNU `timeout`. No network access, no
credentials, no wall-clock assertions anywhere: all timing is in
integer virtual ticks for the asyncio and threaded dispatchers and
in completion-order indices for the shell runner. (The live probe
uses real short sleeps but asserts only boolean outcomes and
completion order, never a wall-clock threshold.)

## Trace schema

See `TRACE_SCHEMA.md`. One JSON object per line, keys sorted,
`seq` assigned in emission order, `tick` in virtual ticks. The
replayer asserts byte equality between the recorded and replayed trace.
