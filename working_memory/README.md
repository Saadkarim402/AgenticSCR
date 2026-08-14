# AgenticSCR Working Memory (M_w) — Piece 3

In LangGraph, working memory isn't a separate store — it's the **graph
state** itself, flowing through and accumulating across nodes within one
Detector run. This piece defines that state schema and the tools that read
and write it.

## Files
- `working_memory.py`
  - `DetectorState` (TypedDict) — the graph state schema: `messages`,
    `repo_path`, `diff`, `changed_files`, `file_cache`, `chunk_cache`,
    `candidate_findings`, `tool_call_count`.
  - `new_detector_state(repo_path, diff, changed_files)` — state constructor.
  - `CandidateFinding` (Pydantic) — schema for one finding: file, line
    range, title, explanation, suspected CWE IDs, matched SAST rule id,
    confidence.
  - `record_candidate_finding` — a **stateful tool** the Detector calls to
    push a finding into `state["candidate_findings"]`. This is the
    Detector → Validator handoff: after the Detector's LangGraph node
    finishes, the orchestrator reads this list straight out of state.
  - `wrap_with_cache(base_tools)` — wraps `open_files` and
    `expand_code_chunks` from Piece 2's toolset so a repeated call for the
    same file/range returns from `state["file_cache"]` /
    `state["chunk_cache"]` instead of re-reading disk and re-spending
    context tokens on an identical result. Other tools pass through
    unchanged.
- `test_working_memory.py` — smoke test, no LLM required. Confirms findings
  accumulate (not overwrite), cache hits/misses behave correctly, and
  `tool_call_count` increments.

## How this wires into LangGraph
```python
from working_memory import new_detector_state, record_candidate_finding, wrap_with_cache
from tools import build_toolset
from langgraph.prebuilt import create_react_agent

base_tools = build_toolset(repo_path)
detector_tools = wrap_with_cache(base_tools) + [record_candidate_finding]

agent = create_react_agent(model, tools=detector_tools, state_schema=DetectorState)
result = agent.invoke(new_detector_state(repo_path, diff=diff_text, changed_files=[...]))
findings = result["candidate_findings"]  # -> handed to Validator subagent
```

## Gotcha worth knowing about
Hit a classic Python late-binding closure bug while building the cache
wrapper: both wrapped tools initially referenced a variable named
`original`, so by the time `open_files_cached` actually ran, `original` had
been reassigned to the `expand_code_chunks` tool from the second block —
`open_files_cached` was silently calling the wrong underlying tool. Fixed
by giving each captured reference a distinct name
(`original_open_files` / `original_expand_chunks`). Caught by the smoke
test's Pydantic validation error, not silently — worth knowing about if you
extend `wrap_with_cache` to wrap more tools later.

## Next
Piece 4 — the Detector subagent itself: a `create_react_agent` (or custom
graph) bound to these tools + `sast_rules.json`, given the diff, and run
until it stops emitting `record_candidate_finding` calls.
