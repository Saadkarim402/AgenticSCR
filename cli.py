#!/usr/bin/env python3
"""
AgenticSCR CLI entry point. Requires ANTHROPIC_API_KEY for live use — not
runnable in this sandbox (no key here); see orchestrator/test_orchestrator.py
for a scripted, key-free verification of the same pipeline.

Usage:
    python cli.py review <repo_path> [--json] [--model MODEL_ID]

Exit code: 1 if any confirmed findings, 0 if clean (CI-friendly).
"""
import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "orchestrator"))


def main():
    parser = argparse.ArgumentParser(prog="agenticscr")
    sub = parser.add_subparsers(dest="command", required=True)
    review = sub.add_parser("review", help="Review the currently staged changes for security issues")
    review.add_argument("repo_path", help="Path to the git repository")
    review.add_argument("--json", action="store_true", help="Output machine-readable JSON instead of CLI text")
    review.add_argument("--model", default="claude-sonnet-4-6", help="Anthropic model to use")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY is not set. Export it and try again.", file=sys.stderr)
        sys.exit(1)

    from langchain_anthropic import ChatAnthropic
    from orchestrator import format_json_output, run_full_review

    model = ChatAnthropic(model=args.model)
    result = run_full_review(model, args.repo_path)

    if args.json:
        print(format_json_output(result["confirmed_findings"]))
    else:
        print(result["cli_output"])
        print(f"(full audit trail: {result['log_path']})", file=sys.stderr)

    sys.exit(1 if result["confirmed_findings"] else 0)


if __name__ == "__main__":
    main()
