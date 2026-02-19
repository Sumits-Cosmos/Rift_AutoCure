# ✅ Verification Checklist

Run these commands to verify all fixes are in place:

## 1. Verify Deduplication Code

```bash
# Command
grep -n "_is_fix_already_attempted" python-service/src/agents/fix_generator.py

# Expected Output (2 matches):
# Should show in run() method call
# Should show as method definition
```

**✅ PASS if**: Both matches found

---

## 2. Verify Docker Cache Fix

```bash
# Command
grep -n "use_cache" python-service/src/agents/test_runner.py

# Expected Output (3+ matches):
# _docker_build signature with use_cache parameter
# use_cache=True for first iteration
# use_cache=False for subsequent iterations
```

**✅ PASS if**: At least 3 matches with False appearing

---

## 3. Verify Early Stop Detection

```bash
# Command
grep -n "FIX_NOT_WORKING" python-service/src/agents/ci_monitor.py

# Expected Output (1 match):
# New stop condition in run() method
```

**✅ PASS if**: At least 1 match found

---

## 4. Verify Score Calculation

```bash
# Command
grep -n '"score"' python-service/src/agents/orchestrator.py

# Expected Output (1+ matches):
# Score dictionary in _build_result method
```

**✅ PASS if**: At least 1 match with score object

---

## 5. Verify Team Info in Output

```bash
# Command
grep -n '"team_name"\|"leader_name"' python-service/src/agents/orchestrator.py

# Expected Output (2 matches minimum):
# team_name in result dict
# leader_name in result dict
```

**✅ PASS if**: Both matches found

---

## 6. Python Syntax Check

```bash
# Command
python -m py_compile python-service/src/agents/fix_generator.py
python -m py_compile python-service/src/agents/test_runner.py
python -m py_compile python-service/src/agents/ci_monitor.py
python -m py_compile python-service/src/agents/orchestrator.py

# Expected: No errors for any file
```

**✅ PASS if**: All 4 commands complete without output

---

## 7. Check for Required Imports

```bash
# Command
grep -n "from .shared_state import SharedState" python-service/src/agents/fix_generator.py

# Expected: Match found (imports are correct)
```

**✅ PASS if**: Match found

---

## 8. Verify Branch Naming

```bash
# Command
grep -n "_make_branch_name" python-service/src/agents/orchestrator.py

# Expected Output: Function definition exists
```

**✅ PASS if**: Function found and uses correct format

---

## 9. Frontend Dashboard Components

```bash
# Command (in frontend directory)
grep -n "ScoreBoard\|FixesTable\|CITimeline" src/pages/HealingAgentPage/HealingAgentPage.jsx

# Expected: All 3 components found
```

**✅ PASS if**: All 3 components present

---

## 10. API Endpoint Check

```bash
# Command
grep -n "@app.post\|@app.get" python-service/src/main.py

# Expected Output: /run-agent and /agent-status endpoints
```

**✅ PASS if**: Both endpoints defined

---

## Summary Command (Run All at Once)

```bash
echo "=== Deduplication ===" && \
grep "_is_fix_already_attempted" python-service/src/agents/fix_generator.py | wc -l && \
echo "=== Docker Cache ===" && \
grep "use_cache" python-service/src/agents/test_runner.py | wc -l && \
echo "=== Early Stop ===" && \
grep "FIX_NOT_WORKING" python-service/src/agents/ci_monitor.py | wc -l && \
echo "=== Score ===" && \
grep '"score"' python-service/src/agents/orchestrator.py | wc -l && \
echo "=== All Checks Complete ===" && \
echo "Expected: 2+ for dedup, 3+ for cache, 1+ for stop, 1+ for score"
```

---

## Detailed Verification Test

Run this Python script to verify the logic:

```python
# test_fixes.py
import os
import sys
sys.path.insert(0, 'python-service')

# Test 1: Check SharedState has tracking
from src.agents.shared_state import SharedState, FixRecord
state = SharedState(repo_url="test", team_name="TEST", leader_name="USER")
print(f"✅ SharedState initialized: {state.team_name}")

# Test 2: Check FixRecord creation
fix = FixRecord(file="test.py", bug_type="SYNTAX", line=2)
print(f"✅ FixRecord created: {fix.file}")

# Test 3: Verify all_fixes_applied exists
print(f"✅ all_fixes_applied available: {hasattr(state, 'all_fixes_applied')}")

# Test 4: Verify iteration tracking
state.record_iteration()
print(f"✅ Iteration recorded: {len(state.iteration_history)} snapshots")

print("\n✅ All structural tests passed!")
```

---

## Runtime Verification

After starting services, verify with curl:

```bash
# Health check
curl http://localhost:8000/health
# Expected: {"status":"ok","service":"cognitest-healing-agent"}

# List jobs
curl http://localhost:8000/agent-jobs  
# Expected: Empty [] or list of jobs with team_name and leader_name

# Sample submission (if job running)
curl -X POST http://localhost:8000/run-agent \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/test/repo",
    "team_name": "TEST_TEAM",
    "leader_name": "TEST_LEADER",
    "retry_limit": 5
  }'
# Expected: job_id returned with QUEUED status
```

---

## Browser Dashboard Check

Visit `http://localhost:5173` and verify:

- [ ] Input fields visible: Repo URL, Team Name, Leader Name
- [ ] Branch preview shows: TEAM_NAME_LEADER_NAME_AI_Fix
- [ ] Run button clickable
- [ ] After submission:
  - [ ] Loading spinner appears
  - [ ] Status updates in real-time
  - [ ] Run Summary Card shows team info
  - [ ] Score Breakdown shows calculation
  - [ ] Fixes Table shows applied fixes
  - [ ] Timeline shows iterations (X/5)

---

## Performance Baseline

After running a test repo, measure:

```bash
# Check iteration count
grep "Iteration.*/" logs/orchestrator.log | tail -1

# Check time taken  
grep "time_taken_seconds" results.json

# Check score
grep -A5 '"score"' results.json

# Verify no oscillation
grep -c "Preventing oscillation" logs/fix_generator.log
# Expected: 0 or minimal
```

---

## Final Checklist Before Submission

- [ ] All 4 file modifications verified (grep tests pass)
- [ ] Python syntax valid (no compile errors)
- [ ] Backend started successfully
- [ ] Frontend running without errors
- [ ] Dashboard loads at http://localhost:5173
- [ ] Sample agent run completes in 2-3 iterations
- [ ] No "already attempted" messages show oscillation prevention
- [ ] Score displayed with breakdown
- [ ] Branch name format correct
- [ ] All fixes marked as "[AI-AGENT]" commits
- [ ] results.json generated with all fields
- [ ] Team name and leader name in output
- [ ] No Python syntax errors reported

---

## Quick Status Check

```bash
echo "🔍 Checking all fixes..."
echo ""
echo "1. Deduplication:"
grep -q "_is_fix_already_attempted" python-service/src/agents/fix_generator.py && echo "   ✅ Found" || echo "   ❌ MISSING"
echo ""
echo "2. Docker no-cache:"
grep -q "use_cache" python-service/src/agents/test_runner.py && echo "   ✅ Found" || echo "   ❌ MISSING"
echo ""
echo "3. Early stop:"
grep -q "FIX_NOT_WORKING" python-service/src/agents/ci_monitor.py && echo "   ✅ Found" || echo "   ❌ MISSING"
echo ""
echo "4. Score calculation:"
grep -q '"score"' python-service/src/agents/orchestrator.py && echo "   ✅ Found" || echo "   ❌ MISSING"
echo ""
echo "5. Python syntax:"
python -m py_compile python-service/src/agents/*.py 2>&1 && echo "   ✅ Valid" || echo "   ❌ ERRORS"
echo ""
echo "🎉 All checks complete!"
```

---

**STATUS**: Ready for final submission when all checks pass ✅
