import json
import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from dashboard.app import router
from fastapi import FastAPI

app = FastAPI()
app.include_router(router)
client = TestClient(app)

@patch("dashboard.app.DEFAULT_LOG_DIR")
def test_get_runs_no_log_dir(mock_log_dir):
    mock_log_dir.exists.return_value = False
    response = client.get("/api/runs")
    assert response.status_code == 200
    assert response.json() == []

@patch("dashboard.app.DEFAULT_LOG_DIR")
def test_get_runs_with_logs(mock_log_dir):
    mock_log_dir.exists.return_value = True
    
    # Mock log file
    mock_log_file = MagicMock()
    mock_log_file.stem = "run123"
    
    mock_log_file_open = MagicMock()
    mock_log_file.open.return_value.__enter__.return_value = mock_log_file_open
    mock_log_file_open.readline.return_value = json.dumps({
        "timestamp": 1234567890,
        "repo_path": "/path/to/repo",
        "detector_candidate_count": 5,
        "validator_confirmed_count": 2,
        "validator_rejected_count": 3
    })
    
    # Mock sidecar file
    mock_sidecar = MagicMock()
    mock_sidecar.exists.return_value = True
    mock_sidecar_open = MagicMock()
    mock_sidecar.open.return_value.__enter__.return_value = mock_sidecar_open
    mock_sidecar_open.read.return_value = json.dumps({
        "owner": "testowner",
        "repo": "testrepo",
        "pr_number": 42,
        "pr_url": "https://github.com/testowner/testrepo/pull/42",
        "posted_review_url": "https://github.com/review"
    })
    
    # Patch json.load to return dict directly for sidecar since mock_open with json.load is tricky
    with patch("dashboard.app.json.load") as mock_json_load:
        mock_json_load.return_value = {
            "owner": "testowner",
            "repo": "testrepo",
            "pr_number": 42,
            "pr_url": "https://github.com/testowner/testrepo/pull/42",
            "posted_review_url": "https://github.com/review"
        }
        
        mock_log_dir.glob.return_value = [mock_log_file]
        mock_log_dir.__truediv__.return_value = mock_sidecar
        
        response = client.get("/api/runs")
        assert response.status_code == 200
        runs = response.json()
        assert len(runs) == 1
        assert runs[0]["run_id"] == "run123"
        assert runs[0]["pr_number"] == 42
        assert runs[0]["owner"] == "testowner"
        assert runs[0]["validator_confirmed_count"] == 2

@patch("dashboard.app.STATIC_DIR")
def test_serve_dashboard(mock_static_dir):
    mock_index = MagicMock()
    mock_static_dir.__truediv__.return_value = mock_index
    mock_index.exists.return_value = True
    
    mock_open = MagicMock()
    mock_index.open.return_value.__enter__.return_value = mock_open
    mock_open.read.return_value = "<html>Hello Dashboard</html>"
    
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Hello Dashboard" in response.text
