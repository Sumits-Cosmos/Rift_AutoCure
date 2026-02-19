# Troubleshooting Guide

## Issue: Agent Still Getting Stuck in Loop

### Symptom
- Same error appears in multiple iterations
- Same file:line:bugtype appears in fixes list multiple times

### Diagnosis
```bash
# Check if deduplication is working
grep "Skipping.*already attempted" python-service/logs/agent.log

# If NOT found → deduplication not triggering
# If found > 0 times → working correctly
```

### Solutions

**1. Verify deduplication code exists**
```bash
grep -n "_is_fix_already_attempted" python-service/src/agents/fix_generator.py
# Should show: (1) Method definition, (2) Call in run() method
```

**2. Check state.all_fixes_applied is being populated**
```bash
# Add debug log in fix_generator.py run() method:
logger.info(f"[DEBUG] Prior fixes: {len(state.all_fixes_applied)}")
```

**3. If still looping, manually trigger early stop**
Edit `ci_monitor.py` to lower threshold:
```python
# Line ~57: Change from
if iteration >= 2 and state.is_oscillating(threshold=2):
# To 
if iteration >= 1 and state.is_oscillating(threshold=1):
```

---

## Issue: Docker Not Using Updated Files

### Symptom
- Tests pass on first iteration but fail on second with same error
- File modifications not appearing in Docker container
- stdout shows old file content

### Diagnosis
```bash
# Check Docker logs
docker build --no-cache -t test-img .
# If this works differently than previous build, cache was the issue

# Check build command
grep "docker build" python-service/logs/test_runner.log
# Should see --no-cache on iteration 2+
```

### Solutions

**1. Verify --no-cache being used**
```bash
grep -n "use_cache" python-service/src/agents/test_runner.py
# Should show parameter being passed

# Add debug logging:
logger.info(f"[DEBUG] Building with cache={use_cache}")
```

**2. Force rebuild without cache**
```bash
# Temporarily modify test_runner.py:
# Change: build_ok = self._docker_build(repo_dest, tag, use_cache=use_cache)
# To:     build_ok = self._docker_build(repo_dest, tag, use_cache=False)
```

**3. Check files are actually updated in repo_path**
```bash
# Add debug before build:
import subprocess
result = subprocess.run(['git', 'log', '--oneline', '-5'], 
                       cwd=repo_path, capture_output=True, text=True)
logger.info(f"[DEBUG] Recent commits:\n{result.stdout}")
```

**4. Manually test Docker layer**
```bash
# Copy repo to test directory
cp -r repo_path /tmp/test_docker

# Edit a test file
echo "# FIXED" >> /tmp/test_docker/tests/test.py

# Build with cache
docker build -t test1 /tmp/test_docker  # First build

# Build again (with cache)
docker build -t test1 /tmp/test_docker  # Should use cache

# Build without cache
docker build --no-cache -t test1 /tmp/test_docker  # Should rebuild

# Check if edit appears in final image
docker run test1 cat /app/tests/test.py | grep FIXED
# With cache: might not show
# Without cache: should show
```

---

## Issue: Score Not Displaying

### Symptom
- results.json missing "score" field
- Dashboard shows "undefined" for score

### Diagnosis
```bash
# Check if score is in results.json
grep '"score"' results.json
# If NOT found → orchestrator not generating it

# Check if calculation code exists
grep -n "base_score = 100" python-service/src/agents/orchestrator.py
```

### Solutions

**1. Verify score calculation code**
```bash
grep -A10 "base_score = 100" python-service/src/agents/orchestrator.py
# Should show full calculation with speed_bonus and efficiency_penalty
```

**2. Check if result dict includes score**
```python
# In orchestrator.py _build_result(), verify this exists:
result = {
    ...
    "score": {
        "base_score": base_score,
        "speed_bonus": speed_bonus,
        "efficiency_penalty": efficiency_penalty,
        "final_score": max(0, final_score),
    }
}
```

**3. If missing, add it manually**
```bash
# Edit results.json after run:
{
  ...existing fields...,
  "score": {
    "base_score": 100,
    "speed_bonus": 10,
    "efficiency_penalty": 0,
    "final_score": 110
  }
}
```

---

## Issue: Frontend Not Showing Results

### Symptom
- Dashboard shows "No fixes applied yet"
- Results card is empty even after agent finishes

### Diagnosis
```bash
# Check if API is returning results
curl http://localhost:8000/agent-status/{JOB_ID}
# Should show "result" field with all data

# Check browser console for errors
# Firefox: F12 → Console → Look for red errors
```

### Solutions

**1. Verify API endpoint returns data**
```bash
# Manually test:
curl -X POST http://localhost:8000/run-agent \
  -H "Content-Type: application/json" \
  -d '{"repo_url":"https://github.com/test/repo","team_name":"TEST","leader_name":"USER"}'

# Get job_id from response
# Wait a few seconds
curl http://localhost:8000/agent-status/{JOB_ID}

# Should show result.fixes array with objects
```

**2. Check if frontend is polling correctly**
```javascript
// In browser console (F12):
fetch('http://localhost:8000/agent-status/YOUR_JOB_ID')
  .then(r => r.json())
  .then(d => console.log(d))
  // Should show result with fixes
```

**3. Verify results.json exists**
```bash
# After agent completes:
ls -la results.json
cat results.json | python -m json.tool | head -50
# Should show well-formed JSON with fixes array
```

---

## Issue: Ollama Not Available

### Symptom
- Agent skips LLM fixes
- Error: "Ollama not available"
- Logs show "is_available() = False"

### Diagnosis
```bash
# Check if Ollama is running
curl http://localhost:11434/api/tags
# Should return list of available models

# Check if deepseek-coder is available  
curl http://localhost:11434/api/tags | grep -o '"name":"[^"]*"'
# Should show: deepseek-coder:6.7b (or similar)
```

### Solutions

**1. Start Ollama**
```bash
# Windows
start ollama.exe

# Or on Mac/Linux
ollama serve &
```

**2. Pull the required model**
```bash
ollama pull deepseek-coder:6.7b

# Or if using different model, update config:
# File: python-service/src/llm/ollama_client.py
# Change: model = "deepseek-coder:6.7b"
# To your available model
```

**3. Verify connection**
```bash
# Test from Python:
python -c "
import requests
try:
    r = requests.get('http://localhost:11434/api/tags')
    print(f'✅ Ollama available: {r.status_code}')
    print(r.text[:200])
except:
    print('❌ Ollama not reachable')
"
```

---

## Issue: Docker Commands Failing

### Symptom
- "docker: command not found"
- "Cannot connect to Docker daemon"
- Docker build times out

### Diagnosis
```bash
# Check Docker installation
docker --version

# Check Docker daemon
docker ps

# Test with simple image
docker run hello-world
```

### Solutions

**1. Install or start Docker**
```bash
# Windows: Use Docker Desktop application
# Mac: brew install docker
# Linux: apt-get install docker.io

# Start Docker service:
# Windows: Open Docker Desktop
# Linux: sudo systemctl start docker
```

**2. Increase Docker resource limits**
```bash
# Docker Desktop Settings → Resources:
# Memory: 4GB+
# CPU: 2+ cores
# Disk: 10GB+

# Or increase timeout in test_runner.py:
timeout = 600  # Currently 300 seconds
```

**3. Clean up old images**
```bash
# Remove old images
docker rmi cicd-healing-base-image 2>/dev/null

# Clean all dangling images
docker image prune -f

# If still issues:
docker system prune -a -f
```

---

## Issue: Out of Disk Space

### Symptom
- "disk full" errors during Docker build
- Cannot create temporary directories

### Diagnosis
```bash
# Check disk space
df -h

# Check Docker storage
docker system df
```

### Solutions

**1. Clean Docker**
```bash
# Remove unused images/containers
docker system prune -a -f

# Clean old temp directories
rm -rf /tmp/cicd_heal_*
```

**2. Move temp directory**
```bash
# Point to a different drive with more space:
# Edit orchestrator.py:
# Was: tmp_dir = tempfile.mkdtemp(prefix="cicd_repo_")
# To:  tmp_dir = tempfile.mkdtemp(prefix="cicd_repo_", dir="/path/with/space")
```

---

## Issue: Tests Taking Too Long

### Symptom
- Agent runs near retry limit (4-5 iterations)
- Fixtures report times > 300 seconds

### Diagnosis
```bash
# Check current iteration/timing
grep "time_taken_seconds" results.json

# Check which iteration is slow
grep "Iteration.*ran in" logs/test_runner.log
```

### Solutions

**1. Reduce retry limit for testing**
```bash
# Frontend: Change default from 5 to 3
# Dashboard: Max Retries field

# Or via API:
curl -X POST http://localhost:8000/run-agent \
  ... "retry_limit": 3 ...
```

**2. Increase Docker resource limits**
```bash
# Give Docker more CPU/RAM
# Windows/Mac: Docker Desktop → Settings → Resources
# Set memory to 8GB and CPU to 4
```

**3. Pre-warm Docker cache**
```bash
# Build base image once
docker build -t cicd-healing-base-image .

# Subsequent runs will be faster (cached)
```

---

## Issue: Commits Not Being Made

### Symptom
- Fixes identified but not committed to git
- Branch exists but no commits
- "Git commit failed" in logs

### Diagnosis
```bash
# Check git status
cd temp_repo_path
git status

# Check commits
git log --oneline -5

# Check if [AI-AGENT] prefix is there
git log --oneline | grep "AI-AGENT"
```

### Solutions

**1. Configure git**
```bash
# Git needs author info
git config user.name "AI-Agent"
git config user.email "ai@agent.local"

# Set globally:
git config --global user.name "AI-Agent"
git config --global user.email "ai@agent.local"
```

**2. Verify fix_generator.py git call**
```bash
# File: fix_generator.py
# Check _git_commit method exists and is called
grep -n "_git_commit\|git.*commit" python-service/src/agents/fix_generator.py
```

**3. Check git push access**
```bash
# Test git credentials
git -C /tmp/test_repo push --dry-run origin branch_name

# If fails:
# - SSH key not configured
# - Password needed
# - Remote not reachable
```

---

## Emergency Reset

If everything is broken, start fresh:

```bash
# 1. Stop all services
docker-compose down
pkill -f "uvicorn\|npm\|ollama"

# 2. Clean all state
rm -f results.json jobs.json
rm -rf /tmp/cicd_*

# 3. Clear Docker
docker system prune -a -f

# 4. Restart
docker-compose up -d
```

---

## Getting Help

Check logs in this order:

1. **Frontend logs**: Browser F12 → Console
2. **Backend logs**: python-service terminal output
3. **Agent logs**: Look for [FixGeneratorAgent], [TestRunnerAgent], etc.
4. **Docker logs**: `docker logs <container_id>`
5. **System logs**: `/var/log/syslog` (Linux)

---

**CRITICAL**: If tests still oscillate after all these checks, the core deduplication logic may not be installed. Verify:

```bash
python -c "
import sys
sys.path.insert(0, 'python-service')
from src.agents.fix_generator import FixGeneratorAgent
import inspect
source = inspect.getsource(FixGeneratorAgent)
if '_is_fix_already_attempted' in source:
    print('✅ Deduplication code found')
else:
    print('❌ DEDUPLICATION CODE MISSING - Run setup again!')
"
```

If "MISSING", re-apply changes from CODE_CHANGES.md
