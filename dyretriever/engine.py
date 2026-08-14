"""
DyRetriever — Part B: Multi-Hop Reasoning Loop (Select -> Visit -> Expand)

Implements the paper's core loop as a LangGraph StateGraph (not a ReAct
agent — this is a structured 3-step pipeline with a bounded loop, not
free-form tool calling). Each hop makes exactly one small, forced-tool-call
LLM decision:

  select  — pick the most relevant unvisited candidate to explore next
            (or decide to stop)
  visit   — deterministic: pull the function's code from the registry
  expand  — read the visited function's code + its file's imports, report
            downstream callee names it thinks are worth exploring

Between "expand" and the next "select", every LLM-reported callee name is
resolved against the local function registry (extractor.py) — anything
that isn't a real local function (stdlib calls, hallucinations, third-party
APIs) is silently discarded. This is the paper's Part C post-filter.

After the loop ends (hop limit reached, or no candidates left, or the model
chose to stop), a Top-K pass ranks the full trajectory and keeps only the
most relevant functions as final context for the Detector — but skips the
extra LLM call entirely when the trajectory is already <= K (no need to
"rank" 3 functions down to 5).
"""
from __future__ import annotations

from typing import Optional, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from extractor import build_function_registry, build_simple_name_index, resolve_call_name


# --------------------------------------------------------------------------
# Forced-choice "tools" — these aren't agent tools the model chooses freely
# among; each node binds exactly the one(s) relevant to that decision and
# forces a call, so they're really structured-output schemas wearing a
# tool-calling hat (keeps one consistent interface across fake/live models).
# --------------------------------------------------------------------------

@tool
def select_candidate(qualified_name: str, reason: str) -> str:
    """Pick the single most relevant candidate function to explore next,
    given the task and what's already been visited."""
    return f"selected {qualified_name}: {reason}"


@tool
def stop_traversal(reason: str) -> str:
    """Stop the traversal now — no remaining candidate is relevant enough
    to justify another hop."""
    return f"stopped: {reason}"


@tool
def report_callees(callee_names: list[str]) -> str:
    """Report the names of downstream functions this code calls that are
    worth exploring further (skip stdlib/third-party calls — only ones
    that look like they belong to this repo). Empty list if none."""
    return f"reported {len(callee_names)} callees"


@tool
def select_top_k(qualified_names: list[str]) -> str:
    """Pick the K most relevant qualified function names from the full
    traversal, ordered most- to least-relevant."""
    return f"selected top {len(qualified_names)}"


SELECT_SYSTEM_PROMPT = """\
You are the Select step of a code dependency traversal. Task context: {target}

You'll be shown a list of unvisited candidate functions and the functions \
already visited. Pick the ONE candidate most likely to matter for \
understanding the security implications of the task — e.g. a function that \
handles the same data, or that the changed code calls. Call \
`stop_traversal` instead if no remaining candidate looks relevant, or if \
you already have enough context.
"""

EXPAND_SYSTEM_PROMPT = """\
You are the Expand step of a code dependency traversal. You'll be shown one \
function's source code and its file's imports. Identify any downstream \
function calls in this code that are worth exploring further — functions \
that plausibly live elsewhere in this repository. Skip standard library \
calls, third-party package calls, and builtins. Call `report_callees` with \
just the bare or dotted names as they appear in the code (e.g. \
'get_connection' or 'db.get_connection') — don't guess at full paths.
"""

TOPK_SYSTEM_PROMPT = """\
You are the Top-K step. Task context: {target}

You'll be shown every function visited during traversal. Select the {k} \
most relevant ones for understanding the security implications of the \
task, ordered most-relevant first. Call `select_top_k`.
"""


class DyRetrieverState(TypedDict):
    repo_path: str
    target_description: str
    registry: dict
    simple_index: dict
    candidate_pool: list[dict]        # [{"qualified_name","reason","discovered_from"?}]
    visited: list[str]
    trajectory: list[dict]            # [{"qualified_name","file","code"}]
    hop_count: int
    max_hops: int
    top_k: int
    stopped: bool
    selected: Optional[str]
    top_k_result: list[str]


def new_dyretriever_state(
    repo_path: str,
    entry_points: list[dict],
    target_description: str,
    max_hops: int = 10,
    top_k: int = 5,
) -> DyRetrieverState:
    registry = build_function_registry(repo_path)
    simple_index = build_simple_name_index(registry)
    return DyRetrieverState(
        repo_path=repo_path,
        target_description=target_description,
        registry=registry,
        simple_index=simple_index,
        candidate_pool=list(entry_points),
        visited=[],
        trajectory=[],
        hop_count=0,
        max_hops=max_hops,
        top_k=top_k,
        stopped=False,
        selected=None,
        top_k_result=[],
    )


def build_dyretriever_graph(model):
    """Build the compiled Select->Visit->Expand->[loop]->TopK graph.
    `model` needs `.bind_tools()`; pass a real ChatAnthropic for live use."""

    def select_node(state: DyRetrieverState) -> dict:
        pool = state["candidate_pool"]
        if not pool:
            return {"stopped": True}

        listing = "\n".join(
            f"- {c['qualified_name']}: {c['reason']}" for c in pool
        )
        visited_listing = ", ".join(state["visited"]) or "(none yet)"
        sys = SELECT_SYSTEM_PROMPT.format(target=state["target_description"])
        human = HumanMessage(
            content=f"Unvisited candidates:\n{listing}\n\nAlready visited: {visited_listing}"
        )
        bound = model.bind_tools([select_candidate, stop_traversal])
        resp = bound.invoke([SystemMessage(content=sys), human])
        if not resp.tool_calls:
            return {"stopped": True}
        call = resp.tool_calls[0]
        if call["name"] == "stop_traversal":
            return {"stopped": True}

        chosen = call["args"]["qualified_name"]
        valid_names = {c["qualified_name"] for c in pool}
        if chosen not in valid_names:
            # model picked something not actually offered — fail safe, stop
            return {"stopped": True}
        return {"selected": chosen, "stopped": False}

    def visit_node(state: DyRetrieverState) -> dict:
        name = state["selected"]
        entry = state["registry"].get(name)
        new_pool = [c for c in state["candidate_pool"] if c["qualified_name"] != name]
        if entry is None:
            return {"candidate_pool": new_pool}
        return {
            "visited": state["visited"] + [name],
            "trajectory": state["trajectory"] + [{"qualified_name": name, "file": entry["file"], "code": entry["code"]}],
            "candidate_pool": new_pool,
            "hop_count": state["hop_count"] + 1,
        }

    def expand_node(state: DyRetrieverState) -> dict:
        last = state["trajectory"][-1]
        imports = state["registry"][last["qualified_name"]]["imports"]
        human = HumanMessage(
            content=f"Function {last['qualified_name']} in {last['file']}:\n\n{last['code']}\n\nFile imports: {imports}"
        )
        bound = model.bind_tools([report_callees])
        resp = bound.invoke([SystemMessage(content=EXPAND_SYSTEM_PROMPT), human])
        raw_names = []
        if resp.tool_calls:
            raw_names = resp.tool_calls[0]["args"].get("callee_names", [])

        visited_set = set(state["visited"])
        pool_names = {c["qualified_name"] for c in state["candidate_pool"]}
        added = []
        for raw in raw_names:
            resolved = resolve_call_name(raw, last["file"], state["registry"], state["simple_index"])
            if resolved and resolved not in visited_set and resolved not in pool_names:
                added.append({
                    "qualified_name": resolved,
                    "reason": f"called from {last['qualified_name']}",
                    "discovered_from": last["qualified_name"],
                })
                pool_names.add(resolved)

        return {"candidate_pool": state["candidate_pool"] + added}

    def after_select(state: DyRetrieverState) -> str:
        return "topk" if state.get("stopped") else "visit"

    def after_expand(state: DyRetrieverState) -> str:
        if state["hop_count"] >= state["max_hops"]:
            return "topk"
        if not state["candidate_pool"]:
            return "topk"
        return "select"

    def topk_node(state: DyRetrieverState) -> dict:
        traj_names = [t["qualified_name"] for t in state["trajectory"]]
        k = state["top_k"]
        if len(traj_names) <= k:
            return {"top_k_result": traj_names}

        listing = "\n".join(f"- {t['qualified_name']} ({t['file']})" for t in state["trajectory"])
        sys = TOPK_SYSTEM_PROMPT.format(target=state["target_description"], k=k)
        human = HumanMessage(content=f"Visited functions:\n{listing}")
        bound = model.bind_tools([select_top_k])
        resp = bound.invoke([SystemMessage(content=sys), human])
        if not resp.tool_calls:
            return {"top_k_result": traj_names[:k]}
        chosen = [n for n in resp.tool_calls[0]["args"].get("qualified_names", []) if n in traj_names]
        return {"top_k_result": chosen or traj_names[:k]}

    graph = StateGraph(DyRetrieverState)
    graph.add_node("select", select_node)
    graph.add_node("visit", visit_node)
    graph.add_node("expand", expand_node)
    graph.add_node("topk", topk_node)
    graph.set_entry_point("select")
    graph.add_conditional_edges("select", after_select, {"visit": "visit", "topk": "topk"})
    graph.add_edge("visit", "expand")
    graph.add_conditional_edges("expand", after_expand, {"select": "select", "topk": "topk"})
    graph.add_edge("topk", END)
    return graph.compile()


def get_context_snippets(final_state: dict) -> list[dict]:
    """Convenience accessor: pull out just the Top-K functions' code, ready
    to hand to the Detector subagent as extra context."""
    traj_by_name = {t["qualified_name"]: t for t in final_state["trajectory"]}
    return [traj_by_name[name] for name in final_state["top_k_result"] if name in traj_by_name]
