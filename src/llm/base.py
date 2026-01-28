"""Base LLM interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import httpx

# Constants for HTTP LLM clients
DEFAULT_TIMEOUT = 300.0  # 5 minute timeout for generation
DEFAULT_MAX_TOKENS = 4096


@dataclass
class LLMResponse:
    """Response from an LLM provider."""

    content: str
    model: str
    tokens_used: Optional[int] = None
    finish_reason: Optional[str] = None

    @property
    def success(self) -> bool:
        """Check if the response was successful."""
        return bool(self.content)


class BaseLLM(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> LLMResponse:
        """Generate a response from the LLM.

        Args:
            prompt: The user prompt to send to the model.
            system_prompt: Optional system prompt for context.

        Returns:
            LLMResponse containing the generated content.
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Check if the LLM provider is available and configured.

        Returns:
            True if the provider is ready to use.
        """
        pass

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Get the name of this provider.

        Returns:
            Human-readable provider name.
        """
        pass


class BaseHTTPLLM(BaseLLM):
    """Base class for HTTP-based LLM providers with shared functionality."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT):
        """Initialize HTTP LLM base.

        Args:
            timeout: Request timeout in seconds.
        """
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        """Close the HTTP client."""
        if hasattr(self, "_client") and self._client is not None:
            self._client.close()

    def __enter__(self) -> "BaseHTTPLLM":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# Common prompts for code review
CODE_REVIEW_SYSTEM_PROMPT = """You are an expert code reviewer focused on finding and fixing bugs.
Your job is to analyze code and identify clear, unambiguous bugs that can be safely fixed.

Focus on these conservative fix types:
- Null/None checks that are missing
- Error handling gaps
- Off-by-one errors
- Logic typos (wrong operators, inverted conditions)
- Resource leaks (unclosed files, connections)
- Obvious security issues (SQL injection, XSS)

DO NOT suggest:
- Style changes or refactoring
- Performance optimizations (unless critical)
- Adding new features
- Changing API signatures
- Anything that requires deep domain knowledge

When you find a bug, provide:
1. File path and line number
2. Description of the bug
3. The exact fix (old code → new code)
4. Confidence level (high/medium/low)

Only report HIGH confidence bugs that have clear, safe fixes."""

FIX_GENERATION_PROMPT_TEMPLATE = """Analyze this code for bugs and provide ONE actionable fix.

Repository: {repo_name}

Open Issues (bugs/enhancements):
{issues}

Code Files:
{code_files}

Instructions:
1. Review the code carefully
2. Consider the open issues if relevant
3. Identify the most important bug that can be safely fixed
4. Provide a complete fix

Respond in this exact JSON format:
{{
    "found_bug": true/false,
    "file_path": "path/to/file.py",
    "bug_description": "Brief description of the bug",
    "fix_description": "Brief description of the fix",
    "original_code": "The exact code to replace",
    "fixed_code": "The corrected code",
    "pr_title": "Short PR title",
    "pr_body": "Detailed PR description",
    "confidence": "high/medium/low",
    "related_issue": null or issue_number
}}

If no bugs are found, set found_bug to false and leave other fields empty."""
