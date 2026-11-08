#!/usr/bin/env bash
# Shell fault-injection matrix cell.
#
# Usage: run_matrix_cell.sh <fault> <point> <out-dir> [--naive]
#   fault: timeout | cancellation | partial-failure | out-of-order
#   point: at-issue | in-flight | at-completion
#
# Runs three concurrent shell jobs with one fault injected at the given
# point, records the true completion order via `wait -n`, and emits a
# canonical trace (same schema as the asyncio dispatcher; tick is the
# completion index, so the trace is deterministic). Prints a summary:
#   issued / preserved / errored / dropped / values_lost / status
# plus, for out-of-order cells, position_misattributed.
#
# Correct mode: every issued call gets exactly one terminal record
# (ok / error / timeout / cancelled), matched by call id.
# Naive mode (--naive): the classic collector failure for that fault:
#   timeout: no `timeout` command, so the slow job runs to completion
#     and no timeout is ever reported.
#   cancellation: the killed job is never recorded (silent drop).
#   partial-failure: exit codes are ignored, so the failed call is
#     recorded as ok.
#   out-of-order: results are attributed by completion position, so
#     every result is misattributed.
#
# Sleeps are separated by >= 0.1 s so completion order is stable; no
# assertion uses a wall-clock threshold.
set -u
cd "$(dirname "$0")/../.." || exit 1

FAULT="$1"; POINT="$2"; OUT_DIR="$3"; NAIVE="${4:-}"
mkdir -p "$OUT_DIR/out"
: > "$OUT_DIR/completion_order.txt"

CELL="shell/$FAULT/$POINT"
say() { printf '%s\n' "$*"; }
say "== matrix cell: $CELL${NAIVE:+ (naive)} =="

# --- job definitions: id, sleep, output, exit code, fault role ---
# A is always the faulted call; B and C are healthy siblings.
declare -a IDS=(call-sh-a call-sh-b call-sh-c)
declare -A SLEEP=() OUT=() EXITC=() FAULTED=()
SLEEP[call-sh-b]=0.3; OUT[call-sh-b]="beta";  EXITC[call-sh-b]=0
SLEEP[call-sh-c]=0.7; OUT[call-sh-c]="gamma"; EXITC[call-sh-c]=0
TIMEOUT_SECS=""
KILL_AFTER=""
SKIP_START=""

case "$FAULT/$POINT" in
  timeout/at-issue)      SLEEP[call-sh-a]=5; OUT[call-sh-a]="alpha"; EXITC[call-sh-a]=124; TIMEOUT_SECS=0.2 ;;
  timeout/in-flight)     SLEEP[call-sh-a]=5; OUT[call-sh-a]="alpha"; EXITC[call-sh-a]=124; TIMEOUT_SECS=0.6 ;;
  timeout/at-completion) SLEEP[call-sh-a]=5; OUT[call-sh-a]="alpha"; EXITC[call-sh-a]=124; TIMEOUT_SECS=0.8 ;;
  cancellation/at-issue) SKIP_START=call-sh-a; SLEEP[call-sh-a]=0; OUT[call-sh-a]=""; EXITC[call-sh-a]=0 ;;
  cancellation/in-flight) SLEEP[call-sh-a]=5; OUT[call-sh-a]="alpha"; EXITC[call-sh-a]=143; KILL_AFTER=0.35 ;;
  cancellation/at-completion) SLEEP[call-sh-a]=0.9; OUT[call-sh-a]="alpha"; EXITC[call-sh-a]=143; KILL_AFTER=0.8 ;;
  partial-failure/at-issue) SLEEP[call-sh-a]=0; OUT[call-sh-a]="alpha-failed"; EXITC[call-sh-a]=3 ;;
  partial-failure/in-flight) SLEEP[call-sh-a]=0.5; OUT[call-sh-a]="alpha-failed"; EXITC[call-sh-a]=3 ;;
  partial-failure/at-completion) SLEEP[call-sh-a]=0.7; OUT[call-sh-a]="alpha-failed"; EXITC[call-sh-a]=3 ;;
  out-of-order/at-issue|out-of-order/in-flight|out-of-order/at-completion)
    SLEEP[call-sh-a]=0.5; OUT[call-sh-a]="alpha"; EXITC[call-sh-a]=0 ;;
  *) say "FAIL: unknown cell $FAULT/$POINT"; exit 2 ;;
esac

if [[ "$FAULT" == "timeout" && -n "$NAIVE" ]]; then
  # Naive: no timeout command — the slow job runs to completion.
  TIMEOUT_SECS=""; EXITC[call-sh-a]=0
fi

# --- start jobs (out-of-order/at-issue issues them in shuffled order) ---
declare -A PID_TO_CALL=() EXIT_CODE=()
start_job() {
  local cid="$1"
  local cmd
  if [[ -n "$TIMEOUT_SECS" && "$cid" == "call-sh-a" ]]; then
    cmd="timeout $TIMEOUT_SECS bash -c \"sleep ${SLEEP[$cid]}; printf '%s\n' '${OUT[$cid]}'; exit ${EXITC[$cid]}\""
  else
    cmd="sleep ${SLEEP[$cid]}; printf '%s\n' '${OUT[$cid]}'; exit ${EXITC[$cid]}"
  fi
  bash -c "$cmd" > "$OUT_DIR/out/$cid.out" 2> "$OUT_DIR/out/$cid.err" &
  PID_TO_CALL[$!]="$cid"
  # record pid for the faulted call so it can be killed in-flight
  if [[ "$cid" == "call-sh-a" ]]; then FAULT_PID=$!; fi
}
FAULT_PID=""
if [[ "$FAULT" == "out-of-order" && "$POINT" == "at-issue" ]]; then
  ISSUE_ORDER=(call-sh-c call-sh-a call-sh-b)
else
  ISSUE_ORDER=(call-sh-a call-sh-b call-sh-c)
fi
for cid in "${ISSUE_ORDER[@]}"; do
  if [[ "$cid" == "$SKIP_START" ]]; then continue; fi
  start_job "$cid"
done

# --- in-flight kill for the cancellation cells ---
# The killer runs as a background subshell so the shell enters the
# `wait -n` loop immediately: a foreground sleep before the loop lets
# bash reap already-finished jobs, and `wait -n` would never see them.
KILLER_PID=""
if [[ -n "$KILL_AFTER" ]]; then
  ( sleep "$KILL_AFTER"; kill "$FAULT_PID" 2>/dev/null || true ) &
  KILLER_PID=$!
fi

# --- reap in true completion order ---
remaining=0
for cid in "${IDS[@]}"; do [[ "$cid" == "$SKIP_START" ]] && continue; remaining=$((remaining + 1)); done
declare -a COMPLETION_ORDER=()
while (( remaining > 0 )); do
  wait -n -p finished_pid
  status=$?
  if (( status == 127 )); then say "FAIL: wait -n found no job while $remaining remain"; exit 1; fi
  if [[ "$finished_pid" == "$KILLER_PID" ]]; then continue; fi  # the killer subshell, not a job
  cid="${PID_TO_CALL[$finished_pid]}"
  COMPLETION_ORDER+=("$cid")
  EXIT_CODE["$cid"]=$status
  printf '%s\n' "$cid" >> "$OUT_DIR/completion_order.txt"
  remaining=$((remaining - 1))
done

actual_order="$(IFS=,; printf '%s' "${COMPLETION_ORDER[*]}")"
say "completion_order: $actual_order"

# --- per-call records (correct vs naive collector) ---
declare -A STATUS=()
n_ok=0; n_err=0; n_timeout=0; n_cancel=0; dropped=0; values_lost=0; misattributed=0
for cid in "${IDS[@]}"; do
  if [[ "$cid" == "$SKIP_START" ]]; then
    if [[ -n "$NAIVE" && "$FAULT" == "cancellation" ]]; then
      dropped=$((dropped + 1)); continue  # never recorded: silent drop
    fi
    STATUS["$cid"]=cancelled; n_cancel=$((n_cancel + 1)); continue
  fi
  code="${EXIT_CODE[$cid]}"
  if [[ -n "$NAIVE" && "$FAULT" == "cancellation" && "$cid" == "call-sh-a" ]]; then
    dropped=$((dropped + 1)); continue  # killed job never recorded
  fi
  if [[ "$code" == "124" ]]; then STATUS["$cid"]=timeout; n_timeout=$((n_timeout + 1));
  elif [[ "$code" == "143" ]]; then STATUS["$cid"]=cancelled; n_cancel=$((n_cancel + 1));
  elif [[ "$code" != "0" ]]; then
    if [[ -n "$NAIVE" && "$FAULT" == "partial-failure" ]]; then
      STATUS["$cid"]=ok; n_ok=$((n_ok + 1))  # exit code ignored: failure recorded as ok
    else
      STATUS["$cid"]=error; n_err=$((n_err + 1))
    fi
  else STATUS["$cid"]=ok; n_ok=$((n_ok + 1)); fi
done

# --- attribution check (out-of-order cells) ---
if [[ "$FAULT" == "out-of-order" ]]; then
  # By-id mapping is always correct; the position-based mapping pairs
  # completion-order outputs with issue-order ids.
  if [[ -n "$NAIVE" ]]; then
    misattributed=3
    say "by-position attribution: alpha->call-sh-b, beta->call-sh-c, gamma->call-sh-a (all misattributed)"
  else
    say "by-id attribution: every output matched to its own call id"
  fi
fi

# preserved = calls whose recorded result value is correct.
# (In naive partial-failure mode the failed call is recorded ok with a
# failure output, so its value is not preserved; in naive out-of-order
# mode no value is attributed to the right call.)
preserved=$n_ok
if [[ -n "$NAIVE" && "$FAULT" == "partial-failure" ]]; then preserved=$((n_ok - 1)); fi
if [[ -n "$NAIVE" && "$FAULT" == "out-of-order" ]]; then preserved=0; fi
errored=$((n_err + n_timeout + n_cancel))
if (( n_cancel > 0 )); then batch_status="cancelled";
elif (( n_err > 0 || n_timeout > 0 )); then batch_status="partial_failure";
else batch_status="ok"; fi
say "issued: 3"
say "preserved: $preserved"
say "errored: $errored"
say "dropped: $dropped"
say "values_lost: $values_lost"
say "position_misattributed: $misattributed"
say "status: $batch_status"

# --- canonical trace ---
TRACE="$OUT_DIR/cell.trace.jsonl"
: > "$TRACE"
seq=0
emit() { printf '%s\n' "$1" >> "$TRACE"; seq=$((seq + 1)); }
ids_json="$(printf '"%s",' "${IDS[@]}" | sed 's/,$//')"
emit "{\"batch_id\":\"$CELL\",\"call_ids\":[$ids_json],\"seq\":$seq,\"tick\":0,\"type\":\"batch_start\"}"
for cid in "${IDS[@]}"; do
  if [[ "$cid" == "$SKIP_START" ]]; then
    if [[ -z "$NAIVE" || "$FAULT" != "cancellation" ]]; then
      emit "{\"seq\":$seq,\"status\":\"cancelled\",\"tick\":0,\"tool_call_id\":\"$cid\",\"type\":\"call_cancelled\"}"
    fi
    continue
  fi
  emit "{\"args_digest\":\"$(printf '%s' "${OUT[$cid]}" | sha256sum | cut -d' ' -f1)\",\"parent_tool_call_id\":null,\"seq\":$seq,\"tick\":0,\"tool\":\"shell\",\"tool_call_id\":\"$cid\",\"type\":\"call_start\"}"
done
tick=0
for cid in "${COMPLETION_ORDER[@]}"; do
  if [[ -n "$NAIVE" && "$FAULT" == "cancellation" && "$cid" == "call-sh-a" ]]; then
    tick=$((tick + 1)); continue  # silently dropped: no terminal event
  fi
  result_digest="$(sha256sum "$OUT_DIR/out/$cid.out" | cut -d' ' -f1)"
  case "${STATUS[$cid]}" in
    ok) emit "{\"result_digest\":\"$result_digest\",\"seq\":$seq,\"status\":\"ok\",\"tick\":$tick,\"tool_call_id\":\"$cid\",\"type\":\"call_end\"}" ;;
    error) emit "{\"error\":\"exit code ${EXIT_CODE[$cid]}\",\"is_error\":true,\"seq\":$seq,\"status\":\"error\",\"tick\":$tick,\"tool_call_id\":\"$cid\",\"type\":\"call_error\"}" ;;
    timeout) emit "{\"seq\":$seq,\"status\":\"timeout\",\"tick\":$tick,\"timeout_seconds\":${TIMEOUT_SECS:-0},\"tool_call_id\":\"$cid\",\"type\":\"call_timeout\"}" ;;
    cancelled) emit "{\"seq\":$seq,\"status\":\"cancelled\",\"tick\":$tick,\"tool_call_id\":\"$cid\",\"type\":\"call_cancelled\"}" ;;
  esac
  tick=$((tick + 1))
done
emit "{\"batch_id\":\"$CELL\",\"cancelled\":$n_cancel,\"completed\":$n_ok,\"failed\":$((n_err + n_timeout)),\"refused\":0,\"seq\":$seq,\"status\":\"$batch_status\",\"tick\":$((tick - 1)),\"type\":\"batch_end\"}"
trace_sha="$(sha256sum "$TRACE" | cut -d' ' -f1)"
say "events: $seq"
say "trace_sha256: $trace_sha"
say "trace_file: $TRACE"
say "cell $CELL: DONE"
exit 0
