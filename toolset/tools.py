"""
AgenticSCR Toolset (T) — Piece 2

These are the tools the Detector Subagent (a_d) calls to navigate the
codebase while reviewing a staged diff. Built as LangChain `@tool`-decorated
functions so they plug directly into a LangGraph ReAct-style agent
(`create_react_agent` or a custom `ToolNode` graph).

Design choices:
- Every tool is scoped to a single `repo_path` (the target repository root),
  passed once when the toolset is built via `build_toolset(repo_path)`.
  This avoids the LLM ever needing to specify absolute paths and closes off
  path traversal outside the repo.
- All file paths the LLM passes in are treated as relative to `repo_path`
  and resolved+validated before any I/O.
- Every tool returns a plain string (LLM-friendly), never raises on
  "expected" failures (missing file, no matches) — it returns a descriptive
  string instead, since raising breaks the agent loop.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from langchain_core.tools import tool


class ToolsetError(Exception):
    """Raised only for programmer errors (bad repo_path), never for LLM-facing failures."""


def _resolve_safe(repo_root: Path, rel_path: str) -> Path:
    """Resolve a path the LLM supplied, guaranteeing it stays inside repo_root."""
    candidate = (repo_root / rel_path).resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError:
        raise PermissionError(
            f"Path '{rel_path}' resolves outside the repository root and was blocked."
        )
    return candidate


def build_toolset(repo_path: str) -> list:
    """
    Build the Detector's toolset bound to a single repository.

    Returns a list of LangChain tools ready to pass into
    `create_react_agent(model, tools=build_toolset(repo_path))` or a
    LangGraph `ToolNode`.
    """
    repo_root = Path(repo_path).resolve()
    if not repo_root.is_dir():
        raise ToolsetError(f"repo_path '{repo_path}' is not a directory")

    # ---------------------------------------------------------------- diff

    @tool
    def get_staged_diff() -> str:
        """Return the current staged (pre-commit) git diff for the repository,
        including file paths and changed line numbers. This is the primary
        entry point for a review — call this first to see what changed."""
        result = subprocess.run(
            ["git", "diff", "--staged", "--unified=3"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return f"git diff failed: {result.stderr.strip()}"
        diff = result.stdout
        if not diff.strip():
            return "No staged changes found. (Working tree may be clean, or changes are unstaged — use `git add` first.)"
        return diff

    @tool
    def get_changed_files() -> str:
        """List just the file paths that were changed in the staged diff,
        one per line, with their change status (Added/Modified/Deleted)."""
        result = subprocess.run(
            ["git", "diff", "--staged", "--name-status"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return f"git diff failed: {result.stderr.strip()}"
        if not result.stdout.strip():
            return "No staged changes."
        status_map = {"A": "Added", "M": "Modified", "D": "Deleted", "R": "Renamed"}
        lines = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            status = status_map.get(parts[0][0], parts[0])
            lines.append(f"{status}: {parts[-1]}")
        return "\n".join(lines)

    # ----------------------------------------------------------- open_files

    @tool
    def open_files(file_paths: list[str]) -> str:
        """Read and return the full contents of one or more files, with line
        numbers. Paths are relative to the repository root (e.g.
        'app/db/queries.py'). Use this when you need to see a file in full
        rather than just an expanded chunk."""
        chunks = []
        for rel_path in file_paths:
            try:
                path = _resolve_safe(repo_root, rel_path)
            except PermissionError as e:
                chunks.append(f"=== {rel_path} ===\nERROR: {e}")
                continue
            if not path.exists():
                chunks.append(f"=== {rel_path} ===\nERROR: file not found")
                continue
            if not path.is_file():
                chunks.append(f"=== {rel_path} ===\nERROR: not a file")
                continue
            try:
                text = path.read_text(errors="replace")
            except Exception as e:
                chunks.append(f"=== {rel_path} ===\nERROR reading file: {e}")
                continue
            numbered = "\n".join(
                f"{i + 1:>5}\t{line}" for i, line in enumerate(text.splitlines())
            )
            chunks.append(f"=== {rel_path} ===\n{numbered}")
        return "\n\n".join(chunks)

    # ------------------------------------------------------- expand_chunks

    @tool
    def expand_code_chunks(file_path: str, start_line: int, end_line: int, context_lines: int = 10) -> str:
        """Return a specific line range from a file, expanded with extra
        context lines before and after. Use this to see the surrounding
        function/class definition around a diff hunk without opening the
        whole file. Line numbers are 1-indexed."""
        try:
            path = _resolve_safe(repo_root, file_path)
        except PermissionError as e:
            return f"ERROR: {e}"
        if not path.is_file():
            return f"ERROR: '{file_path}' not found"

        lines = path.read_text(errors="replace").splitlines()
        total = len(lines)
        lo = max(1, start_line - context_lines)
        hi = min(total, end_line + context_lines)

        numbered = "\n".join(f"{i:>5}\t{lines[i - 1]}" for i in range(lo, hi + 1))
        return f"=== {file_path} (lines {lo}-{hi} of {total}) ===\n{numbered}"

    # ------------------------------------------------------------- grep

    @tool
    def grep(pattern: str, file_glob: str = "**/*.py") -> str:
        """Search for a regex pattern across files in the repository matching
        a glob (default: all .py files). Returns matching lines as
        'file:line: content'. Use this to trace where a variable, function,
        or user-input source is used elsewhere in the codebase."""
        result = subprocess.run(
            ["git", "grep", "-n", "-I", "-E", pattern, "--", file_glob],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 1:
            return f"No matches for pattern '{pattern}' in files matching '{file_glob}'."
        if result.returncode not in (0, 1):
            return f"grep failed: {result.stderr.strip()}"
        lines = result.stdout.strip().splitlines()
        if len(lines) > 200:
            lines = lines[:200] + [f"... ({len(lines) - 200} more matches truncated)"]
        return "\n".join(lines)

    # -------------------------------------------------------- expand_folder

    @tool
    def expand_folder(folder_path: str = ".", max_depth: int = 2) -> str:
        """List files and subfolders under a given folder (relative to repo
        root), up to max_depth. Use this to understand project layout before
        deciding which files to open, e.g. to find a config file or a
        sibling module."""
        try:
            path = _resolve_safe(repo_root, folder_path)
        except PermissionError as e:
            return f"ERROR: {e}"
        if not path.is_dir():
            return f"ERROR: '{folder_path}' is not a directory"

        ignore = {".git", "__pycache__", "node_modules", ".venv", "venv"}
        lines = []

        def walk(d: Path, depth: int, prefix: str):
            if depth > max_depth:
                return
            try:
                entries = sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name))
            except PermissionError:
                return
            for entry in entries:
                if entry.name in ignore:
                    continue
                rel = entry.relative_to(repo_root)
                if entry.is_dir():
                    lines.append(f"{prefix}{entry.name}/")
                    walk(entry, depth + 1, prefix + "  ")
                else:
                    lines.append(f"{prefix}{entry.name}")

        walk(path, 1, "")
        if not lines:
            return f"'{folder_path}' is empty."
        return "\n".join(lines)

    return [get_staged_diff, get_changed_files, open_files, expand_code_chunks, grep, expand_folder]
