# AgenticSCR Orchestrator (Π) + Episodic Memory + Output — Piece 6 (final piece)

Ties Pieces 4 and 5 together into the actual end-to-end tool: Detector →
Validator → audit log → formatted output.

## Files
- `orchestrator/orchestrator.py`
  - `run_full_review(model, repo_path, log_dir=...)` — the whole pipeline
    in one call. Builds and runs the Detector; if it found nothing,
    short-circuits (Validator is never even built — no wasted work). If it
    found candidates, builds and runs the Validator on them, writes the
    episodic log, and formats the output.
  - `write_episodic_log(...)` — JSONL audit trail: one header line (run
    metadata + counts) then one line per message across *both* subagents
    (human/system prompts, every tool call with its args, every tool
    result truncated to 2000 chars, every assistant message) — enough to
    reconstruct why any finding was flagged or dropped without re-running
    the model.
  - `format_cli_output(confirmed_findings)` — human-readable
    `[Line, CWE-ID, Explanation]` text, grouped by file, sorted by line.
  - `format_json_output(confirmed_findings)` — machine-readable equivalent
    for CI/PR-bot integration.
- `cli.py` (repo root) — the actual CLI: `python cli.py review <repo_path>
  [--json] [--model MODEL_ID]`. Requires `ANTHROPIC_API_KEY`; exits 1 if
  any confirmed findings (CI-friendly), 0 if clean.
- `orchestrator/test_orchestrator.py` — smoke test, no API key needed.
  Chains the *exact* scripted trajectories from `test_detector.py` and
  `test_validator.py` through one shared fake model (matching how a real
  run uses a single model instance for both subagents), then verifies the
  full pipeline, the log file's contents, and the empty-diff short-circuit.

## Verified
- Full run: 2 candidates → 1 confirmed / 1 rejected, correctly reflected in
  `cli_output` (the rejected duplicate does **not** appear).
- Episodic log: header counts match, 22 total events split correctly
  (14 detector / 8 validator), and both `record_validation_verdict` calls
  are traceable in the validator events.
- Empty-diff path: when the Detector's first message has no tool calls,
  `candidate_findings` is `[]`, the Validator is never invoked, and the
  log still gets written (with `validator_confirmed_count: 0`) so a clean
  run is still auditable.
- `format_cli_output([])` returns the correct "nothing found" message.

## Running the real thing
This sandbox has no `ANTHROPIC_API_KEY`, so everything above is verified
via scripted fake models — the graph wiring, state handoffs, and output
formatting are all confirmed correct, but actual detection *quality* still
needs a live run. To try it for real:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
cd /path/to/your/repo
git add <files with staged changes>
python cli.py review .
```

## The full pipeline, recapped
1. **Semantic memory** — `sast_rules.json` (50 CodeQL Python rules) +
   `cwe_tree.json` (839 MITRE CWE-1000 entries).
2. **Toolset** — diff/file/grep/folder tools, scoped and path-guarded per
   repo.
3. **Working memory** — LangGraph state schema + caching + the stateful
   `record_candidate_finding` handoff mechanism.
4. **Detector** — LangGraph agent: diff → SAST rule search → navigation
   (grep + DyRetriever's `trace_dependencies` for real call-graph tracing)
   → candidate findings.
5. **Validator** — LangGraph agent: candidates → CWE tree lookup →
   optional re-verification → confirmed/rejected verdicts (including
   cross-candidate reasoning like duplicate detection).
6. **Orchestrator** — sequences 4→5, logs everything, formats output.

Plus the DyRetriever integration: an AST-based function registry and a
multi-hop Select→Visit→Expand LangGraph loop, wired into the Detector as
`trace_dependencies` alongside `grep`.

## Honest gaps, all documented in their own README
- 3 CWE IDs referenced by SAST rules aren't in the taxonomy package
  (semantic memory README).
- Nothing here has run against a real Anthropic model — only scripted fake
  models, which verify wiring/logic but not actual detection accuracy.
- DyRetriever's callee-name resolution is name-based, not scope-aware
  (dyretriever README).
- The Validator doesn't yet call `trace_dependencies` itself (validator
  README) — a natural next step if false-positive rates need work.
- Pieces 6's benchmark evaluation (SCRBench, from the original data
  breakdown) was never built — this pipeline has no automated accuracy
  measurement yet.

## Suggested next steps, your call
- Run it live against a real repo with a real API key and see what breaks.
- Build the SCRBench evaluation harness to actually measure precision/recall.
- Extend the SAST rules + function extractor beyond Python.
- Wire `trace_dependencies` into the Validator.
