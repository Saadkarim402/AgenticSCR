#!/usr/bin/env python3
"""
Smoke test for DyRetriever's Select->Visit->Expand loop, no LLM API key
required. Runs against /tmp/toy_repo (queries.py: get_connection,
get_user_by_name; views.py: show_user, which calls get_user_by_name).

Scripted trajectory exercises every interesting path:
1. select entry point get_user_by_name
2. expand it -> reports ["get_connection", "sqlite3.connect"]
   -> get_connection resolves (real, local); sqlite3.connect is correctly
      discarded (not a local function)
3. select get_connection (newly discovered)
4. expand it -> reports [] (no further callees)
5. select the remaining entry point show_user
6. expand it -> reports ["get_user_by_name"] -> already visited, so it's
   correctly NOT re-added to the candidate pool
7. candidate pool now empty -> loop exits straight to topk WITHOUT another
   model call (trajectory has 3 items <= default top_k=5, so topk_node
   also skips its own model call) -> confirms the "skip LLM when not
   needed" short-circuits actually fire.
"""
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "dyretriever"))

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402

from engine import build_dyretriever_graph, get_context_snippets, new_dyretriever_state  # noqa: E402
from extractor import find_entry_points, parse_diff_new_line_ranges  # noqa: E402

REPO = "/tmp/toy_repo"


class FakeToolCallingModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def scripted_trajectory() -> list[AIMessage]:
    return [
        AIMessage(content="", tool_calls=[{
            "name": "select_candidate",
            "args": {"qualified_name": "app/db/queries.py::get_user_by_name", "reason": "entry point, changed in diff"},
            "id": "1",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "report_callees",
            "args": {"callee_names": ["get_connection", "sqlite3.connect"]},
            "id": "2",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "select_candidate",
            "args": {"qualified_name": "app/db/queries.py::get_connection", "reason": "callee of get_user_by_name, worth inspecting"},
            "id": "3",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "report_callees",
            "args": {"callee_names": []},
            "id": "4",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "select_candidate",
            "args": {"qualified_name": "app/web/views.py::show_user", "reason": "remaining entry point"},
            "id": "5",
        }]),
        AIMessage(content="", tool_calls=[{
            "name": "report_callees",
            "args": {"callee_names": ["get_user_by_name"]},
            "id": "6",
        }]),
    ]


def main():
    diff = subprocess.run(["git", "diff", "--staged"], cwd=REPO, capture_output=True, text=True).stdout
    ranges = parse_diff_new_line_ranges(diff)

    from extractor import build_function_registry
    registry = build_function_registry(REPO)
    entry_points = find_entry_points(registry, ranges)
    print("Entry points:", [e["qualified_name"] for e in entry_points])
    assert len(entry_points) == 2

    fake_model = FakeToolCallingModel(messages=iter(scripted_trajectory()))
    graph = build_dyretriever_graph(fake_model)

    state = new_dyretriever_state(
        REPO,
        entry_points=entry_points,
        target_description="Trace data flow around the staged SQL query changes",
        max_hops=10,
        top_k=5,
    )
    final = graph.invoke(state)

    print("\nVisited (in order):", final["visited"])
    print("hop_count:", final["hop_count"])
    print("top_k_result:", final["top_k_result"])
    print("candidate_pool remaining:", final["candidate_pool"])

    assert final["visited"] == [
        "app/db/queries.py::get_user_by_name",
        "app/db/queries.py::get_connection",
        "app/web/views.py::show_user",
    ], "visit order should match the scripted selections"

    assert final["hop_count"] == 3
    assert final["candidate_pool"] == [], "pool should be empty: sqlite3.connect filtered, get_user_by_name deduped"
    assert set(final["top_k_result"]) == set(final["visited"]), "trajectory <= top_k, should skip topk LLM call and return everything"

    snippets = get_context_snippets(final)
    print(f"\nContext snippets returned: {len(snippets)}")
    for s in snippets:
        print(f"  - {s['qualified_name']} ({s['file']})")
    assert len(snippets) == 3

    print("\nDyRetriever multi-hop loop verified end-to-end:")
    print("  - stdlib call (sqlite3.connect) correctly filtered out")
    print("  - already-visited callee (get_user_by_name) correctly deduped")
    print("  - both entry points from the diff were explored")
    print("  - Top-K short-circuit skipped an unnecessary LLM call")


if __name__ == "__main__":
    main()
