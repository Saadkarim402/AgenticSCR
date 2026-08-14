# DyRetriever Context Engine — integrated into AgenticSCR

A multi-hop, LLM-driven dependency tracer that replaces "guess and grep"
with actual call-graph traversal, per the DyRetriever/DyCoder paper. Wired
into the Detector subagent as a new tool, `trace_dependencies`, alongside
(not replacing) `grep`.

## Files
- `extractor.py` — Part A: AST-based function registry.
  - `build_function_registry(repo_path)` — indexes every module-level
    function and one level of class methods across all `.py` files, keyed
    `"<file>::<qualname>"`, each with source, line range, signature,
    imports.
  - `build_simple_name_index` / `resolve_call_name` — resolves an
    LLM-reported callee name (bare or dotted) back to a real registry
    entry, or `None` if it isn't a local function (stdlib, third-party,
    hallucination — all correctly discarded).
  - `parse_diff_new_line_ranges` / `find_entry_points` — maps a git diff's
    changed hunks onto the functions that actually contain them. This is
    how entry points are derived directly from the diff, skipping the
    paper's own file-selection LLM step entirely (we already know exactly
    what changed).
- `engine.py` — Part B: the Select→Visit→Expand loop as a LangGraph
  `StateGraph` (not a ReAct agent — a bounded structured loop with one
  forced-tool-call LLM decision per step):
  - `select` — pick one candidate to explore, or `stop_traversal`
  - `visit` — deterministic: pull code from the registry
  - `expand` — read the code + imports, report callee names; every name is
    immediately resolved against the registry (post-filter) before being
    added back to the candidate pool
  - `topk` — ranks the full trajectory, but **skips the LLM call entirely**
    when the trajectory is already `<= top_k`
  - `build_dyretriever_graph(model)` / `new_dyretriever_state(...)` /
    `get_context_snippets(final_state)`
- `test_dyretriever.py` — smoke test, no API key needed. Scripted
  trajectory against the toy repo exercises: stdlib-call filtering
  (`sqlite3.connect` discarded), already-visited dedup, both diff entry
  points explored, and the Top-K short-circuit.

## Verified separately (inline, not a saved script)
- `stop_traversal` on the very first `select` call → visits nothing,
  `stopped=True`.
- `max_hops` cuts the loop off mid-traversal with candidates still
  pending in the pool (tested with `max_hops=1` against a scenario that
  would otherwise continue).
- Natural pool exhaustion ends the loop early, before the hop limit, when
  there's genuinely nothing left to explore.

## Wired into the Detector (`detector/detector.py`)
- `build_dependency_trace_tool(repo_path, model)` — builds the
  `trace_dependencies` tool. It runs the *entire* DyRetriever loop
  internally using the **same model instance** the Detector itself uses
  for its per-hop Select/Expand decisions, then returns the Top-K
  functions' source as one formatted string.
- `build_detector_tools(repo_path, model)` now takes `model` too (needed
  to construct `trace_dependencies`), and returns 9 tools total: the 6
  navigation/diff tools (2 cache-wrapped), `search_sast_rules`,
  `trace_dependencies`, and `record_candidate_finding`.
- System prompt updated: `grep` for text/pattern search, `trace_dependencies`
  when the Detector needs to confirm actual data flow through function
  calls.
- Verified directly (not through the full agent loop, to keep the test
  targeted): calling `trace_dependencies("get_user_by_name",
  "app/db/queries.py", ...)` with a scripted inner model correctly returns
  both `get_user_by_name` and its real callee `get_connection`'s source,
  in traversal order.

## Known limitations (documented, not silently swept under the rug)
- **Python only**, one level of nesting (module functions + class
  methods) — inner/nested functions aren't indexed separately. Extending
  to other languages needs a different registry builder per language
  (tree-sitter would generalize this instead of stdlib `ast`).
- **Callee resolution is name-based, not scope-aware** — if two classes in
  different files each have a `get()` method and the LLM just says "get",
  resolution falls back to a deterministic but arbitrary tie-break
  (alphabetically first match) rather than true reference resolution. Good
  enough for a first pass; a real implementation would want the caller's
  import statements to narrow candidates first.
- **`trace_dependencies` rebuilds the function registry on every call**
  (no caching across calls within one Detector run) — fine for a toy repo,
  worth caching per-`repo_path` for a large real one.

## Next
Piece 5 — the Validator subagent (paused to build this integration first,
per your call on build order).
