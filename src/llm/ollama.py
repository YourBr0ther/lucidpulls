"""Ollama LLM client implementation."""

import logging
from typing import Optional

import httpx

from src.llm.base import BaseLLM, LLMResponse
from src.utils import retry

logger = logging.getLogger("lucidpulls.llm.ollama")


class OllamaLLM(BaseLLM):
    """Ollama local LLM client."""

    def __init__(self, host: str = "http://localhost:11434", model: str = "codellama"):
        """Initialize Ollama client.

        Args:
            host: Ollama server URL.
            model: Model name to use.
        """
        self.host = host.rstrip("/")
        self.model = model
        self._client = httpx.Client(timeout=300.0)  # 5 minute timeout for generation

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> LLMResponse:
        """Generate a response using Ollama.

        Args:
            prompt: The user prompt.
            system_prompt: Optional system prompt.

        Returns:
            LLMResponse with generated content.
        """
        try:
            return self._generate_with_retry(prompt, system_prompt)
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            logger.error(f"Ollama request failed after retries: {e}")
            return LLMResponse(content="", model=self.model)

    @retry(max_attempts=3, delay=2.0, backoff=2.0, exceptions=(httpx.HTTPStatusError, httpx.RequestError))
    def _generate_with_retry(self, prompt: str, system_prompt: Optional[str] = None) -> LLMResponse:
        """Internal generate method with retry logic."""
        url = f"{self.host}/api/generate"

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }

        if system_prompt:
            payload["system"] = system_prompt

        logger.debug(f"Sending request to Ollama: model={self.model}")

        response = self._client.post(url, json=payload)
        response.raise_for_status()

        data = response.json()
        content = data.get("response", "")

        logger.debug(f"Received response: {len(content)} characters")

        return LLMResponse(
            content=content,
            model=self.model,
            tokens_used=data.get("eval_count"),
            finish_reason=data.get("done_reason"),
        )

    def is_available(self) -> bool:
        """Check if Ollama server is available.

        Returns:
            True if Ollama is reachable and the model is available.
        """
        try:
            # Check server is up
            response = self._client.get(f"{self.host}/api/tags", timeout=5.0)
            response.raise_for_status()

            # Check if our model is available
            data = response.json()
            models = [m.get("name", "").split(":")[0] for m in data.get("models", [])]

            if self.model not in models and f"{self.model}:latest" not in [
                m.get("name", "") for m in data.get("models", [])
            ]:
                logger.warning(f"Model {self.model} not found in Ollama")
                return False

            return True
        except Exception as e:
            logger.error(f"Ollama availability check failed: {e}")
            return False

    @property
    def provider_name(self) -> str:
        """Get provider name."""
        return "Ollama"

    def close(self) -> None:
        """Close the HTTP client."""
        if hasattr(self, "_client"):
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        """Clean up HTTP client."""
        self.close()
