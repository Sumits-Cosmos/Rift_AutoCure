"""
CIMonitorAgent: Tracks iteration count, checks stopping conditions,
and provides status for the orchestrator loop.

v2: Progress-aware stop logic with oscillation detection,
    no-progress detection (consecutive 0-fix iterations),
    and richer return metadata.
"""
import logging
from .shared_state import SharedState

logger = logging.getLogger(__name__)


class CIMonitorAgent:
    """Monitors the CI healing loop and determines when to stop."""

    def run(self, state: SharedState) -> dict:
        """
        Evaluate stopping conditions AFTER classification and fix attempts.

        Returns dict with:
          - should_stop (bool)
          - reason (str): TESTS_PASSED | RETRY_LIMIT_REACHED | NO_PROGRESS | OSCILLATING | FIX_NOT_WORKING | CONTINUE
          - metadata (dict): additional context
        """
        iteration = state.current_iteration
        limit = state.retry_limit
        exit_code = state.test_exit_code
        fixes_this_round = state.total_fixes
        failures_this_round = state.total_failures

        # Record iteration snapshot for history tracking
        state.record_iteration()

        meta = {
            "iteration": iteration,
            "fixes_this_round": fixes_this_round,
            "failures_remaining": failures_this_round,
            "cumulative_fixes": state.cumulative_fixes,
            "is_oscillating": state.is_oscillating(),
            "repeated_failures": state.get_repeated_failures(),
        }

        logger.info(
            f"[CIMonitorAgent] Iteration {iteration}/{limit} | "
            f"Exit code: {exit_code} | "
            f"Fixes this round: {fixes_this_round} | "
            f"Cumulative fixes: {state.cumulative_fixes} | "
            f"Failures: {failures_this_round} | "
            f"Oscillating: {meta['is_oscillating']}"
        )

        # Stop condition 1: Tests passed
        if exit_code == 0:
            logger.info("[CIMonitorAgent] ✅ Tests passed — stopping loop.")
            return {"should_stop": True, "reason": "TESTS_PASSED", "metadata": meta}

        # Stop condition 2: Fix was attempted but didn't work (0 fixes + still have failures)
        # This catches the case where LLM suggested a fix but it didn't resolve the failure
        if iteration >= 2 and fixes_this_round == 0 and failures_this_round > 0:
            logger.warning(
                f"[CIMonitorAgent] ⚠️ No fixes applied this iteration but {failures_this_round} "
                f"failures remain — suggests LLM fixes are not helping. Stopping."
            )
            return {"should_stop": True, "reason": "FIX_NOT_WORKING", "metadata": meta}

        # Stop condition 3: Oscillation detected (same failure 2+ iterations with fix attempts)
        # IMPROVED: Check after iteration 2 instead of 3 for faster detection
        if iteration >= 2 and state.is_oscillating(threshold=2):
            repeated = state.get_repeated_failures(threshold=2)
            logger.warning(
                f"[CIMonitorAgent] 🔄 Oscillation detected — same failures recurring "
                f"for 2+ iterations: {repeated}. Fix attempts not working. Stopping."
            )
            return {"should_stop": True, "reason": "OSCILLATING", "metadata": meta}

        # Stop condition 4: No progress (2 consecutive iterations with 0 fixes)
        # Only meaningful after at least 3 iterations (give the system a fair chance)
        if state.last_n_had_no_progress(2) and iteration >= 3:
            logger.warning(
                f"[CIMonitorAgent] 📉 No progress for 2 consecutive iterations — stopping."
            )
            return {"should_stop": True, "reason": "NO_PROGRESS", "metadata": meta}

        # Stop condition 5: Retry limit reached
        if iteration >= limit:
            logger.warning(f"[CIMonitorAgent] ⚠️ Retry limit ({limit}) reached — stopping loop.")
            return {"should_stop": True, "reason": "RETRY_LIMIT_REACHED", "metadata": meta}

        # Continue — more iterations available
        logger.info(f"[CIMonitorAgent] 🔄 Continuing to iteration {iteration + 1}...")
        return {"should_stop": False, "reason": "CONTINUE", "metadata": meta}

    def status_badge(self, state: SharedState) -> str:
        """Returns a concise status badge string like '2/5'."""
        return f"{state.current_iteration}/{state.retry_limit}"
