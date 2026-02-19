import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Dict, Optional

from src.agents.orchestrator import OrchestratorAgent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Cognitest AI CI/CD Healing Agent API")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── In-memory job store ───────────────────────────────────────────────────────
import json
import os

# ─── File-based job store ──────────────────────────────────────────────────────
JOBS_FILE = "jobs.json"
_executor = ThreadPoolExecutor(max_workers=4)

def _load_jobs() -> Dict[str, dict]:
    if not os.path.exists(JOBS_FILE):
        return {}
    try:
        with open(JOBS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load jobs: {e}")
        return {}

def _save_jobs(jobs: Dict[str, dict]):
    try:
        with open(JOBS_FILE, "w", encoding="utf-8") as f:
            json.dump(jobs, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save jobs: {e}")

_jobs: Dict[str, dict] = _load_jobs()

# ─── Request/Response models ──────────────────────────────────────────────────

class RunAgentRequest(BaseModel):
    repo_url: str
    team_name: str
    leader_name: str
    retry_limit: Optional[int] = 5

class RunAgentResponse(BaseModel):
    job_id: str
    message: str
    status: str

# ── Health check ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "cognitest-healing-agent"}


# ── CI/CD Healing Agent endpoints ─────────────────────────────────────────────

def _run_orchestrator(job_id: str, repo_url: str, team_name: str, leader_name: str, retry_limit: int):
    """Background task that runs the full healing pipeline."""
    _jobs[job_id]["status"] = "RUNNING"
    try:
        agent = OrchestratorAgent(retry_limit=retry_limit)
        result = agent.run(repo_url, team_name, leader_name)
        _jobs[job_id]["result"] = result
        _jobs[job_id]["status"] = result.get("status", "COMPLETE")
        _save_jobs(_jobs)
    except Exception as e:
        logger.exception(f"[API] Job {job_id} crashed: {e}")
        _jobs[job_id]["status"] = "ERROR"
        _jobs[job_id]["result"] = {"error": str(e), "status": "FAILED"}
    finally:
        _save_jobs(_jobs)


@app.post("/run-agent", response_model=RunAgentResponse)
async def run_agent(req: RunAgentRequest, background_tasks: BackgroundTasks):
    """
    Trigger the autonomous CI/CD healing pipeline.
    Returns a job_id immediately. Poll /agent-status/{job_id} for results.
    """
    import uuid, time
    job_id = str(uuid.uuid4())

    _jobs[job_id] = {
        "status": "QUEUED",
        "result": None,
        "repo_url": req.repo_url,
        "team_name": req.team_name,
        "leader_name": req.leader_name,
        "created_at": time.time()
    }
    _save_jobs(_jobs)

    # Run in background thread (orchestrator is CPU/IO bound)
    background_tasks.add_task(
        _run_orchestrator,
        job_id, req.repo_url, req.team_name, req.leader_name, req.retry_limit
    )

    logger.info(f"[API] Job {job_id} queued for repo: {req.repo_url}")
    return RunAgentResponse(
        job_id=job_id,
        message="Agent job queued successfully. Poll /agent-status/{job_id} for progress.",
        status="QUEUED"
    )


@app.get("/agent-status/{job_id}")
def agent_status(job_id: str):
    """Poll the status and results of a healing agent run."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    job = _jobs[job_id]
    return {
        "job_id": job_id,
        "status": job["status"],
        "result": job.get("result"),
        "repo_url": job.get("repo_url"),
        "team_name": job.get("team_name"),
        "leader_name": job.get("leader_name"),
    }


@app.get("/agent-jobs")
def list_jobs():
    """List all agent jobs and their statuses."""
    return [
        {
            "job_id": jid,
            "status": j["status"],
            "repo_url": j.get("repo_url"),
            "team_name": j.get("team_name"),
        }
        for jid, j in _jobs.items()
    ]
