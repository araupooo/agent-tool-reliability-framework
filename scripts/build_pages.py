"""Render the findings page (docs/index.html) from the pack's own evidence.

Reads findings.md (findings), evidence.json (claims), and SHA256SUMS
(digests). Self-contained output: inline CSS, no external resources,
no timestamps, so the page is deterministic. Run from the pack root:
python3 scripts/build_pages.py
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parents[1]

CSS = """
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:2em auto;max-width:960px;padding:0 1em;color:#1a1a1a;line-height:1.5}
h1{font-size:1.8em}h2{border-bottom:2px solid #ddd;padding-bottom:.2em;margin-top:2em}
table{border-collapse:collapse;width:100%;font-size:.92em}
th,td{border:1px solid #ccc;padding:.45em .6em;text-align:left;vertical-align:top}
th{background:#f0f0f0}code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.88em;background:#f6f6f6;padding:.1em .25em;border-radius:3px}
pre{background:#f6f6f6;padding:1em;overflow-x:auto;border-radius:4px}
.sev-High{color:#b00020;font-weight:700}.sev-Medium{color:#8a5a00;font-weight:700}.sev-Low{color:#205a8a;font-weight:700}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.85em;word-break:break-all}
"""

FINDING_RE = re.compile(
    r"^## (F-\d+) — (High|Medium|Low) — (.+?)\n\n"
    r"\*\*Trigger:\*\* `(.+?)`\n\n"
    r"\*\*Observed:\*\* (.+?)\n\n"
    r"\*\*Expected:\*\* (.+?)\n\n"
    r"\*\*Evidence:\*\* (.+?)(?=\n\n## |\n\n## Reference|\Z)",
    re.DOTALL | re.MULTILINE,
)


def parse_findings(text: str) -> list[dict]:
    findings = []
    for match in FINDING_RE.finditer(text):
        fid, severity, title, trigger, observed, expected, evidence = match.groups()
        findings.append(
            {
                "id": fid,
                "severity": severity,
                "title": title.strip(),
                "trigger": trigger.strip(),
                "observed": " ".join(observed.split()),
                "expected": " ".join(expected.split()),
                "evidence": " ".join(evidence.split()),
            }
        )
    if len(findings) != 12:
        raise SystemExit(f"expected 12 findings in findings.md, parsed {len(findings)}")
    return findings


def main() -> int:
    findings = parse_findings((PACK_ROOT / "findings.md").read_text(encoding="utf-8"))
    evidence = json.loads((PACK_ROOT / "evidence.json").read_text(encoding="utf-8"))
    digests = (PACK_ROOT / "SHA256SUMS").read_text(encoding="utf-8").strip().splitlines()

    esc = html.escape
    parts = [
        "<!DOCTYPE html>",
        "<html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        "<title>Agent Tool Reliability Framework — Findings</title>",
        f"<style>{CSS}</style></head><body>",
        "<h1>Agent Tool Reliability Framework — Findings</h1>",
        "<p>Map timeout, cancellation, and partial-failure propagation "
        "across three agent-tooling components, replay a network-scale "
        "corpus deterministically, and measure recovery with "
        "deterministic assertions for preserved results, reported "
        "errors, and zero silent drops. The record/replay core is "
        "reused verbatim from the parallel-tool-replay-harness "
        "(<code>harness/</code>, the seven asyncio scenarios, the shell "
        "runner); the thread-pool dispatcher, the 48-trace corpus, the "
        "36-cell fault-injection matrix, and the propagation map are new "
        "in this framework. Every assertion is deterministic: a virtual "
        "clock replaces wall-clock time, so a replayed trace is "
        "byte-identical to the recorded one.</p>",
        "<h2>Reproduce</h2>",
        "<pre>bash scripts/run_all.sh outputs/run1\n"
        "bash scripts/clean_reproduce.sh   # fresh-copy run; must match run1 byte-for-byte\n"
        "bash scripts/verify_digests.sh    # verifies SHA256SUMS</pre>",
        "<p>Requirements: Python 3.11+ (standard library only), GNU bash "
        "5.1+ (<code>wait -n -p</code>), <code>sha256sum</code>. No network "
        "access, no credentials.</p>",
        "<h2>Components validated</h2>",
        "<ol>",
        "<li><strong>Asyncio parallel tool dispatcher</strong> "
        "(<code>harness/</code>): Python <code>asyncio</code> dispatcher "
        "running batches of parallel tool calls on a virtual clock; "
        "semantics follow the OpenAI function-calling guide (multiple "
        "calls per turn, matched by <code>call_id</code>) and the "
        "Anthropic parallel-tool-use guide (one <code>tool_result</code> "
        "per <code>tool_use</code> block, matched by "
        "<code>tool_use_id</code>).</li>",
        "<li><strong>Shell parallel runner</strong> "
        "(<code>components/shell_runner/</code>): the same trace schema "
        "applied to real concurrent shell processes (background jobs, "
        "<code>wait -n</code> completion order, exit codes, output "
        "digests).</li>",
        "<li><strong>Threaded thread-pool dispatcher</strong> "
        "(<code>components/threaded_dispatcher/</code>): models "
        "Python's <code>concurrent.futures.ThreadPoolExecutor</code> on "
        "the same virtual clock — worker slots, FIFO queue, "
        "<code>Future.cancel()</code> semantics, collector "
        "<code>wait()</code> timeout with late-result reconciliation — "
        "verified against the CPython source and a live probe with a "
        "real executor.</li>",
        "<li><strong>Reference implementation inspection</strong>: "
        "CorbanuTerminal's parallel tool runtime "
        "(<code>codex-rs/core/src/tools/parallel.rs</code>, commit "
        "<code>7b5b368f</code>): parallel-execution gate, "
        "<code>CancellationToken</code> abort path, nested-call timing "
        "guard, 3-per-turn identical-call cap.</li>",
        "</ol>",
        "<h2>Findings</h2>",
        "<table><tr><th>ID</th><th>Severity</th><th>Finding</th><th>Trigger</th><th>Observed</th><th>Expected</th><th>Evidence</th></tr>",
    ]
    for f in findings:
        parts.append(
            f"<tr><td>{esc(f['id'])}</td>"
            f"<td class=\"sev-{f['severity']}\">{esc(f['severity'])}</td>"
            f"<td>{esc(f['title'])}</td>"
            f"<td><code>{esc(f['trigger'])}</code></td>"
            f"<td>{esc(f['observed'])}</td>"
            f"<td>{esc(f['expected'])}</td>"
            f"<td class=\"mono\">{esc(f['evidence'])}</td></tr>"
        )
    parts.append("</table>")

    parts.append("<h2>Claim-to-evidence index</h2>")
    parts.append("<table><tr><th>Claim</th><th>Statement</th><th>Script</th><th>Output log</th><th>Log SHA-256</th></tr>")
    for claim in evidence["claims"]:
        traces = "<br>".join(
            f"{esc(name)}: <span class=\"mono\">{esc(digest[:16])}…</span>"
            for name, digest in claim["traces"].items()
        )
        parts.append(
            f"<tr><td>{esc(claim['id'])}</td><td>{esc(claim['statement'])}</td>"
            f"<td><code>{esc(claim['script'])}</code></td>"
            f"<td><code>{esc(claim['log'])}</code>{'<br>' + traces if traces else ''}</td>"
            f"<td class=\"mono\">{esc(claim['log_sha256'][:16])}…</td></tr>"
        )
    parts.append("</table>")
    parts.append("<p>Full 64-hex digests are in <code>SHA256SUMS</code> and "
                 "<code>evidence.json</code>.</p>")

    parts.append("<h2>SHA-256 digests</h2>")
    parts.append("<p>Digests of the evidence files below (this page's own "
                 "digest is recorded in <code>SHA256SUMS</code>).</p>")
    parts.append("<table><tr><th>SHA-256</th><th>File</th></tr>")
    for line in digests:
        digest, path = line.split(None, 1)
        parts.append(f"<tr><td class=\"mono\">{esc(digest)}</td><td><code>{esc(path)}</code></td></tr>")
    parts.append("</table>")
    parts.append("<p>Sources: <code>citations.md</code> (OpenAI function "
                 "calling, Anthropic parallel tool use, Python asyncio, "
                 "CorbanuTerminal parallel.rs, GNU Bash manual, Python "
                 "concurrent.futures). Trace schema: "
                 "<code>TRACE_SCHEMA.md</code>. Propagation map: "
                 "<code>propagation-map.md</code> and "
                 "<code>propagation-map.json</code>.</p>")
    parts.append("</body></html>")
    out = PACK_ROOT / "docs" / "index.html"
    out.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(f"wrote docs/index.html ({len(findings)} findings, "
          f"{len(evidence['claims'])} claims, {len(digests)} digests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
