#!/usr/bin/env python3
"""
Smoke test for Piece 3 (Working Memory), no LLM required.

Verifies:
1. `new_detector_state` initializes correctly.
2. `record_candidate_finding` returns a Command that correctly appends to
   state["candidate_findings"] (simulating what LangGraph's ToolNode does
   when it applies the Command to graph state).
3. `wrap_with_cache` actually caches: a second identical open_files/
   expand_code_chunks call doesn't re-hit disk and the cache dict grows
   correctly across sequential calls (as it would across graph steps).
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "toolset"))
sys.path.insert(0, str(_REPO_ROOT / "working_memory"))

from tools import build_toolset  # noqa: E402
from working_memory import (  # noqa: E402
    new_detector_state,
    record_candidate_finding,
    wrap_with_cache,
)

REPO = "/tmp/toy_repo"


def invoke_stateful(t, args: dict, call_id: str, state: dict):
    """Tools with InjectedToolCallId must be invoked via the full ToolCall
    dict form, not a plain args dict — LangChain injects tool_call_id from
    the 'id' field automatically."""
    return t.invoke({"name": t.name, "args": {**args, "state": state}, "id": call_id, "type": "tool_call"})


def apply_command(state: dict, command) -> dict:
    """Mimic what LangGraph does when a tool returns a Command(update=...):
    merge the update dict into state. (messages use add_messages reducer;
    here we just append for the smoke test.)"""
    new_state = dict(state)
    for k, v in command.update.items():
        if k == "messages":
            new_state["messages"] = state.get("messages", []) + v
        else:
            new_state[k] = v
    return new_state


def main():
    state = new_detector_state(REPO)
    print("Initial state keys:", list(state.keys()))
    assert state["candidate_findings"] == []
    assert state["file_cache"] == {}

    # --- test record_candidate_finding -----------------------------------
    cmd = invoke_stateful(
        record_candidate_finding,
        {
            "file": "app/db/queries.py",
            "line_start": 10,
            "line_end": 10,
            "title": "SQL query built with string formatting",
            "explanation": "User-controlled `name` is interpolated into the SQL string via %-formatting, allowing SQL injection.",
            "suspected_cwe_ids": ["CWE-089"],
            "matched_rule_id": "py/sql-injection",
            "confidence": 0.9,
        },
        "call_1",
        state,
    )
    state = apply_command(state, cmd)
    print("\nAfter 1 finding recorded:")
    print(" candidate_findings count:", len(state["candidate_findings"]))
    print(" finding:", state["candidate_findings"][0])
    assert len(state["candidate_findings"]) == 1
    assert state["candidate_findings"][0]["suspected_cwe_ids"] == ["CWE-089"]

    # record a second finding to confirm accumulation (not overwrite)
    cmd2 = invoke_stateful(
        record_candidate_finding,
        {
            "file": "app/web/views.py",
            "line_start": 6,
            "line_end": 6,
            "title": "Unsanitized user input reflected in response",
            "explanation": "`username` from request.args is placed directly into the response string.",
            "suspected_cwe_ids": ["CWE-079"],
            "matched_rule_id": None,
            "confidence": 0.4,
        },
        "call_2",
        state,
    )
    state = apply_command(state, cmd2)
    assert len(state["candidate_findings"]) == 2, "findings should accumulate, not overwrite"
    print(" after 2nd finding, count:", len(state["candidate_findings"]))

    # --- test caching wrapper ----------------------------------------------
    base_tools = build_toolset(REPO)
    cached_tools = wrap_with_cache(base_tools)
    by_name = {t.name: t for t in cached_tools}
    print("\nWrapped tool names:", list(by_name.keys()))

    open_files_cached = by_name["open_files_cached"]

    cmd3 = invoke_stateful(open_files_cached, {"file_paths": ["app/db/queries.py"]}, "call_3", state)
    state = apply_command(state, cmd3)
    first_result = state["messages"][-1].content
    print("\nFirst open_files call (cache miss):")
    print(" cache note in result:", "cache: 0 hit, 1 fetched" in first_result)
    assert "app/db/queries.py" in state["file_cache"]

    cmd4 = invoke_stateful(open_files_cached, {"file_paths": ["app/db/queries.py"]}, "call_4", state)
    state = apply_command(state, cmd4)
    second_result = state["messages"][-1].content
    print("Second open_files call (should be cache hit):")
    print(" cache note in result:", "cache: 1 hit, 0 fetched" in second_result)
    assert "cache: 1 hit, 0 fetched" in second_result, "second identical call should be a full cache hit"

    expand_cached = by_name["expand_code_chunks_cached"]
    cmd5 = invoke_stateful(
        expand_cached,
        {"file_path": "app/db/queries.py", "start_line": 9, "end_line": 10, "context_lines": 2},
        "call_5",
        state,
    )
    state = apply_command(state, cmd5)
    assert len(state["chunk_cache"]) == 1
    cmd6 = invoke_stateful(
        expand_cached,
        {"file_path": "app/db/queries.py", "start_line": 9, "end_line": 10, "context_lines": 2},
        "call_6",
        state,
    )
    state = apply_command(state, cmd6)
    assert "(from cache)" in state["messages"][-1].content
    print("\nexpand_code_chunks cache hit confirmed: (from cache) present in repeat call")

    print("\ntool_call_count tracked:", state["tool_call_count"])
    print("\nAll working-memory checks passed.")


if __name__ == "__main__":
    main()
