# AgenticSCR — Phase 2 Build: GitHub PR Integration + Dashboard

**Paste this whole file into Antigravity's Agent Panel (Cmd/Ctrl+L), in
Planning mode, after opening this folder as your project.** Everything
referenced below already exists in this repo and is tested — read
`README.md` first for the full map.

## What already exists (do not rebuild this — call it)

A working secure-code-review pipeline: `Detector subagent → Validator
subagent → Orchestrator`, built with LangGraph, verified with 6 passing
test suites (`*/test_*.py`, all runnable with `python <path>`, no API key
needed — they use scripted fake models). The one function you need:

```python
# orchestrator/orchestrator.py
def run_full_review(model, repo_path: str, log_dir: Path = DEFAULT_LOG_DIR) -> dict:
    """Returns: {run_id, candidate_findings, confirmed_findings,
    rejected_findings, cli_output, log_path}"""
```

It expects `repo_path` to be a local git repo with the target changes
**staged** (`git diff --staged` is what the Detector reads first). That
constraint matters for Phase 2 below — read the trick in Phase 2, Step 2
before implementing.

Also available: `format_json_output(confirmed_findings)` in the same
file, and `write_episodic_log(...)` which already writes one JSONL file
per run to `logs/` with a header line (run metadata + counts) — you'll
extend this, not replace it.

## Goal

A GitHub webhook receiver that: PR opened/updated → clone + review with
the existing pipeline → post results as inline PR review comments → log
the run → show it on a small dashboard. Fully automated, no manual step
between "PR opened" and "comments appear."

## Constraints — read before touching anything

1. **Don't modify files under `toolset/`, `working_memory/`,
   `dyretriever/`, `detector/`, `validator/`, `orchestrator/`** unless you
   find an actual bug — and if you do, the relevant `test_*.py` must still
   pass after your fix. These 6 pieces are already verified; treat them as
   a stable library, not a place to refactor.
2. **New code goes in new top-level directories**: `webhook/` (FastAPI app
   + the clone/review/post pipeline) and `dashboard/` (the UI). Give each
   its own tests.
3. **Before declaring anything done**, run the full existing suite and
   confirm all 6 still pass:
   ```
   python toolset/test_tools.py && python working_memory/test_working_memory.py && \
   python detector/test_detector.py && python dyretriever/test_dyretriever.py && \
   python validator/test_validator.py && python orchestrator/test_orchestrator.py
   ```
4. Add any new dependencies to `requirements.txt`. Update the top-level
   `README.md` with how to run the webhook service.
5. Secrets (`ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`)
   come from environment variables only — never hardcode them, never
   commit a `.env` file (add one to `.gitignore` alongside an
   `.env.example` showing the required keys with empty values).

## Phase 1 — Webhook receiver

Build `webhook/app.py`, a FastAPI app with:

- `POST /webhook/github` — verify the `X-Hub-Signature-256` header
  (HMAC-SHA256 over the raw request body, keyed by `GITHUB_WEBHOOK_SECRET`)
  before touching the payload; reject with 401 on mismatch.
- Only act on `pull_request` events with `action` in `["opened",
  "synchronize", "reopened"]`. Return `200` immediately for everything
  else (GitHub expects a fast response — do the actual review as a
  background task, e.g. FastAPI's `BackgroundTasks` or a simple queue; do
  not block the HTTP response on the review finishing, GitHub will retry
  and duplicate work otherwise).
- From the payload, extract: `repository.full_name`, `repository.clone_url`,
  `pull_request.number`, `pull_request.head.sha`, `pull_request.base.sha`,
  `pull_request.html_url`.

## Phase 2 — Clone, review, and the staging trick

Build `webhook/review_runner.py` with a function like
`run_pr_review(owner, repo, pr_number, head_sha, base_sha, clone_url) -> dict`.

**Step 1 — clone.** Shallow-clone `clone_url` to a temp directory (`git
clone --depth=50 <url> <tmpdir>` — depth needs to be enough to reach
`base_sha`; if that's too shallow for a given PR, `git fetch --deepen` or
just `git fetch origin <base_sha> <head_sha>` explicitly to be safe).

**Step 2 — the staging trick (important, read carefully).** The existing
pipeline expects a **staged** diff. A PR has no "staging area" — it's just
two commits. Don't touch the Detector's tools to work around this;
instead, make the local repo's staging area *equal* the PR's diff with
one command:

```bash
git checkout <head_sha> --detach
git reset --soft <base_sha>
```

`reset --soft` moves `HEAD` back to `base_sha` **without touching the
working tree**, which is still at `head_sha`'s content — so every file
that differs between base and head ends up staged automatically. After
this, `git diff --staged` (which `get_staged_diff` already calls) returns
exactly the PR's changes. Zero changes needed to `toolset/tools.py`.

**Step 3 — run the review.**
```python
from langchain_anthropic import ChatAnthropic
from orchestrator import run_full_review
model = ChatAnthropic(model="claude-sonnet-4-6")
result = run_full_review(model, tmpdir)
```

**Step 4 — clean up** the temp directory when done (success or failure —
use `try/finally` or a context manager).

## Phase 3 — Post results back to the PR

Build `webhook/github_client.py` (plain `requests`/`httpx` calls, or
`PyGithub` if you prefer — your call) wrapping:

`POST https://api.github.com/repos/{owner}/{repo}/pulls/{pull_number}/reviews`
with header `Authorization: Bearer <GITHUB_TOKEN>` and body:
```json
{
  "commit_id": "<head_sha>",
  "event": "COMMENT",
  "body": "<summary — e.g. use format_cli_output()'s text, or write your own short summary>",
  "comments": [
    {"path": "<finding.file>", "line": "<finding.line_start>", "side": "RIGHT", "body": "<finding.title + validator_explanation + final_cwe_id>"}
  ]
}
```
One `comments` entry per `confirmed_finding`. If `confirmed_findings` is
empty, either skip posting entirely or post a short "no issues found"
review with `event: "COMMENT"` and no `comments` — your call, make it
configurable via an env var (e.g. `POST_ON_CLEAN=true/false`).

Use `event: "COMMENT"`, not `REQUEST_CHANGES` — don't block merges by
default; that's a much bigger decision than this integration should make
silently. If you want it configurable, fine, but default to non-blocking.

Note GitHub's review API has practical limits on comment count per
request on very large PRs — if `len(confirmed_findings)` is large (e.g.
>50), consider chunking into multiple review submissions.

## Phase 4 — Extend the episodic log with PR metadata

`write_episodic_log()` in `orchestrator/orchestrator.py` already writes a
JSONL header line. Rather than modifying that function, have
`webhook/review_runner.py` write its own small sidecar (e.g.
`logs/{run_id}.pr_meta.json`) with `{run_id, owner, repo, pr_number,
pr_url, posted_review_url}` — keeps the existing, tested logging code
untouched while giving the dashboard what it needs.

## Phase 5 — Minimal dashboard

Build `dashboard/` — FastAPI + Jinja2 is fine, or a single static HTML
page hitting a small `/api/runs` JSON endpoint, whichever is faster for
you to get right. It should:

- Read every `logs/*.jsonl` header line + matching `*.pr_meta.json`
  sidecar (if present).
- Show a table: timestamp, repo, PR # (linked to `pr_url` if available),
  confirmed count, rejected count, link to view the full JSONL trajectory.
- No auth needed for v1. Keep it genuinely minimal — this is for glancing
  at "did the bot run, what did it find," not a full product UI.

## Phase 6 — Local testing + deployment

- Document (in a new `webhook/README.md`) how to test locally: run the
  FastAPI app, expose it with `ngrok http <port>` (or similar), point a
  GitHub repo webhook at the ngrok URL + `/webhook/github`, content type
  `application/json`, secret = `GITHUB_WEBHOOK_SECRET`, events =
  "Pull requests".
- Add a `Dockerfile` for the webhook service (and dashboard, same
  container or separate — your call, note the tradeoff either way).
- Note in the README that `ANTHROPIC_API_KEY`, `GITHUB_TOKEN`, and
  `GITHUB_WEBHOOK_SECRET` must be set as environment variables /
  container secrets in whatever you deploy to.

## Definition of done

- [ ] All 6 existing test suites still pass, unmodified or with a
      documented bug-fix that keeps them passing.
- [ ] `webhook/app.py` runs locally, verifies GitHub's signature, and
      returns 200 fast while reviewing in the background.
- [ ] A real test PR against a scratch repo, with a real `ANTHROPIC_API_KEY`
      and `GITHUB_TOKEN`, results in inline review comments appearing on
      that PR automatically.
- [ ] The dashboard shows that run.
- [ ] New code has its own tests (mock the GitHub API calls — don't hit
      real GitHub in automated tests).
- [ ] `requirements.txt` and `README.md` updated.

Work through the phases in order and check in with me before Phase 6's
deployment target if you're unsure which platform I want (I haven't told
you yet — ask rather than assume).
