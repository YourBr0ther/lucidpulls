"""Tests for LLM providers."""

from unittest.mock import Mock, patch, MagicMock

import pytest
import httpx

from src.llm.base import BaseLLM, LLMResponse
from src.llm.ollama import OllamaLLM
from src.llm.azure import AzureLLM
from src.llm.nanogpt import NanoGPTLLM
from src.llm import get_llm


class TestLLMResponse:
    """Tests for LLMResponse."""

    def test_success_with_content(self):
        """Test success property with content."""
        response = LLMResponse(content="Hello", model="test")
        assert response.success is True

    def test_success_without_content(self):
        """Test success property without content."""
        response = LLMResponse(content="", model="test")
        assert response.success is False


class TestOllamaLLM:
    """Tests for OllamaLLM."""

    def test_init(self):
        """Test initialization."""
        llm = OllamaLLM(host="http://localhost:11434", model="codellama")
        assert llm.host == "http://localhost:11434"
        assert llm.model == "codellama"
        assert llm.provider_name == "Ollama"

    def test_host_trailing_slash_removed(self):
        """Test trailing slash is removed from host."""
        llm = OllamaLLM(host="http://localhost:11434/")
        assert llm.host == "http://localhost:11434"

    @patch.object(httpx.Client, "post")
    def test_generate_success(self, mock_post):
        """Test successful generation."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "response": "Test response",
            "eval_count": 100,
            "done_reason": "stop",
        }
        mock_post.return_value = mock_response

        llm = OllamaLLM()
        response = llm.generate("Test prompt")

        assert response.content == "Test response"
        assert response.model == "codellama"
        assert response.tokens_used == 100

    @patch.object(httpx.Client, "post")
    def test_generate_with_system_prompt(self, mock_post):
        """Test generation with system prompt."""
        mock_response = Mock()
        mock_response.json.return_value = {"response": "Test"}
        mock_post.return_value = mock_response

        llm = OllamaLLM()
        llm.generate("User prompt", system_prompt="System prompt")

        call_args = mock_post.call_args
        payload = call_args[1]["json"]
        assert payload["system"] == "System prompt"
        assert payload["prompt"] == "User prompt"

    @patch.object(httpx.Client, "post")
    def test_generate_http_error(self, mock_post):
        """Test handling of HTTP errors."""
        mock_post.side_effect = httpx.HTTPStatusError(
            "Error", request=Mock(), response=Mock(status_code=500)
        )

        llm = OllamaLLM()
        response = llm.generate("Test")

        assert response.content == ""
        assert response.success is False

    @patch.object(httpx.Client, "get")
    def test_is_available_success(self, mock_get):
        """Test availability check when available."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "models": [{"name": "codellama:latest"}]
        }
        mock_get.return_value = mock_response

        llm = OllamaLLM(model="codellama")
        assert llm.is_available() is True

    @patch.object(httpx.Client, "get")
    def test_is_available_model_not_found(self, mock_get):
        """Test availability check when model not found."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "models": [{"name": "other:latest"}]
        }
        mock_get.return_value = mock_response

        llm = OllamaLLM(model="codellama")
        assert llm.is_available() is False


class TestAzureLLM:
    """Tests for AzureLLM."""

    def test_init(self):
        """Test initialization."""
        llm = AzureLLM(
            endpoint="https://test.openai.azure.com",
            api_key="test-key",
            deployment_name="gpt-4",
        )
        assert llm.endpoint == "https://test.openai.azure.com"
        assert llm.api_key == "test-key"
        assert llm.deployment_name == "gpt-4"
        assert llm.provider_name == "Azure AI Studios"

    @patch.object(httpx.Client, "post")
    def test_generate_success(self, mock_post):
        """Test successful generation."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "choices": [
                {"message": {"content": "Test response"}, "finish_reason": "stop"}
            ],
            "usage": {"total_tokens": 150},
        }
        mock_post.return_value = mock_response

        llm = AzureLLM(
            endpoint="https://test.openai.azure.com",
            api_key="test-key",
        )
        response = llm.generate("Test prompt")

        assert response.content == "Test response"
        assert response.tokens_used == 150

    def test_is_available_no_key(self):
        """Test availability check without API key."""
        llm = AzureLLM(endpoint="https://test.openai.azure.com", api_key="")
        assert llm.is_available() is False


class TestNanoGPTLLM:
    """Tests for NanoGPTLLM."""

    def test_init(self):
        """Test initialization."""
        llm = NanoGPTLLM(api_key="test-key", model="gpt-4")
        assert llm.api_key == "test-key"
        assert llm.model == "gpt-4"
        assert llm.provider_name == "NanoGPT"

    @patch.object(httpx.Client, "post")
    def test_generate_success(self, mock_post):
        """Test successful generation."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "choices": [
                {"message": {"content": "Test response"}, "finish_reason": "stop"}
            ],
            "usage": {"total_tokens": 100},
        }
        mock_post.return_value = mock_response

        llm = NanoGPTLLM(api_key="test-key")
        response = llm.generate("Test prompt")

        assert response.content == "Test response"

    def test_is_available_no_key(self):
        """Test availability check without API key."""
        llm = NanoGPTLLM(api_key="")
        assert llm.is_available() is False


class TestGetLLM:
    """Tests for get_llm factory function."""

    def test_get_ollama(self):
        """Test getting Ollama LLM."""
        llm = get_llm("ollama", {"host": "http://localhost:11434", "model": "codellama"})
        assert isinstance(llm, OllamaLLM)

    def test_get_azure(self):
        """Test getting Azure LLM."""
        llm = get_llm("azure", {
            "endpoint": "https://test.openai.azure.com",
            "api_key": "test-key",
            "deployment_name": "gpt-4",
        })
        assert isinstance(llm, AzureLLM)

    def test_get_nanogpt(self):
        """Test getting NanoGPT LLM."""
        llm = get_llm("nanogpt", {"api_key": "test-key", "model": "gpt-4"})
        assert isinstance(llm, NanoGPTLLM)

    def test_get_invalid_provider(self):
        """Test getting invalid provider raises error."""
        with pytest.raises(ValueError, match="Unsupported LLM provider"):
            get_llm("invalid", {})
