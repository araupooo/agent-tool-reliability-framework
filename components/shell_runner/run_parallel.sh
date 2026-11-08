#!/usr/bin/env bash
# Shell parallel runner: runs the manifest's commands concurrently as
# background jobs, records the true completion order via `wait -n`,
# and emits a canonical trace (same schema as the asyncio dispatcher).
#
# Usage: run_parallel.sh <manifest.json> <out-dir>
#   <out-dir> receives batch-shell.trace.jsonl, completion_order.txt,
#   and one raw output file per call under out/.
#
# Assertions are deterministic: completion order, exit codes, and
# SHA-256 digests of the raw outputs. No wall-clock thresholds.
set -u
cd "$(dirname "$0")/../.." || exit 1

MANIFEST="$1"
OUT_DIR="$2"
mkdir -p "$OUT_DIR/out"
: > "$OUT_DIR/completion_order.txt"

fail=0
say() { printf '%s\n' "$*"; }

# --- read the manifest (python3 only parses JSON; execution is bash) ---
BATCH_ID=""
EXPECTED_ORDER=""
declare -a CALL_IDS=()
declare -A CMD_B64=() OUT_B64=() EXPECT_EXIT=()
while IFS=$'\t' read -r kind f1 f2 f3 f4; do
  case "$kind" in
    BATCH) BATCH_ID="$f1" ;;
    ORDER) EXPECTED_ORDER="$f1" ;;
    CALL)
      CALL_IDS+=("$f1")
      CMD_B64["$f1"]="$f2"
      OUT_B64["$f1"]="$f3"
      EXPECT_EXIT["$f1"]="$f4"
      ;;
  esac
done < <(python3 components/shell_runner/manifest_to_tsv.py "$MANIFEST")

say "== scenario: shell parallel runner =="
say "batch: $BATCH_ID"

# --- run all commands concurrently ---
declare -A PID_TO_CALL=()
for cid in "${CALL_IDS[@]}"; do
  cmd="$(printf '%s' "${CMD_B64[$cid]}" | base64 -d)"
  bash -c "$cmd" > "$OUT_DIR/out/$cid.out" 2> "$OUT_DIR/out/$cid.err" &
  PID_TO_CALL[$!]="$cid"
done

# `wait -n -p` reports jobs in true completion order.
remaining=${#CALL_IDS[@]}
declare -a COMPLETION_ORDER=()
declare -A EXIT_CODE=()
while (( remaining > 0 )); do
  wait -n -p finished_pid
  status=$?
  cid="${PID_TO_CALL[$finished_pid]}"
  COMPLETION_ORDER+=("$cid")
  EXIT_CODE["$cid"]=$status
  printf '%s\n' "$cid" >> "$OUT_DIR/completion_order.txt"
  remaining=$((remaining - 1))
done

actual_order="$(IFS=,; printf '%s' "${COMPLETION_ORDER[*]}")"
say "completion_order: $actual_order"
if [[ "$actual_order" == "$EXPECTED_ORDER" ]]; then
  say "PASS: completion order matches the manifest"
else
  say "FAIL: completion order differs from the manifest -- expected $EXPECTED_ORDER"
  fail=1
fi

# --- per-call checks: exit code and output digest ---
for cid in "${CALL_IDS[@]}"; do
  # NOTE: compute the expected digest through a pipe, not command
  # substitution, so the trailing newline of the expected output is kept.
  expected_digest="$(printf '%s' "${OUT_B64[$cid]}" | base64 -d | sha256sum | cut -d' ' -f1)"
  actual_digest="$(sha256sum "$OUT_DIR/out/$cid.out" | cut -d' ' -f1)"
  if [[ "${EXIT_CODE[$cid]}" == "${EXPECT_EXIT[$cid]}" ]]; then
    say "PASS: $cid exit code ${EXIT_CODE[$cid]}"
  else
    say "FAIL: $cid exit code ${EXIT_CODE[$cid]}, expected ${EXPECT_EXIT[$cid]}"
    fail=1
  fi
  if [[ "$actual_digest" == "$expected_digest" ]]; then
    say "PASS: $cid output digest matches"
  else
    say "FAIL: $cid output digest differs -- got $actual_digest"
    fail=1
  fi
done

# --- canonical trace (same schema as the asyncio dispatcher) ---
TRACE="$OUT_DIR/batch-shell.trace.jsonl"
: > "$TRACE"
seq=0
emit() { printf '%s\n' "$1" >> "$TRACE"; seq=$((seq + 1)); }
ids_json="$(printf '"%s",' "${CALL_IDS[@]}" | sed 's/,$//')"
emit "{\"batch_id\":\"$BATCH_ID\",\"call_ids\":[$ids_json],\"seq\":$seq,\"tick\":0,\"type\":\"batch_start\"}"
for cid in "${CALL_IDS[@]}"; do
  cmd="$(printf '%s' "${CMD_B64[$cid]}" | base64 -d)"
  args_digest="$(printf '%s' "$cmd" | sha256sum | cut -d' ' -f1)"
  emit "{\"args_digest\":\"$args_digest\",\"parent_tool_call_id\":null,\"seq\":$seq,\"tick\":0,\"tool\":\"shell\",\"tool_call_id\":\"$cid\",\"type\":\"call_start\"}"
done
tick=0
for cid in "${COMPLETION_ORDER[@]}"; do
  result_digest="$(sha256sum "$OUT_DIR/out/$cid.out" | cut -d' ' -f1)"
  if [[ "${EXIT_CODE[$cid]}" == "0" ]]; then
    emit "{\"result_digest\":\"$result_digest\",\"seq\":$seq,\"status\":\"ok\",\"tick\":$tick,\"tool_call_id\":\"$cid\",\"type\":\"call_end\"}"
  else
    emit "{\"error\":\"exit code ${EXIT_CODE[$cid]}\",\"is_error\":true,\"seq\":$seq,\"status\":\"error\",\"tick\":$tick,\"tool_call_id\":\"$cid\",\"type\":\"call_error\"}"
  fi
  tick=$((tick + 1))
done
n_ok=0; n_err=0
for cid in "${CALL_IDS[@]}"; do
  if [[ "${EXIT_CODE[$cid]}" == "0" ]]; then n_ok=$((n_ok + 1)); else n_err=$((n_err + 1)); fi
done
if (( n_err > 0 )); then batch_status="partial_failure"; else batch_status="ok"; fi
emit "{\"batch_id\":\"$BATCH_ID\",\"cancelled\":0,\"completed\":$n_ok,\"failed\":$n_err,\"refused\":0,\"seq\":$seq,\"status\":\"$batch_status\",\"tick\":$((tick - 1)),\"type\":\"batch_end\"}"
trace_sha="$(sha256sum "$TRACE" | cut -d' ' -f1)"
say "status: $batch_status"
say "events: $seq"
say "trace_sha256: $trace_sha"
say "trace_file: $TRACE"

if (( fail == 0 )); then
  say "scenario shell: ALL PASS"
else
  say "scenario shell: FAILURES PRESENT"
  exit 1
fi
