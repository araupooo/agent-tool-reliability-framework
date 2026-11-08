# Trace schema

One JSON object per line (JSONL), keys sorted alphabetically, no
insignificant whitespace. Each event carries `seq` (0-based, emission
order) and `tick` (integer virtual-clock tick at emission time).
`tick` is never a wall-clock reading: for the asyncio and threaded
dispatchers it is the virtual clock; for the shell runner it is the
completion-order index (0 = first to finish).

## Batch events

| `type` | Fields | Meaning |
|---|---|---|
| `batch_start` | `batch_id`, `call_ids` | Batch begins; `call_ids` in issue order. The threaded dispatcher also records `max_workers`. |
| `batch_end` | `batch_id`, `status`, `completed`, `failed`, `cancelled`, `refused` | Batch ends. `status` is `ok`, `partial_failure`, `cancelled`, or `aborted`. The threaded dispatcher also records `collection_closed_tick` (null unless a collector `wait()` timeout closed the batch), `late_results_reconciled`, and `values_lost`. |
| `batch_aborted` | `batch_id`, `status="aborted"`, `failed_tool_call_id`, `error` | Naive path only: the first error aborted the batch. |

## Per-call events

| `type` | Fields | Meaning |
|---|---|---|
| `call_start` | `tool_call_id`, `tool`, `args_digest`, `parent_tool_call_id` | Call dispatched. `args_digest` is the SHA-256 of the canonical JSON of the call arguments. `parent_tool_call_id` is null for top-level calls. The threaded dispatcher also records `worker_slot`. |
| `call_queued` | `tool_call_id`, `tool`, `queue_position` | Threaded dispatcher only: the call waits in the FIFO queue because all workers are busy. |
| `call_end` | `tool_call_id`, `status="ok"`, `result_digest`, `parent_tool_call_id` | Call completed. `result_digest` is the SHA-256 of the result string (or of the raw stdout file for the shell runner). |
| `call_error` | `tool_call_id`, `status="error"`, `error`, `is_error=true` | Call failed with a declared error; siblings continue. |
| `call_timeout` | `tool_call_id`, `status="timeout"`, `deadline_ticks` | Call cancelled at its per-call deadline. The shell runner records `timeout_seconds` instead of `deadline_ticks`; the threaded dispatcher records `reason` instead when the timeout is a collector `wait()` timeout. |
| `call_cancelled` | `tool_call_id`, `status="cancelled"` | Call cancelled by a batch cancellation. |
| `cancel_rejected` | `tool_call_id`, `status="running"`, `reason` | Threaded dispatcher only: a cancel of a running call was rejected (`Future.cancel()` returns False for a running call). |
| `call_late_result` | `tool_call_id`, `status="ok"` or `"discarded"`, `result_digest`, `collection_closed_tick` | Threaded dispatcher only: a call completed after the collector's `wait()` timeout closed the batch. Correct mode reconciles it (`ok`); naive mode discards it (`discarded`, value lost). |
| `call_refused` | `tool_call_id`, `tool`, `args_digest`, `status="refused"`, `reason` | Identical call refused without running (cap: 3 identical calls per turn). |

## Determinism rules

- Event order is fully determined by (tick, insertion sequence).
- Arguments and results are identified by SHA-256 digests, never by
  position: completion order may differ from issue order.
- The replayer re-runs the same batch manifest through the same
  dispatcher and requires the emitted trace to be byte-identical to
  the recorded trace.

## Example (abridged, `batch-basic`)

```json
{"batch_id":"batch-basic","call_ids":["call-read-1","call-fetch-1","call-hash-1"],"seq":0,"tick":0,"type":"batch_start"}
{"args_digest":"c3cf6dfe...","parent_tool_call_id":null,"seq":1,"tick":0,"tool":"read_file","tool_call_id":"call-read-1","type":"call_start"}
{"result_digest":"78ff1bf5...","seq":4,"status":"ok","tick":1,"tool_call_id":"call-hash-1","type":"call_end"}
{"batch_id":"batch-basic","cancelled":0,"completed":3,"failed":0,"refused":0,"seq":7,"status":"ok","tick":4,"type":"batch_end"}
```
