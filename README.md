# AgenticSCR — Agentic Secure Code Review

An implementation of the AgenticSCR paper's dual-agent (Detector +
Validator) secure code review architecture, integrated with DyRetriever's
multi-hop dependency tracing, built with LangGraph.

**Status:** the core review pipeline (6/6 pieces) is built and verified
with scripted fake models — no live LLM calls have been made yet (this was
built in a sandbox with no `ANTHROPIC_API_KEY`). See
`ANTIGRAVITY_PROMPT.md` for the next phase: GitHub webhook + PR comment
integration + a minimal dashboard UI, meant to be handed to Google
Antigravity to build.

## Quick start

```bash
pip install -r requirements.txt

# run the full test suite (no API key needed — uses scripted fake models)
python toolset/test_tools.py
python working_memory/test_working_memory.py
python detector/test_detector.py
python dyretriever/test_dyretriever.py
python validator/test_validator.py
python orchestrator/test_orchestrator.py

# run for real, against a real repo with staged changes
export ANTHROPIC_API_KEY=sk-ant-...
pip install langchain-anthropic
cd /path/to/some/repo && git add <files>
python /path/to/agenticscr/cli.py review . --json
```

## Architecture

```
git diff (staged) ──► DETECTOR ──► candidate_findings ──► VALIDATOR ──► confirmed_findings ──► ORCHESTRATOR ──► CLI / PR comments
                         │  ▲                                  │  ▲
                         │  └── sast_rules.json (Piece 1)      │  └── cwe_tree.json (Piece 1)
                         └── trace_dependencies                └── search_cwe_tree
                             (DyRetriever multi-hop loop)
```

## Directory map

| Path | Piece | What it is |
|---|---|---|
| `sast_rules/sast_rules.json` | 1 | 50 CodeQL Python security rules, semantic memory for the Detector |
| `cwe_tree/cwe_tree.json` | 1 | 839 MITRE CWE-1000 entries, semantic memory for the Validator |
| `toolset/tools.py` | 2 | Diff/file/grep/folder tools, scoped + path-guarded per repo |
| `working_memory/working_memory.py` | 3 | LangGraph state schema, caching wrappers, `record_candidate_finding` |
| `dyretriever/extractor.py`, `dyretriever/engine.py` | — | AST function registry + multi-hop Select→Visit→Expand loop |
| `detector/detector.py` | 4 | Detector subagent: diff → rules → navigation (incl. DyRetriever) → candidates |
| `validator/validator.py` | 5 | Validator subagent: candidates → CWE lookup → confirmed/rejected verdicts |
| `orchestrator/orchestrator.py` | 6 | Sequences Detector→Validator, episodic JSONL log, output formatting |
| `cli.py` | 6 | `python cli.py review <repo_path> [--json]` |
| `scripts/build_sast_rules.py`, `scripts/build_cwe_tree.py` | 1 | Re-runnable generators for the semantic memory files |
| `*/test_*.py` | all | Scripted-fake-model smoke tests, no API key needed, all currently passing |
| `*/README.md` | all | Per-piece build notes, bugs found/fixed, known limitations |

Every subdirectory has its own `README.md` with the detailed build story,
including three real bugs that were caught and fixed during development
(a search-relevance bug, a closure late-binding bug, and this path-hardcoding
issue) — worth reading if you're extending any of these pieces.

## Known gaps (all documented in their respective READMEs)
- Never run against a real Anthropic model — only scripted fake models, which
  verify wiring/logic but not actual detection accuracy.
- 3 CWE IDs referenced by SAST rules aren't in the local taxonomy package.
- Python-only (both the SAST rules and the DyRetriever function extractor).
- DyRetriever's callee-name resolution is name-based, not scope-aware.
- No SCRBench evaluation harness — no automated precision/recall measurement.
- No GitHub integration yet — see `ANTIGRAVITY_PROMPT.md`.
