import hmac
import hashlib
import json
import os
import logging
from pathlib import Path
from fastapi import FastAPI, Header, Request, HTTPException, BackgroundTasks
from typing import Optional, Dict, Any

from webhook.review_runner import run_pr_review
from webhook.github_client import post_pr_review
from orchestrator.orchestrator import DEFAULT_LOG_DIR
from dashboard.app import router as dashboard_router

from langchain_core.globals import set_debug
set_debug(True)

from logging.handlers import RotatingFileHandler

DEFAULT_LOG_DIR = Path("logs")
DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        RotatingFileHandler(DEFAULT_LOG_DIR / "agenticscr.log", maxBytes=5*1024*1024, backupCount=5),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

app = FastAPI(title="AgenticSCR Webhook Receiver")
app.include_router(dashboard_router)

def verify_signature(payload_body: bytes, secret_token: str, signature_header: str) -> bool:
    """Verify that the payload was sent from GitHub by validating SHA256."""
    if not signature_header:
        return False
    hash_object = hmac.new(secret_token.encode('utf-8'), msg=payload_body, digestmod=hashlib.sha256)
    expected_signature = "sha256=" + hash_object.hexdigest()
    return hmac.compare_digest(expected_signature, signature_header)

def process_review_background(pr_data: Dict[str, Any]):
    logger.info(f"Processing PR review in background: {pr_data}")
    try:
        owner = pr_data["repository_full_name"].split("/")[0]
        repo = pr_data["repository_full_name"].split("/")[1]
        
        result = run_pr_review(
            owner=owner,
            repo=repo,
            pr_number=pr_data["pull_request_number"],
            head_sha=pr_data["pull_request_head_sha"],
            base_sha=pr_data["pull_request_base_sha"],
            clone_url=pr_data["repository_clone_url"]
        )
        
        # Phase 3: Post results back to PR
        findings = result.get('confirmed_findings', [])
        summary = result.get('cli_output', 'AgenticSCR Review Finished.')
        
        review_url = post_pr_review(
            owner=owner,
            repo=repo,
            pr_number=pr_data["pull_request_number"],
            head_sha=pr_data["pull_request_head_sha"],
            findings=findings,
            summary=summary
        )
        if review_url:
            logger.info(f"Review posted successfully for PR #{pr_data['pull_request_number']}: {review_url}")
        else:
            logger.info(f"Review finished for PR #{pr_data['pull_request_number']}, but no review was posted.")
            
        # Phase 4: Sidecar logging
        run_id = result.get('run_id')
        if run_id:
            DEFAULT_LOG_DIR.mkdir(parents=True, exist_ok=True)
            meta_path = DEFAULT_LOG_DIR / f"{run_id}.pr_meta.json"
            meta_data = {
                "run_id": run_id,
                "owner": owner,
                "repo": repo,
                "pr_number": pr_data["pull_request_number"],
                "pr_url": pr_data.get("pull_request_html_url", ""),
                "posted_review_url": review_url
            }
            with meta_path.open("w") as f:
                json.dump(meta_data, f)
            logger.info(f"Wrote PR meta sidecar to {meta_path}")
    except Exception as e:
        logger.error(f"Error running PR review: {e}")

@app.post("/webhook/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: Optional[str] = Header(None)
):
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if not secret:
        logger.error("GITHUB_WEBHOOK_SECRET not set")
        raise HTTPException(status_code=500, detail="Server misconfiguration")

    # Get the raw body for HMAC verification
    payload_body = await request.body()
    
    if not verify_signature(payload_body, secret, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Parse JSON payload
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event = request.headers.get("x-github-event")
    
    if event != "pull_request":
        return {"status": "ignored", "reason": "not a pull_request event"}

    action = payload.get("action")
    if action not in ["opened", "synchronize", "reopened"]:
        return {"status": "ignored", "reason": f"action {action} ignored"}

    # Extract required fields
    try:
        pr_data = {
            "repository_full_name": payload["repository"]["full_name"],
            "repository_clone_url": payload["repository"]["clone_url"],
            "pull_request_number": payload["pull_request"]["number"],
            "pull_request_head_sha": payload["pull_request"]["head"]["sha"],
            "pull_request_base_sha": payload["pull_request"]["base"]["sha"],
            "pull_request_html_url": payload["pull_request"]["html_url"],
        }
    except KeyError as e:
        logger.error(f"Missing expected field in payload: {e}")
        raise HTTPException(status_code=400, detail=f"Missing field: {e}")

    # Queue background task
    background_tasks.add_task(process_review_background, pr_data)

    return {"status": "accepted", "message": "Review queued in background"}
