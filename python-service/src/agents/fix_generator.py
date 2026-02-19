"""
FixGeneratorAgent: Uses the LLM (Gemini or Grok) to generate minimal code patches
for classified failures and commits them to the local branch.
"""
import os
import re
import json
import logging
import subprocess
from .shared_state import SharedState, FixRecord
from ..llm_client import get_llm_client

logger = logging.getLogger(__name__)

FIX_PROMPT = """
You are an expert software engineer. You will be given a code file and a classified bug.
Your task is to generate the minimal fix required to resolve the bug.

File: {file}
Bug Type: {bug_type}
Line Number: {line}
Error Description: {description}
Raw Error: {raw_error}

Current File Content:
```
{file_content}
```

Instructions:
1. Return ONLY a JSON object with the following fields:
   - "fixed_content": the complete fixed file content as a string
   - "commit_message": a git commit message starting with "[AI-AGENT]" describing the fix
   - "explanation": one sentence explaining the change
2. Make MINIMAL changes — only fix the identified bug.
3. Preserve all indentation, formatting, and coding style.
4. Do NOT add comments unless they were already there.
5. Return ONLY the JSON object — no markdown, no prose.

Example response:
{{
  "fixed_content": "...",
  "commit_message": "[AI-AGENT] Fix SYNTAX error in calculator.py line 8 - add colon",
  "explanation": "Added missing colon at end of function definition on line 8."
}}
"""


class FixGeneratorAgent:
    """Generates and applies code fixes using LLM (Gemini or Grok)."""

    def __init__(self):
        self.client = get_llm_client()
        if not self.client:
            logger.warning("[FixGeneratorAgent] No LLM client available - fixes will be skipped.")

    def run(self, state: SharedState) -> SharedState:
        if not state.classified_failures:
            logger.info("[FixGeneratorAgent] No failures to fix.")
            return state

        if not self.client:
            logger.warning("[FixGeneratorAgent] Skipping fixes - no LLM available.")
            return state

        fixes_applied = []
        repo_path = state.repo_path

        for failure in state.classified_failures:
            fix_record = self._fix_failure(failure, repo_path, state.branch_name)
            if fix_record:
                fixes_applied.append(fix_record)

        state.fixes_applied = fixes_applied
        state.total_fixes = len(fixes_applied)
        logger.info(f"[FixGeneratorAgent] Applied {len(fixes_applied)} fix(es).")
        return state

    def _fix_failure(self, failure: dict, repo_path: str, branch: str):
        file_rel = failure.get("file", "")
        if not file_rel or file_rel == "unknown":
            logger.warning(f"[FixGeneratorAgent] Skipping fix for unknown file.")
            return None

        file_abs = os.path.join(repo_path, file_rel)
        if not os.path.isfile(file_abs):
            # Try to find the file
            found = self._find_file(repo_path, file_rel)
            if not found:
                logger.warning(f"[FixGeneratorAgent] File not found: {file_rel}")
                return None
            file_abs = found

        try:
            with open(file_abs, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            logger.error(f"[FixGeneratorAgent] Cannot read {file_abs}: {e}")
            return None

        prompt = FIX_PROMPT.format(
            file=file_rel,
            bug_type=failure.get("bug_type", ""),
            line=failure.get("line", 0),
            description=failure.get("description", ""),
            raw_error=failure.get("raw_error", ""),
            file_content=content[:8000]  # Limit content size
        )

        try:
            text = self.client.generate(prompt)
            text = re.sub(r"^```[a-z]*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)
            fix_data = json.loads(text)
        except Exception as e:
            logger.error(f"[FixGeneratorAgent] LLM fix generation failed for {file_rel}: {e}")
            return None

        fixed_content = fix_data.get("fixed_content", "")
        commit_message = fix_data.get("commit_message", f"[AI-AGENT] Fix {failure.get('bug_type')} in {file_rel}")
        if not fixed_content:
            return None

        # Write fixed file
        try:
            with open(file_abs, "w", encoding="utf-8") as f:
                f.write(fixed_content)
        except Exception as e:
            logger.error(f"[FixGeneratorAgent] Cannot write fix to {file_abs}: {e}")
            return None

        # Git add + commit
        try:
            subprocess.run(["git", "add", file_abs], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", commit_message],
                cwd=repo_path, check=True, capture_output=True,
                env={**os.environ, "GIT_AUTHOR_NAME": "AI-Agent", "GIT_AUTHOR_EMAIL": "ai@agent.local",
                     "GIT_COMMITTER_NAME": "AI-Agent", "GIT_COMMITTER_EMAIL": "ai@agent.local"}
            )
            logger.info(f"[FixGeneratorAgent] Committed fix: {commit_message}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"[FixGeneratorAgent] Git commit failed: {e.stderr}")

        return FixRecord(
            file=file_rel,
            bug_type=failure.get("bug_type", ""),
            line=failure.get("line", 0),
            commit_message=commit_message,
            status="Fixed"
        )

    def _find_file(self, repo_path: str, filename: str):
        basename = os.path.basename(filename)
        for root, _, files in os.walk(repo_path):
            if basename in files:
                return os.path.join(root, basename)
        return None
