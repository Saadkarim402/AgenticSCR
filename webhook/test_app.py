import os
import hmac
import hashlib
import json
from fastapi.testclient import TestClient
from webhook.app import app

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
