"""
integrations/base.py — Connector Protocol + Registry

Every integration (GitHub, Linear, Jira, Slack, Calendar...) implements
the TaskConnector protocol. The execute_tools node calls all registered,
enabled connectors — no code change needed to add a new one.

To add a new connector:
  1. Create integrations/linear_client.py
  2. Implement TaskConnector protocol
  3. Register it in CONNECTOR_REGISTRY below
  4. Set ENABLED_CONNECTORS in .env
  → Done. Zero changes to graph.py or app.py.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable
from core.models import ActionItem


# ─────────────────────────────────────────────
# Connector Protocol
# Every connector MUST implement these two methods.
# ─────────────────────────────────────────────

@runtime_checkable
class TaskConnector(Protocol):

    @property
    def name(self) -> str:
        """Human-readable connector name e.g. 'GitHub Issues'"""
        ...

    @property
    def enabled(self) -> bool:
        """Whether this connector is active (checks env vars)."""
        ...

    def create_task(self, item: ActionItem, meeting_date: str) -> dict:
        """
        Create a task/issue/ticket for one approved action item.

        Returns:
            {
                "connector":    "github",
                "item_id":      "ai_001",
                "task_url":     "https://...",
                "task_id":      "42",
                "success":      True,
                "error":        None,
            }
        """
        ...

    def task_exists(self, item: ActionItem) -> str | None:
        """
        Check if a task already exists for this item (for dedup).
        Returns the existing task URL if found, None otherwise.
        """
        ...

    def post_recap(self, created: list[dict], meeting_date: str) -> bool:
        """
        Optional: post a recap/summary after all tasks are created.
        Return True if posted, False if not applicable / failed.
        """
        ...


# ─────────────────────────────────────────────
# Base class (optional convenience — not required)
# ─────────────────────────────────────────────

class BaseConnector:
    """Optional base class with sensible defaults."""

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @property
    def enabled(self) -> bool:
        return False

    def task_exists(self, item: ActionItem) -> str | None:
        return None   # Default: no dedup check

    def post_recap(self, created: list[dict], meeting_date: str) -> bool:
        return False  # Default: no recap


# ─────────────────────────────────────────────
# Connector Registry
# ─────────────────────────────────────────────

def _load_connectors() -> dict[str, type]:
    """
    Lazily import connector classes.
    Only connectors with the right env vars will be .enabled.
    """
    registry = {}

    try:
        from integrations.github_connector import GitHubConnector
        registry["github"] = GitHubConnector
    except ImportError:
        pass

    try:
        from integrations.slack_connector import SlackConnector
        registry["slack"] = SlackConnector
    except ImportError:
        pass

    try:
        from integrations.linear_connector import LinearConnector
        registry["linear"] = LinearConnector
    except ImportError:
        pass

    try:
        from integrations.jira_connector import JiraConnector
        registry["jira"] = JiraConnector
    except ImportError:
        pass

    return registry


def get_enabled_connectors() -> list[TaskConnector]:
    """
    Returns instances of all connectors that are currently enabled
    (i.e., have their required env vars set).
    """
    registry  = _load_connectors()
    instances = [cls() for cls in registry.values()]
    return [c for c in instances if c.enabled]


def get_connector_status() -> dict[str, bool]:
    """
    Returns a status dict of all known connectors and whether they're enabled.
    Used by the Streamlit sidebar to show integration status.
    """
    registry = _load_connectors()
    return {
        key: cls().enabled
        for key, cls in registry.items()
    }
