import json
import logging
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
import sys

# Ensure we can import orchestrator
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from orchestrator.orchestrator import DEFAULT_LOG_DIR

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

DASHBOARD_DIR = Path(__file__).resolve().parent
STATIC_DIR = DASHBOARD_DIR / "static"

@router.get("/api/runs")
def get_runs():
    """
    Returns a combined list of runs from JSONL and sidecar files.
    """
    runs = []
    if not DEFAULT_LOG_DIR.exists():
        return runs
        
    for log_file in DEFAULT_LOG_DIR.glob("*.jsonl"):
        run_id = log_file.stem
        
        # Read header from jsonl
        header_data = {}
        try:
            with log_file.open("r") as f:
                first_line = f.readline()
                if first_line:
                    header_data = json.loads(first_line)
        except Exception as e:
            logger.error(f"Error reading {log_file}: {e}")
            continue
            
        # Read sidecar if exists
        sidecar_file = DEFAULT_LOG_DIR / f"{run_id}.pr_meta.json"
        meta_data = {}
        if sidecar_file.exists():
            try:
                with sidecar_file.open("r") as f:
                    meta_data = json.load(f)
            except Exception as e:
                logger.error(f"Error reading {sidecar_file}: {e}")
        
        # Merge
        run_info = {
            "run_id": run_id,
            "timestamp": header_data.get("timestamp", 0),
            "repo_path": header_data.get("repo_path", ""),
            "detector_candidate_count": header_data.get("detector_candidate_count", 0),
            "validator_confirmed_count": header_data.get("validator_confirmed_count", 0),
            "validator_rejected_count": header_data.get("validator_rejected_count", 0),
            "owner": meta_data.get("owner", ""),
            "repo": meta_data.get("repo", ""),
            "pr_number": meta_data.get("pr_number"),
            "pr_url": meta_data.get("pr_url", ""),
            "posted_review_url": meta_data.get("posted_review_url", "")
        }
        runs.append(run_info)
        
    runs.sort(key=lambda x: x["timestamp"], reverse=True)
    return runs

@router.get("/api/runs/{run_id}")
def get_run_logs(run_id: str):
    """
    Returns the full JSONL conversation for a specific run.
    """
    if not DEFAULT_LOG_DIR.exists():
        return {"error": "Logs directory not found"}
        
    log_file = DEFAULT_LOG_DIR / f"{run_id}.jsonl"
    if not log_file.exists():
        return {"error": f"Log file {run_id}.jsonl not found"}
        
    logs = []
    try:
        with log_file.open("r") as f:
            for line in f:
                line = line.strip()
                if line:
                    logs.append(json.loads(line))
        return {"run_id": run_id, "logs": logs}
    except Exception as e:
        logger.error(f"Error reading {log_file}: {e}")
        return {"error": f"Error reading log file: {e}"}

@router.get("/dashboard", response_class=HTMLResponse)
def serve_dashboard():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return "<html><body>Dashboard UI not found.</body></html>"
    with index_file.open("r") as f:
        return f.read()

@router.get("/api/system_logs")
def get_system_logs():
    """
    Returns the last 500 lines of the main server log.
    """
    log_file = DEFAULT_LOG_DIR / "agenticscr.log"
    if not log_file.exists():
        return {"logs": "Server log file not found."}
        
    try:
        with log_file.open("r") as f:
            lines = f.readlines()
            # Return last 500 lines
            return {"logs": "".join(lines[-500:])}
    except Exception as e:
        logger.error(f"Error reading system logs: {e}")
        return {"logs": f"Error reading logs: {e}"}
