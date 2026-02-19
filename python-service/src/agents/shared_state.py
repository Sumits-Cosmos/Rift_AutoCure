"""
Shared state dataclass that all agents use to communicate and maintain context
throughout the CI/CD healing pipeline.
"""
from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class FixRecord:
    """A record of a single fix applied by the healing agent."""
    file: str = ""
    bug_type: str = ""
    line: int = 0
    commit_message: str = ""
    status: str = "Fixed"


@dataclass
class SharedState:
    """Shared state container passed between all agents in the pipeline."""

    # --- Input ---
    repo_url: str = ""
    team_name: str = ""
    leader_name: str = ""
    retry_limit: int = 5

    # --- Repo Analysis ---
    repo_path: str = ""
    branch_name: str = ""
    language: str = ""          # e.g. "python", "node"
    test_framework: str = ""    # e.g. "pytest", "jest"
    test_command: str = ""      # e.g. "pytest tests/"

    # --- Test Execution ---
    test_stdout: str = ""
    test_stderr: str = ""
    test_exit_code: int = 0

    # --- Failure Classification ---
    classified_failures: List[dict] = field(default_factory=list)

    # --- Fix Generation ---
    fixes_applied: List[FixRecord] = field(default_factory=list)

    # --- Orchestration ---
    current_iteration: int = 0
    total_failures: int = 0
    total_fixes: int = 0
    final_status: str = "PENDING"  # PASSED | FAILED | PARTIAL

    # --- Timing ---
    start_time: Optional[float] = None
    end_time: Optional[float] = None

    # --- Meta ---
    error_message: Optional[str] = None
