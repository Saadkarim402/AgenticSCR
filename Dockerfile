FROM python:3.11-slim

# Install git and clean up apt cache
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Ensure the logs directory exists
RUN mkdir -p logs

# Expose the port FastAPI runs on
EXPOSE 8000

# Set environment variables (these should be overridden at runtime)
ENV GITHUB_TOKEN=""
ENV GITHUB_WEBHOOK_SECRET=""
ENV OLLAMA_HOST="http://host.docker.internal:11434"

# Start the unified Webhook Receiver and Dashboard service
CMD ["uvicorn", "webhook.app:app", "--host", "0.0.0.0", "--port", "8000"]
