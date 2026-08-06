"""
config.py — centralised environment variable loader for MeetingMind.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# LLM
GROQ_API_KEY       = os.getenv("GROQ_API_KEY", "")

# Transcription
DEEPGRAM_API_KEY   = os.getenv("DEEPGRAM_API_KEY", "")

# GitHub
GITHUB_TOKEN       = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO        = os.getenv("GITHUB_REPO", "")

# Slack
SLACK_WEBHOOK_URL  = os.getenv("SLACK_WEBHOOK_URL", "")

# Storage
MEETING_DB_PATH    = os.getenv("MEETING_DB_PATH", "data/audit.db")
LOG_PATH           = os.getenv("LOG_PATH", "data/audit_log.jsonl")


def validate_required() -> list[str]:
    """Returns a list of missing required config keys."""
    missing = []
    if not GROQ_API_KEY:
        missing.append("GROQ_API_KEY")
    if not GITHUB_TOKEN:
        missing.append("GITHUB_TOKEN")
    if not GITHUB_REPO:
        missing.append("GITHUB_REPO")
    return missing
