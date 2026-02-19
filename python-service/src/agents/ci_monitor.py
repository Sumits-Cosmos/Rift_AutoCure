"""
CIMonitorAgent: Tracks iteration count, checks stopping conditions,
and provides status for the orchestrator loop.

Includes early-stop logic when no fixes are being applied (stuck loop).
"""
import logging
from .shared_state import SharedState

logger = logging.getLogger(__name__)


class CIMonitorAgent:
    """Monitors the CI healing loop and determines when to stop."""

    def __init__(self):
        self._no_fix_streak = 0  # Track consecutive iterations with zero fixes

    def run(self, state: SharedState, fixes_this_iteration: int = 0) -> dict:
        """
        Evaluate stopping conditions AFTER classification and fix attempts.

        Args:
          fixes_this_iteration: Number of fixes applied in THIS iteration only
                                (not cumulative total_fixes).

        Returns:
          - should_stop (bool): whether the loop should terminate
          - reason (str): TESTS_PASSED | RETRY_LIMIT_REACHED | NO_PROGRESS | CONTINUE
        """
        iteration = state.current_iteration
        limit = state.retry_limit
        exit_code = state.test_exit_code

        logger.info(
            f"[CIMonitorAgent] Iteration {iteration}/{limit} | "
            f"Exit code: {exit_code} | "
            f"Fixes this iteration: {fixes_this_iteration} | "
            f"Total fixes: {state.total_fixes} | "
            f"Total failures: {state.total_failures}"
        )

        # Stop condition 1: Tests passed
        if exit_code == 0:
            logger.info("[CIMonitorAgent] ✅ Tests passed — stopping loop.")
            self._no_fix_streak = 0
            return {"should_stop": True, "reason": "TESTS_PASSED"}

        # Stop condition 2: Retry limit reached
        if iteration >= limit:
            logger.warning(f"[CIMonitorAgent] ⚠️ Retry limit ({limit}) reached — stopping loop.")
            return {"should_stop": True, "reason": "RETRY_LIMIT_REACHED"}

        # Stop condition 3: No fixes applied in this iteration
        if fixes_this_iteration == 0:
            self._no_fix_streak += 1
        else:
            self._no_fix_streak = 0

        if self._no_fix_streak >= 2:
            logger.warning(
                f"[CIMonitorAgent] ⚠️ No fixes applied for {self._no_fix_streak} consecutive iterations — "
                f"stopping to avoid infinite loop."
            )
            return {"should_stop": True, "reason": "NO_PROGRESS"}

        # Continue — more iterations available
        logger.info(f"[CIMonitorAgent] 🔄 Continuing to iteration {iteration + 1}...")
        return {"should_stop": False, "reason": "CONTINUE"}

    def status_badge(self, state: SharedState) -> str:
        """Returns a concise status badge string like '2/5'."""
        return f"{state.current_iteration}/{state.retry_limit}"
