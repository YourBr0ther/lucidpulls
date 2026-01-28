"""Azure AI Studios LLM client implementation."""

import logging
from typing import Optional

import httpx

from src.llm.base import BaseLLM, LLMResponse

logger = logging.getLogger("lucidpulls.llm.azure")


class AzureLLM(BaseLLM):
    """Azure AI Studios (OpenAI) client."""

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        deployment_name: str = "gpt-4",
        api_version: str = "2024-02-15-preview",
    ):
        """Initialize Azure OpenAI client.

        Args:
            endpoint: Azure OpenAI endpoint URL.
            api_key: Azure OpenAI API key.
            deployment_name: Name of the deployed model.
            api_version: API version to use.
        """
        self.endpoint = endpoint.rstrip("/")
        self.api_key = api_key
        self.deployment_name = deployment_name
        self.api_version = api_version
        self._client = httpx.Client(timeout=300.0)

    def generate(self, prompt: str, system_prompt: Optional[str] = None) -> LLMResponse:
        """Generate a response using Azure OpenAI.

        Args:
            prompt: The user prompt.
            system_prompt: Optional system prompt.

        Returns:
            LLMResponse with generated content.
        """
        url = (
            f"{self.endpoint}/openai/deployments/{self.deployment_name}"
            f"/chat/completions?api-version={self.api_version}"
        )

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "messages": messages,
            "temperature": 0.1,  # Low temperature for consistent code fixes
            "max_tokens": 4096,
        }

        headers = {
            "Content-Type": "application/json",
            "api-key": self.api_key,
        }

        logger.debug(f"Sending request to Azure: deployment={self.deployment_name}")

        try:
            response = self._client.post(url, json=payload, headers=headers)
            response.raise_for_status()

            data = response.json()
            choices = data.get("choices", [])

            if not choices:
                logger.warning("No choices in Azure response")
                return LLMResponse(content="", model=self.deployment_name)

            content = choices[0].get("message", {}).get("content", "")
            usage = data.get("usage", {})

            logger.debug(f"Received response: {len(content)} characters")

            return LLMResponse(
                content=content,
                model=self.deployment_name,
                tokens_used=usage.get("total_tokens"),
                finish_reason=choices[0].get("finish_reason"),
            )
        except httpx.HTTPStatusError as e:
            logger.error(f"Azure HTTP error: {e.response.status_code}")
            return LLMResponse(content="", model=self.deployment_name)
        except httpx.RequestError as e:
            logger.error(f"Azure request error: {e}")
            return LLMResponse(content="", model=self.deployment_name)

    def is_available(self) -> bool:
        """Check if Azure OpenAI is available.

        Returns:
            True if endpoint is reachable and API key is valid.
        """
        if not self.api_key or not self.endpoint:
            logger.warning("Azure API key or endpoint not configured")
            return False

        # Try a minimal request to verify credentials
        try:
            url = (
                f"{self.endpoint}/openai/deployments/{self.deployment_name}"
                f"/chat/completions?api-version={self.api_version}"
            )

            payload = {
                "messages": [{"role": "user", "content": "test"}],
                "max_tokens": 1,
            }

            headers = {
                "Content-Type": "application/json",
                "api-key": self.api_key,
            }

            response = self._client.post(url, json=payload, headers=headers, timeout=10.0)
            # 200 means it works, 429 means rate limited but credentials are valid
            return response.status_code in (200, 429)
        except Exception as e:
            logger.error(f"Azure availability check failed: {e}")
            return False

    @property
    def provider_name(self) -> str:
        """Get provider name."""
        return "Azure AI Studios"

    def __del__(self):
        """Clean up HTTP client."""
        if hasattr(self, "_client"):
            self._client.close()
