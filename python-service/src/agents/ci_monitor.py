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
          - reason (str): TESTS_PASSED | RETRY_LIMIT_REACHED | NO_PROGRESS | OSCILLATING | CONTINUE
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

        # Stop condition 2: Oscillation detected (same failure 3+ times)
        if state.is_oscillating():
            repeated = state.get_repeated_failures()
            logger.warning(
                f"[CIMonitorAgent] 🔄 Oscillation detected — same failures recurring: {repeated}. "
                f"Stopping to prevent infinite loop."
            )
            return {"should_stop": True, "reason": "OSCILLATING", "metadata": meta}

        # Stop condition 3: No progress (2 consecutive iterations with 0 fixes)
        if state.last_n_had_no_progress(2) and iteration >= 2:
            logger.warning(
                f"[CIMonitorAgent] 📉 No progress for 2 consecutive iterations — stopping."
            )
            return {"should_stop": True, "reason": "NO_PROGRESS", "metadata": meta}

        # Stop condition 4: Retry limit reached
        if iteration >= limit:
            logger.warning(f"[CIMonitorAgent] ⚠️ Retry limit ({limit}) reached — stopping loop.")
            return {"should_stop": True, "reason": "RETRY_LIMIT_REACHED", "metadata": meta}

        # Continue — more iterations available
        logger.info(f"[CIMonitorAgent] 🔄 Continuing to iteration {iteration + 1}...")
        return {"should_stop": False, "reason": "CONTINUE", "metadata": meta}

    def status_badge(self, state: SharedState) -> str:
        """Returns a concise status badge string like '2/5'."""
        return f"{state.current_iteration}/{state.retry_limit}"
