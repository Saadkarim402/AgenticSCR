import json
import time
import sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
from webhook.review_runner import run_pr_review

sys.stdout.reconfigure(encoding='utf-8')

def main():
    print("==================================================")
    print("🚀 Triggering Local AI Agentic Review Demo")
    print("==================================================")
    
    # We will simulate exactly what the webhook does by passing the PR #2 payload
    # which contains the vulnerable eval() logic.
    start_time = time.time()
    
    try:
        result = run_pr_review(
            owner="Saadkarim402",
            repo="AgenticSCR",
            pr_number=2,
            head_sha="75cd8d6bfa8628829f53f12cdbe85a4c6cc20d35",
            base_sha="6514b1486b5717ef0c2f3193a56371739a648fb4",
            clone_url="https://github.com/Saadkarim402/AgenticSCR.git"
        )
    except Exception as e:
        print(f"\n❌ Error during review: {e}")
        return

    duration = time.time() - start_time
    print(f"\n✅ Review Pipeline Completed in {duration:.1f} seconds!")
    print(f"Run ID: {result.get('run_id')}")
    print(f"Found {len(result.get('confirmed_findings', []))} confirmed issues.")
    
    # Now parse the JSONL episodic memory log to show exactly how the agents worked
    log_path = result.get("log_path")
    if not log_path:
        print("No log path returned.")
        return
        
    print("\n\n" + "="*80)
    print("🧠 DETAILED AGENT INTERACTION LOGS")
    print("="*80)
    
    with open(log_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    # Skip header
    for line in lines[1:]:
        try:
            event = json.loads(line)
        except:
            continue
            
        role = event.get("role", "unknown")
        subagent = event.get("subagent", "system")
        
        # Color codes for pretty printing
        COLOR_SYSTEM = '\033[94m' # Blue
        COLOR_ASSISTANT = '\033[92m' # Green
        COLOR_HUMAN = '\033[93m' # Yellow
        COLOR_TOOL = '\033[96m' # Cyan
        COLOR_END = '\033[0m'
        
        if role == "human":
            print(f"\n{COLOR_HUMAN}[👤 {subagent.upper()} PROMPT]{COLOR_END}")
            print(event.get("content", ""))
            
        elif role == "assistant":
            print(f"\n{COLOR_ASSISTANT}[🤖 {subagent.upper()} THOUGHT/RESPONSE]{COLOR_END}")
            content = event.get("content", "")
            if content:
                print(content.strip())
            
            tool_calls = event.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    print(f"  {COLOR_ASSISTANT}▶ Calls Tool: {tc['name']}{COLOR_END}")
                    print(f"    Args: {json.dumps(tc['args'])}")
                    
        elif role == "tool_result":
            print(f"\n{COLOR_TOOL}[🛠️ {subagent.upper()} TOOL OUTPUT]{COLOR_END}")
            content = event.get("content", "")
            # Truncate for terminal display just so we don't spam 10k lines
            if len(content) > 800:
                print(content[:800] + "\n... [TRUNCATED] ...")
            else:
                print(content.strip())

    print("\n" + "="*80)
    print("🚨 FINAL OUTPUT SENT TO GITHUB:")
    print("="*80)
    print(result.get("cli_output", ""))

if __name__ == "__main__":
    main()
