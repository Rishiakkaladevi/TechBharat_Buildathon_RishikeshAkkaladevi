"""
Pydantic schemas for MeetingMind.
All data flowing through the LangGraph pipeline is typed against these models.
"""

from __future__ import annotations
from datetime import date, datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────

class Priority(str, Enum):
    HIGH   = "high"
    MEDIUM = "medium"
    LOW    = "low"


class ResolutionStatus(str, Enum):
    CLEAN    = "clean"     # fully resolved, no human needed
    FLAGGED  = "flagged"   # resolved with low confidence — show to human
    FAILED   = "failed"    # could not resolve — human must fix before approval


class ItemStatus(str, Enum):
    PENDING  = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    FLAGGED  = "flagged"


# ─────────────────────────────────────────────
# Ingestion output
# ─────────────────────────────────────────────

class Utterance(BaseModel):
    """One speaker turn from the transcript."""
    speaker:   str            # "Alice" / "Speaker 0" / "UNKNOWN"
    timestamp: str            # "00:03:12"
    text:      str


# ─────────────────────────────────────────────
# Extraction output (raw — before resolution)
# ─────────────────────────────────────────────

class Decision(BaseModel):
    decision:  str
    context:   Optional[str] = None
    timestamp: Optional[str] = None          # "00:08:41"


class ActionItemRaw(BaseModel):
    """Action item as extracted by the LLM — owners/dates still raw strings."""
    id:                 str                  # "ai_001"
    title:              str
    description:        Optional[str] = None
    raw_owner:          str                  # "Priya" / "the backend team"
    raw_due_date:       Optional[str] = None # "by next Friday" / "ASAP"
    priority:           Priority = Priority.MEDIUM
    confidence:         float = Field(ge=0.0, le=1.0)
    evidence_quote:     Optional[str] = None # exact words from transcript
    evidence_timestamp: Optional[str] = None # "00:03:12"


class MeetingRecordRaw(BaseModel):
    """Full extraction output from the LLM — before any resolution."""
    meeting_id:     str                      # sha256 of transcript content
    meeting_date:   str                      # "2026-08-06"
    summary:        str
    decisions:      list[Decision]   = []
    open_questions: list[str]        = []
    risks:          list[str]        = []
    action_items:   list[ActionItemRaw] = []


# ─────────────────────────────────────────────
# Resolution output (resolved — ready for review)
# ─────────────────────────────────────────────

class ResolvedOwner(BaseModel):
    name:             str
    email:            str
    github_username:  Optional[str] = None
    match_score:      float = 1.0            # fuzzy match confidence
    resolution_method: str = "exact"         # "exact" | "fuzzy" | "llm" | "manual"


class ActionItem(BaseModel):
    """Action item after resolution — what the human reviews."""
    id:                 str
    title:              str
    description:        Optional[str] = None

    # Owner fields
    raw_owner:          str
    resolved_owner:     Optional[ResolvedOwner] = None
    owner_status:       ResolutionStatus = ResolutionStatus.FAILED

    # Date fields
    raw_due_date:       Optional[str] = None
    resolved_date:      Optional[date] = None
    date_method:        Optional[str] = None  # "dateparser" | "llm" | "manual"
    date_status:        ResolutionStatus = ResolutionStatus.FAILED

    # Metadata
    priority:           Priority = Priority.MEDIUM
    confidence:         float = Field(ge=0.0, le=1.0)
    evidence_quote:     Optional[str] = None
    evidence_timestamp: Optional[str] = None

    # Human review
    status:             ItemStatus = ItemStatus.PENDING
    human_notes:        Optional[str] = None

    # Dedup
    dedup_key:          Optional[str] = None  # sha256 computed after resolution


# ─────────────────────────────────────────────
# Audit log entry
# ─────────────────────────────────────────────

class AuditEntry(BaseModel):
    timestamp:      datetime
    event:          str           # "github_issue_created" | "item_rejected" | "skipped_duplicate"
    meeting_id:     str
    item_id:        Optional[str] = None
    title:          Optional[str] = None
    owner_email:    Optional[str] = None
    dedup_key:      Optional[str] = None
    payload:        Optional[dict] = None     # exact data sent to GitHub
    external_ref:   Optional[str] = None      # GitHub issue URL
    approved_by:    Optional[str] = None
    approved_at:    Optional[datetime] = None
    skipped_reason: Optional[str] = None


# ─────────────────────────────────────────────
# LangGraph State
# ─────────────────────────────────────────────

class ParticipantRoster(BaseModel):
    name:            str
    email:           str
    github_username: Optional[str] = None
    aliases:         list[str] = []          # ["Priya", "PS", "priya.s"]


class MeetingState(BaseModel):
    """
    The single state object that flows through every LangGraph node.
    Each node reads what it needs and writes its outputs back.
    """

    # ── Inputs (set at graph start) ──────────────────────
    transcript_raw:     str = ""
    transcript_path:    Optional[str] = None
    meeting_date:       str = ""             # "YYYY-MM-DD"
    participant_roster: list[ParticipantRoster] = []
    approved_by:        str = "user"         # who is running the review

    # ── After ingestion ──────────────────────────────────
    utterances:         list[Utterance] = []

    # ── After extraction ─────────────────────────────────
    meeting_record_raw: Optional[MeetingRecordRaw] = None

    # ── After resolution ─────────────────────────────────
    action_items:       list[ActionItem] = []   # all items, resolved or flagged
    has_flags:          bool = False             # any unresolved owners/dates?

    # ── After human review ───────────────────────────────
    approved_items:     list[ActionItem] = []
    rejected_items:     list[ActionItem] = []

    # ── After execution ──────────────────────────────────
    created_issues:     list[dict] = []          # [{item_id, url, number}]
    skipped_duplicates: list[str]  = []          # item ids that were duplicates
    slack_posted:       bool = False

    # ── Audit ─────────────────────────────────────────────
    audit_entries:      list[AuditEntry] = []

    # ── Error handling ───────────────────────────────────
    errors:             list[str] = []
    warnings:           list[str] = []

    class Config:
        use_enum_values = True
