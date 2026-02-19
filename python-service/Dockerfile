FROM python:3.11-slim

WORKDIR /app

# System dependencies needed for git
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Git config for commits
RUN git config --global user.email "ai@agent.local" && \
    git config --global user.name "AI-Agent"

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
