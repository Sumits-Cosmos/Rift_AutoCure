"""
FixGeneratorAgent: Uses the Gemini LLM to generate minimal code patches
for classified failures and commits them to the local branch.
"""
import os
import re
import json
import time
import logging
import subprocess
import google.generativeai as genai
from dotenv import load_dotenv
from .shared_state import SharedState, FixRecord
from .failure_classifier import format_output_line

load_dotenv()
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
   - "explanation": one sentence explaining the change
2. Make MINIMAL changes — only fix the identified bug.
3. Preserve all indentation, formatting, and coding style.
4. Do NOT add comments unless they were already there.
5. Return ONLY the JSON object — no markdown, no prose.

Example response:
{{
  "fixed_content": "...",
  "explanation": "Added missing colon at end of function definition on line 8."
}}
"""


class FixGeneratorAgent:
    """Generates and applies code fixes using Gemini LLM."""

    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY", "")
        if api_key and api_key not in ("your_gemini_api_key_here", "YOUR_GEMINI_KEY_HERE"):
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel("gemini-2.0-flash")
        else:
            self.model = None
            logger.warning("[FixGeneratorAgent] No Gemini API key - fixes will be skipped.")

    def _call_llm_with_retry(self, prompt: str, file_rel: str, max_retries: int = 2):
        """Call LLM with retry + backoff for 429 rate-limit errors."""
        for attempt in range(max_retries + 1):
            try:
                response = self.model.generate_content(prompt)
                text = response.text.strip()
                text = re.sub(r"^```[a-z]*\n?", "", text)
                text = re.sub(r"\n?```$", "", text)
                return json.loads(text)
            except Exception as e:
                err_str = str(e)
                if "429" in err_str and attempt < max_retries:
                    # Extract retry delay from error message
                    import re as _re
                    delay_match = _re.search(r'retry in ([\d.]+)s', err_str)
                    wait_time = float(delay_match.group(1)) if delay_match else 15.0
                    wait_time = min(wait_time + 2, 60)  # Add 2s buffer, cap at 60s
                    logger.warning(
                        f"[FixGeneratorAgent] Rate limited for {file_rel}, "
                        f"waiting {wait_time:.0f}s (attempt {attempt + 1}/{max_retries + 1})..."
                    )
                    time.sleep(wait_time)
                    continue
                logger.error(f"[FixGeneratorAgent] LLM fix generation failed for {file_rel}: {e}")
                return None
        return None

    def run(self, state: SharedState) -> SharedState:
        if not state.classified_failures:
            logger.info("[FixGeneratorAgent] No failures to fix.")
            return state

        if not self.model:
            logger.warning("[FixGeneratorAgent] Skipping fixes - no LLM available.")
            return state

        fixes_applied = []
        repo_path = state.repo_path

        for failure in state.classified_failures:
            fix_record = self._fix_failure(failure, repo_path, state.branch_name)
            if fix_record:
                fixes_applied.append(fix_record)

        state.fixes_applied.extend(fixes_applied)
        state.total_fixes = len(state.fixes_applied)
        state.total_commits += len(fixes_applied)
        logger.info(f"[FixGeneratorAgent] Applied {len(fixes_applied)} fix(es) this iteration.")
        return state

    def _fix_failure(self, failure: dict, repo_path: str, branch: str):
        file_rel = failure.get("file", "")
        if not file_rel or file_rel == "unknown":
            logger.warning(f"[FixGeneratorAgent] Skipping fix for unknown file.")
            return None

        file_abs = os.path.join(repo_path, file_rel)
        if not os.path.isfile(file_abs):
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
            description=failure.get("fix_description", failure.get("description", "")),
            raw_error=failure.get("raw_error", ""),
            file_content=content[:8000]
        )

        try:
            fix_data = self._call_llm_with_retry(prompt, file_rel)
            if fix_data is None:
                return None
        except Exception as e:
            logger.error(f"[FixGeneratorAgent] LLM fix generation failed for {file_rel}: {e}")
            return None

        fixed_content = fix_data.get("fixed_content", "")
        if not fixed_content:
            return None

        # Write fixed file
        try:
            with open(file_abs, "w", encoding="utf-8") as f:
                f.write(fixed_content)
        except Exception as e:
            logger.error(f"[FixGeneratorAgent] Cannot write fix to {file_abs}: {e}")
            return None

        # Build commit message with [AI-AGENT] prefix
        bug_type = failure.get("bug_type", "LOGIC")
        line_num = failure.get("line", 0)
        fix_desc = failure.get("fix_description", "fix the incorrect logic")
        commit_message = f"[AI-AGENT] fix {bug_type} in {file_rel} line {line_num}"

        # Build output_line using the canonical format
        output_line = format_output_line(bug_type, file_rel, line_num, fix_desc)

        # Git add + commit
        try:
            env = {
                **os.environ,
                "GIT_AUTHOR_NAME": "AI-Agent",
                "GIT_AUTHOR_EMAIL": "ai@agent.local",
                "GIT_COMMITTER_NAME": "AI-Agent",
                "GIT_COMMITTER_EMAIL": "ai@agent.local",
            }
            subprocess.run(["git", "add", file_abs], cwd=repo_path, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", commit_message],
                cwd=repo_path, check=True, capture_output=True, env=env
            )
            logger.info(f"[FixGeneratorAgent] Committed fix: {commit_message}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"[FixGeneratorAgent] Git commit failed: {e.stderr}")

        return FixRecord(
            file=file_rel,
            bug_type=bug_type,
            line=line_num,
            commit_message=commit_message,
            status="FIXED",
            output_line=output_line,
            fix_description=fix_desc,
        )

    def _find_file(self, repo_path: str, filename: str):
        basename = os.path.basename(filename)
        for root, _, files in os.walk(repo_path):
            if basename in files:
                return os.path.join(root, basename)
        return None
