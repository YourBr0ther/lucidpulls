"""Tests for configuration module."""

import os
from unittest.mock import patch

import pytest

from src.config import Settings, load_settings, get_settings


class TestSettings:
    """Tests for Settings class."""

    def test_default_values(self):
        """Test that default values are set correctly."""
        settings = Settings(_env_file=None)

        assert settings.llm_provider == "ollama"
        assert settings.notification_channel == "discord"
        assert settings.schedule_start == "02:00"
        assert settings.schedule_deadline == "06:00"
        assert settings.report_delivery == "07:00"
        assert settings.timezone == "America/New_York"
        assert settings.log_level == "INFO"

    def test_repo_list_empty(self):
        """Test repo_list with empty repos string."""
        settings = Settings(_env_file=None, repos="")
        assert settings.repo_list == []

    def test_repo_list_single(self):
        """Test repo_list with single repo."""
        settings = Settings(_env_file=None, repos="owner/repo")
        assert settings.repo_list == ["owner/repo"]

    def test_repo_list_multiple(self):
        """Test repo_list with multiple repos."""
        settings = Settings(_env_file=None, repos="owner/repo1,owner/repo2,owner/repo3")
        assert settings.repo_list == ["owner/repo1", "owner/repo2", "owner/repo3"]

    def test_repo_list_with_whitespace(self):
        """Test repo_list handles whitespace."""
        settings = Settings(_env_file=None, repos="owner/repo1, owner/repo2 , owner/repo3")
        assert settings.repo_list == ["owner/repo1", "owner/repo2", "owner/repo3"]

    def test_ssh_path_expansion(self):
        """Test SSH path expands ~."""
        settings = Settings(_env_file=None, ssh_key_path="~/.ssh/id_rsa")
        assert "~" not in settings.ssh_key_path
        assert settings.ssh_key_path.endswith(".ssh/id_rsa")

    def test_get_llm_config_ollama(self):
        """Test LLM config for Ollama."""
        settings = Settings(
            _env_file=None,
            llm_provider="ollama",
            ollama_host="http://localhost:11434",
            ollama_model="codellama",
        )
        config = settings.get_llm_config()

        assert config["host"] == "http://localhost:11434"
        assert config["model"] == "codellama"

    def test_get_llm_config_azure(self):
        """Test LLM config for Azure."""
        settings = Settings(
            _env_file=None,
            llm_provider="azure",
            azure_endpoint="https://test.openai.azure.com",
            azure_api_key="test-key",
            azure_deployment_name="gpt-4",
        )
        config = settings.get_llm_config()

        assert config["endpoint"] == "https://test.openai.azure.com"
        assert config["api_key"] == "test-key"
        assert config["deployment_name"] == "gpt-4"

    def test_get_llm_config_nanogpt(self):
        """Test LLM config for NanoGPT."""
        settings = Settings(
            _env_file=None,
            llm_provider="nanogpt",
            nanogpt_api_key="test-key",
            nanogpt_model="gpt-4",
        )
        config = settings.get_llm_config()

        assert config["api_key"] == "test-key"
        assert config["model"] == "gpt-4"

    def test_get_notification_config_discord(self):
        """Test notification config for Discord."""
        settings = Settings(
            _env_file=None,
            notification_channel="discord",
            discord_webhook_url="https://discord.com/api/webhooks/123/abc",
        )
        config = settings.get_notification_config()

        assert config["webhook_url"] == "https://discord.com/api/webhooks/123/abc"

    def test_get_notification_config_teams(self):
        """Test notification config for Teams."""
        settings = Settings(
            _env_file=None,
            notification_channel="teams",
            teams_webhook_url="https://outlook.office.com/webhook/123",
        )
        config = settings.get_notification_config()

        assert config["webhook_url"] == "https://outlook.office.com/webhook/123"


class TestLoadSettings:
    """Tests for load_settings function."""

    @patch.dict(os.environ, {}, clear=True)
    def test_load_settings(self):
        """Test loading settings."""
        settings = Settings(_env_file=None)
        assert isinstance(settings, Settings)

    @patch.dict(os.environ, {}, clear=True)
    def test_get_settings_singleton(self):
        """Test get_settings returns cached instance."""
        # Reset the global
        import src.config
        src.config._settings = None

        with patch("src.config.load_settings", return_value=Settings(_env_file=None)):
            settings1 = get_settings()
            settings2 = get_settings()

            assert settings1 is settings2
