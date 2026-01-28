"""LLM provider implementations."""

from src.llm.base import BaseLLM, BaseHTTPLLM, LLMResponse, DEFAULT_TIMEOUT, DEFAULT_MAX_TOKENS
from src.llm.azure import AzureLLM
from src.llm.nanogpt import NanoGPTLLM
from src.llm.ollama import OllamaLLM

__all__ = [
    "BaseLLM",
    "BaseHTTPLLM",
    "LLMResponse",
    "AzureLLM",
    "NanoGPTLLM",
    "OllamaLLM",
    "DEFAULT_TIMEOUT",
    "DEFAULT_MAX_TOKENS",
]


def get_llm(provider: str, config: dict) -> BaseLLM:
    """Factory function to get the appropriate LLM provider.

    Args:
        provider: Provider name (azure, nanogpt, ollama).
        config: Provider-specific configuration dictionary.

    Returns:
        Configured LLM instance.

    Raises:
        ValueError: If provider is not supported.
    """
    providers = {
        "azure": AzureLLM,
        "nanogpt": NanoGPTLLM,
        "ollama": OllamaLLM,
    }

    if provider not in providers:
        raise ValueError(f"Unsupported LLM provider: {provider}")

    return providers[provider](**config)
