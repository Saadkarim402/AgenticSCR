# Webhook Receiver and Dashboard

This module extends the core `AgenticSCR` secure-code-review pipeline to run automatically against GitHub Pull Requests. 

## What is getting implemented?

The full implementation encompasses:

1. **Phase 1: Webhook Receiver (Completed)**
   - A FastAPI application (`app.py`) that listens for GitHub webhook events.
   - Secure verification of webhook payloads using `X-Hub-Signature-256`.
   - Filtering to only act on `pull_request` events with actions: `opened`, `synchronize`, and `reopened`.
   - Dispatching the review to a background task so the HTTP response returns immediately.

2. **Phase 2: PR Review Runner**
   - Shallow cloning of the repository to a temporary directory.
   - Adjusting the local git staging area (`git reset --soft`) so that the local diff exactly matches the PR diff.
   - Executing the existing secure-code-review pipeline (Orchestrator → Detector → Validator) on the staged changes.

3. **Phase 3: GitHub PR Commenting**
   - Formatting the findings from the review pipeline into a structured GitHub review.
   - Pushing inline review comments directly to the Pull Request.

4. **Phase 4 & 5: Episodic Logging & Dashboard**
   - Generating `pr_meta.json` sidecar logs for each run to store PR metadata.
   - A minimal UI Dashboard displaying a table of all runs, timestamps, PR links, and the number of confirmed/rejected findings.

## Local Testing

Once fully built, you can test this locally by running the FastAPI app via `uvicorn webhook.app:app --reload` and exposing it to GitHub using a tool like `ngrok`.
