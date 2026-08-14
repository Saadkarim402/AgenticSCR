#!/usr/bin/env python3
"""
Smoke test for the AgenticSCR toolset, run directly (no LLM involved) to
verify each tool works correctly against a real git repo before wiring it
into the Detector's LangGraph agent.
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "toolset"))
from tools import build_toolset  # noqa: E402

REPO = "/tmp/toy_repo"


def call(t, **kwargs):
    """LangChain tools are invoked via .invoke(dict) in newer versions."""
    return t.invoke(kwargs)


def main():
    tools = build_toolset(REPO)
    by_name = {t.name: t for t in tools}
    print("Registered tools:", list(by_name.keys()))
    print("=" * 70)

    print("\n### get_changed_files ###")
    print(call(by_name["get_changed_files"]))

    print("\n### get_staged_diff ###")
    print(call(by_name["get_staged_diff"]))

    print("\n### expand_folder('.', max_depth=3) ###")
    print(call(by_name["expand_folder"], folder_path=".", max_depth=3))

    print("\n### open_files(['app/db/queries.py']) ###")
    print(call(by_name["open_files"], file_paths=["app/db/queries.py"]))

    print("\n### expand_code_chunks (around the sink) ###")
    print(
        call(
            by_name["expand_code_chunks"],
            file_path="app/db/queries.py",
            start_line=9,
            end_line=10,
            context_lines=3,
        )
    )

    print("\n### grep('get_user_by_name') ###")
    print(call(by_name["grep"], pattern="get_user_by_name"))

    print("\n### grep on nonexistent pattern ###")
    print(call(by_name["grep"], pattern="THIS_WONT_MATCH_ANYTHING_XYZ"))

    print("\n### path traversal guard: open_files(['../../etc/passwd']) ###")
    print(call(by_name["open_files"], file_paths=["../../etc/passwd"]))

    print("\n### missing file: open_files(['nope.py']) ###")
    print(call(by_name["open_files"], file_paths=["nope.py"]))

    print("\nAll tool calls completed without raising.")


if __name__ == "__main__":
    main()
