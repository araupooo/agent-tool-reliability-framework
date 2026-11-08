"""Build the claim-to-evidence index (claims.md + evidence.json).

Reads the unedited run logs under outputs/run1 and outputs/run2-clean,
extracts each trace's SHA-256 from its log, hashes each log file, and
writes claims.md (human-readable index) and evidence.json
(machine-readable copy). Deterministic: no timestamps, sorted output.
Run from the pack root: python3 scripts/build_claims.py
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[1]
RUN1 = PACK_ROOT / "outputs" / "run1"
RUN2 = PACK_ROOT / "outputs" / "run2-clean"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def trace_sha_from_log(log_path: Path) -> str:
    text = log_path.read_text(encoding="utf-8")
    match = re.search(r"^(?:replay_)?trace_sha256: ([0-9a-f]{64})$", text, re.MULTILINE)
    return match.group(1) if match else ""


# (claim id, statement, script, primary log, trace files)
CLAIMS = [
    (
        "C-01",
        "All seven asyncio batches record and replay byte-identically, "
        "and all four naive-variant traces replay byte-identically.",
        "scripts/run_all.sh (scenarios/run_*.py, harness/replayer.py)",
        "replay-batch-basic.log",
        ["batch-basic.trace.jsonl"],
    ),
    (
        "C-02",
        "A per-call deadline cuts the slow call at tick 5; the batch "
        "ends at tick 6 on the siblings' schedule (status partial_failure).",
        "scenarios/run_timeout.py",
        "timeout.log",
        ["batch-timeout.trace.jsonl", "batch-timeout-naive.trace.jsonl"],
    ),
    (
        "C-03",
        "Batch cancellation at tick 4 reports a result for every issued "
        "call (3/3): the quick call completed, the two in-flight calls "
        "are reported cancelled.",
        "scenarios/run_cancellation.py",
        "cancellation.log",
        ["batch-cancel.trace.jsonl", "batch-cancel-naive.trace.jsonl"],
    ),
    (
        "C-04",
        "A failing call is isolated: reported with is_error, both "
        "siblings complete, batch status partial_failure, 3/3 results.",
        "scenarios/run_partial_failure.py",
        "partial-failure.log",
        ["batch-partial.trace.jsonl", "batch-partial-naive.trace.jsonl"],
    ),
    (
        "C-05",
        "Out-of-order completions (fast, mid, slow) are matched to "
        "calls by tool_call_id; position-based matching misattributes "
        "all three results.",
        "scenarios/run_out_of_order.py",
        "out-of-order.log",
        ["batch-reorder.trace.jsonl"],
    ),
    (
        "C-06",
        "The fourth identical call in one turn is refused without "
        "running (cap 3); it is still reported (4/4 results).",
        "scenarios/run_repeat_cap.py",
        "repeat-cap.log",
        ["batch-repeat.trace.jsonl"],
    ),
    (
        "C-07",
        "The nested-call timing guard emits no nested timing events; "
        "without the guard the nested interval (ticks 2..5) lies "
        "strictly inside the outer interval (ticks 0..6).",
        "scenarios/run_nested_timing.py",
        "nested-timing.log",
        ["batch-nested.trace.jsonl", "batch-nested-unguarded.trace.jsonl"],
    ),
    (
        "C-08",
        "The shell runner's four real parallel processes complete in "
        "the manifest's declared order with matching exit codes and "
        "output digests; its trace replays byte-identically.",
        "components/shell_runner/run_parallel.sh",
        "shell.log",
        ["shell/batch-shell.trace.jsonl"],
    ),
    (
        "C-09",
        "A clean-environment reproduction from a fresh copy produces "
        "outputs byte-identical to the first run.",
        "scripts/clean_reproduce.sh",
        "run2-clean/clean-reproduce.log",
        [],
    ),
    (
        "C-10",
        "The harness rules match the inspected reference implementation "
        "(CorbanuTerminal parallel.rs, commit 7b5b368f): parallel gate, "
        "cancellation reporting, nested timing guard, 3-call repeat cap.",
        "source inspection (see citations.md section 4)",
        "findings.md",
        [],
    ),
    (
        "C-11",
        "All seven threaded batches record and replay byte-identically, "
        "and all four naive-variant traces replay byte-identically.",
        "scripts/run_all.sh (scenarios/run_threaded_*.py, "
        "components/threaded_dispatcher/replayer.py)",
        "replay-batch-threaded-basic.log",
        ["batch-threaded-basic.trace.jsonl"],
    ),
    (
        "C-12",
        "Threaded cancellation at tick 4 cancels the two queued calls "
        "and rejects the running call's cancel (Future.cancel() returns "
        "False); 3/3 results are reported, 0 dropped.",
        "scenarios/run_threaded_cancel.py",
        "threaded-cancel.log",
        ["batch-threaded-cancel.trace.jsonl", "batch-threaded-cancel-naive.trace.jsonl"],
    ),
    (
        "C-13",
        "A collector wait() timeout at tick 4 does not stop the work: "
        "both calls complete late (ticks 8, 10) and are reconciled "
        "(2/2 preserved); discarding late results loses both values.",
        "scenarios/run_threaded_wait_timeout.py",
        "threaded-wait-timeout.log",
        ["batch-threaded-wait-timeout.trace.jsonl", "batch-threaded-wait-timeout-naive.trace.jsonl"],
    ),
    (
        "C-14",
        "The network-scale corpus replays byte-identically: 48 traces, "
        "214 issued calls, 163 preserved, 51 errored, 0 dropped.",
        "scripts/run_corpus.py",
        "corpus.log",
        ["corpus/corpus-summary.json"],
    ),
    (
        "C-15",
        "The fault-injection matrix passes 36/36 correct-mode cells "
        "(4 faults x 3 components x 3 injection points) with 0 silent "
        "drops across 108 issued calls.",
        "scripts/run_matrix.py",
        "matrix.log",
        ["matrix/matrix-results.json"],
    ),
    (
        "C-16",
        "The propagation map covers all 36 matrix cells with stage "
        "narratives, measured correct/naive counts, and evidence paths.",
        "scripts/build_propagation_map.py",
        "matrix/matrix-results.json",
        ["propagation-map.json"],
    ),
    (
        "C-17",
        "A live probe against a real ThreadPoolExecutor confirms the "
        "modelled semantics: cancel() returns True for a queued future "
        "and False for a running one; wait(timeout) cancels nothing; "
        "shutdown(cancel_futures=True) cancels queued futures only.",
        "components/threaded_dispatcher/probe_live.py",
        "threaded-live-probe.log",
        [],
    ),
    (
        "C-18",
        "Deadline == duration behaves differently per component: the "
        "asyncio dispatcher reports a timeout, the threaded dispatcher "
        "reports a completion (both deterministic, both replayed "
        "byte-identically).",
        "scripts/run_matrix.py (cells asyncio/threaded timeout at-completion)",
        "matrix.log",
        ["matrix/asyncio-timeout-at-completion.trace.jsonl",
         "matrix/threaded-timeout-at-completion.trace.jsonl"],
    ),
]


def main() -> int:
    rows = []
    evidence = {"claims": []}
    for claim_id, statement, script, log_name, traces in CLAIMS:
        if log_name.startswith("run2-clean/"):
            log_path = PACK_ROOT / "outputs" / log_name
            log_rel = f"outputs/{log_name}"
        elif log_name == "findings.md":
            log_path = PACK_ROOT / "findings.md"
            log_rel = "findings.md"
        else:
            log_path = RUN1 / log_name
            log_rel = f"outputs/run1/{log_name}"
        log_digest = sha256_file(log_path)
        trace_digests = {}
        for trace_name in traces:
            if trace_name.startswith(("outputs/", "propagation-map.json", "matrix/")):
                # Pack-root-relative path (e.g. matrix results, the
                # propagation map); the C-16 log itself is one of these.
                if trace_name.startswith("outputs/"):
                    trace_path = PACK_ROOT / trace_name
                elif trace_name.startswith("matrix/"):
                    trace_path = RUN1 / trace_name
                else:
                    trace_path = PACK_ROOT / trace_name
            else:
                trace_path = RUN1 / trace_name
            digest = sha256_file(trace_path)
            logged = trace_sha_from_log(log_path) if trace_name in [
                "batch-basic.trace.jsonl",
                "batch-timeout.trace.jsonl",
                "batch-cancel.trace.jsonl",
                "batch-partial.trace.jsonl",
                "batch-reorder.trace.jsonl",
                "batch-repeat.trace.jsonl",
                "batch-nested.trace.jsonl",
                "batch-threaded-basic.trace.jsonl",
                "batch-threaded-timeout.trace.jsonl",
                "batch-threaded-cancel.trace.jsonl",
                "batch-threaded-partial.trace.jsonl",
                "batch-threaded-reorder.trace.jsonl",
                "batch-threaded-queue.trace.jsonl",
                "batch-threaded-wait-timeout.trace.jsonl",
            ] else ""
            # Cross-check: the digest recorded in the scenario log must
            # equal the digest of the trace file on disk.
            if logged and logged != digest:
                raise SystemExit(
                    f"digest mismatch for {trace_name}: log says {logged}, file is {digest}"
                )
            trace_digests[trace_name] = digest
        rows.append((claim_id, statement, script, log_rel, log_digest, trace_digests))
        evidence["claims"].append(
            {
                "id": claim_id,
                "statement": statement,
                "script": script,
                "log": log_rel,
                "log_sha256": log_digest,
                "traces": trace_digests,
            }
        )

    lines = [
        "# Claim-to-evidence index",
        "",
        "Each claim links to the runnable script that produces it, the "
        "unedited log that records it, and the SHA-256 digests of the "
        "log and trace files. Verify any digest with "
        "`bash scripts/verify_digests.sh`.",
        "",
        "| Claim | Statement | Script | Output log | Log SHA-256 | Trace SHA-256 |",
        "|---|---|---|---|---|---|",
    ]
    for claim_id, statement, script, log_rel, log_digest, trace_digests in rows:
        trace_cell = "<br>".join(
            f"`{name}`: `{digest[:16]}…`" for name, digest in trace_digests.items()
        ) or "—"
        lines.append(
            f"| {claim_id} | {statement} | `{script}` | `{log_rel}` | "
            f"`{log_digest[:16]}…` | {trace_cell} |"
        )
    lines += [
        "",
        "Full 64-hex digests are in `SHA256SUMS` and `evidence.json`.",
        "",
    ]
    (PACK_ROOT / "claims.md").write_text("\n".join(lines), encoding="utf-8")
    (PACK_ROOT / "evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"wrote claims.md ({len(rows)} claims) and evidence.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
