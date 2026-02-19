"""
Shared state dataclass that all agents use to communicate and maintain context
throughout the CI/CD healing pipeline.

v2: Added iteration memory for convergence tracking —
  - all_fixes_applied: cumulative across ALL iterations (never reset)
  - iteration_history: per-iteration snapshots for trend analysis
  - failure_fingerprints: detect oscillation / repeated failures
  - files_modified: track which files the agent has touched
  - module_system: ESM vs CJS detection from repo analyzer
  - docker_image_built: avoid redundant Docker builds
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class FixRecord:
    """A record of a single fix applied by the healing agent."""
    file: str = ""
    bug_type: str = ""
    line: int = 0
    commit_message: str = ""
    explanation: str = ""
    status: str = "Fixed"
    iteration: int = 0          # which iteration this fix was applied in


@dataclass
class IterationSnapshot:
    """Snapshot of a single healing iteration for trend analysis."""
    iteration: int = 0
    exit_code: int = 1
    failure_count: int = 0
    fix_count: int = 0
    failure_signatures: List[str] = field(default_factory=list)  # e.g. ["src/foo.js:SYNTAX", "utils.py:IMPORT"]
    fixes_applied_files: List[str] = field(default_factory=list)


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
    language: str = ""            # e.g. "python", "node"
    test_framework: str = ""      # e.g. "pytest", "jest", "vitest"
    test_command: str = ""        # e.g. "pytest tests/"
    module_system: str = ""       # "esm" | "cjs" | "" (for node repos)
    has_jsx: bool = False         # whether repo uses JSX
    vitest_globals: bool = False  # whether vitest config has globals:true

    # --- Test Execution (current iteration) ---
    test_stdout: str = ""
    test_stderr: str = ""
    test_exit_code: int = 0

    # --- Failure Classification (current iteration) ---
    classified_failures: List[dict] = field(default_factory=list)

    # --- Fix Generation (current iteration) ---
    fixes_applied: List[FixRecord] = field(default_factory=list)

    # --- Cumulative Tracking (across ALL iterations) ---
    all_fixes_applied: List[FixRecord] = field(default_factory=list)
    iteration_history: List[IterationSnapshot] = field(default_factory=list)
    failure_fingerprints: Dict[str, int] = field(default_factory=dict)  # signature → count
    files_modified: set = field(default_factory=set)                    # set of file paths touched

    # --- Orchestration ---
    current_iteration: int = 0
    total_failures: int = 0
    total_fixes: int = 0           # THIS iteration's fix count
    cumulative_fixes: int = 0      # Total fixes across ALL iterations
    final_status: str = "PENDING"  # PASSED | FAILED | PARTIAL
    no_progress_count: int = 0     # consecutive iterations with 0 new fixes

    # --- Docker ---
    docker_image_built: bool = False   # True after first successful build

    # --- Timing ---
    start_time: Optional[float] = None
    end_time: Optional[float] = None

    # --- Meta ---
    error_message: Optional[str] = None

    def record_iteration(self):
        """Snapshot the current iteration state for history tracking."""
        sigs = [f"{f.get('file', '?')}:{f.get('bug_type', '?')}" for f in self.classified_failures]
        fix_files = [f.file for f in self.fixes_applied]

        snapshot = IterationSnapshot(
            iteration=self.current_iteration,
            exit_code=self.test_exit_code,
            failure_count=len(self.classified_failures),
            fix_count=len(self.fixes_applied),
            failure_signatures=sigs,
            fixes_applied_files=fix_files,
        )
        self.iteration_history.append(snapshot)

        # Track failure fingerprints for oscillation detection
        for sig in sigs:
            self.failure_fingerprints[sig] = self.failure_fingerprints.get(sig, 0) + 1

        # Track modified files
        self.files_modified.update(fix_files)

    def is_oscillating(self, threshold: int = 3) -> bool:
        """Return True if any failure signature has appeared >= threshold times."""
        return any(count >= threshold for count in self.failure_fingerprints.values())

    def get_repeated_failures(self, threshold: int = 2) -> List[str]:
        """Return signatures that appear >= threshold times."""
        return [sig for sig, count in self.failure_fingerprints.items() if count >= threshold]

    def last_n_had_no_progress(self, n: int = 2) -> bool:
        """Return True if the last n iterations all produced 0 fixes."""
        if len(self.iteration_history) < n:
            return False
        return all(snap.fix_count == 0 for snap in self.iteration_history[-n:])
