#!/usr/bin/env python3
"""
Smoke test for Piece 6 (Orchestrator), no LLM API key required.

Chains the exact scripted trajectories from test_detector.py and
test_validator.py through ONE shared fake model instance (matching how a
real run uses a single model for both subagents), then verifies:
1. run_full_review() correctly sequences Detector -> Validator.
2. The episodic log file is written with the right header counts and one
   event per message across both subagents.
3. format_cli_output() produces the expected [Line, CWE-ID, Explanation]
   text.
4. The empty-candidate-findings path (clean diff) skips the Validator
   entirely and short-circuits correctly.
"""
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "orchestrator"))
sys.path.insert(0, str(_REPO_ROOT / "detector"))
sys.path.insert(0, str(_REPO_ROOT / "validator"))

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402

import test_detector  # noqa: E402
import test_validator  # noqa: E402
from orchestrator import DEFAULT_LOG_DIR, format_cli_output, run_full_review  # noqa: E402

REPO = "/tmp/toy_repo"


class FakeToolCallingModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_full_pipeline():
    combined = test_detector.scripted_trajectory() + test_validator.scripted_trajectory()
    model = FakeToolCallingModel(messages=iter(combined))

    result = run_full_review(model, REPO)

    print("run_id:", result["run_id"])
    print("candidate_findings:", len(result["candidate_findings"]))
    print("confirmed_findings:", len(result["confirmed_findings"]))
    print("rejected_findings:", len(result["rejected_findings"]))
    print("log_path:", result["log_path"])
    print()
    print("--- cli_output ---")
    print(result["cli_output"])

    assert len(result["candidate_findings"]) == 2
    assert len(result["confirmed_findings"]) == 1
    assert len(result["rejected_findings"]) == 1
    assert result["confirmed_findings"][0]["final_cwe_id"] == "CWE-089"

    assert "Found 1 confirmed security issue:" in result["cli_output"]
    assert "app/db/queries.py" in result["cli_output"]
    assert "[CWE-089]" in result["cli_output"]
    assert "L10" in result["cli_output"]
    # the rejected duplicate must NOT appear in the output
    assert "app/web/views.py" not in result["cli_output"]

    # --- verify the episodic log file ---------------------------------
    import json
    from pathlib import Path

    log_path = Path(result["log_path"])
    assert log_path.exists(), "episodic log file should have been written"
    lines = log_path.read_text().strip().splitlines()
    header = json.loads(lines[0])
    print("--- log header ---")
    print(json.dumps(header, indent=2))

    assert header["detector_candidate_count"] == 2
    assert header["validator_confirmed_count"] == 1
    assert header["validator_rejected_count"] == 1
    assert header["repo_path"] == REPO

    events = [json.loads(l) for l in lines[1:]]
    detector_events = [e for e in events if e["subagent"] == "detector"]
    validator_events = [e for e in events if e["subagent"] == "validator"]
    assert len(detector_events) > 0 and len(validator_events) > 0
    # spot-check: the confirmed verdict's tool call should be traceable in the log
    confirm_calls = [
        e for e in validator_events
        if e.get("role") == "assistant" and e.get("tool_calls")
        and any(c["name"] == "record_validation_verdict" for c in e["tool_calls"])
    ]
    assert len(confirm_calls) == 2, "both verdict tool calls should be in the audit trail"

    print(f"\nEpisodic log verified: {len(events)} events ({len(detector_events)} detector, {len(validator_events)} validator)")


def test_empty_candidate_path():
    """A model that immediately stops with no tool calls -> Detector finds
    nothing -> Validator should never even be built."""
    model = FakeToolCallingModel(messages=iter([AIMessage(content="Reviewed the staged diff. No security-relevant changes found.")]))
    result = run_full_review(model, REPO)

    print("\n--- empty-candidate path ---")
    print(result["cli_output"])
    assert result["candidate_findings"] == []
    assert result["confirmed_findings"] == []
    assert "No candidate findings" in result["cli_output"]
    from pathlib import Path
    assert Path(result["log_path"]).exists()
    print("Empty-candidate short-circuit verified: Validator correctly skipped.")


def test_format_cli_output_no_findings():
    assert format_cli_output([]) == "No confirmed security issues found in the staged changes.\n"


if __name__ == "__main__":
    test_full_pipeline()
    test_empty_candidate_path()
    test_format_cli_output_no_findings()
    print("\nAll orchestrator checks passed. Full pipeline verified end-to-end (scripted model).")
