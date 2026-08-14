#!/usr/bin/env python3
"""
Smoke test for Piece 5 (Validator Subagent), no LLM API key required.

Feeds in the exact 2 candidate findings the Detector (Piece 4) produced
against /tmp/toy_repo's staged SQL-injection diff, and scripts a realistic
Validator trajectory:
  search_cwe_tree(cwe_id="CWE-089") -> confirm candidate 0 as CWE-089
  -> reject candidate 1 as a duplicate (same root cause, not a separate
     vulnerability) -> stop

Also directly tests record_validation_verdict's out-of-range guard.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "validator"))

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402

from validator import build_validator_agent, record_validation_verdict, run_validator  # noqa: E402

REPO = "/tmp/toy_repo"

# exactly what test_detector.py produced
CANDIDATE_FINDINGS = [
    {
        "file": "app/db/queries.py",
        "line_start": 10,
        "line_end": 10,
        "title": "SQL query built via %-string formatting",
        "explanation": "`name` is interpolated directly into the SQL string with %-formatting instead of being passed as a bound parameter, allowing SQL injection if `name` is user-controlled.",
        "suspected_cwe_ids": ["CWE-089"],
        "matched_rule_id": "py/sql-injection",
        "confidence": 0.85,
    },
    {
        "file": "app/web/views.py",
        "line_start": 6,
        "line_end": 6,
        "title": "Unsanitized request parameter reaches SQL query",
        "explanation": "`username` comes from `request.args.get(\"username\")` and flows unmodified into `get_user_by_name`, confirming the SQL injection sink upstream is reachable from user input.",
        "suspected_cwe_ids": ["CWE-089", "CWE-020"],
        "matched_rule_id": None,
        "confidence": 0.8,
    },
]


class FakeToolCallingModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def scripted_trajectory() -> list[AIMessage]:
    return [
        AIMessage(
            content="",
            tool_calls=[{"name": "search_cwe_tree", "args": {"cwe_id": "CWE-089"}, "id": "v1"}],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "record_validation_verdict",
                    "args": {
                        "candidate_index": 0,
                        "verdict": "confirmed",
                        "final_cwe_id": "CWE-089",
                        "explanation": "Confirmed: %-formatting builds the SQL string directly from `name` with no parameterization. Matches CWE-089 exactly.",
                        "confidence": 0.95,
                    },
                    "id": "v2",
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "record_validation_verdict",
                    "args": {
                        "candidate_index": 1,
                        "verdict": "rejected",
                        "final_cwe_id": None,
                        "explanation": "Duplicate of finding #0 — this is the same SQL injection sink observed from the caller's side (username flowing into get_user_by_name), not a separate vulnerability. Already captured by the confirmed finding.",
                        "confidence": 0.6,
                    },
                    "id": "v3",
                }
            ],
        ),
        AIMessage(content="Reviewed 2 candidates: 1 confirmed, 1 rejected as a duplicate."),
    ]


def main():
    # --- out-of-range guard, tested directly (no graph needed) ------------
    dummy_state = {"candidate_findings": CANDIDATE_FINDINGS}
    result = record_validation_verdict.invoke(
        {
            "name": "record_validation_verdict",
            "args": {
                "candidate_index": 99,
                "verdict": "confirmed",
                "explanation": "x",
                "confidence": 0.5,
                "final_cwe_id": "CWE-089",
                "state": dummy_state,
            },
            "id": "bad_call",
            "type": "tool_call",
        }
    )
    assert "out of range" in result.update["messages"][0].content
    print("Out-of-range candidate_index guard: OK")
    print(" ->", result.update["messages"][0].content)

    # --- full graph run -----------------------------------------------------
    fake_model = FakeToolCallingModel(messages=iter(scripted_trajectory()))
    agent = build_validator_agent(fake_model, REPO)
    final = run_validator(agent, REPO, CANDIDATE_FINDINGS)

    confirmed = final["confirmed_findings"]
    rejected = final["rejected_findings"]
    print(f"\nconfirmed_findings: {len(confirmed)}")
    for f in confirmed:
        print(f"  - {f['file']}:{f['line_start']} -> {f['final_cwe_id']} (validator confidence={f['validator_confidence']})")
    print(f"rejected_findings: {len(rejected)}")
    for f in rejected:
        print(f"  - {f['file']}:{f['line_start']} -> {f['validator_explanation']}")

    assert len(confirmed) == 1, f"expected 1 confirmed, got {len(confirmed)}"
    assert len(rejected) == 1, f"expected 1 rejected, got {len(rejected)}"
    assert confirmed[0]["file"] == "app/db/queries.py"
    assert confirmed[0]["final_cwe_id"] == "CWE-089"
    assert rejected[0]["file"] == "app/web/views.py"
    assert "duplicate" in rejected[0]["validator_explanation"].lower()

    from langchain_core.messages import AIMessage as AIM
    final_ai = [m for m in final["messages"] if isinstance(m, AIM)][-1]
    print(f"\nfinal assistant message: {final_ai.content!r}")
    assert "1 confirmed" in final_ai.content and "1 rejected" in final_ai.content

    print("\nValidator graph wiring verified end-to-end (scripted model).")
    print("Final output ready for CLI: [Line, CWE-ID, Explanation] x", len(confirmed))


if __name__ == "__main__":
    main()
