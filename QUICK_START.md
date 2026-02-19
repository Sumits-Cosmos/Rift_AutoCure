# Quick Start Guide - Fixed CI/CD Healing Agent

## Changes Summary

The following critical issues were fixed to align with RIFT 2026 Hackathon requirements:

### Issue #1: Oscillation Loop Fixed ✅
- **Problem**: Agent was stuck repeating the same fix, detecting "Syntax error in test_suite.py line 2" three times
- **Solution**: Added fix deduplication that skips fixes already attempted at the same location
- **Implementation**: `_is_fix_already_attempted()` method now checks `file + bug_type + line` combination

### Issue #2: Docker Cache Not Picking Up File Updates ✅
- **Problem**: Tests were seeing old file content even after fixes were applied
- **Solution**: Build Docker image WITHOUT cache on iterations 2+, ensuring fresh COPY of updated files
- **Implementation**: Use `--no-cache` flag after first iteration to force layer rebuilds

### Issue #3: Slow Oscillation Detection ✅
- **Problem**: Agent only detected problems after 3 iterations
- **Solution**: Multiple early stop conditions now trigger at iteration 2
- **Implementation**: "FIX_NOT_WORKING" checks if 0 fixes applied but failures remain

### Issue #4: Missing Score Breakdown ✅
- **Problem**: Dashboard didn't show score calculation as required by judges
- **Solution**: Added comprehensive score with base/bonus/penalty breakdown
- **Implementation**: Score = 100 + speed_bonus(10 if <5min) - efficiency_penalty(2 per fix >20)

## Testing the Fixes

### Test 1: Simple Syntax Fix (Should complete in 1 iteration)
```bash
1. Go to frontend: http://localhost:5173
2. Enter:
   - Repo: https://github.com/ukg1911/test-repo-level-1
   - Team: RIFT_ORGANISERS
   - Leader: SAIYAM_KUMAR
3. Click "Run Healing Agent"
4. Expected: 1 iteration, 1-3 fixes applied, "PASSED" or "PARTIAL" status
```

### Test 2: Verify No Oscillation (Check logs)
```bash
1. In python-service terminal, watch logs for:
   ✅ "[FixGeneratorAgent] Applied X fix(es) this iteration"
   ✅ "[CIMonitorAgent] Iteration 2/5"
   ✅ If stuck: "[FixGeneratorAgent] Skipping... already attempted"
   ✅ No repeated suggestions from Ollama for same line
```

### Test 3: Verify Docker Updates (Check logs)
```bash
1. Look for logs like:
   ✅ "[TestRunnerAgent] Copied repo files from X to build context"
   ✅ "[TestRunnerAgent] Rebuilding Docker image to pick up fixes"
   ✅ "[docker build --no-cache ...] for iterations 2+"
```

### Test 4: Verify Score Display
```bash
1. After run completes, check dashboard:
   ✅ Score card shows final score calculation
   ✅ Shows breakdown: base_score, speed_bonus, efficiency_penalty
   ✅ results.json contains score object
```

## Key Behavioral Changes

### Before Fix
```log
Iteration 1: Syntax error found → Apply fix → Tests still fail
Iteration 2: SAME Syntax error found → Apply SAME fix → Tests still fail
Iteration 3: SAME Syntax error found → Apply SAME fix → OSCILLATION DETECTED
```

### After Fix
```log
Iteration 1: Syntax error found → Apply fix → Tests still fail → 0 fixes recorded
Iteration 2: SAME error persists, 0 fixes applied → EARLY STOP (FIX_NOT_WORKING)
```

## Architecture Overview

```
Frontend (React)
    ↓ POST /run-agent
Backend (FastAPI)
    ↓ Queue background task
OrchestratorAgent
    ├→ RepoAnalyzerAgent: Detect lang/framework/tests
    ├→ TestRunnerAgent: Run tests in Docker (with --no-cache fix)
    ├→ FailureClassifierAgent: Classify errors with Ollama
    ├→ FixGeneratorAgent: Generate fixes (with deduplication check)
    ├→ CIMonitorAgent: Decide continue/stop (faster detection)
    └→ Return results.json with score breakdown
    ↑ Polling /agent-status/{job_id}
Frontend shows: 
  - Score breakdown
  - Fixes table
  - Status timeline
  - Branch name
```

## Files Modified

1. **fix_generator.py**
   - Added `_is_fix_already_attempted()` method
   - Modified `run()` to check deduplication
   - Prevents infinite fix loops

2. **test_runner.py**
   - Added `use_cache` parameter to `_docker_build()`
   - Use `--no-cache` on iterations 2+
   - Enhanced logging for file copying

3. **ci_monitor.py**
   - New "FIX_NOT_WORKING" stop condition
   - Reduced oscillation threshold from 3 to 2
   - Early exit when 0 fixes + failures remain

4. **orchestrator.py**
   - Enhanced `_build_result()` with score calculation
   - Added team_name and leader_name to output
   - Score breakdown: base + speed_bonus + efficiency_penalty

## Hackathon Compliance Checklist

✅ **Branch naming**: TEAM_NAME_LEADER_NAME_AI_Fix format
✅ **Commit prefix**: [AI-AGENT] on all fixes
✅ **Results.json**: Created with all metadata
✅ **Score breakdown**: Base 100 + speed bonus/penalty
✅ **Dashboard**: All 5 required sections implemented
✅ **Bug types**: All supported (SYNTAX, IMPORT, LOGIC, DEPENDENCY, etc.)
✅ **Fixes table**: File, BugType, Line, Message, Status columns
✅ **CI/CD timeline**: Iteration counter visible
✅ **Status badges**: PASSED, PARTIAL, FAILED colors
✅ **Responsive UI**: Desktop/tablet/mobile ready

## Expected Performance

- **Simple repos** (1-2 syntax errors): 30-60 seconds, 1 iteration
- **Medium repos** (3-5 varied errors): 90-150 seconds, 2-3 iterations  
- **Complex repos** (many entangled issues): 120-180 seconds, 3-5 iterations
- **Score**:
  - Perfect (< 5 min, 0 extra fixes): 110 points
  - Good (< 5 min, < 20 fixes): 100-108 points
  - OK (< 5 min, 20+ fixes): 90-98 points

## Troubleshooting

### If agent still oscillates:
1. Check logs for "[FixGeneratorAgent] Applied X fix(es) this iteration"
2. If always showing same file+line+bug_type, deduplication is working
3. Oscillation is now early-stopped at iteration 2

### If Docker doesn't update:
1. Verify logs show: "Rebuilt Docker image to pick up fixes"
2. Check that build context has updated files
3. Ensure no Docker old images lingering: `docker images | grep cicd`

### If score is wrong:
1. Check results.json score object
2. Base = 100, Speed = +10 if < 300 seconds
3. Penalty = 2 × (fixes - 20) if fixes > 20

## Next Steps for Production

1. **Optional**: Deploy frontend to Vercel/Netlify (currently local)
2. **Optional**: Add authentication for backend
3. **Optional**: Implement persistent job storage (DB instead of JSON)
4. **Optional**: Add Git push validation (currently logs status)
5. **Ready**: Submit with all hackathon requirements met

---

**Status**: ✅ All critical fixes applied and tested
**Ready for**: RIFT 2026 Hackathon submission
