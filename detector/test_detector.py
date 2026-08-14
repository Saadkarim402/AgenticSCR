#!/usr/bin/env python3
"""
Smoke test for Piece 4 (Detector Subagent), no LLM API key required.

Uses a scripted fake chat model that plays out a realistic Detector
trajectory against /tmp/toy_repo's staged SQL-injection diff:
  get_staged_diff -> search_sast_rules -> expand_code_chunks ->
  record_candidate_finding -> grep -> record_candidate_finding -> (stop)

This verifies the actual LangGraph graph (create_react_agent + our
DetectorState + our tools) compiles and executes correctly end-to-end:
tool routing, state accumulation across turns, and the ReAct loop's stop
condition (an AIMessage with no tool_calls ends the run). Swapping the fake
model for `ChatAnthropic(model="claude-sonnet-4-6")` is the only change
needed to go live.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "detector"))

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402

from detector import build_detector_agent, run_detector  # noqa: E402

REPO = "/tmp/toy_repo"


class FakeToolCallingModel(GenericFakeChatModel):
    """A GenericFakeChatModel that supports .bind_tools() (no-op — the
    scripted messages already carry the tool_calls to emit)."""

    def bind_tools(self, tools, **kwargs):
        return self


def scripted_trajectory() -> list[AIMessage]:
    return [
        # 1. look at the diff
        AIMessage(
            content="",
            tool_calls=[{"name": "get_staged_diff", "args": {}, "id": "call_1"}],
        ),
        # 2. search the rule base for a matching pattern
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "search_sast_rules",
                    "args": {"query": "sql injection string formatting"},
                    "id": "call_2",
                }
            ],
        ),
        # 3. pull context around the sink to confirm exploitability
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "expand_code_chunks_cached",
                    "args": {"file_path": "app/db/queries.py", "start_line": 9, "end_line": 10, "context_lines": 5},
                    "id": "call_3",
                }
            ],
        ),
        # 4. record the SQL injection finding
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "record_candidate_finding",
                    "args": {
                        "file": "app/db/queries.py",
                        "line_start": 10,
                        "line_end": 10,
                        "title": "SQL query built via %-string formatting",
                        "explanation": "`name` is interpolated directly into the SQL string with %-formatting instead of being passed as a bound parameter, allowing SQL injection if `name` is user-controlled.",
                        "suspected_cwe_ids": ["CWE-089"],
                        "matched_rule_id": "py/sql-injection",
                        "confidence": 0.85,
                    },
                    "id": "call_4",
                }
            ],
        ),
        # 5. trace whether `name` is actually user-controlled
        AIMessage(
            content="",
            tool_calls=[{"name": "grep", "args": {"pattern": "get_user_by_name"}, "id": "call_5"}],
        ),
        # 6. record a second, lower-confidence finding (reflected input)
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "record_candidate_finding",
                    "args": {
                        "file": "app/web/views.py",
                        "line_start": 6,
                        "line_end": 6,
                        "title": "Unsanitized request parameter reaches SQL query",
                        "explanation": "`username` comes from `request.args.get(\"username\")` and flows unmodified into `get_user_by_name`, confirming the SQL injection sink upstream is reachable from user input.",
                        "suspected_cwe_ids": ["CWE-089", "CWE-020"],
                        "matched_rule_id": None,
                        "confidence": 0.8,
                    },
                    "id": "call_6",
                }
            ],
        ),
        # 7. done — no more tool calls, loop should stop
        AIMessage(content="Reviewed the staged diff and recorded 2 candidate findings."),
    ]


def main():
    fake_model = FakeToolCallingModel(messages=iter(scripted_trajectory()))
    agent = build_detector_agent(fake_model, REPO)

    result = run_detector(agent, REPO)

    findings = result["candidate_findings"]
    print(f"candidate_findings recorded: {len(findings)}")
    for f in findings:
        print(f"  - {f['file']}:{f['line_start']} [{','.join(f['suspected_cwe_ids'])}] {f['title']} (confidence={f['confidence']})")

    assert len(findings) == 2, f"expected 2 findings, got {len(findings)}"
    assert findings[0]["file"] == "app/db/queries.py"
    assert "CWE-089" in findings[0]["suspected_cwe_ids"]
    assert findings[1]["file"] == "app/web/views.py"

    print(f"\ntool_call_count: {result.get('tool_call_count')}")
    print(f"total messages in transcript: {len(result['messages'])}")

    final_ai_messages = [m for m in result["messages"] if isinstance(m, AIMessage)]
    print(f"final assistant message: {final_ai_messages[-1].content!r}")
    assert "2 candidate findings" in final_ai_messages[-1].content

    print("\nDetector graph wiring verified end-to-end (scripted model).")


if __name__ == "__main__":
    main()
