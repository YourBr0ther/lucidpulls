"""Code analyzer for bug detection and fix generation."""

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

import httpx

from src.analyzers.base import BaseAnalyzer, AnalysisResult, FixSuggestion
from src.llm.base import BaseLLM, CODE_REVIEW_SYSTEM_PROMPT, FIX_GENERATION_PROMPT_TEMPLATE
from src.utils import retry

logger = logging.getLogger("lucidpulls.analyzers.code")


class CodeAnalyzer(BaseAnalyzer):
    """Analyzes code for bugs and generates fixes using an LLM."""

    def __init__(self, llm: BaseLLM):
        """Initialize code analyzer.

        Args:
            llm: LLM provider instance.
        """
        self.llm = llm

    @retry(
        max_attempts=2,
        delay=2.0,
        backoff=2.0,
        exceptions=(ValueError, ConnectionError, TimeoutError, OSError, httpx.TimeoutException),
    )
    def _call_llm_with_retry(self, prompt: str, system_prompt: str) -> str:
        """Call LLM with retry logic.

        Args:
            prompt: The prompt to send to the LLM.
            system_prompt: The system prompt to use.

        Returns:
            LLM response content.

        Raises:
            ValueError: If LLM returns an unsuccessful response.
        """
        response = self.llm.generate(prompt, system_prompt=system_prompt)
        if not response.success:
            raise ValueError("LLM returned empty response")
        return response.content

    def analyze(
        self,
        repo_path: Path,
        repo_name: str,
        issues: Optional[list[dict]] = None,
    ) -> AnalysisResult:
        """Analyze a repository for bugs.

        Args:
            repo_path: Local path to the repository.
            repo_name: Full repository name (owner/repo).
            issues: Optional list of open issues to consider.

        Returns:
            AnalysisResult with potential fix.
        """
        start_time = time.time()

        try:
            # Get code files
            files = self._get_code_files(repo_path)

            if not files:
                logger.warning(f"No code files found in {repo_name}")
                return AnalysisResult(
                    repo_name=repo_name,
                    found_fix=False,
                    error="No code files found",
                    analysis_time_seconds=time.time() - start_time,
                )

            # Format code for LLM
            code_content = self._format_code_for_llm(files)

            # Format issues
            issues_content = self._format_issues(issues) if issues else "No open issues."

            # Build prompt
            prompt = FIX_GENERATION_PROMPT_TEMPLATE.format(
                repo_name=repo_name,
                issues=issues_content,
                code_files=code_content,
            )

            # Send to LLM with retry
            logger.info(f"Sending {len(files)} files to LLM for analysis")
            try:
                response_content = self._call_llm_with_retry(
                    prompt, system_prompt=CODE_REVIEW_SYSTEM_PROMPT
                )
            except (ValueError, httpx.TimeoutException) as e:
                error_msg = "LLM request timed out" if isinstance(e, httpx.TimeoutException) else "LLM returned empty response after retries"
                logger.error(f"LLM call failed after retries: {e}")
                return AnalysisResult(
                    repo_name=repo_name,
                    found_fix=False,
                    error=error_msg,
                    files_analyzed=len(files),
                    issues_reviewed=len(issues) if issues else 0,
                    analysis_time_seconds=time.time() - start_time,
                )

            # Parse LLM response
            fix = self._parse_llm_response(response_content)

            analysis_time = time.time() - start_time
            logger.info(
                f"Analysis complete: found_fix={fix is not None}, time={analysis_time:.1f}s"
            )

            return AnalysisResult(
                repo_name=repo_name,
                found_fix=fix is not None,
                fix=fix,
                files_analyzed=len(files),
                issues_reviewed=len(issues) if issues else 0,
                analysis_time_seconds=analysis_time,
            )

        except Exception as e:
            logger.error(f"Analysis failed for {repo_name}: {e}")
            return AnalysisResult(
                repo_name=repo_name,
                found_fix=False,
                error=str(e),
                analysis_time_seconds=time.time() - start_time,
            )

    def _format_issues(self, issues: list[dict]) -> str:
        """Format issues for LLM consumption.

        Args:
            issues: List of issue dictionaries.

        Returns:
            Formatted string.
        """
        if not issues:
            return "No open issues."

        lines = []
        for issue in issues:
            labels = ", ".join(issue.get("labels", []))
            lines.append(f"Issue #{issue['number']}: {issue['title']}")
            lines.append(f"  Labels: {labels}")
            if issue.get("body"):
                # Truncate long bodies
                body = issue["body"]
                if len(body) > 500:
                    body = body[:500] + "..."
                lines.append(f"  Description: {body}")
            lines.append("")

        return "\n".join(lines)

    MAX_LLM_RESPONSE_SIZE = 500_000  # 500KB

    def _parse_llm_response(self, response: str) -> Optional[FixSuggestion]:
        """Parse LLM response into a FixSuggestion.

        Args:
            response: Raw LLM response.

        Returns:
            FixSuggestion if valid response, None otherwise.
        """
        if len(response) > self.MAX_LLM_RESPONSE_SIZE:
            logger.warning(f"LLM response too large ({len(response)} chars), truncating")
            response = response[:self.MAX_LLM_RESPONSE_SIZE]

        try:
            # Try to extract JSON from response
            json_str = self._extract_json(response)
            if not json_str:
                logger.warning("Could not extract JSON from LLM response")
                return None

            data = json.loads(json_str)

            # Check if bug was found
            if not data.get("found_bug", False):
                logger.info("LLM did not find any bugs")
                return None

            # Validate required fields
            required = [
                "file_path", "bug_description", "fix_description",
                "original_code", "fixed_code", "pr_title", "pr_body",
            ]
            for field in required:
                if not data.get(field):
                    logger.warning(f"Missing required field in LLM response: {field}")
                    return None

            # Validate file_path early
            file_path_str = data["file_path"]
            if ("\x00" in file_path_str or file_path_str.startswith("/")
                    or ".." in file_path_str.split("/") or ".." in file_path_str.split("\\")):
                logger.warning(f"Suspicious file_path from LLM: {file_path_str!r}")
                return None

            # Only accept high confidence fixes
            confidence = data.get("confidence", "low").lower()
            if confidence != "high":
                logger.info(f"Skipping {confidence} confidence fix")
                return None

            return FixSuggestion(
                file_path=file_path_str,
                bug_description=data["bug_description"],
                fix_description=data["fix_description"],
                original_code=data["original_code"],
                fixed_code=data["fixed_code"],
                pr_title=data["pr_title"],
                pr_body=data["pr_body"],
                confidence=confidence,
                related_issue=int(data["related_issue"]) if data.get("related_issue") and str(data["related_issue"]).isdigit() else None,
            )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            return None
        except (KeyError, TypeError) as e:
            logger.error(f"Invalid LLM response structure: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error parsing LLM response ({type(e).__name__}): {e}")
            return None

    def _extract_json(self, text: str) -> Optional[str]:
        """Extract JSON object from text.

        Args:
            text: Text potentially containing JSON.

        Returns:
            JSON string if found, None otherwise.
        """
        # Try to find JSON block in markdown code fence
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                return text[start:end].strip()

        if "```" in text:
            start = text.find("```") + 3
            end = text.find("```", start)
            if end > start:
                content = text[start:end].strip()
                if content.startswith("{"):
                    return content

        # Try to find raw JSON
        start = text.find("{")
        if start == -1:
            return None

        # Find matching closing brace
        depth = 0
        for i, char in enumerate(text[start:], start):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]

        return None

    def apply_fix(self, repo_path: Path, fix: FixSuggestion) -> bool:
        """Apply a fix to the repository.

        Args:
            repo_path: Path to the repository.
            fix: Fix to apply.

        Returns:
            True if fix was applied successfully.
        """
        try:
            file_path = (repo_path / fix.file_path).resolve()

            # Security: Prevent path traversal attacks
            if not file_path.is_relative_to(repo_path.resolve()):
                logger.error(f"Path traversal detected: {fix.file_path}")
                return False

            if not file_path.exists():
                logger.error(f"File not found: {fix.file_path}")
                return False

            content = file_path.read_text(encoding="utf-8")

            # Check for exact match
            match_count = content.count(fix.original_code)
            if match_count == 0:
                logger.error(f"Original code not found in {fix.file_path}")
                return False
            if match_count > 1:
                logger.error(
                    f"Found {match_count} matches for original code in {fix.file_path}, "
                    "cannot safely apply fix — LLM response too ambiguous"
                )
                return False

            # Apply the fix (single exact match)
            new_content = content.replace(fix.original_code, fix.fixed_code, 1)

            # Write to temp file first, then validate before replacing the original
            tmp_fd, tmp_path = tempfile.mkstemp(
                suffix=file_path.suffix, dir=file_path.parent
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as tmp_f:
                    tmp_f.write(new_content)

                # Validate syntax on the temp file
                if not self._validate_syntax(Path(tmp_path)):
                    logger.error(f"Syntax validation failed, discarding fix for {fix.file_path}")
                    return False

                # Atomic replace: rename temp file over original
                os.replace(tmp_path, file_path)
                logger.info(f"Applied fix to {fix.file_path}")
                return True
            finally:
                # Clean up temp file if it still exists (validation failed)
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)

        except Exception as e:
            logger.error(f"Failed to apply fix: {e}")
            return False

    def _validate_syntax(self, file_path: Path) -> bool:
        """Validate file syntax after fix.

        Args:
            file_path: Path to the file.

        Returns:
            True if syntax is valid.
        """
        suffix = file_path.suffix.lower()

        if suffix == ".py":
            return self._validate_python_syntax(file_path)
        elif suffix in (".js", ".jsx", ".ts", ".tsx"):
            return self._validate_js_syntax(file_path)

        # For other languages, assume valid
        return True

    def _validate_python_syntax(self, file_path: Path) -> bool:
        """Validate Python syntax.

        Args:
            file_path: Path to the Python file.

        Returns:
            True if syntax is valid.
        """
        try:
            import ast
            content = file_path.read_text(encoding="utf-8")
            ast.parse(content)
            return True
        except (SyntaxError, UnicodeDecodeError, OSError, MemoryError) as e:
            logger.debug(f"Python syntax validation failed for {file_path}: {e}")
            return False

    def _validate_js_syntax(self, file_path: Path) -> bool:
        """Validate JavaScript/TypeScript syntax using Node.js.

        Args:
            file_path: Path to the JS/TS file.

        Returns:
            True if syntax is valid, False otherwise (fail-safe).
        """
        try:
            result = subprocess.run(
                ["node", "--check", str(file_path)],
                capture_output=True,
                timeout=2,
            )
            return result.returncode == 0
        except FileNotFoundError:
            # Node.js not installed - fail safe, don't allow unvalidated JS
            logger.warning("Node.js not available, cannot validate JS/TS syntax")
            return False
        except subprocess.TimeoutExpired:
            logger.warning(f"JS syntax validation timed out for {file_path}")
            return False
        except Exception as e:
            logger.error(f"JS syntax validation error: {e}")
            return False
