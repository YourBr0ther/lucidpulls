"""Code analyzer for bug detection and fix generation."""

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal, Optional

import httpx
from pydantic import BaseModel, field_validator

from src.analyzers.base import BaseAnalyzer, AnalysisResult, FixSuggestion, TestResult
from src.llm.base import BaseLLM, CODE_REVIEW_SYSTEM_PROMPT, FIX_GENERATION_PROMPT_TEMPLATE
from src.models import GithubIssue
from src.utils import retry

logger = logging.getLogger("lucidpulls.analyzers.code")


class LLMFixResponse(BaseModel):
    """Pydantic model for validated LLM fix responses."""

    found_bug: bool
    file_path: str = ""
    bug_description: str = ""
    fix_description: str = ""
    original_code: str = ""
    fixed_code: str = ""
    pr_title: str = ""
    pr_body: str = ""
    confidence: str = "low"
    related_issue: Optional[int] = None

    @field_validator("file_path")
    @classmethod
    def validate_file_path(cls, v: str) -> str:
        """Reject suspicious file paths from LLM output."""
        if v and ("\x00" in v or v.startswith("/")
                or ".." in PurePosixPath(v).parts
                or ".." in PureWindowsPath(v).parts):
            raise ValueError(f"Suspicious file_path: {v!r}")
        return v

    @field_validator("related_issue", mode="before")
    @classmethod
    def coerce_related_issue(cls, v: object) -> Optional[int]:
        """Coerce related_issue to int, handling strings/floats/booleans from LLMs."""
        if v is None or v is False or v == "":
            return None
        try:
            val = int(v)  # type: ignore[arg-type]
            return val if val > 0 else None
        except (ValueError, TypeError):
            return None


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
        exceptions=(ValueError, ConnectionError, TimeoutError, OSError, httpx.RequestError),
    )
    def _call_llm_with_retry(self, prompt: str, system_prompt: str) -> "LLMResponse":
        """Call LLM with retry logic.

        Args:
            prompt: The prompt to send to the LLM.
            system_prompt: The system prompt to use.

        Returns:
            LLMResponse from the provider.

        Raises:
            ValueError: If LLM returns an unsuccessful response.
        """
        response = self.llm.generate(prompt, system_prompt=system_prompt)
        if not response.success:
            raise ValueError("LLM returned empty response")
        return response

    def analyze(
        self,
        repo_path: Path,
        repo_name: str,
        issues: Optional[list[GithubIssue]] = None,
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
            tokens_used = None
            try:
                llm_response = self._call_llm_with_retry(
                    prompt, system_prompt=CODE_REVIEW_SYSTEM_PROMPT
                )
                response_content = llm_response.content
                tokens_used = llm_response.tokens_used
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
                llm_tokens_used=tokens_used,
            )

        except Exception as e:
            logger.error(f"Analysis failed for {repo_name}: {e}")
            return AnalysisResult(
                repo_name=repo_name,
                found_fix=False,
                error=str(e),
                analysis_time_seconds=time.time() - start_time,
            )

    def _format_issues(self, issues: list[GithubIssue]) -> str:
        """Format issues for LLM consumption.

        Args:
            issues: List of GithubIssue typed dicts.

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
        """Parse LLM response into a FixSuggestion using Pydantic validation.

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

            logger.debug(f"Extracted JSON: {len(json_str)} chars")

            try:
                data = json.loads(json_str)
            except json.JSONDecodeError:
                # LLMs often produce unescaped newlines inside JSON string values;
                # escape bare newlines that appear inside strings and retry.
                cleaned = self._fix_json_newlines(json_str)
                logger.debug(f"Retrying JSON parse after newline fix, first 200 chars: {cleaned[:200]!r}")
                data = json.loads(cleaned)
            fix_response = LLMFixResponse.model_validate(data)

            if not fix_response.found_bug:
                logger.info("LLM did not find any bugs")
                return None

            # Validate required fields are non-empty
            required_fields = [
                "file_path", "bug_description", "fix_description",
                "original_code", "fixed_code", "pr_title", "pr_body",
            ]
            for field in required_fields:
                if not getattr(fix_response, field):
                    logger.warning(f"Missing required field in LLM response: {field}")
                    return None

            # Only accept high confidence fixes
            confidence = fix_response.confidence.lower()
            if confidence != "high":
                logger.info(f"Skipping {confidence} confidence fix")
                return None

            return FixSuggestion(
                file_path=fix_response.file_path,
                bug_description=fix_response.bug_description,
                fix_description=fix_response.fix_description,
                original_code=fix_response.original_code,
                fixed_code=fix_response.fixed_code,
                pr_title=fix_response.pr_title,
                pr_body=fix_response.pr_body,
                confidence=confidence,
                related_issue=fix_response.related_issue,
            )

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            return None
        except Exception as e:
            logger.error(f"Error parsing LLM response ({type(e).__name__}): {e}")
            return None

    def _extract_json(self, text: str) -> Optional[str]:
        """Extract JSON object from text using string-aware brace matching.

        LLM responses may contain markdown code fences (```json ... ```) around
        the JSON, and the JSON itself may contain ``` sequences inside string
        values (e.g. in pr_body with embedded code blocks). Naive fence
        detection breaks on these, so we always use brace matching that tracks
        whether we're inside a JSON string to avoid being confused by braces
        or other syntax inside string values.

        Args:
            text: Text potentially containing JSON.

        Returns:
            JSON string if found, None otherwise.
        """
        # Find the first '{' to start brace matching
        start = text.find("{")
        if start == -1:
            return None

        # String-aware brace matching: track depth while skipping string contents
        depth = 0
        in_string = False
        i = start
        while i < len(text):
            ch = text[i]

            if in_string:
                if ch == "\\" and i + 1 < len(text):
                    i += 2  # Skip escaped character
                    continue
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return text[start:i + 1]

            i += 1

        return None

    @staticmethod
    def _fix_json_newlines(text: str) -> str:
        """Escape bare newlines inside JSON string values.

        LLMs often emit literal newlines inside JSON strings instead of \\n.
        This walks the text character-by-character and escapes newlines that
        appear between unescaped double-quotes.
        """
        result: list[str] = []
        in_string = False
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == '"':
                # Count consecutive preceding backslashes
                backslash_count = 0
                j = i - 1
                while j >= 0 and text[j] == "\\":
                    backslash_count += 1
                    j -= 1
                # Quote is escaped only if preceded by odd number of backslashes
                if backslash_count % 2 == 0:
                    in_string = not in_string
                result.append(ch)
            elif ch == "\n" and in_string:
                result.append("\\n")
            elif ch == "\r" and in_string:
                result.append("\\r")
            elif ch == "\t" and in_string:
                result.append("\\t")
            else:
                result.append(ch)
            i += 1
        return "".join(result)

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

            # Reject oversized fixes — prevents LLM from rewriting entire functions
            max_fix_lines = 200
            max_growth_factor = 3.0
            orig_lines = fix.original_code.count("\n") + 1
            fixed_lines = fix.fixed_code.count("\n") + 1
            if fixed_lines > max_fix_lines:
                logger.error(
                    f"Fix too large ({fixed_lines} lines, max {max_fix_lines})"
                )
                return False
            if orig_lines > 0 and fixed_lines / orig_lines > max_growth_factor:
                logger.error(
                    f"Fix grows code too much ({orig_lines} → {fixed_lines} lines, "
                    f"max {max_growth_factor}x)"
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
                if not self._validate_syntax(Path(tmp_path), repo_path=repo_path):
                    logger.error(f"Syntax validation failed, discarding fix for {fix.file_path}")
                    return False

                # Atomic replace: rename temp file over original
                os.replace(tmp_path, file_path)
                logger.info(f"Applied fix to {fix.file_path}")
                return True
            finally:
                # Clean up temp file if it still exists (validation failed)
                try:
                    os.unlink(tmp_path)
                except FileNotFoundError:
                    pass

        except Exception as e:
            logger.error(f"Failed to apply fix: {e}")
            return False

    def _validate_syntax(self, file_path: Path, repo_path: Optional[Path] = None) -> bool:
        """Validate file syntax after fix.

        Args:
            file_path: Path to the file.
            repo_path: Repository root path (used to bound Cargo.toml search).

        Returns:
            True if syntax is valid.
        """
        suffix = file_path.suffix.lower()

        if suffix == ".py":
            return self._validate_python_syntax(file_path)
        elif suffix in (".js", ".jsx"):
            return self._validate_js_syntax(file_path)
        elif suffix in (".ts", ".tsx"):
            return self._validate_ts_syntax(file_path)
        elif suffix == ".go":
            return self._validate_go_syntax(file_path)
        elif suffix == ".java":
            return self._validate_java_syntax(file_path)
        elif suffix == ".rs":
            return self._validate_rust_syntax(file_path, repo_path=repo_path)

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
        """Validate JavaScript syntax using Node.js.

        Args:
            file_path: Path to the JS file.

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
            logger.warning("Node.js not available, cannot validate JS syntax")
            return False
        except subprocess.TimeoutExpired:
            logger.warning(f"JS syntax validation timed out for {file_path}")
            return False
        except Exception as e:
            logger.error(f"JS syntax validation error: {e}")
            return False

    def _validate_ts_syntax(self, file_path: Path) -> bool:
        """Validate TypeScript syntax using tsc.

        Node.js `--check` cannot parse TypeScript syntax, so we use
        `npx tsc --noEmit` instead. If tsc is not available, we skip
        validation (return True) rather than incorrectly rejecting valid TS.

        Args:
            file_path: Path to the TS/TSX file.

        Returns:
            True if syntax is valid or if tsc is not available.
        """
        try:
            result = subprocess.run(
                ["npx", "--yes", "typescript", "tsc", "--noEmit", "--allowJs",
                 "--esModuleInterop", "--jsx", "react-jsx",
                 "--isolatedModules", "--noResolve",
                 "--moduleResolution", "bundler",
                 str(file_path)],
                capture_output=True,
                timeout=30,
                text=True,
            )
            if result.returncode == 0:
                return True
            # Only reject on syntax errors (TS1xxx); ignore type errors
            # from missing imports/config which are expected on standalone files.
            stderr = result.stdout + result.stderr
            if "error TS1" in stderr:
                logger.debug(f"TS syntax error found in {file_path}")
                return False
            return True
        except FileNotFoundError:
            # npx/tsc not available — skip validation rather than reject valid TS
            logger.debug("TypeScript compiler not available, skipping TS syntax validation")
            return True
        except subprocess.TimeoutExpired:
            logger.warning(f"TS syntax validation timed out for {file_path}")
            return True
        except Exception as e:
            logger.debug(f"TS syntax validation error (skipping): {e}")
            return True

    def _validate_go_syntax(self, file_path: Path) -> bool:
        """Validate Go syntax using go vet.

        Args:
            file_path: Path to the Go file.

        Returns:
            True if syntax is valid, or if Go is not installed (fail-open).
        """
        try:
            result = subprocess.run(
                ["go", "vet", str(file_path)],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except FileNotFoundError:
            logger.debug("Go not available, skipping Go syntax validation")
            return True
        except subprocess.TimeoutExpired:
            logger.warning(f"Go syntax validation timed out for {file_path}")
            return True
        except Exception as e:
            logger.debug(f"Go syntax validation error (skipping): {e}")
            return True

    def _validate_java_syntax(self, file_path: Path) -> bool:
        """Validate Java syntax using javac (compile-only, no output).

        Args:
            file_path: Path to the Java file.

        Returns:
            True if syntax is valid, or if javac is not installed (fail-open).
        """
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                result = subprocess.run(
                    ["javac", "-d", tmpdir, "-proc:none", str(file_path)],
                    capture_output=True,
                    timeout=15,
                )
            # javac returns 0 on success; non-zero includes both syntax and
            # type errors.  Only reject on clear syntax issues (same approach
            # as TS: accept import/type errors since we compile a single file).
            if result.returncode == 0:
                return True
            stderr = (result.stdout + result.stderr).decode(errors="replace")
            # "error:" with no "cannot find symbol" → likely syntax error
            if "error:" in stderr and "cannot find symbol" not in stderr:
                logger.debug(f"Java syntax error found in {file_path}")
                return False
            return True
        except FileNotFoundError:
            logger.debug("javac not available, skipping Java syntax validation")
            return True
        except subprocess.TimeoutExpired:
            logger.warning(f"Java syntax validation timed out for {file_path}")
            return True
        except Exception as e:
            logger.debug(f"Java syntax validation error (skipping): {e}")
            return True

    def _validate_rust_syntax(self, file_path: Path, repo_path: Optional[Path] = None) -> bool:
        """Validate Rust syntax using rustc (parse-only via --edition flag).

        Args:
            file_path: Path to the Rust file.
            repo_path: Repository root path to bound Cargo.toml search.

        Returns:
            True if syntax is valid, or if rustc is not installed (fail-open).
        """
        try:
            # Use cargo check if Cargo.toml exists (better for real projects)
            found_cargo = False
            if repo_path is not None:
                repo_root = repo_path.resolve()
                search_dir = file_path.parent
                while True:
                    if (search_dir / "Cargo.toml").exists():
                        found_cargo = True
                        cargo_dir = search_dir
                        break
                    # Stop at repo boundary — never walk outside the repo
                    if search_dir.resolve() == repo_root or search_dir == search_dir.parent:
                        break
                    search_dir = search_dir.parent

            if found_cargo:
                result = subprocess.run(
                    ["cargo", "check", "--message-format=short"],
                    cwd=str(cargo_dir),
                    capture_output=True,
                    timeout=30,
                )
            else:
                result = subprocess.run(
                    ["rustc", "--edition", "2021", "--crate-type", "lib",
                     "-o", os.devnull, str(file_path)],
                    capture_output=True,
                    timeout=15,
                )
            if result.returncode == 0:
                return True
            stderr = (result.stdout + result.stderr).decode(errors="replace")
            # Only reject on syntax-level errors; allow unresolved import errors
            if "error[E" in stderr and "cannot find" not in stderr:
                logger.debug(f"Rust syntax error found in {file_path}")
                return False
            return True
        except FileNotFoundError:
            logger.debug("Rust toolchain not available, skipping Rust syntax validation")
            return True
        except subprocess.TimeoutExpired:
            logger.warning(f"Rust syntax validation timed out for {file_path}")
            return True
        except Exception as e:
            logger.debug(f"Rust syntax validation error (skipping): {e}")
            return True

    def run_repo_tests(self, repo_path: Path, timeout: int = 120) -> "TestResult":
        """Detect and run a repo's test suite.

        Looks for common test markers (pytest, package.json, go.mod) and
        runs the appropriate test command.  Returns a TestResult indicating
        whether tests passed, failed, or could not be run.

        Args:
            repo_path: Root path of the cloned repository.
            timeout: Maximum seconds to allow tests to run.

        Returns:
            TestResult with status and details.
        """
        cmd = self._detect_test_command(repo_path)
        if cmd is None:
            return TestResult(status="skipped", detail="No test runner detected")

        try:
            result = subprocess.run(
                cmd,
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode == 0:
                return TestResult(status="passed")
            # Capture last few lines of output for diagnostics
            output = (result.stdout + result.stderr).strip()
            tail = "\n".join(output.splitlines()[-10:]) if output else ""
            return TestResult(status="failed", detail=tail)
        except subprocess.TimeoutExpired:
            return TestResult(status="timeout", detail=f"Tests exceeded {timeout}s limit")
        except FileNotFoundError as e:
            return TestResult(status="skipped", detail=f"Test runner not installed: {e}")
        except Exception as e:
            return TestResult(status="skipped", detail=f"Error running tests: {e}")

    @staticmethod
    def _detect_test_command(repo_path: Path) -> Optional[list[str]]:
        """Detect the appropriate test command for a repository.

        Args:
            repo_path: Root path of the repository.

        Returns:
            Command list to execute, or None if no test runner detected.
        """
        # Python: look for pytest markers
        if any(
            (repo_path / f).exists()
            for f in ("pytest.ini", "pyproject.toml", "setup.py", "setup.cfg")
        ):
            tests_dir = repo_path / "tests"
            test_dir = repo_path / "test"
            if tests_dir.is_dir() or test_dir.is_dir():
                return ["python3", "-m", "pytest", "-x", "-q", "--tb=short", "--no-header"]

        # JavaScript / TypeScript: look for package.json with test script
        pkg_json = repo_path / "package.json"
        if pkg_json.exists():
            try:
                import json
                pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
                if "test" in pkg.get("scripts", {}):
                    return ["npm", "test", "--", "--passWithNoTests"]
            except (json.JSONDecodeError, OSError):
                pass

        # Go: look for go.mod
        if (repo_path / "go.mod").exists():
            return ["go", "test", "-short", "-count=1", "./..."]

        return None
