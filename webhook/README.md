# Webhook Receiver and Dashboard

This directory contains the FastAPI service that serves both the GitHub Webhook receiver (which automatically kicks off the PR review pipeline) and the Minimal Dashboard to view review runs.

## Local Testing via Ngrok

To test the integration end-to-end locally with GitHub, follow these steps:

1. **Start the Webhook/Dashboard Server**
   Ensure you are in the root directory of the repository and have your environment variables set:
   ```bash
   export GITHUB_TOKEN=your_personal_access_token
   export GITHUB_WEBHOOK_SECRET=your_secret_string
   
   uvicorn webhook.app:app --host 0.0.0.0 --port 8000
   ```

2. **Expose Localhost to the Internet**
   Use ngrok to expose your local port 8000:
   ```bash
   ngrok http 8000
   ```
   *Note the `https://[random-string].ngrok-free.app` URL.*

3. **Configure the GitHub Webhook**
   - Go to your test repository on GitHub.
   - Navigate to **Settings** > **Webhooks** > **Add webhook**.
   - **Payload URL**: `https://[random-string].ngrok-free.app/webhook/github`
   - **Content type**: `application/json`
   - **Secret**: The exact string you set for `GITHUB_WEBHOOK_SECRET`
   - **Which events would you like to trigger this webhook?**: Select **Let me select individual events**, then explicitly check **Pull requests**.
   - Ensure the webhook is marked **Active** and click **Add webhook**.

4. **Test the Pipeline**
   - Open a new Pull Request with a staged security vulnerability in your test repository.
   - GitHub will ping your ngrok URL.
   - Your local terminal will show the webhook receiving the event, cloning the repository, running the Agentic pipeline (with your local Ollama `qwen2.5:7b` model), and finally commenting on the Pull Request in GitHub!
   - Navigate to `http://localhost:8000/dashboard` in your browser to see the run populate in the minimal dashboard.

## Required Secrets & Environment Variables

When deploying this service in any environment (e.g. Docker, AWS), you **must** supply the following environment variables:

- `GITHUB_TOKEN`: A Personal Access Token (PAT) with repository read/write permissions so the bot can post review comments back to the PR.
- `GITHUB_WEBHOOK_SECRET`: A secure string used to verify the HMAC signature of incoming requests from GitHub. This guarantees we only act on authorized webhooks.

*(Note: We are using a local Ollama model in this configuration, so an `ANTHROPIC_API_KEY` is not required. However, ensure the machine running the pipeline has access to the local Ollama daemon or supply the Ollama base URL if it's hosted elsewhere).*
