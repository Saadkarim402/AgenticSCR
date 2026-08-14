from unittest.mock import patch, MagicMock
from webhook.review_runner import run_pr_review

@patch("webhook.review_runner.subprocess.run")
@patch("webhook.review_runner.run_full_review")
@patch("webhook.review_runner.ChatOllama")
def test_run_pr_review(mock_chat_ollama, mock_run_full_review, mock_subprocess_run):
    # Setup mocks
    mock_run_full_review.return_value = {
        "confirmed_findings": [{"id": 1}],
        "rejected_findings": []
    }
    
    # Run the function
    result = run_pr_review(
        owner="testowner",
        repo="testrepo",
        pr_number=123,
        head_sha="head123",
        base_sha="base456",
        clone_url="https://github.com/testowner/testrepo.git"
    )
    
    # Assert result
    assert result["confirmed_findings"] == [{"id": 1}]
    
    # Assert that subprocess.run was called with expected git commands
    # Clone
    clone_call = mock_subprocess_run.call_args_list[0]
    assert clone_call[0][0][:4] == ["git", "clone", "--depth=10", "https://github.com/testowner/testrepo.git"]
    
    # Fetch
    fetch_call = mock_subprocess_run.call_args_list[1]
    assert fetch_call[0][0] == ["git", "fetch", "origin", "base456", "head123"]
    
    # Checkout
    checkout_call = mock_subprocess_run.call_args_list[2]
    assert checkout_call[0][0] == ["git", "checkout", "head123", "--detach"]
    
    # Reset
    reset_call = mock_subprocess_run.call_args_list[3]
    assert reset_call[0][0] == ["git", "reset", "--soft", "base456"]
    
    # Assert run_full_review called
    mock_run_full_review.assert_called_once()
