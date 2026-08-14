import os
import subprocess
import tempfile
import logging
from typing import Dict, Any

from langchain_ollama import ChatOllama
from orchestrator.orchestrator import run_full_review

logger = logging.getLogger(__name__)

def run_pr_review(
    owner: str, 
    repo: str, 
    pr_number: int, 
    head_sha: str, 
    base_sha: str, 
    clone_url: str
) -> Dict[str, Any]:
    """
    Clones a PR into a temporary directory, prepares the staging area to match the PR diff, 
    and runs the review pipeline.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        logger.info(f"Created temp directory {tmpdir} for {owner}/{repo} PR #{pr_number}")
        
        # Step 1: Clone
        clone_cmd = ["git", "clone", "--depth=10", clone_url, tmpdir]
        logger.info(f"Cloning: {' '.join(clone_cmd)}")
        subprocess.run(clone_cmd, check=True, cwd=tmpdir)
        
        # Fetch base_sha and head_sha just in case they aren't in the shallow clone
        fetch_cmd = ["git", "fetch", "origin", base_sha, head_sha]
        logger.info(f"Fetching SHAs: {' '.join(fetch_cmd)}")
        # Ignore errors if already present
        subprocess.run(fetch_cmd, cwd=tmpdir, stderr=subprocess.DEVNULL)

        # Step 2: The staging trick
        checkout_cmd = ["git", "checkout", head_sha, "--detach"]
        logger.info(f"Checking out head: {' '.join(checkout_cmd)}")
        subprocess.run(checkout_cmd, check=True, cwd=tmpdir)
        
        reset_cmd = ["git", "reset", "--soft", base_sha]
        logger.info(f"Soft resetting to base: {' '.join(reset_cmd)}")
        subprocess.run(reset_cmd, check=True, cwd=tmpdir)
        
        # Step 3: Run the review
        logger.info("Starting orchestrator review pipeline")
        model = ChatOllama(model="qwen2.5:7b", temperature=0)
        result = run_full_review(model, tmpdir)
        
        findings_count = len(result.get('confirmed_findings', []))
        logger.info(f"Review completed. Found {findings_count} confirmed findings.")
        
        return result
