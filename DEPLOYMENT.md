# Deployment Instructions

## Prerequisites
- **Node.js** (v18+)
- **Python** (v3.9+)
- **Docker** (Must be running)
- **Git**

## 1. Backend Setup (Python Service)
The core agent logic runs here.
```bash
cd python-service
# Create virtual env
python3 -m venv venv
source venv/bin/activate
# Install deps
pip install -r requirements.txt
# Start Server
python3 -m uvicorn src.main:app --reload --port 8000
```
**Env Variables (`python-service/.env`)**:
- `GEMINI_API_KEY`: Your Gemini API Key.
- `GEMINI_MODEL`: `gemini-2.5-flash` (default).

## 2. Middleware Setup (Node/Express)
Handles legacy API requests.
```bash
cd backend
npm install
npm run dev
# Runs on Port 5000
```

## 3. Frontend Setup (React)
The Dashboard UI.
```bash
cd frontend
npm install
npm run dev
# Runs on Port 5173
```

## 4. How to Submit
1. Open `http://localhost:5173/healing-agent`
2. Enter **Repository URL** (e.g. `https://github.com/my-user/my-broken-repo`)
3. Enter **Team Name**: `RIFT ORGANISERS`
4. Enter **Leader Name**: `Saiyam Kumar`
5. Click **Run Healing Agent**.

## 5. Deployment & Sandboxing strategy
- **Sandboxing**: Tests run in ephemeral Docker containers (`cicd-healing-base-image`).
- **Autonomous Deployment**: Upon success, the agent builds a **new** Docker image (`cicd-deploy-<branch>`) and runs it on a random port (e.g., `http://localhost:9042`). This URL is displayed on the dashboard.
