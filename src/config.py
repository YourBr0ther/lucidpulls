"""Configuration management for LucidPulls."""

import re
from pathlib import Path
from typing import Literal

import pytz
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Repository Configuration
    repos: str = Field(
        default="",
        description="Comma-separated list of repositories (owner/repo format)",
    )

    # GitHub Authentication
    github_token: str = Field(
        default="",
        description="GitHub Personal Access Token for API operations",
    )
    github_username: str = Field(
        default="",
        description="GitHub username for commits",
    )
    github_email: str = Field(
        default="",
        description="GitHub email for commits",
    )
    ssh_key_path: str = Field(
        default="~/.ssh/id_rsa",
        description="Path to SSH private key for git operations",
    )

    # LLM Provider
    llm_provider: Literal["azure", "nanogpt", "ollama"] = Field(
        default="ollama",
        description="LLM provider to use",
    )

    # Azure AI Studios
    azure_endpoint: str = Field(
        default="",
        description="Azure OpenAI endpoint URL",
    )
    azure_api_key: str = Field(
        default="",
        description="Azure OpenAI API key",
    )
    azure_deployment_name: str = Field(
        default="gpt-4",
        description="Azure OpenAI deployment name",
    )

    # NanoGPT
    nanogpt_api_key: str = Field(
        default="",
        description="NanoGPT API key",
    )
    nanogpt_model: str = Field(
        default="",
        description="NanoGPT model name",
    )

    # Ollama
    ollama_host: str = Field(
        default="http://localhost:11434",
        description="Ollama server URL",
    )
    ollama_model: str = Field(
        default="codellama",
        description="Ollama model name",
    )

    # Notification Channel
    notification_channel: Literal["teams", "discord"] = Field(
        default="discord",
        description="Notification channel to use",
    )

    # Microsoft Teams
    teams_webhook_url: str = Field(
        default="",
        description="Microsoft Teams webhook URL",
    )

    # Discord
    discord_webhook_url: str = Field(
        default="",
        description="Discord webhook URL",
    )

    # Schedule
    schedule_start: str = Field(
        default="02:00",
        description="Time to start nightly review (HH:MM format)",
    )
    schedule_deadline: str = Field(
        default="06:00",
        description="Deadline for completing review (HH:MM format)",
    )
    report_delivery: str = Field(
        default="07:00",
        description="Time to deliver morning report (HH:MM format)",
    )
    timezone: str = Field(
        default="America/New_York",
        description="Timezone for scheduling",
    )

    # Clone directory
    clone_dir: str = Field(
        default="/tmp/lucidpulls/repos",
        description="Directory to clone repositories into",
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Logging level",
    )

    @field_validator("ssh_key_path")
    @classmethod
    def expand_ssh_path(cls, v: str) -> str:
        """Expand ~ in SSH key path."""
        return str(Path(v).expanduser())

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        """Validate timezone is a valid IANA timezone name."""
        try:
            pytz.timezone(v)
            return v
        except pytz.UnknownTimeZoneError:
            raise ValueError(f"Invalid timezone: {v}. Must be a valid IANA timezone name.")

    @field_validator("schedule_start", "schedule_deadline", "report_delivery")
    @classmethod
    def validate_time_format(cls, v: str) -> str:
        """Validate time format is HH:MM."""
        if not re.match(r"^([01]?\d|2[0-3]):[0-5]\d$", v):
            raise ValueError(f"Invalid time format: {v}. Must be HH:MM (e.g., 02:00, 14:30)")
        return v

    @field_validator("repos")
    @classmethod
    def validate_repo_format(cls, v: str) -> str:
        """Validate repository format is owner/repo."""
        if not v:
            return v
        for repo in v.split(","):
            repo = repo.strip()
            if repo and not re.match(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$", repo):
                raise ValueError(
                    f"Invalid repository format: {repo}. Must be owner/repo format."
                )
        return v

    @model_validator(mode="after")
    def validate_github_credentials(self) -> "Settings":
        """Validate GitHub credentials are set together."""
        github_fields = [self.github_token, self.github_username, self.github_email]
        non_empty = [f for f in github_fields if f]

        # If any are set, all should be set
        if non_empty and len(non_empty) < 3:
            missing = []
            if not self.github_token:
                missing.append("github_token")
            if not self.github_username:
                missing.append("github_username")
            if not self.github_email:
                missing.append("github_email")
            raise ValueError(
                f"Incomplete GitHub configuration. Missing: {', '.join(missing)}"
            )
        return self

    @property
    def repo_list(self) -> list[str]:
        """Get list of repositories from comma-separated string."""
        if not self.repos:
            return []
        return [r.strip() for r in self.repos.split(",") if r.strip()]

    def get_llm_config(self) -> dict:
        """Get configuration for the selected LLM provider.

        Returns:
            Dictionary with provider-specific configuration.
        """
        if self.llm_provider == "azure":
            return {
                "endpoint": self.azure_endpoint,
                "api_key": self.azure_api_key,
                "deployment_name": self.azure_deployment_name,
            }
        elif self.llm_provider == "nanogpt":
            return {
                "api_key": self.nanogpt_api_key,
                "model": self.nanogpt_model,
            }
        else:  # ollama
            return {
                "host": self.ollama_host,
                "model": self.ollama_model,
            }

    def get_notification_config(self) -> dict:
        """Get configuration for the selected notification channel.

        Returns:
            Dictionary with channel-specific configuration.
        """
        if self.notification_channel == "teams":
            return {"webhook_url": self.teams_webhook_url}
        else:  # discord
            return {"webhook_url": self.discord_webhook_url}


def load_settings() -> Settings:
    """Load and return application settings.

    Returns:
        Settings instance with values from environment.
    """
    return Settings()


# Module-level singleton for convenience
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get or create the global settings instance.

    Returns:
        Cached Settings instance.
    """
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
