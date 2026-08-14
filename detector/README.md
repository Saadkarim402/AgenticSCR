# AgenticSCR Detector Subagent (a_d) — Piece 4

The first piece with an actual agentic loop: a LangGraph `create_react_agent`
bound to the diff/navigation tools (Piece 2, cached via Piece 3), a new
`search_sast_rules` tool over Piece 1's `sast_rules.json`, and the
`record_candidate_finding` tool (Piece 3) as its output mechanism.

## Files
- `detector.py`
  - `DETECTOR_SYSTEM_PROMPT` — instructs the agent to fetch the diff first,
    search rules for security-relevant changes, navigate for context before
    judging exploitability, and call `record_candidate_finding` once per
    distinct issue.
  - `build_search_sast_rules_tool()` — keyword search over
    `sast_rules.json`, word-boundary matched over `id + name + description +
    tags`.
  - `build_detector_tools(repo_path)` — assembles the full tool list.
  - `build_detector_agent(model, repo_path)` — builds the compiled graph.
    `model` is any LangChain chat model with `.bind_tools()` — pass
    `ChatAnthropic(model="claude-sonnet-4-6")` for live use.
  - `run_detector(agent, repo_path)` — runs one review pass, returns final
    state including `candidate_findings`.
- `test_detector.py` — smoke test using a scripted fake model (no API key
  needed) that plays out a realistic 7-step trajectory against the toy
  repo's SQL-injection diff and asserts on the final `candidate_findings`.

## Why `search_sast_rules` is a tool, not a system-prompt dump
The paper describes the Detector "consulting" SAST rules from semantic
memory. Rather than stuffing all 50 rules into the system prompt every
turn, this is a retrieval tool the Detector calls with a few keywords
describing what it's looking at — closer to how the real system would
scale to hundreds/thousands of rules without blowing the context budget on
every single turn.

## Bug caught and fixed during testing
Original search scored by raw substring containment. Two problems:
1. `py/sql-injection`'s name/description never literally contain the word
   "injection" (it says "insertion of malicious SQL code"), so a query for
   "sql injection" scored it below more literal matches.
2. Substring matching let `"sql" in "nosql injection"` — so
   `py/nosql-injection` **outranked** the actual SQL injection rule for a
   `"sql injection"` query.

Fixed by (a) including the rule `id` field in the searchable text (`id`
contains the clearest signal, e.g. `py/sql-injection`), and (b) switching
to whole-word-token matching (`set` intersection over
`re.split(r"[^a-z0-9]+", text)`) instead of substring containment, so
`"nosql"` and `"sql"` are distinct tokens. Verified: `search_sast_rules`
now correctly ranks `py/sql-injection` first for the query `"sql injection
string formatting"`.

## Required for live use (not run in this sandbox — no API key here)
```python
from langchain_anthropic import ChatAnthropic
model = ChatAnthropic(model="claude-sonnet-4-6")
agent = build_detector_agent(model, "/path/to/real/repo")
result = run_detector(agent, "/path/to/real/repo")
print(result["candidate_findings"])
```

## Next
Piece 5 — the Validator Subagent: takes `candidate_findings`, cross-checks
each against `cwe_tree.json`, filters out low-confidence/false-positive
findings, and assigns confirmed CWE classifications. Structurally very
similar to this piece (LangGraph agent + its own working-memory state +
its own semantic-memory tool over the CWE tree instead of the SAST rules).
