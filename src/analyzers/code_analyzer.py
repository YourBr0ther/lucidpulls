"""Code analyzer for bug detection and fix generation."""

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

from src.analyzers.base import BaseAnalyzer, AnalysisResult, FixSuggestion
from src.llm.base import BaseLLM, CODE_REVIEW_SYSTEM_PROMPT, FIX_GENERATION_PROMPT_TEMPLATE

logger = logging.getLogger("lucidpulls.analyzers.code")


class CodeAnalyzer(BaseAnalyzer):
    """Analyzes code for bugs and generates fixes using an LLM."""

    def __init__(self, llm: BaseLLM):
        """Initialize code analyzer.

        Args:
            llm: LLM provider instance.
        """
        self.llm = llm

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

            # Send to LLM
            logger.info(f"Sending {len(files)} files to LLM for analysis")
            response = self.llm.generate(prompt, system_prompt=CODE_REVIEW_SYSTEM_PROMPT)

            if not response.success:
                logger.error("LLM returned empty response")
                return AnalysisResult(
                    repo_name=repo_name,
                    found_fix=False,
                    error="LLM returned empty response",
                    files_analyzed=len(files),
                    issues_reviewed=len(issues) if issues else 0,
                    analysis_time_seconds=time.time() - start_time,
                )

            # Parse LLM response
            fix = self._parse_llm_response(response.content)

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
                body = issue["body"][:500]
                if len(issue["body"]) > 500:
                    body += "..."
                lines.append(f"  Description: {body}")
            lines.append("")

        return "\n".join(lines)

    def _parse_llm_response(self, response: str) -> Optional[FixSuggestion]:
        """Parse LLM response into a FixSuggestion.

        Args:
            response: Raw LLM response.

        Returns:
            FixSuggestion if valid response, None otherwise.
        """
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

            # Only accept high confidence fixes
            confidence = data.get("confidence", "low").lower()
            if confidence != "high":
                logger.info(f"Skipping {confidence} confidence fix")
                return None

            return FixSuggestion(
                file_path=data["file_path"],
                bug_description=data["bug_description"],
                fix_description=data["fix_description"],
                original_code=data["original_code"],
                fixed_code=data["fixed_code"],
                pr_title=data["pr_title"],
                pr_body=data["pr_body"],
                confidence=confidence,
                related_issue=data.get("related_issue"),
            )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"Error parsing LLM response: {e}")
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
            file_path = repo_path / fix.file_path

            if not file_path.exists():
                logger.error(f"File not found: {fix.file_path}")
                return False

            content = file_path.read_text(encoding="utf-8")

            # Check for multiple matches
            match_count = content.count(fix.original_code)
            if match_count == 0:
                logger.error(f"Original code not found in {fix.file_path}")
                return False
            if match_count > 1:
                logger.warning(
                    f"Found {match_count} matches for original code in {fix.file_path}, "
                    "applying to first match only"
                )

            # Apply the fix (only first occurrence)
            new_content = content.replace(fix.original_code, fix.fixed_code, 1)

            file_path.write_text(new_content, encoding="utf-8")
            logger.info(f"Applied fix to {fix.file_path}")

            # Validate syntax (basic check)
            if not self._validate_syntax(file_path):
                # Revert
                file_path.write_text(content, encoding="utf-8")
                logger.error(f"Syntax validation failed, reverted {fix.file_path}")
                return False

            return True

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
        except SyntaxError:
            return False

    def _validate_js_syntax(self, file_path: Path) -> bool:
        """Validate JavaScript/TypeScript syntax using Node.js.

        Args:
            file_path: Path to the JS/TS file.

        Returns:
            True if syntax is valid or if Node.js is not available.
        """
        try:
            result = subprocess.run(
                ["node", "--check", str(file_path)],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except FileNotFoundError:
            # Node.js not installed, skip validation
            logger.debug("Node.js not available for JS/TS syntax validation")
            return True
        except subprocess.TimeoutExpired:
            logger.warning(f"JS syntax validation timed out for {file_path}")
            return True
        except Exception as e:
            logger.debug(f"JS syntax validation failed: {e}")
            return True
