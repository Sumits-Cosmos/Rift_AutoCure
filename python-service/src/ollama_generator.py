"""
Healing Prompt — centralized CI/CD healing agent prompt template.

This module contains the prompt used by the fix_generator
to instruct the LLM on how to analyze and fix failing tests.
"""

HEALING_PROMPT_TEMPLATE = """You are an Autonomous CI/CD Healing Agent.

Your job is to analyze failing test output and generate precise code fixes.

STRICT RULES:
1. Fix ONLY the root cause of the failure.
2. Do NOT modify unrelated files.
3. Do NOT rewrite entire files.
4. Produce minimal diffs.
5. Preserve project structure.
6. Do NOT disable tests.
7. Do NOT remove failing assertions unless absolutely necessary.
8. If dependency is missing, modify requirements/package.json — not tests.
9. Avoid oscillation (do not undo previous fix).
10. If failure cannot be fixed safely, return "UNFIXABLE".

INPUT:
- Repository structure:
{repo_structure}

- Failing test output (stdout + stderr):
{test_output}

- Previously applied fixes:
{prior_fixes}

- Retry iteration: {iteration}

You must:

1. Classify the failure:
   LINTING | SYNTAX | LOGIC | TYPE_ERROR | IMPORT | INDENTATION | DEPENDENCY | STRUCTURAL

2. Identify:
   - file
   - line number
   - root cause explanation

3. Generate the COMPLETE fixed file content.

Return STRICTLY in this JSON format:
{{
  "bug_type": "LOGIC",
  "file": "src/utils.py",
  "line": 15,
  "explanation": "Function was missing return statement",
  "fixed_content": "...the entire fixed file content...",
  "commit_message": "[AI-AGENT] Fix LOGIC — add missing return statement in utils.py"
}}

IMPORTANT: "fixed_content" must contain the COMPLETE file after the fix, not a diff.
Return ONLY the JSON object. No markdown, no explanation outside JSON."""


def build_healing_prompt(
    file: str,
    bug_type: str,
    line: int,
    description: str,
    raw_error: str,
    file_content: str,
    test_output: str,
    repo_structure: str = "",
    prior_fixes: str = "None",
    iteration: int = 1,
) -> str:
    """Build a healing prompt using the HEALING_PROMPT_TEMPLATE.

    Called by fix_generator._fix_failure() to create the prompt
    sent to Ollama for generating code fixes.
    """
    full_test_output = (
        f"File: {file}\n"
        f"Bug type: {bug_type}\n"
        f"Line: {line}\n"
        f"Description: {description}\n"
        f"Raw error: {raw_error}\n\n"
        f"Test output:\n{test_output}\n\n"
        f"Current file content ({file}):\n{file_content}"
    )

    return HEALING_PROMPT_TEMPLATE.format(
        repo_structure=repo_structure or "Not available",
        test_output=full_test_output[:12000],
        prior_fixes=prior_fixes or "None",
        iteration=iteration,
    )
