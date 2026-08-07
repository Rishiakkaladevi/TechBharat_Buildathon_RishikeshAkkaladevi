"""
integrations/slack_connector.py — Rich Slack Block Kit recap connector.

Posts a comprehensive, formatted meeting summary to Slack after execution.
Includes: summary, action items with owners/dates, decisions, risks, open questions.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional
from groq import Groq

from integrations.base import BaseConnector
from core.models import ActionItem
from core.extraction import MODEL


class SlackConnector(BaseConnector):

    @property
    def name(self) -> str:
        return "Slack"

    @property
    def enabled(self) -> bool:
        return bool(os.getenv("SLACK_WEBHOOK_URL"))

    def create_task(self, item: ActionItem, meeting_date: str) -> dict:
        """Slack doesn't create tasks — recap posted in post_recap."""
        return {
            "connector": "slack",
            "item_id":   item.id,
            "task_url":  None,
            "task_id":   None,
            "success":   True,
            "error":     None,
        }

    def post_recap(
        self,
        created: list[dict],
        meeting_date: str,
        meeting_record: Optional[dict] = None,
    ) -> bool:
        """Post a rich Slack recap with full meeting context."""
        webhook = os.getenv("SLACK_WEBHOOK_URL")
        if not webhook:
            return False

        mr = meeting_record or {}
        summary   = mr.get("summary", "")
        decisions = mr.get("decisions", [])
        risks     = mr.get("risks", [])
        questions = mr.get("open_questions", [])

        issues  = [r for r in created if r.get("task_url")]

        prompt = f"""You are an executive assistant. Please write a highly professional, concise, corporate-toned Slack meeting recap based on the raw meeting data below. 

CRITICAL INSTRUCTIONS:
1. DO NOT USE ANY EMOJIS.
2. Use clean, professional markdown formatting appropriate for Slack (e.g., *bold* for headings, bullet points).
3. Do not blindly copy-paste the raw data. Synthesize it into a polished, professional update.
4. Only include sections that have data.

--- RAW MEETING DATA ---
Date: {meeting_date}
Summary: {summary}
Decisions: {decisions}
Risks: {risks}
Open Questions: {questions}
Action Items / Assigned Tasks: {json.dumps(issues, indent=2)}
"""

        try:
            client = Groq(api_key=os.getenv("GROQ_API_KEY"))
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": "You are a professional executive assistant."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=2048,
            )
            professional_recap = response.choices[0].message.content.strip()
        except Exception as e:
            professional_recap = f"*Meeting Summary - {meeting_date}*\n\n_Note: Professional recap generation failed ({e})._\n\nSummary: {summary}"

        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": professional_recap
                }
            },
            {"type": "divider"},
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"MeetingMind · {meeting_date} · {len(issues)} tasks assigned"
                    }
                ]
            }
        ]

        message = {"blocks": blocks}

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
