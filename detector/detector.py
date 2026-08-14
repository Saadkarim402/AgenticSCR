"""
AgenticSCR Detector Subagent (a_d) — Piece 4

Builds a LangGraph ReAct-style agent that:
1. Fetches the staged diff (via Piece 2's `get_staged_diff` tool).
2. Consults sast_rules.json (via `search_sast_rules`, a keyword-search tool
   over Piece 1's semantic memory — kept as a tool rather than stuffed into
   the system prompt so the Detector pulls in only the rules relevant to
   what it's actually looking at, matching the paper's "consults SAST
   rules" framing rather than preloading all 50 rules every turn).
3. Navigates the codebase as needed (open_files / expand_code_chunks / grep
   / expand_folder from Piece 2, wrapped with Piece 3's caching).
4. Emits one `record_candidate_finding` call per distinct issue (Piece 3),
   which writes into `DetectorState["candidate_findings"]`.

This module builds the *graph*; it does not call a real model itself
(see `run_detector_live.py` for that, which needs `ANTHROPIC_API_KEY`).
`test_detector.py` exercises the graph with a scripted fake model so the
wiring can be verified without API access.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "toolset"))
sys.path.insert(0, str(_REPO_ROOT / "working_memory"))
sys.path.insert(0, str(_REPO_ROOT / "dyretriever"))

from langchain_core.tools import tool  # noqa: E402
from langgraph.prebuilt import create_react_agent  # noqa: E402

from tools import build_toolset  # noqa: E402
from working_memory import (  # noqa: E402
    DetectorState,
    new_detector_state,
    record_candidate_finding,
    wrap_with_cache,
)

SAST_RULES_PATH = (_REPO_ROOT / "sast_rules/sast_rules.json")

DETECTOR_SYSTEM_PROMPT = """\
You are the Detector Subagent in an automated secure code review tool.

Your job: review the developer's currently STAGED (pre-commit) changes and \
find genuine security vulnerabilities introduced or exposed by the diff. \
You are not reviewing the whole codebase — focus on what changed, but you \
may navigate to related code to understand the impact of a change (e.g. \
where a new parameter flows to, or what an existing function already does).

Work like this:
1. Call `get_staged_diff` first to see what changed.
2. For each change that looks security-relevant (new user input handling, \
new SQL/shell/file/network operations, changed auth/crypto/cookie logic, \
etc.), call `search_sast_rules` with a few keywords describing what you're \
looking at (e.g. "sql injection", "path traversal", "cookie") to check \
whether a known rule pattern matches.
3. Use `open_files`, `expand_code_chunks`, `grep`, and `expand_folder` as \
needed to see enough context to judge whether something is actually \
exploitable. For text/pattern search (e.g. "where else is this secret used"), \
use `grep`. When you need to actually trace whether user-controlled data \
flows from a source into a specific sink through function calls, use \
`trace_dependencies` instead — it follows real call relationships \
multi-hop, not just text matches, and pulls in the actual downstream code.
4. For every distinct issue you're reasonably confident about, call \
`record_candidate_finding` exactly once with the file, line range, a clear \
explanation of the data flow from source to sink, any suspected CWE IDs, \
the matched SAST rule id if one applied, and your own confidence (0-1).
5. When you've reviewed all security-relevant changes in the diff, stop \
calling tools and reply with a one-line summary of how many findings you \
recorded.

Be precise, not exhaustive — a false positive costs the developer's trust. \
If you're unsure whether something is exploitable, still record it but \
with lower confidence and say why you're unsure in the explanation; the \
Validator will make the final call, not you.
"""


def _load_sast_rules() -> list[dict]:
    return json.loads(SAST_RULES_PATH.read_text())


def build_search_sast_rules_tool():
    rules = _load_sast_rules()

    import re as _re

    def _words(text: str) -> set:
        """Tokenize into whole lowercase words/word-fragments split on any
        non-alphanumeric char, so 'sql-injection' -> {'sql','injection'} and
        'nosql' stays a single distinct token (won't spuriously match 'sql')."""
        return set(_re.split(r"[^a-z0-9]+", text.lower())) - {""}

    def _rule_haystack_words(r: dict) -> set:
        # include `id` (e.g. "py/sql-injection") — the clearest signal —
        # plus name, description, and tags.
        parts = [r.get("id", ""), r.get("name", ""), r.get("description", ""), " ".join(r.get("tags", []))]
        return _words(" ".join(parts))

    @tool
    def search_sast_rules(query: str, cwe_id: Optional[str] = None, max_results: int = 5) -> str:
        """Search the SAST rule knowledge base for rules matching a
        description (keyword search over rule id/name/description/tags),
        and optionally filter to a specific CWE ID like 'CWE-089'. Use this
        before recording a finding to check whether a known, named pattern
        applies — it lets you cite a matched_rule_id."""
        q_terms = _words(query)
        scored = []
        for r in rules:
            if cwe_id and cwe_id not in r.get("cwe_ids", []):
                continue
            haystack_words = _rule_haystack_words(r)
            score = len(q_terms & haystack_words)
            if score > 0 or (cwe_id and cwe_id in r.get("cwe_ids", [])):
                scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [r for _, r in scored[:max_results]]
        if not top:
            return f"No SAST rules matched query='{query}' cwe_id={cwe_id}."
        lines = []
        for r in top:
            lines.append(
                f"- id={r['id']} | cwe={r['cwe_ids']} | severity={r.get('severity')} | "
                f"{r['name']}: {r['description']}"
            )
        return "\n".join(lines)

    return search_sast_rules


def build_dependency_trace_tool(repo_path: str, model):
    """Build the `trace_dependencies` tool: runs DyRetriever's full
    Select->Visit->Expand multi-hop loop (see dyretriever/engine.py)
    starting from one function, using the *same* model as the Detector for
    its internal per-hop decisions. Returns the source of the most relevant
    downstream functions actually reachable from the starting point —
    real call-graph tracing, not text search."""
    from engine import build_dyretriever_graph, get_context_snippets, new_dyretriever_state
    from extractor import build_function_registry, build_simple_name_index

    @tool
    def trace_dependencies(function_name: str, file_path: str, reason: str) -> str:
        """Trace the call/dependency graph starting from a specific function
        using multi-hop reasoning (Select -> Visit -> Expand), not text
        search. Use this instead of `grep` when you need to know exactly
        what a changed function calls and whether user-controlled data
        actually flows through those calls to a sensitive sink — it pulls
        in the real downstream function source, cross-checked against what
        actually exists in the repo (hallucinated or stdlib calls are
        filtered out). `function_name` is the bare function or
        'ClassName.method' name; `file_path` is relative to the repo root;
        `reason` is a short description of what you're trying to confirm
        (e.g. 'confirm whether user input reaches the SQL query')."""
        registry = build_function_registry(repo_path)
        qualified = f"{file_path}::{function_name}"
        if qualified not in registry:
            idx = build_simple_name_index(registry)
            matches = idx.get(function_name.split(".")[-1])
            if not matches:
                return f"ERROR: no function '{function_name}' found in '{file_path}' or elsewhere in the repo."
            qualified = matches[0]

        entry_points = [{"qualified_name": qualified, "reason": "starting point requested by Detector"}]
        graph = build_dyretriever_graph(model)
        state = new_dyretriever_state(repo_path, entry_points, target_description=reason, max_hops=6, top_k=5)
        final = graph.invoke(state)
        snippets = get_context_snippets(final)
        if not snippets:
            return f"Traced from {qualified} but found no additional local dependency context."
        blocks = [f"=== {s['qualified_name']} ===\n{s['code']}" for s in snippets]
        return "\n\n".join(blocks)

    return trace_dependencies


def build_detector_tools(repo_path: str, model) -> list:
    """Assemble the Detector's full toolset: cached navigation tools +
    the SAST rule search tool + DyRetriever's dependency tracer + the
    stateful finding recorder."""
    base = build_toolset(repo_path)
    cached = wrap_with_cache(base)
    return cached + [
        build_search_sast_rules_tool(),
        build_dependency_trace_tool(repo_path, model),
        record_candidate_finding,
    ]


def build_detector_agent(model, repo_path: str):
    """Build the compiled LangGraph agent. `model` is any LangChain
    chat-model-like object supporting `.bind_tools()` — pass a real
    `ChatAnthropic(model="claude-sonnet-4-6")` for live use, or a fake model
    for testing (see test_detector.py). The same model instance also drives
    DyRetriever's internal per-hop decisions inside `trace_dependencies`."""
    tools = build_detector_tools(repo_path, model)
    return create_react_agent(
        model,
        tools=tools,
        state_schema=DetectorState,
        prompt=DETECTOR_SYSTEM_PROMPT,
    )


def run_detector(agent, repo_path: str) -> dict:
    """Run one Detector review pass and return the final state, including
    `candidate_findings` — ready to hand to the Validator subagent."""
    from langchain_core.messages import HumanMessage

    initial_state = new_detector_state(repo_path)
    initial_state["messages"] = [
        HumanMessage(content="Review the currently staged changes in this repository for security vulnerabilities.")
    ]
    return agent.invoke(initial_state)
