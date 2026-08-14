# AgenticSCR Toolset (T) — Piece 2

LangChain `@tool`-decorated functions the Detector Subagent (a_d) calls to
navigate a repository during review. Built for direct use with LangGraph's
`create_react_agent(model, tools=...)` or a custom `ToolNode`.

## Files
- `tools.py` — `build_toolset(repo_path) -> list[Tool]`. Returns 6 tools:
  - `get_staged_diff` — the pre-commit `git diff --staged`
  - `get_changed_files` — just the changed file list with status
  - `open_files` — full file contents, line-numbered
  - `expand_code_chunks` — a line range + surrounding context (for hunk expansion)
  - `grep` — regex search across the repo via `git grep`
  - `expand_folder` — directory listing up to a max depth
- `test_tools.py` — smoke test, no LLM required. Exercises every tool
  against a toy repo with a staged SQL-injection-style diff at
  `/tmp/toy_repo`.

## Design notes
- **One toolset per repo.** `build_toolset(repo_path)` closes over
  `repo_root`, so the LLM never sees or needs absolute paths — all paths in
  tool calls are relative to the repo.
- **Path traversal is blocked.** `_resolve_safe()` rejects any path that
  resolves outside `repo_root` (tested: `open_files(['../../etc/passwd'])`
  returns an error string, doesn't raise, doesn't read the file).
- **Tools never raise on expected failures** (missing file, no grep
  matches, empty diff) — they return a descriptive string instead, since an
  uncaught exception would break the LangGraph tool-calling loop mid-run.
  They *do* raise on setup errors (bad `repo_path` to `build_toolset`)
  since that's a programmer error, not something the LLM can act on.
- **`grep` uses `git grep`**, not raw `grep`, so it's automatically
  scoped to tracked files only, respects `.gitignore`, and is fast on large
  repos.

## Verified
Ran `test_tools.py` against a toy repo (`/tmp/toy_repo`, not persisted —
recreate with the commands in this session's history if needed) with a
staged diff introducing `cursor.execute("...%s..." % name)` — confirmed:
- diff/changed-files extraction works
- `expand_code_chunks` correctly pulls context around a target line
- `grep` traces `get_user_by_name` across both changed files
- path traversal and missing-file cases degrade gracefully

## Next
Piece 3 (working memory) will wrap `build_toolset()` + a session state
object so the Detector's LangGraph node can persist the diff, expanded
chunks, and the growing candidate-findings list across tool calls within
one review run.
