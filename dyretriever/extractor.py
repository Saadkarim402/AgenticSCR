"""
DyRetriever — Part A: Function Registry & Entry Points

Builds a lightweight, file-scoped index of every function/method in a repo
(via Python's `ast` module — no external parser needed), and maps a git
diff's changed line ranges onto the functions that actually contain them.
This is what lets us skip the paper's own file-selection LLM call: we
already know exactly which lines changed, so entry points are derived
directly and deterministically.

Registry entry key format: "<relative/file/path>::<QualName>" where
QualName is either "func_name" (module-level) or "ClassName.method_name"
(one level of nesting — nested/inner functions aren't indexed separately,
see README for why).
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}


def _extract_imports(tree: ast.Module) -> list[str]:
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}" if module else alias.name)
    return imports


def build_function_registry(repo_root: str | Path) -> dict:
    """Walk every .py file under repo_root and index its module-level
    functions and one level of class methods.

    Returns: dict[qualified_name] -> {
        "file": str (relative path),
        "start_line": int, "end_line": int,
        "code": str (source with line numbers),
        "signature": str,
        "imports": list[str],
    }
    """
    repo_root = Path(repo_root).resolve()
    registry: dict[str, dict] = {}

    for py_file in sorted(repo_root.rglob("*.py")):
        if any(part in IGNORE_DIRS for part in py_file.parts):
            continue
        rel = str(py_file.relative_to(repo_root))
        try:
            source = py_file.read_text(errors="ignore")
            tree = ast.parse(source, filename=rel)
        except SyntaxError:
            continue

        lines = source.splitlines()
        imports = _extract_imports(tree)

        def add_entry(node, qualname: str):
            start, end = node.lineno, node.end_lineno
            snippet = "\n".join(f"{i:>5}\t{lines[i - 1]}" for i in range(start, end + 1))
            args = [a.arg for a in node.args.args]
            sig = f"def {node.name}({', '.join(args)})"
            registry[f"{rel}::{qualname}"] = {
                "file": rel,
                "start_line": start,
                "end_line": end,
                "code": snippet,
                "signature": sig,
                "imports": imports,
            }

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                add_entry(node, node.name)
            elif isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        add_entry(sub, f"{node.name}.{sub.name}")

    return registry


def build_simple_name_index(registry: dict) -> dict[str, list[str]]:
    """Reverse index: bare function/method name -> list of qualified names
    that end with it. Used to resolve an LLM-mentioned callee like
    'get_connection' or 'queries.get_connection' back to a real registry
    entry."""
    index: dict[str, list[str]] = {}
    for qualified in registry:
        simple = qualified.split("::", 1)[1].split(".")[-1]
        index.setdefault(simple, []).append(qualified)
    return index


def resolve_call_name(raw_name: str, current_file: str, registry: dict, simple_index: dict) -> str | None:
    """Best-effort resolution of an LLM-reported callee name to a real
    registry entry, or None if it doesn't exist locally (stdlib call,
    third-party call, or hallucination — all correctly discarded)."""
    simple = raw_name.split(".")[-1]
    matches = simple_index.get(simple)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    same_file = [m for m in matches if m.startswith(current_file + "::")]
    if same_file:
        return same_file[0]
    return sorted(matches)[0]  # deterministic tie-break


# --------------------------------------------------------------------------
# Diff parsing -> entry points
# --------------------------------------------------------------------------

_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_diff_new_line_ranges(diff_text: str) -> dict[str, list[tuple[int, int]]]:
    """Parse a unified git diff into {file: [(start,end), ...]} covering
    every hunk's line range in the NEW version of the file (i.e. post-change
    line numbers, which is what the registry's start_line/end_line use)."""
    ranges: dict[str, list[tuple[int, int]]] = {}
    current_file = None
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            current_file = None if path == "/dev/null" else path
            continue
        m = _HUNK_HEADER_RE.match(line)
        if m and current_file:
            start = int(m.group(1))
            length = int(m.group(2)) if m.group(2) else 1
            ranges.setdefault(current_file, []).append((start, max(start, start + length - 1)))
    return ranges


def find_entry_points(registry: dict, changed_ranges: dict[str, list[tuple[int, int]]]) -> list[dict]:
    """Return registry entries whose [start_line, end_line] overlaps any
    changed hunk range in their file. Each result: {"qualified_name", "reason"}."""
    entries = []
    seen = set()
    for qualified, meta in registry.items():
        file_ranges = changed_ranges.get(meta["file"])
        if not file_ranges:
            continue
        for (lo, hi) in file_ranges:
            if meta["start_line"] <= hi and meta["end_line"] >= lo:
                if qualified not in seen:
                    entries.append({"qualified_name": qualified, "reason": "changed in the staged diff"})
                    seen.add(qualified)
                break
    return entries
