"""
GitHub Issues integration.

Handles:
  - Creating issues from approved action items
  - Dedup check (search existing issues before creating)
  - Returning issue URL for audit log
"""

from __future__ import annotations

import os
from typing import Optional
from github import Github, GithubException
from core.models import ActionItem


def _get_client() -> Github:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN not set in environment")
    return Github(token)


def _get_repo(client: Github):
    repo_name = os.getenv("GITHUB_REPO")
    if not repo_name:
        raise ValueError("GITHUB_REPO not set (format: username/repo-name)")
    return client.get_repo(repo_name)


# ─────────────────────────────────────────────
# Dedup check
# ─────────────────────────────────────────────

def issue_already_exists(title: str) -> Optional[str]:
    """
    Search GitHub for an open issue with the same title.
    Returns the issue URL if found, None otherwise.
    """
    try:
        client = _get_client()
        repo   = _get_repo(client)

        # Search open issues by title
        query  = f'repo:{os.getenv("GITHUB_REPO")} is:issue is:open "{title}" in:title'
        issues = client.search_issues(query=query)

        for issue in issues:
            if issue.title.strip().lower() == title.strip().lower():
                return issue.html_url

        return None
    except Exception:
        return None  # On search failure, don't block creation


# ─────────────────────────────────────────────
# Issue body builder
# ─────────────────────────────────────────────

def _build_issue_body(item: ActionItem, meeting_date: str) -> str:
    owner_name  = item.resolved_owner.name  if item.resolved_owner else item.raw_owner
    owner_email = item.resolved_owner.email if item.resolved_owner else "unresolved"
    due_date    = str(item.resolved_date) if item.resolved_date else item.raw_due_date or "Not set"
    priority    = item.priority.value if hasattr(item.priority, "value") else str(item.priority)

    evidence_section = ""
    if item.evidence_quote:
        ts = f" (@ {item.evidence_timestamp})" if item.evidence_timestamp else ""
        evidence_section = f"""
## 🗣️ Evidence
> "{item.evidence_quote}"
{ts}
"""

    description_section = ""
    if item.description:
        description_section = f"\n## 📝 Description\n{item.description}\n"

    return f"""## Action Item
{description_section}
| Field       | Value |
|-------------|-------|
| **Owner**   | {owner_name} ({owner_email}) |
| **Due Date**| {due_date} |
| **Priority**| {priority.capitalize()} |
| **Confidence** | {int(item.confidence * 100)}% |
{evidence_section}
---
*🤖 Created by MeetingMind from meeting on {meeting_date}*
*Item ID: `{item.id}` | Dedup key: `{item.dedup_key or 'N/A'}`*
"""


# ─────────────────────────────────────────────
# Create issue
# ─────────────────────────────────────────────

def create_github_issue(
    item: ActionItem,
    meeting_date: str,
) -> dict:
    """
    Create a GitHub issue for one approved action item.

    Returns:
        {
            "item_id": str,
            "issue_number": int,
            "issue_url": str,
            "title": str,
        }
    """
    client = _get_client()
    repo   = _get_repo(client)

    # Build assignees list
    assignees = []
    if item.resolved_owner and item.resolved_owner.github_username:
        assignees = [item.resolved_owner.github_username]

    # Build labels
    priority_str = item.priority.value if hasattr(item.priority, "value") else str(item.priority)
    labels = [
        "meeting-action-item",
        f"priority:{priority_str}",
    ]

    # Ensure labels exist in the repo (create if missing)
    _ensure_labels(repo, labels)

    body = _build_issue_body(item, meeting_date)

    issue = repo.create_issue(
        title     = item.title,
        body      = body,
        assignees = assignees,
        labels    = labels,
    )

    return {
        "item_id":      item.id,
        "issue_number": issue.number,
        "issue_url":    issue.html_url,
        "title":        item.title,
    }


def _ensure_labels(repo, label_names: list[str]) -> None:
    """Create any labels that don't exist yet in the repo."""
    existing = {label.name for label in repo.get_labels()}
    color_map = {
        "meeting-action-item": "0075ca",
        "priority:high":       "d93f0b",
        "priority:medium":     "e4e669",
        "priority:low":        "0e8a16",
    }
    for name in label_names:
        if name not in existing:
            try:
                repo.create_label(
                    name=name,
                    color=color_map.get(name, "cccccc")
                )
            except GithubException:
                pass  # label might have been created concurrently


# ─────────────────────────────────────────────
# Batch create (called by execute_tools node)
# ─────────────────────────────────────────────

async def create_issues_async(
    items: list[ActionItem],
    meeting_date: str,
) -> list[dict]:
    """
    Create GitHub issues for all items concurrently using asyncio.
    Returns list of result dicts (successes) and logs failures.
    """
    import asyncio

    async def _create_one(item: ActionItem) -> Optional[dict]:
        try:
            loop   = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, create_github_issue, item, meeting_date
            )
            return result
        except Exception as e:
            return {"item_id": item.id, "error": str(e), "issue_url": None}

    tasks   = [_create_one(item) for item in items]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r]
