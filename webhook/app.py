import hmac
import hashlib
import os
import logging
from fastapi import FastAPI, Header, Request, HTTPException, BackgroundTasks
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

app = FastAPI(title="AgenticSCR Webhook Receiver")

def verify_signature(payload_body: bytes, secret_token: str, signature_header: str) -> bool:
    """Verify that the payload was sent from GitHub by validating SHA256."""
    if not signature_header:
        return False
    hash_object = hmac.new(secret_token.encode('utf-8'), msg=payload_body, digestmod=hashlib.sha256)
    expected_signature = "sha256=" + hash_object.hexdigest()
    return hmac.compare_digest(expected_signature, signature_header)

def process_review_background(pr_data: Dict[str, Any]):
    # Placeholder for Phase 2 implementation
    logger.info(f"Processing PR review in background: {pr_data}")

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
