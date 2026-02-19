/**
 * Healing Agent API Service
 * Communicates with the python-service backend.
 */

const BASE_URL = import.meta.env.VITE_PYTHON_API_URL || 'http://localhost:8000';

/**
 * Trigger a new healing agent run.
 * Returns { job_id, status, message }
 */
export async function startHealingAgent({ repo_url, team_name, leader_name, retry_limit = 5 }) {
    const res = await fetch(`${BASE_URL}/run-agent`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_url, team_name, leader_name, retry_limit }),
    });
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || 'Failed to start agent');
    }
    return res.json();
}

/**
 * Poll the status of a running job.
 * Returns { job_id, status, result, repo_url, team_name, leader_name }
 */
export async function getAgentStatus(jobId) {
    const res = await fetch(`${BASE_URL}/agent-status/${jobId}`);
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || 'Failed to fetch status');
    }
    return res.json();
}

/**
 * Fetch agent logs for a running job.
 * Returns { job_id, logs: [{ ts, level, message }] }
 * Use `since` to fetch only new entries after that timestamp.
 */
export async function getAgentLogs(jobId, since = 0) {
    const url = since > 0
        ? `${BASE_URL}/agent-logs/${jobId}?since=${since}`
        : `${BASE_URL}/agent-logs/${jobId}`;
    const res = await fetch(url);
    if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || 'Failed to fetch logs');
    }
    return res.json();
}

/**
 * List all jobs.
 */
export async function listAgentJobs() {
    const res = await fetch(`${BASE_URL}/agent-jobs`);
    if (!res.ok) throw new Error('Failed to fetch jobs');
    return res.json();
}
