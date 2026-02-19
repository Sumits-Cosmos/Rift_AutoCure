"""
CIMonitorAgent: Tracks iteration count, checks stopping conditions,
and provides status for the orchestrator loop.

NOTE: This agent is called AFTER FailureClassificationAgent has run.
The 'UNCLASSIFIABLE_FAILURE' stop is only fired when failures > 0 but
we still can't make progress (no fixes generated after multiple attempts).
"""
import logging
from .shared_state import SharedState

logger = logging.getLogger(__name__)


class CIMonitorAgent:
    """Monitors the CI healing loop and determines when to stop."""

    def run(self, state: SharedState) -> dict:
        """
        Evaluate stopping conditions AFTER classification and fix attempts.

        Returns:
          - should_stop (bool): whether the loop should terminate
          - reason (str): TESTS_PASSED | RETRY_LIMIT_REACHED | NO_PROGRESS | CONTINUE
        """
        iteration = state.current_iteration
        limit = state.retry_limit
        exit_code = state.test_exit_code
        fixes_this_round = state.total_fixes

        logger.info(
            f"[CIMonitorAgent] Iteration {iteration}/{limit} | "
            f"Exit code: {exit_code} | "
            f"Fixes applied: {fixes_this_round} | "
            f"Total failures: {state.total_failures}"
        )

        # Stop condition 1: Tests passed
        if exit_code == 0:
            logger.info("[CIMonitorAgent] ✅ Tests passed — stopping loop.")
            return {"should_stop": True, "reason": "TESTS_PASSED"}

        # Stop condition 2: Retry limit reached
        if iteration >= limit:
            logger.warning(f"[CIMonitorAgent] ⚠️ Retry limit ({limit}) reached — stopping loop.")
            return {"should_stop": True, "reason": "RETRY_LIMIT_REACHED"}

        # Continue — more iterations available
        logger.info(f"[CIMonitorAgent] 🔄 Continuing to iteration {iteration + 1}...")
        return {"should_stop": False, "reason": "CONTINUE"}

    def status_badge(self, state: SharedState) -> str:
        """Returns a concise status badge string like '2/5'."""
        return f"{state.current_iteration}/{state.retry_limit}"
