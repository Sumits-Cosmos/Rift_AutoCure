# 🧠 AutoCure  
### Autonomous CI/CD Healing Agent  
Built for RIFT 2026 Hackathon — AI/ML Track  

AutoCure is an AI-powered autonomous DevOps platform that detects, fixes, validates, and iterates on failing repositories — without human intervention.

It clones a GitHub repository, runs test suites inside a sandboxed Docker environment, classifies failures, generates targeted fixes, commits with `[AI-AGENT]` prefix, and continues until all tests pass or retry limits are reached.

---

# 🚀 What AutoCure Does

AutoCure automates the debugging lifecycle inside CI/CD pipelines.

Instead of developers manually fixing failures, AutoCure:

1. Clones the repository
2. Creates a new AI branch
3. Detects language and test framework
4. Runs tests inside Docker sandbox
5. Classifies failures
6. Generates targeted fixes
7. Commits fixes with `[AI-AGENT]`
8. Re-runs tests
9. Stops when:
   - All tests pass ✅
   - Retry limit reached
   - Oscillation detected

---

# 🏗 Architecture Overview

```
React Dashboard (Frontend)
        ↓
Node.js Backend (API Layer)
        ↓
Python FastAPI Multi-Agent Engine
        ↓
Docker Sandbox Execution
        ↓
GitHub Branch + Commits
```

---

# 🧠 Multi-Agent System

| Agent | Responsibility |
|--------|---------------|
| RepoAnalyzerAgent | Detects language, framework, module system |
| TestRunnerAgent | Executes tests in Docker sandbox |
| FailureClassifierAgent | Parses and structures failure output |
| FixGeneratorAgent | Generates contextual code fixes |
| CIMonitorAgent | Detects oscillation & retry logic |
| OrchestratorAgent | Coordinates full healing lifecycle |

---

# 🔄 Healing Lifecycle (Step-by-Step)

1. User inputs:
   - GitHub Repository URL
   - Team Name
   - Team Leader Name

2. AutoCure:
   - Clones repo
   - Creates branch:
     TEAMNAME_LEADERNAME_AI_Fix
   - Runs tests inside Docker
   - Extracts structured failures
   - Generates patches
   - Commits fixes
   - Re-runs tests
   - Tracks iteration history

3. Final Output:
   - results.json
   - Dashboard summary
   - Fix timeline
   - Score breakdown

---

# 📁 Project Structure

```
Rift_AutoCure/
│
├── frontend/            # React + Vite dashboard
├── backend/             # Node.js Express API
├── python-service/      # FastAPI AI multi-agent engine
└── README.md
```

---

# 🛠 Local Development Setup

---

# 1️⃣ Clone Repository

```bash
git clone https://github.com/Sumits-Cosmos/Rift_AutoCure.git
cd Rift_AutoCure
```

---

# 🐍 2️⃣ Setup Python AI Service

Navigate:

```bash
cd python-service
```

Create virtual environment:

```bash
python -m venv .venv
```

Activate:

Windows:
```bash
.venv\Scripts\activate
```

Mac/Linux:
```bash
source .venv/bin/activate
```

Install dependencies:

```bash
uv add -r requirements.txt
```

Create `.env` file:

```
LLM_PROVIDER=gemini
GEMINI_API_KEY=your_key_here
GITHUB_TOKEN=your_github_token
USE_DOCKER=true
MAX_RETRIES=5
LLM_MIN_SLEEP=3
```

Start server:

```bash
uvicorn src.main:app --reload --port 8000
```

Runs at:

```
http://localhost:8000
```

---

# 🟢 3️⃣ Setup Node Backend

Navigate:

```bash
cd backend
```

Install dependencies:

```bash
npm install
```

Create `.env` file:

```
PORT=5000
FASTAPI_URL=http://localhost:8000
STORAGE_DIR=./storage
```

Start server:

```bash
node server.js
```

Runs at:

```
http://localhost:5000
```

---

# 🎨 4️⃣ Setup React Frontend

Navigate:

```bash
cd frontend
```

Install dependencies:

```bash
npm install --legacy-peer-deps
```

Create `.env` file:

```
VITE_API_URL=http://localhost:5000
VITE_PYTHON_API_URL=http://localhost:8000
```

Start dev server:

```bash
npm run dev
```

Runs at:

```
http://localhost:5173
```

---

# 🐳 Docker Requirement

AutoCure uses Docker for sandboxed test execution.

Install Docker Desktop and ensure it is running.

Verify installation:

```bash
docker --version
```

Docker ensures:

- Secure isolated execution
- No host pollution
- Clean environment per iteration
- Safe execution of untrusted repositories

---

# 📊 Dashboard Features

## Input Panel
- GitHub Repository URL
- Team Name
- Team Leader Name

## Run Summary
- Branch created
- Total failures
- Total fixes
- Iterations used
- Final CI status
- Total time taken

## Fix Table
| File | Bug Type | Line | Commit Message | Status |

## CI/CD Timeline
- Iteration history
- Exit codes
- Retry tracking
- Oscillation detection

## Score Breakdown
- Base Score: 100
- Speed Bonus
- Efficiency Penalty
- Final Score

---

# 🧩 Supported Bug Types

- IMPORT
- SYNTAX
- LOGIC
- TYPE_ERROR
- LINTING
- INDENTATION
- STRUCTURAL

---

# 🔐 Security & Safety

- All execution inside Docker sandbox
- No direct execution on host machine
- Retry limit configurable
- Oscillation detection prevents infinite loops
- No direct push to main branch
- All commits prefixed with `[AI-AGENT]`

---

# ⚠ Known Limitations

- Large monorepos may exceed memory limits
- Complex DB-dependent tests require environment setup
- Optimized currently for Node.js repositories
- Python repo support is experimental

---

# 🧠 Tech Stack

Frontend:
- React
- Vite
- TailwindCSS

Backend:
- Node.js
- Express

AI Engine:
- Python
- FastAPI
- Multi-Agent Architecture
- Gemini / Ollama LLM

Infrastructure:
- Docker
- GitHub API

---

# 🏆 Hackathon Alignment — RIFT 2026

✔ Autonomous CI/CD healing  
✔ Multi-agent architecture  
✔ Sandboxed execution  
✔ Exact branch naming format  
✔ `[AI-AGENT]` commit prefix  
✔ Iteration monitoring  
✔ Structured results.json output  
✔ Fully deployed dashboard  

---

# 👥 Team

Team Name: CosmoNibblers  
  

---

