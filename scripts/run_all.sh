#!/usr/bin/env bash
# Run every scenario, the corpus, the fault-injection matrix, and the
# independent replay checks.
# Usage: run_all.sh <out-dir>   (relative to the pack root)
# Every scenario prints only deterministic values (ticks, counts,
# digests) to stdout; logs are captured unedited under <out-dir>.
set -u
cd "$(dirname "$0")/.." || exit 1
export PYTHONHASHSEED=0
export LC_ALL=C
export PYTHONDONTWRITEBYTECODE=1

OUT_DIR="${1:-outputs/run1}"
mkdir -p "$OUT_DIR"
fail=0

run() { # run <log-name> <command...>
  local name="$1"; shift
  if "$@" > "$OUT_DIR/$name.log" 2>&1; then
    echo "ok: $name"
  else
    echo "FAIL: $name (see $OUT_DIR/$name.log)"
    fail=1
  fi
}

# --- reused asyncio scenarios (from the parallel-tool-replay-harness) ---
run basic python3 scenarios/run_basic.py --out-dir "$OUT_DIR"
run timeout python3 scenarios/run_timeout.py --out-dir "$OUT_DIR"
run cancellation python3 scenarios/run_cancellation.py --out-dir "$OUT_DIR"
run partial-failure python3 scenarios/run_partial_failure.py --out-dir "$OUT_DIR"
run out-of-order python3 scenarios/run_out_of_order.py --out-dir "$OUT_DIR"
run repeat-cap python3 scenarios/run_repeat_cap.py --out-dir "$OUT_DIR"
run nested-timing python3 scenarios/run_nested_timing.py --out-dir "$OUT_DIR"
run shell bash components/shell_runner/run_parallel.sh components/shell_runner/manifest.json "$OUT_DIR/shell"

# --- new threaded-dispatcher scenarios ---
run threaded-basic python3 scenarios/run_threaded_basic.py --out-dir "$OUT_DIR"
run threaded-cancel python3 scenarios/run_threaded_cancel.py --out-dir "$OUT_DIR"
run threaded-timeout python3 scenarios/run_threaded_timeout.py --out-dir "$OUT_DIR"
run threaded-partial python3 scenarios/run_threaded_partial.py --out-dir "$OUT_DIR"
run threaded-out-of-order python3 scenarios/run_threaded_out_of_order.py --out-dir "$OUT_DIR"
run threaded-wait-timeout python3 scenarios/run_threaded_wait_timeout.py --out-dir "$OUT_DIR"
run threaded-queue python3 scenarios/run_threaded_queue.py --out-dir "$OUT_DIR"
run threaded-live-probe python3 components/threaded_dispatcher/probe_live.py

# --- network-scale replay corpus (48 traces) ---
run corpus python3 scripts/run_corpus.py "$OUT_DIR"

# --- fault-injection matrix (36 cells, correct + naive modes) ---
run matrix python3 scripts/run_matrix.py "$OUT_DIR"

# --- independent replay checks through the replayer CLIs ---
for batch in batch-basic batch-timeout batch-cancel batch-partial batch-reorder batch-repeat batch-nested; do
  run "replay-$batch" python3 -m harness.replayer "fixtures/$batch.json" "$OUT_DIR/$batch.trace.jsonl"
done
run replay-timeout-naive python3 -m harness.replayer --no-timeout fixtures/batch-timeout.json "$OUT_DIR/batch-timeout-naive.trace.jsonl"
run replay-cancel-naive python3 -m harness.replayer --naive-cancel fixtures/batch-cancel.json "$OUT_DIR/batch-cancel-naive.trace.jsonl"
run replay-partial-naive python3 -m harness.replayer --naive-gather fixtures/batch-partial.json "$OUT_DIR/batch-partial-naive.trace.jsonl"
run replay-nested-unguarded python3 -m harness.replayer --unguarded-nested fixtures/batch-nested.json "$OUT_DIR/batch-nested-unguarded.trace.jsonl"

for batch in batch-threaded-basic batch-threaded-cancel batch-threaded-timeout batch-threaded-partial batch-threaded-reorder batch-threaded-queue batch-threaded-wait-timeout; do
  run "replay-$batch" python3 -m components.threaded_dispatcher.replayer "fixtures/$batch.json" "$OUT_DIR/$batch.trace.jsonl"
done
run replay-threaded-cancel-naive python3 -m components.threaded_dispatcher.replayer --naive-cancel fixtures/batch-threaded-cancel.json "$OUT_DIR/batch-threaded-cancel-naive.trace.jsonl"
run replay-threaded-timeout-naive python3 -m components.threaded_dispatcher.replayer --no-timeout fixtures/batch-threaded-timeout.json "$OUT_DIR/batch-threaded-timeout-naive.trace.jsonl"
run replay-threaded-partial-naive python3 -m components.threaded_dispatcher.replayer --naive-abort fixtures/batch-threaded-partial.json "$OUT_DIR/batch-threaded-partial-naive.trace.jsonl"
run replay-threaded-wait-timeout-naive python3 -m components.threaded_dispatcher.replayer --naive-late-drop fixtures/batch-threaded-wait-timeout.json "$OUT_DIR/batch-threaded-wait-timeout-naive.trace.jsonl"

# Shell replay: re-run into a fixed scratch dir (same path in every run,
# so the log stays reproducible) and compare traces byte-for-byte.
SHELL_REPLAY_DIR="/tmp/atrf-shell-replay"
rm -rf "$SHELL_REPLAY_DIR"
run shell-replay bash components/shell_runner/run_parallel.sh components/shell_runner/manifest.json "$SHELL_REPLAY_DIR"
if cmp -s "$OUT_DIR/shell/batch-shell.trace.jsonl" "$SHELL_REPLAY_DIR/batch-shell.trace.jsonl"; then
  echo "ok: shell trace replay is byte-identical"
  printf 'shell replay: MATCH (byte-identical)\n' > "$OUT_DIR/replay-shell.log"
else
  echo "FAIL: shell trace replay differs"
  printf 'shell replay: MISMATCH\n' > "$OUT_DIR/replay-shell.log"
  diff "$OUT_DIR/shell/batch-shell.trace.jsonl" "$SHELL_REPLAY_DIR/batch-shell.trace.jsonl" >> "$OUT_DIR/replay-shell.log" || true
  fail=1
fi
rm -rf "$SHELL_REPLAY_DIR"

if (( fail == 0 )); then
  echo "run_all: ALL PASS"
else
  echo "run_all: FAILURES PRESENT"
  exit 1
fi
