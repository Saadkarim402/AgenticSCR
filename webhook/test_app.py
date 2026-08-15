import os
import hmac
import hashlib
import json
from fastapi.testclient import TestClient
from webhook.app import app
from unittest.mock import patch, MagicMock

client = TestClient(app)

def generate_signature(payload: bytes, secret: str) -> str:
    hash_object = hmac.new(secret.encode('utf-8'), msg=payload, digestmod=hashlib.sha256)
    return "sha256=" + hash_object.hexdigest()

def test_missing_signature():
    os.environ["GITHUB_WEBHOOK_SECRET"] = "dummy_secret"
    response = client.post("/webhook/github", json={})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid signature"}

def test_invalid_signature():
    os.environ["GITHUB_WEBHOOK_SECRET"] = "dummy_secret"
    payload = b'{"test": "payload"}'
    response = client.post(
        "/webhook/github", 
        content=payload,
        headers={"x-hub-signature-256": "sha256=invalid"}
    )
    assert response.status_code == 401

def test_valid_signature_not_pr_event():
    os.environ["GITHUB_WEBHOOK_SECRET"] = "dummy_secret"
    payload = json.dumps({"test": "payload"}).encode("utf-8")
    signature = generate_signature(payload, "dummy_secret")
    
    response = client.post(
        "/webhook/github", 
        content=payload,
        headers={
            "x-hub-signature-256": signature,
            "x-github-event": "push"
        }
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"

def test_valid_signature_pr_event_ignored_action():
    os.environ["GITHUB_WEBHOOK_SECRET"] = "dummy_secret"
    payload = json.dumps({"action": "closed"}).encode("utf-8")
    signature = generate_signature(payload, "dummy_secret")
    
    response = client.post(
        "/webhook/github", 
        content=payload,
        headers={
            "x-hub-signature-256": signature,
            "x-github-event": "pull_request"
        }
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"

def test_valid_pr_event_missing_fields():
    os.environ["GITHUB_WEBHOOK_SECRET"] = "dummy_secret"
    payload_dict = {
        "action": "opened",
        "repository": {"full_name": "test/repo"}
        # Missing other fields
    }
    payload = json.dumps(payload_dict).encode("utf-8")
    signature = generate_signature(payload, "dummy_secret")
    
    response = client.post(
        "/webhook/github", 
        content=payload,
        headers={
            "x-hub-signature-256": signature,
            "x-github-event": "pull_request"
        }
    )
    assert response.status_code == 400

def test_valid_pr_event_accepted():
    os.environ["GITHUB_WEBHOOK_SECRET"] = "dummy_secret"
    payload_dict = {
        "action": "opened",
        "repository": {
            "full_name": "test/repo",
            "clone_url": "https://github.com/test/repo.git"
        },
        "pull_request": {
            "number": 1,
            "html_url": "https://github.com/test/repo/pull/1",
            "head": {"sha": "headsha123"},
            "base": {"sha": "basesha456"}
        }
    }
    payload = json.dumps(payload_dict).encode("utf-8")
    signature = generate_signature(payload, "dummy_secret")
    
    response = client.post(
        "/webhook/github", 
        content=payload,
        headers={
            "x-hub-signature-256": signature,
            "x-github-event": "pull_request"
        }
    )
    assert response.status_code == 200
    assert response.json()["status"] == "accepted"

@patch("webhook.app.json.dump")
@patch("webhook.app.DEFAULT_LOG_DIR")
@patch("webhook.app.post_pr_review")
@patch("webhook.app.run_pr_review")
def test_process_review_background_sidecar(mock_run, mock_post, mock_log_dir, mock_json_dump):
    mock_run.return_value = {"run_id": "run123", "confirmed_findings": []}
    mock_post.return_value = "http://github.com/review"
    
    mock_path = MagicMock()
    mock_log_dir.__truediv__.return_value = mock_path
    mock_file = MagicMock()
    mock_path.open.return_value.__enter__.return_value = mock_file
    
    from webhook.app import process_review_background
    pr_data = {
        "repository_full_name": "test/repo",
        "repository_clone_url": "url",
        "pull_request_number": 1,
        "pull_request_head_sha": "head",
        "pull_request_base_sha": "base",
        "pull_request_html_url": "pr_url"
    }
    
    process_review_background(pr_data)
    
    mock_log_dir.mkdir.assert_called_once_with(parents=True, exist_ok=True)
    mock_log_dir.__truediv__.assert_called_once_with("run123.pr_meta.json")
    mock_path.open.assert_called_once_with("w")
    
    mock_json_dump.assert_called_once()
    args = mock_json_dump.call_args[0]
    assert args[0]["run_id"] == "run123"
    assert args[0]["owner"] == "test"
    assert args[0]["repo"] == "repo"
    assert args[0]["pr_number"] == 1

