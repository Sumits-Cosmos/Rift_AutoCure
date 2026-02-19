import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Any, Dict, Optional

from src.swagger_parser import parse_swagger
from src.gemini_generator import generate_testcases_from_gemini
from src.postman_builder import build_postman_collection
from src.report_analyzer import analyze_report
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

class SwaggerReq(BaseModel):
    swaggerUrl: str

class SwaggerSpecReq(BaseModel):
    spec: Dict[str, Any]

class GenerateReq(BaseModel):
    parsed: Dict[str, Any]

class ReportReq(BaseModel):
    report: Dict[str, Any]

class RunAgentRequest(BaseModel):
    repo_url: str
    team_name: str
    leader_name: str
    retry_limit: Optional[int] = 5
    pat_token: Optional[str] = None

class RunAgentResponse(BaseModel):
    job_id: str
    message: str
    status: str

# ─── Existing endpoints ───────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "python-service"}


@app.post("/parse-swagger")
async def parse_swagger_api(req: SwaggerReq):
    parsed = await parse_swagger(req.swaggerUrl)
    return parsed


@app.post("/parse-swagger-spec")
async def parse_swagger_spec(req: SwaggerSpecReq):
    """Parse a Swagger spec directly (for file uploads)"""
    spec = req.spec
    base_url = ""
    if "servers" in spec and spec["servers"]:
        base_url = spec["servers"][0]["url"]
    elif "host" in spec:
        scheme = spec.get("schemes", ["https"])[0]
        base_url = f"{scheme}://{spec['host']}{spec.get('basePath', '')}"

    endpoints = []
    for path, methods in spec.get("paths", {}).items():
        for method, details in methods.items():
            if method.upper() in ["GET", "POST", "PUT", "DELETE", "PATCH"]:
                endpoints.append({
                    "method": method.upper(),
                    "path": path,
                    "summary": details.get("summary", "")
                })
    return {"baseUrl": base_url, "endpoints": endpoints}


@app.post("/generate-tests")
async def generate_tests(req: GenerateReq):
    testcases = await generate_testcases_from_gemini(req.parsed)
    collection = build_postman_collection(req.parsed, testcases)
    return {"testcases": testcases, "collection": collection}


@app.post("/analyze-report")
def analyze(req: ReportReq):
    return analyze_report(req.report)


# ─── CI/CD Healing Agent endpoints ───────────────────────────────────────────

def _add_log(job_id: str, message: str, level: str = "info"):
    """Append a timestamped log entry to the job."""
    import time as _time
    entry = {"ts": _time.time(), "level": level, "message": message}
    _jobs[job_id].setdefault("logs", []).append(entry)


def _run_orchestrator(job_id: str, repo_url: str, team_name: str, leader_name: str, retry_limit: int, pat_token: Optional[str] = None):
    """Background task that runs the full healing pipeline."""
    _jobs[job_id]["status"] = "RUNNING"
    _jobs[job_id]["logs"] = []
    _add_log(job_id, "Agent started — initializing pipeline...")

    # Create a callback the orchestrator can use to emit step logs
    def log_callback(msg: str, level: str = "info"):
        _add_log(job_id, msg, level)

    try:
        agent = OrchestratorAgent(retry_limit=retry_limit, log_callback=log_callback)
        result = agent.run(repo_url, team_name, leader_name, pat_token)
        _jobs[job_id]["result"] = result
        _jobs[job_id]["status"] = result.get("status", "COMPLETE")
        _add_log(job_id, f"Pipeline finished with status: {result.get('status', 'COMPLETE')}")
        _save_jobs(_jobs)
    except Exception as e:
        logger.exception(f"[API] Job {job_id} crashed: {e}")
        _jobs[job_id]["status"] = "ERROR"
        _jobs[job_id]["result"] = {"error": str(e), "status": "FAILED"}
        _add_log(job_id, f"Pipeline crashed: {e}", "error")
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
        job_id, req.repo_url, req.team_name, req.leader_name, req.retry_limit, req.pat_token
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


@app.get("/agent-logs/{job_id}")
def agent_logs(job_id: str, since: float = 0):
    """Return log entries for a job, optionally filtered to entries after `since` timestamp."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    logs = _jobs[job_id].get("logs", [])
    if since > 0:
        logs = [l for l in logs if l["ts"] > since]
    return {"job_id": job_id, "logs": logs}


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
