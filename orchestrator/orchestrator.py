"""
AgenticSCR Orchestrator (Pi) + Episodic Memory (M_e) + Output Formatting
— Piece 6, the last piece

Sequences the two subagents built in Pieces 4 and 5:
  Detector(repo_path) -> candidate_findings -> Validator(candidates) -> confirmed_findings

Also:
- Writes an episodic memory audit trail (M_e from the original design doc)
  — every tool call, tool result, and assistant message from both subagent
  runs, as one JSONL file per review run, for offline debugging ("why did
  it flag/drop this line").
- Formats `confirmed_findings` into the final output: [Line, CWE-ID,
  Explanation] per finding — the paper's stated output shape — plus a
  machine-readable JSON variant for CI/PR-bot integration.

Running this end-to-end with a real model is the whole AgenticSCR tool.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "detector"))
sys.path.insert(0, str(_REPO_ROOT / "validator"))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402

from detector import build_detector_agent, run_detector  # noqa: E402
from validator import build_validator_agent, run_validator  # noqa: E402

DEFAULT_LOG_DIR = (_REPO_ROOT / "logs")


# --------------------------------------------------------------------------
# Episodic memory (M_e)
# --------------------------------------------------------------------------

def _message_to_event(msg, subagent: str) -> dict:
    if isinstance(msg, HumanMessage):
        return {"subagent": subagent, "role": "human", "content": msg.content}
    if isinstance(msg, SystemMessage):
        return {"subagent": subagent, "role": "system", "content": msg.content}
    if isinstance(msg, ToolMessage):
        return {
            "subagent": subagent,
            "role": "tool_result",
            "tool_call_id": msg.tool_call_id,
            "content": str(msg.content),
        }
    if isinstance(msg, AIMessage):
        event = {"subagent": subagent, "role": "assistant", "content": msg.content}
        if msg.tool_calls:
            event["tool_calls"] = [{"name": c["name"], "args": c["args"]} for c in msg.tool_calls]
        return event
    return {"subagent": subagent, "role": "unknown", "content": str(msg)}


def write_episodic_log(
    run_id: str,
    repo_path: str,
    detector_result: dict,
    validator_result: Optional[dict],
    log_dir: Path = DEFAULT_LOG_DIR,
) -> Path:
    """Write one JSONL file per review run: a header line with run
    metadata, then one line per message event across both subagents, in
    order. This is the tool's episodic audit trail — everything needed to
    reconstruct why a given finding was flagged or dropped, without
    re-running the model."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{run_id}.jsonl"

    with log_path.open("w") as f:
        header = {
            "run_id": run_id,
            "repo_path": repo_path,
            "timestamp": time.time(),
            "detector_tool_call_count": detector_result.get("tool_call_count"),
            "detector_candidate_count": len(detector_result.get("candidate_findings", [])),
            "validator_confirmed_count": len(validator_result["confirmed_findings"]) if validator_result else 0,
            "validator_rejected_count": len(validator_result["rejected_findings"]) if validator_result else 0,
        }
        f.write(json.dumps(header) + "\n")

        for msg in detector_result.get("messages", []):
            f.write(json.dumps(_message_to_event(msg, "detector")) + "\n")
        if validator_result:
            for msg in validator_result.get("messages", []):
                f.write(json.dumps(_message_to_event(msg, "validator")) + "\n")

    return log_path


# --------------------------------------------------------------------------
# Output formatting
# --------------------------------------------------------------------------

def format_cli_output(confirmed_findings: list[dict]) -> str:
    """Format confirmed findings as [Line, CWE-ID, Explanation], grouped by
    file and sorted by line number — the paper's stated final output shape."""
    if not confirmed_findings:
        return "No confirmed security issues found in the staged changes.\n"

    by_file: dict[str, list[dict]] = {}
    for f in confirmed_findings:
        by_file.setdefault(f["file"], []).append(f)

    total = len(confirmed_findings)
    lines = [f"Found {total} confirmed security issue{'s' if total != 1 else ''}:\n"]
    for file in sorted(by_file):
        lines.append(file)
        for finding in sorted(by_file[file], key=lambda x: x["line_start"]):
            loc = (
                str(finding["line_start"])
                if finding["line_start"] == finding["line_end"]
                else f"{finding['line_start']}-{finding['line_end']}"
            )
            cwe = finding.get("final_cwe_id") or "CWE-unclassified"
            lines.append(f"  L{loc}  [{cwe}]  {finding['title']}")
            lines.append(f"         {finding['validator_explanation']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def format_json_output(confirmed_findings: list[dict]) -> str:
    """Machine-readable equivalent, for CI integration / PR bots."""
    slim = [
        {
            "file": f["file"],
            "line_start": f["line_start"],
            "line_end": f["line_end"],
            "cwe_id": f.get("final_cwe_id"),
            "title": f["title"],
            "explanation": f["validator_explanation"],
            "confidence": f.get("validator_confidence"),
        }
        for f in confirmed_findings
    ]
    return json.dumps(slim, indent=2)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def run_full_review(model, repo_path: str, log_dir: Path = DEFAULT_LOG_DIR) -> dict:
    """Run the complete AgenticSCR pipeline: Detector -> Validator ->
    episodic log -> formatted output. `model` drives both subagents (and,
    inside the Detector, DyRetriever's internal hops too — see Piece 4)."""
    run_id = uuid.uuid4().hex[:12]

    detector_agent = build_detector_agent(model, repo_path)
    detector_result = run_detector(detector_agent, repo_path)
    candidate_findings = detector_result.get("candidate_findings", [])

    if not candidate_findings:
        log_path = write_episodic_log(run_id, repo_path, detector_result, None, log_dir)
        return {
            "run_id": run_id,
            "candidate_findings": [],
            "confirmed_findings": [],
            "rejected_findings": [],
            "cli_output": (
                "No candidate findings — nothing to validate. Staged changes "
                "look clean, or contain no security-relevant modifications.\n"
            ),
            "log_path": str(log_path),
        }

    validator_agent = build_validator_agent(model, repo_path)
    validator_result = run_validator(validator_agent, repo_path, candidate_findings)
    confirmed = validator_result.get("confirmed_findings", [])
    rejected = validator_result.get("rejected_findings", [])

    log_path = write_episodic_log(run_id, repo_path, detector_result, validator_result, log_dir)

    return {
        "run_id": run_id,
        "candidate_findings": candidate_findings,
        "confirmed_findings": confirmed,
        "rejected_findings": rejected,
        "cli_output": format_cli_output(confirmed),
        "log_path": str(log_path),
    }
