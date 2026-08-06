"""
integrations/slack_connector.py — Slack recap connector

Implements the TaskConnector protocol.
Enabled when SLACK_WEBHOOK_URL is set in env.

Note: Slack doesn't create trackable tasks, so create_task() is a no-op.
It only implements post_recap() to send a summary after all tasks are created.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional

from integrations.base import BaseConnector
from core.models import ActionItem


class SlackConnector(BaseConnector):

    @property
    def name(self) -> str:
        return "Slack"

    @property
    def enabled(self) -> bool:
        return bool(os.getenv("SLACK_WEBHOOK_URL"))

    # ── TaskConnector interface ──────────────────

    def create_task(self, item: ActionItem, meeting_date: str) -> dict:
        """Slack doesn't create tasks — handled entirely in post_recap."""
        return {
            "connector": "slack",
            "item_id":   item.id,
            "task_url":  None,
            "task_id":   None,
            "success":   True,
            "error":     None,
        }

    def post_recap(self, created: list[dict], meeting_date: str) -> bool:
        """Post a Slack recap after all tasks are created."""
        webhook = os.getenv("SLACK_WEBHOOK_URL")
        if not webhook:
            return False

        # Only include GitHub issues in recap (filter by task_url)
        issues = [r for r in created if r.get("task_url")]

        items_text = "\n".join(
            f"  ✅ <{r['task_url']}|{r.get('title', r['item_id'])}>"
            for r in issues
        ) or "  _No issues created_"

        message = {
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": f"📋 MeetingMind Recap — {meeting_date}"}
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Action Items Created* ({len(issues)}):\n{items_text}"
                    }
                },
                {
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "🤖 Sent by _MeetingMind_"}
                    ]
                }
            ]
        }

        try:
            data = json.dumps(message).encode()
            req  = urllib.request.Request(
                webhook,
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception:
            return False
