"""
integrations/github_connector.py — GitHub Issues connector

Implements the TaskConnector protocol.
Enabled when GITHUB_TOKEN and GITHUB_REPO are set in env.
"""

from __future__ import annotations

import os
from typing import Optional
from github import Github, GithubException

from integrations.base import BaseConnector
from core.models import ActionItem


class GitHubConnector(BaseConnector):

    @property
    def name(self) -> str:
        return "GitHub Issues"

    @property
    def enabled(self) -> bool:
        return bool(os.getenv("GITHUB_TOKEN") and os.getenv("GITHUB_REPO"))

    def _client(self) -> Github:
        return Github(os.getenv("GITHUB_TOKEN"))

    def _repo(self):
        return self._client().get_repo(os.getenv("GITHUB_REPO"))

    # ── TaskConnector interface ──────────────────

    def task_exists(self, item: ActionItem) -> Optional[str]:
        """Search GitHub for an existing open issue with the same title."""
        try:
            repo  = os.getenv("GITHUB_REPO")
            query = f'repo:{repo} is:issue is:open "{item.title}" in:title'
            for issue in self._client().search_issues(query=query):
                if issue.title.strip().lower() == item.title.strip().lower():
                    return issue.html_url
        except Exception:
            pass
        return None

    def create_task(self, item: ActionItem, meeting_date: str) -> dict:
        """Create a GitHub issue for one approved action item."""
        try:
            repo      = self._repo()
            assignees = []
            if item.resolved_owner and item.resolved_owner.github_username:
                assignees = [item.resolved_owner.github_username]

            priority_str = item.priority.value if hasattr(item.priority, "value") else str(item.priority)
            labels = ["meeting-action-item", f"priority:{priority_str}"]
            self._ensure_labels(repo, labels)

            issue = repo.create_issue(
                title     = item.title,
                body      = self._build_body(item, meeting_date),
                assignees = assignees,
                labels    = labels,
            )

            return {
                "connector": "github",
                "item_id":   item.id,
                "task_url":  issue.html_url,
                "task_id":   str(issue.number),
                "success":   True,
                "error":     None,
            }

        except Exception as e:
            return {
                "connector": "github",
                "item_id":   item.id,
                "task_url":  None,
                "task_id":   None,
                "success":   False,
                "error":     str(e),
            }

    def post_recap(self, created: list[dict], meeting_date: str) -> bool:
        return False  # GitHub doesn't post recaps; Slack connector handles that

    # ── Helpers ──────────────────────────────────

    def _build_body(self, item: ActionItem, meeting_date: str) -> str:
        owner_name  = item.resolved_owner.name  if item.resolved_owner else item.raw_owner
        owner_email = item.resolved_owner.email if item.resolved_owner else "unresolved"
        due_date    = str(item.resolved_date) if item.resolved_date else item.raw_due_date or "Not set"
        priority_str = item.priority.value if hasattr(item.priority, "value") else str(item.priority)

        evidence = ""
        if item.evidence_quote:
            ts = f" (@ {item.evidence_timestamp})" if item.evidence_timestamp else ""
            evidence = f"\n## 🗣️ Evidence\n> \"{item.evidence_quote}\"{ts}\n"

        desc = f"\n## 📝 Description\n{item.description}\n" if item.description else ""

        return (
            f"## Action Item\n{desc}\n"
            f"| Field | Value |\n|---|---|\n"
            f"| **Owner** | {owner_name} ({owner_email}) |\n"
            f"| **Due Date** | {due_date} |\n"
            f"| **Priority** | {priority_str.capitalize()} |\n"
            f"| **Confidence** | {int(item.confidence * 100)}% |\n"
            f"{evidence}\n---\n"
            f"*🤖 Created by MeetingMind · Meeting: {meeting_date} · Item: `{item.id}`*"
        )

    def _ensure_labels(self, repo, label_names: list[str]) -> None:
        existing = {l.name for l in repo.get_labels()}
        colors   = {
            "meeting-action-item": "0075ca",
            "priority:high":       "d93f0b",
            "priority:medium":     "e4e669",
            "priority:low":        "0e8a16",
        }
        for name in label_names:
            if name not in existing:
                try:
                    repo.create_label(name=name, color=colors.get(name, "cccccc"))
                except GithubException:
                    pass
