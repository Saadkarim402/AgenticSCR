"""
AgenticSCR Validator Subagent (a_v) — Piece 5

Builds a LangGraph agent that:
1. Receives `candidate_findings` from the Detector (Piece 4) as input state.
2. For each candidate, looks up the suspected CWE ID(s) against
   `cwe_tree.json` (via `search_cwe_tree`, the Validator's own semantic
   memory access tool — mirrors `search_sast_rules` from the Detector but
   over the taxonomy instead of the rule base).
3. Optionally re-examines code (open_files / expand_code_chunks / grep /
   expand_folder, cached, from Piece 2+3) to judge exploitability rather
   than trusting the Detector's explanation blindly.
4. Calls `record_validation_verdict` exactly once per candidate — either
   "confirmed" (with a final CWE ID, possibly refined from the Detector's
   suspicion) or "rejected" (false positive or duplicate), writing into
   `ValidatorState["confirmed_findings"]` / `["rejected_findings"]`.

This is the paper's Filtering & Output step: only `confirmed_findings`
becomes the tool's final [Line, CWE-ID, Explanation] output.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Annotated, Literal, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "toolset"))

from langchain_core.messages import HumanMessage, ToolMessage  # noqa: E402
from langchain_core.tools import InjectedToolCallId, tool  # noqa: E402
from langgraph.prebuilt import InjectedState, create_react_agent  # noqa: E402
from langgraph.prebuilt.chat_agent_executor import AgentState  # noqa: E402
from langgraph.types import Command  # noqa: E402

from tools import build_toolset  # noqa: E402

sys.path.insert(0, str(_REPO_ROOT / "working_memory"))
from working_memory import wrap_with_cache  # noqa: E402

CWE_TREE_PATH = (_REPO_ROOT / "cwe_tree/cwe_tree.json")

VALIDATOR_SYSTEM_PROMPT = """\
You are the Validator Subagent in an automated secure code review tool. \
The Detector has already scanned a staged diff and recorded candidate \
security findings — your job is to judge each one, not to find new issues.

For EVERY candidate finding (indexed starting at 0), in order:
1. Call `search_cwe_tree` with the candidate's suspected CWE ID (if it has \
one) or a keyword query (if not) to confirm the taxonomy classification is \
correct and see the official description, likelihood of exploit, and \
mitigations.
2. If you're not confident the issue is real or exploitable from the \
Detector's explanation alone, use `open_files`, `expand_code_chunks`, or \
`grep` to re-check the actual code yourself. Don't just trust the \
Detector's explanation — verify it.
3. Call `record_validation_verdict` exactly once for that candidate:
   - "confirmed" if it's a real, exploitable issue — include the final \
CWE ID (refine it if the Detector's guess was wrong or imprecise) and a \
clear explanation.
   - "rejected" if it's a false positive, not actually exploitable, or a \
duplicate of a finding you already confirmed — say why in the explanation.

Process every candidate before finishing. When all candidates have a \
verdict, reply with a one-line summary of how many were confirmed vs \
rejected and stop.
"""


class ValidatorState(AgentState):
    """Graph state for the Validator subagent — its working memory for one
    review session. `candidate_findings` is the input from the Detector;
    `confirmed_findings` / `rejected_findings` accumulate as output."""

    repo_path: str
    candidate_findings: list[dict]
    file_cache: dict
    chunk_cache: dict
    confirmed_findings: list[dict]
    rejected_findings: list[dict]
    tool_call_count: int


def new_validator_state(repo_path: str, candidate_findings: list[dict]) -> ValidatorState:
    return ValidatorState(
        messages=[],
        repo_path=repo_path,
        candidate_findings=candidate_findings,
        file_cache={},
        chunk_cache={},
        confirmed_findings=[],
        rejected_findings=[],
        tool_call_count=0,
    )


# --------------------------------------------------------------------------
# search_cwe_tree — Validator's semantic memory access tool
# --------------------------------------------------------------------------

def _load_cwe_entries() -> dict:
    return json.loads(CWE_TREE_PATH.read_text())["entries"]


def _words(text: str) -> set:
    return set(re.split(r"[^a-z0-9]+", text.lower())) - {""}


def build_search_cwe_tree_tool():
    entries = _load_cwe_entries()

    def _truncate(text: str, limit: int = 220) -> str:
        return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"

    def _format_entry(cwe_id: str, e: dict) -> str:
        parent_names = [f"{p} ({entries[p]['name']})" for p in e.get("parents", []) if p in entries]
        mitigations = [_truncate(m) for m in e.get("potential_mitigations", [])[:3]]
        return (
            f"{cwe_id}: {e['name']} [{e.get('abstraction', '?')}]\n"
            f"Description: {e.get('description', '')}\n"
            f"Parents: {', '.join(parent_names) or '(none — top-level)'}\n"
            f"Likelihood of exploit: {e.get('likelihood_of_exploit') or 'not specified'}\n"
            f"Mitigations: {' | '.join(mitigations) if mitigations else 'not specified'}"
        )

    @tool
    def search_cwe_tree(query: str = "", cwe_id: Optional[str] = None, max_results: int = 5) -> str:
        """Look up the CWE-1000 taxonomy. Pass `cwe_id` (e.g. 'CWE-089') for
        an exact lookup with full description, parent category, likelihood
        of exploit, and mitigations — use this to confirm or correct a
        candidate finding's suspected CWE ID. Pass `query` instead for a
        keyword search when you don't have a specific ID yet."""
        if cwe_id:
            entry = entries.get(cwe_id)
            if not entry:
                return f"No CWE entry found for '{cwe_id}'."
            return _format_entry(cwe_id, entry)

        q_terms = _words(query)
        if not q_terms:
            return "Provide either cwe_id or a non-empty query."
        scored = []
        for cid, e in entries.items():
            haystack = _words(" ".join([cid, e.get("name", ""), e.get("description", "")]))
            score = len(q_terms & haystack)
            if score > 0:
                scored.append((score, cid, e))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:max_results]
        if not top:
            return f"No CWE entries matched query='{query}'."
        return "\n\n".join(_format_entry(cid, e) for _, cid, e in top)

    return search_cwe_tree


# --------------------------------------------------------------------------
# record_validation_verdict — stateful tool, the Validator's output mechanism
# --------------------------------------------------------------------------

@tool
def record_validation_verdict(
    candidate_index: int,
    verdict: Literal["confirmed", "rejected"],
    explanation: str,
    confidence: float,
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
    final_cwe_id: Optional[str] = None,
) -> Command:
    """Record your verdict on one candidate finding by its index (0-based,
    as listed in the candidates you were given). Call this exactly once per
    candidate. verdict must be 'confirmed' or 'rejected'. For 'confirmed',
    include final_cwe_id. explanation should justify the verdict clearly —
    for 'rejected', say specifically why (not exploitable, false positive,
    or duplicate of an already-confirmed finding)."""
    candidates = state.get("candidate_findings", [])
    if not (0 <= candidate_index < len(candidates)):
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=f"ERROR: candidate_index {candidate_index} out of range "
                        f"(valid: 0-{len(candidates) - 1}). No verdict recorded.",
                        tool_call_id=tool_call_id,
                    )
                ]
            }
        )

    candidate = candidates[candidate_index]
    record = {
        **candidate,
        "final_cwe_id": final_cwe_id,
        "validator_explanation": explanation,
        "validator_confidence": confidence,
    }

    if verdict == "confirmed":
        updated = state.get("confirmed_findings", []) + [record]
        update = {"confirmed_findings": updated}
        note = f"Confirmed #{candidate_index} ({candidate.get('title')}) as {final_cwe_id}."
    else:
        updated = state.get("rejected_findings", []) + [record]
        update = {"rejected_findings": updated}
        note = f"Rejected #{candidate_index} ({candidate.get('title')}): {explanation}"

    update["messages"] = [ToolMessage(content=note, tool_call_id=tool_call_id)]
    return Command(update=update)


# --------------------------------------------------------------------------
# Agent construction
# --------------------------------------------------------------------------

def build_validator_tools(repo_path: str) -> list:
    base = build_toolset(repo_path)
    cached = wrap_with_cache(base)
    # the Validator doesn't need get_staged_diff/get_changed_files (that's
    # the Detector's entry point) — keep only the code-reading tools.
    navigation = [t for t in cached if t.name not in ("get_staged_diff", "get_changed_files")]
    return navigation + [build_search_cwe_tree_tool(), record_validation_verdict]


def build_validator_agent(model, repo_path: str):
    tools = build_validator_tools(repo_path)
    return create_react_agent(
        model,
        tools=tools,
        state_schema=ValidatorState,
        prompt=VALIDATOR_SYSTEM_PROMPT,
    )


def run_validator(agent, repo_path: str, candidate_findings: list[dict]) -> dict:
    """Run one Validator pass over the Detector's candidate findings.
    Returns final state including `confirmed_findings` — the tool's actual
    output — and `rejected_findings` for audit/logging."""
    state = new_validator_state(repo_path, candidate_findings)
    listing = "\n".join(
        f"[{i}] {c['file']}:{c['line_start']}-{c['line_end']} — {c['title']} "
        f"(suspected: {c.get('suspected_cwe_ids')}, detector confidence: {c.get('confidence')})\n"
        f"    {c['explanation']}"
        for i, c in enumerate(candidate_findings)
    )
    state["messages"] = [
        HumanMessage(content=f"Validate these {len(candidate_findings)} candidate findings:\n\n{listing}")
    ]
    return agent.invoke(state)
