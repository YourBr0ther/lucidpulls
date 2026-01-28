"""Notification channel implementations."""

from src.notifications.base import BaseNotifier, NotificationResult
from src.notifications.discord import DiscordNotifier
from src.notifications.teams import TeamsNotifier

__all__ = ["BaseNotifier", "NotificationResult", "DiscordNotifier", "TeamsNotifier"]


def get_notifier(channel: str, config: dict) -> BaseNotifier:
    """Factory function to get the appropriate notifier.

    Args:
        channel: Channel name (discord, teams).
        config: Channel-specific configuration dictionary.

    Returns:
        Configured notifier instance.

    Raises:
        ValueError: If channel is not supported.
    """
    channels = {
        "discord": DiscordNotifier,
        "teams": TeamsNotifier,
    }

    if channel not in channels:
        raise ValueError(f"Unsupported notification channel: {channel}")

    return channels[channel](**config)
