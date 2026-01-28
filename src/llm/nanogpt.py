"""NanoGPT API client implementation."""

import logging
from typing import Optional

import httpx

from src.llm.base import BaseLLM, LLMResponse
from src.utils import retry

logger = logging.getLogger("lucidpulls.llm.nanogpt")


class NanoGPTLLM(BaseLLM):
    """NanoGPT API client."""

    BASE_URL = "https://nano-gpt.com/api"

    def __init__(self, api_key: str, model: str = "chatgpt-4o-latest"):
        """Initialize NanoGPT client.

        Args:
            api_key: NanoGPT API key.
            model: Model name to use.
        """
        self.api_key = api_key
        self.model = model
        self._client = httpx.Client(timeout=300.0)

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> LLMResponse:
        """Generate a response using NanoGPT.

        Args:
            prompt: The user prompt.
            system_prompt: Optional system prompt.

        Returns:
            LLMResponse with generated content.
        """
        try:
            return self._generate_with_retry(prompt, system_prompt)
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.error(f"NanoGPT request failed after retries: {e}")
            return LLMResponse(content="", model=self.model)

    @retry(max_attempts=3, delay=2.0, backoff=2.0, exceptions=(httpx.HTTPStatusError, httpx.RequestError))
    def _generate_with_retry(self, prompt: str, system_prompt: Optional[str] = None) -> LLMResponse:
        """Internal generate method with retry logic."""
        url = f"{self.BASE_URL}/v1/chat/completions"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        logger.debug(f"Sending request to NanoGPT: model={self.model}")

        response = self._client.post(url, json=payload, headers=headers)
        response.raise_for_status()

        data = response.json()
        choices = data.get("choices", [])

        if not choices:
            logger.warning("No choices in NanoGPT response")
            return LLMResponse(content="", model=self.model)

        content = choices[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})

        logger.debug(f"Received response: {len(content)} characters")

        return LLMResponse(
            content=content,
            model=self.model,
            tokens_used=usage.get("total_tokens"),
            finish_reason=choices[0].get("finish_reason"),
        )

    def is_available(self) -> bool:
        """Check if NanoGPT is available.

        Returns:
            True if API key is configured and service is reachable.
        """
        if not self.api_key:
            logger.warning("NanoGPT API key not configured")
            return False

        try:
            # Try to list models to verify API key
            url = f"{self.BASE_URL}/v1/models"
            headers = {"Authorization": f"Bearer {self.api_key}"}

            response = self._client.get(url, headers=headers, timeout=10.0)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"NanoGPT availability check failed: {e}")
            return False

    @property
    def provider_name(self) -> str:
        """Get provider name."""
        return "NanoGPT"

    def __del__(self):
        """Clean up HTTP client."""
        if hasattr(self, "_client"):
            self._client.close()
