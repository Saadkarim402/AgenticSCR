"""
AgenticSCR Working Memory (M_w) — Piece 3

In LangGraph, "working memory" isn't a separate object off to the side —
it *is* the graph state that flows through and accumulates across nodes
within one review run. This module defines that state schema plus the
pieces needed to actually use it:

1. `DetectorState` — the TypedDict schema for the Detector subagent's graph.
   Holds the diff, changed files, a growing candidate-findings buffer, and
   caches for expensive tool calls.
2. `CandidateFinding` — a Pydantic model for one candidate security comment
   (the thing the Detector "emits" per the paper's step 3, before the
   Validator sees it).
3. `record_candidate_finding` — a stateful tool the Detector calls to push a
   structured finding into working memory. Uses LangGraph's `Command`
   pattern so the tool can update graph state directly (this is *the*
   Detector -> Validator handoff mechanism: the orchestrator reads
   `state["candidate_findings"]` after the Detector node finishes).
4. `wrap_with_cache` — wraps `open_files` / `expand_code_chunks` from the
   toolset so a second call for the same file/range returns from the graph
   state's cache instead of re-reading disk and re-spending context tokens
   on an identical tool result.
"""
from __future__ import annotations

from typing import Annotated, Optional, TypedDict

from langchain_core.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage
from langgraph.prebuilt import InjectedState
from langgraph.prebuilt.chat_agent_executor import AgentState
from langgraph.types import Command
from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# Candidate finding schema
# --------------------------------------------------------------------------

class CandidateFinding(BaseModel):
    """One candidate security comment emitted by the Detector, pending
    Validator review. Mirrors the paper's [Line, CWE-ID, Explanation]
    output shape, plus provenance fields the Validator needs to judge it."""

    file: str = Field(description="File path relative to repo root, e.g. 'app/db/queries.py'")
    line_start: int = Field(description="First affected line (1-indexed)")
    line_end: int = Field(description="Last affected line (1-indexed); same as line_start for single-line issues")
    title: str = Field(description="Short one-line summary of the issue")
    explanation: str = Field(description="Why this is a security issue, referencing the actual code")
    suspected_cwe_ids: list[str] = Field(
        default_factory=list,
        description="CWE IDs the Detector suspects apply, e.g. ['CWE-089']. May be empty if unsure — the Validator resolves this.",
    )
    matched_rule_id: Optional[str] = Field(
        default=None, description="SAST rule id from sast_rules.json that flagged this, if any, e.g. 'py/sql-injection'"
    )
    confidence: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Detector's own confidence 0-1 that this is a real issue"
    )


# --------------------------------------------------------------------------
# Graph state
# --------------------------------------------------------------------------

class DetectorState(AgentState):
    """Graph state for the Detector subagent's LangGraph run.
    This whole dict *is* Working Memory (M_w) for one review session.
    Extends LangGraph prebuilt's AgentState (which supplies `messages` with
    the add_messages reducer plus the `remaining_steps` key create_react_agent
    requires) with our own working-memory fields."""

    repo_path: str
    diff: str
    changed_files: list[str]
    file_cache: dict[str, str]                # open_files results, keyed by file path
    chunk_cache: dict[str, str]                # expand_code_chunks results, keyed "file:start-end"
    candidate_findings: list[dict]             # accumulating buffer -> handed to Validator
    tool_call_count: int


def new_detector_state(repo_path: str, diff: str = "", changed_files: list[str] | None = None) -> DetectorState:
    """Initialize a fresh working-memory state for one Detector run."""
    return DetectorState(
        messages=[],
        repo_path=repo_path,
        diff=diff,
        changed_files=changed_files or [],
        file_cache={},
        chunk_cache={},
        candidate_findings=[],
        tool_call_count=0,
    )


# --------------------------------------------------------------------------
# Stateful tool: record_candidate_finding
# --------------------------------------------------------------------------

@tool
def record_candidate_finding(
    file: str,
    line_start: int,
    line_end: int,
    title: str,
    explanation: str,
    suspected_cwe_ids: list[str],
    confidence: float,
    tool_call_id: Annotated[str, InjectedToolCallId],
    state: Annotated[dict, InjectedState],
    matched_rule_id: Optional[str] = None,
) -> Command:
    """Record a candidate security finding into working memory for later
    Validator review. Call this once per distinct issue you find — do not
    batch multiple issues into one call. suspected_cwe_ids can be an empty
    list if you're not sure which CWE applies; the Validator will resolve it
    against the CWE taxonomy."""
    finding = CandidateFinding(
        file=file,
        line_start=line_start,
        line_end=line_end,
        title=title,
        explanation=explanation,
        suspected_cwe_ids=suspected_cwe_ids,
        matched_rule_id=matched_rule_id,
        confidence=confidence,
    ).model_dump()

    updated = state.get("candidate_findings", []) + [finding]
    return Command(
        update={
            "candidate_findings": updated,
            "messages": [
                ToolMessage(
                    content=f"Recorded candidate finding #{len(updated)}: {title} ({file}:{line_start}-{line_end})",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


# --------------------------------------------------------------------------
# Caching wrappers around the toolset's read tools
# --------------------------------------------------------------------------

def wrap_with_cache(base_tools: list) -> list:
    """Wrap `open_files` and `expand_code_chunks` from toolset.build_toolset()
    so repeated calls for the same file/range hit the graph-state cache
    instead of re-reading disk and re-spending context tokens on an
    identical result. Other tools (diff, grep, folder) are passed through
    unchanged since their results are cheap and often deliberately re-run
    (e.g. grep with a different pattern each time).

    Returns a new tool list with the same length/order, ready to bind into
    a LangGraph agent alongside `record_candidate_finding`.
    """
    by_name = {t.name: t for t in base_tools}
    wrapped = []

    if "open_files" in by_name:
        original_open_files = by_name["open_files"]

        @tool
        def open_files_cached(
            file_paths: list[str],
            tool_call_id: Annotated[str, InjectedToolCallId],
            state: Annotated[dict, InjectedState],
        ) -> Command:
            """Read and return the full contents of one or more files, with
            line numbers. Paths are relative to the repository root. Results
            are cached in working memory — re-requesting an already-open
            file returns instantly from cache."""
            cache = dict(state.get("file_cache", {}))
            to_fetch = [p for p in file_paths if p not in cache]
            if to_fetch:
                fresh = original_open_files.invoke({"file_paths": to_fetch})
                # original returns one blob for all files; split back out by the "=== path ===" markers
                for block in fresh.split("\n\n=== "):
                    block = block if block.startswith("===") else "=== " + block
                    header_end = block.find(" ===\n")
                    if header_end == -1:
                        continue
                    path = block[4:header_end]
                    if path in to_fetch:
                        cache[path] = block

            result = "\n\n".join(cache[p] for p in file_paths if p in cache)
            hits = len(file_paths) - len(to_fetch)
            note = f"\n\n(cache: {hits} hit, {len(to_fetch)} fetched)" if hits else ""
            return Command(
                update={
                    "file_cache": cache,
                    "tool_call_count": state.get("tool_call_count", 0) + 1,
                    "messages": [ToolMessage(content=result + note, tool_call_id=tool_call_id)],
                }
            )

        wrapped.append(open_files_cached)
    else:
        wrapped.append(by_name.get("open_files"))

    if "expand_code_chunks" in by_name:
        original_expand_chunks = by_name["expand_code_chunks"]

        @tool
        def expand_code_chunks_cached(
            file_path: str,
            start_line: int,
            end_line: int,
            tool_call_id: Annotated[str, InjectedToolCallId],
            state: Annotated[dict, InjectedState],
            context_lines: int = 10,
        ) -> Command:
            """Return a specific line range from a file with surrounding
            context. Results are cached in working memory keyed by
            file+range+context — an identical repeated call returns from
            cache instead of re-reading the file."""
            key = f"{file_path}:{start_line}-{end_line}:{context_lines}"
            cache = dict(state.get("chunk_cache", {}))
            if key in cache:
                result = cache[key] + "\n\n(from cache)"
            else:
                result = original_expand_chunks.invoke(
                    {
                        "file_path": file_path,
                        "start_line": start_line,
                        "end_line": end_line,
                        "context_lines": context_lines,
                    }
                )
                cache[key] = result
            return Command(
                update={
                    "chunk_cache": cache,
                    "tool_call_count": state.get("tool_call_count", 0) + 1,
                    "messages": [ToolMessage(content=result, tool_call_id=tool_call_id)],
                }
            )

        wrapped.append(expand_code_chunks_cached)

    # pass through everything else unchanged
    for name, t in by_name.items():
        if name not in ("open_files", "expand_code_chunks"):
            wrapped.append(t)

    return [t for t in wrapped if t is not None]
