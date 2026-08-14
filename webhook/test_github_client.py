import os
import pytest
from unittest.mock import patch, MagicMock
from webhook.github_client import post_pr_review

def test_post_pr_review_missing_token():
    if "GITHUB_TOKEN" in os.environ:
        del os.environ["GITHUB_TOKEN"]
    
    with pytest.raises(ValueError, match="GITHUB_TOKEN environment variable is missing"):
        post_pr_review("testowner", "testrepo", 1, "sha123", [], "summary")

@patch("webhook.github_client.httpx.post")
def test_post_pr_review_no_findings_clean_false(mock_post):
    os.environ["GITHUB_TOKEN"] = "fake_token"
    os.environ["POST_ON_CLEAN"] = "false"
    
    result = post_pr_review("testowner", "testrepo", 1, "sha123", [], "summary")
    
    assert result == ""
    mock_post.assert_not_called()

@patch("webhook.github_client.httpx.post")
def test_post_pr_review_no_findings_clean_true(mock_post):
    os.environ["GITHUB_TOKEN"] = "fake_token"
    os.environ["POST_ON_CLEAN"] = "true"
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"html_url": "https://github.com/test/review/1"}
    mock_post.return_value = mock_response
    
    result = post_pr_review("testowner", "testrepo", 1, "sha123", [], "AgenticSCR Review: No issues.")
    
    assert result == "https://github.com/test/review/1"
    mock_post.assert_called_once()
    
    # Verify payload
    call_kwargs = mock_post.call_args[1]
    assert call_kwargs["json"]["event"] == "COMMENT"
    assert call_kwargs["json"]["body"] == "AgenticSCR Review: No issues."
    assert call_kwargs["json"]["comments"] == []

@patch("webhook.github_client.httpx.post")
def test_post_pr_review_with_findings(mock_post):
    os.environ["GITHUB_TOKEN"] = "fake_token"
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"html_url": "https://github.com/test/review/2"}
    mock_post.return_value = mock_response
    
    findings = [{
        "title": "SQL Injection",
        "validator_explanation": "Use parameterized queries.",
        "final_cwe_id": "CWE-89",
        "file": "app.py",
        "line_start": 10
    }]
    
    result = post_pr_review("testowner", "testrepo", 1, "sha123", findings, "Found 1 issue.")
    
    assert result == "https://github.com/test/review/2"
    
    call_kwargs = mock_post.call_args[1]
    payload = call_kwargs["json"]
    
    assert payload["commit_id"] == "sha123"
    assert payload["event"] == "COMMENT"
    assert payload["body"] == "Found 1 issue."
    assert len(payload["comments"]) == 1
    
    comment = payload["comments"][0]
    assert comment["path"] == "app.py"
    assert comment["line"] == 10
    assert "SQL Injection" in comment["body"]
    assert "Use parameterized queries." in comment["body"]
    assert "CWE-89" in comment["body"]
