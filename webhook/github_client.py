import os
import httpx
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

def post_pr_review(
    owner: str, 
    repo: str, 
    pr_number: int, 
    head_sha: str, 
    findings: List[Dict[str, Any]],
    summary: str
) -> str:
    """
    Posts a review to the GitHub Pull Request.
    Returns the URL of the posted review.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        logger.error("GITHUB_TOKEN not set, cannot post review.")
        raise ValueError("GITHUB_TOKEN environment variable is missing.")

    url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.v3+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    
    post_on_clean = os.environ.get("POST_ON_CLEAN", "false").lower() == "true"
    
    if not findings and not post_on_clean:
        logger.info(f"No findings for PR #{pr_number} and POST_ON_CLEAN is false. Skipping review post.")
        return ""

    body_text = summary

    comments = []
    for finding in findings:
        comment_body = f"**{finding.get('title', 'Finding')}**\n"
        if 'validator_explanation' in finding:
            comment_body += f"\n{finding['validator_explanation']}\n"
        if 'final_cwe_id' in finding:
            comment_body += f"\nCWE: {finding['final_cwe_id']}"
            
        comments.append({
            "path": finding.get("file", ""),
            "line": finding.get("line_start", 1),
            "side": "RIGHT",
            "body": comment_body
        })

    # Chunking: GitHub limits comments in a single review.
    if len(comments) > 50:
        logger.warning(f"Truncating {len(comments)} findings to 50 due to GitHub limits.")
        comments = comments[:50]
        body_text += f"\n\n*Note: Displaying the first 50 findings only due to API limits.*"

    payload = {
        "commit_id": head_sha,
        "event": "COMMENT",
        "body": body_text,
        "comments": comments
    }

    response = httpx.post(url, headers=headers, json=payload)
    
    if response.status_code not in (200, 201):
        logger.error(f"Failed to post review to GitHub: {response.status_code} {response.text}")
        response.raise_for_status()

    review_url = response.json().get("html_url", "")
    logger.info(f"Successfully posted review for PR #{pr_number}: {review_url}")
    return review_url
