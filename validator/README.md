# AgenticSCR Validator Subagent (a_v) — Piece 5

Consumes the Detector's `candidate_findings`, cross-checks each against the
CWE-1000 taxonomy, optionally re-examines the code, and issues a final
confirmed/rejected verdict. Structurally similar to the Detector (Piece 4)
— LangGraph agent + its own working-memory state + its own semantic-memory
tool — but the direction is filtering, not discovery.

## Files
- `validator.py`
  - `VALIDATOR_SYSTEM_PROMPT` — instructs the agent to process every
    candidate in order, look it up in the CWE tree, re-verify against code
    if unsure, and record exactly one verdict per candidate.
  - `ValidatorState` (extends LangGraph's `AgentState`) — `candidate_findings`
    (input), `confirmed_findings` / `rejected_findings` (output), plus the
    same file/chunk caches as the Detector.
  - `build_search_cwe_tree_tool()` — exact `cwe_id` lookup (full
    description, parent category, mitigations) or keyword search, both
    word-boundary matched like the Detector's rule search.
  - `record_validation_verdict` — stateful tool (`Command`-based, same
    pattern as `record_candidate_finding`): takes a `candidate_index`,
    `verdict` ("confirmed"/"rejected"), `final_cwe_id`, explanation, and
    confidence; appends to the right output list. Guards against an
    out-of-range index instead of raising.
  - `build_validator_tools(repo_path)` — cached navigation tools (minus
    `get_staged_diff`/`get_changed_files`, which are Detector-only) +
    `search_cwe_tree` + `record_validation_verdict`.
  - `build_validator_agent(model, repo_path)` / `run_validator(agent,
    repo_path, candidate_findings)`.
- `test_validator.py` — smoke test, no API key needed. Feeds in the exact 2
  candidate findings the Detector produced in Piece 4's test, scripts a
  realistic trajectory: look up `CWE-089`, confirm candidate 0, reject
  candidate 1 as a duplicate of candidate 0 (same root cause, different
  vantage point in the call chain) — this is exactly the kind of
  cross-candidate reasoning the Validator exists to do that the Detector
  can't, since the Detector only sees one issue at a time.

## Verified
- Full scripted run: 1 confirmed (`CWE-089`, validator confidence 0.95), 1
  rejected as duplicate, final summary message correct.
- `record_validation_verdict` with an out-of-range `candidate_index`
  (tested directly, no graph): returns a clean error message instead of
  raising or silently corrupting state.
- `search_cwe_tree(cwe_id="CWE-089")` and `search_cwe_tree(query="cross
  site scripting")` both spot-checked directly — correct entries,
  correctly ranked (CWE-079 XSS ranks first for the XSS query).

## Small fix made during polish
Initial `search_cwe_tree` output included full, multi-hundred-word
mitigation paragraphs verbatim — three of those per result would eat a lot
of context across a multi-candidate Validator run. Added a `_truncate()`
helper (220 chars, breaks on a word boundary) so mitigation text stays
skimmable without losing the gist.

## Not yet wired: Validator using `trace_dependencies`
The Detector gets DyRetriever's call-graph tracer; the Validator currently
doesn't. Left out to keep this piece scoped — but exploitability
verification (Step 2 in the prompt) is exactly the kind of judgment that
benefits from tracing whether a value is really user-controlled end-to-end,
so wiring it in later is a natural follow-up if false-positive rates turn
out too high in practice.

## Next
Piece 6 — the Orchestrator: sequences Detector → Validator, passes
`candidate_findings` between them, writes the episodic log, and formats
`confirmed_findings` into the final CLI output
(`[Line, CWE-ID, Explanation]`).
