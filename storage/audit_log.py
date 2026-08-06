"""
Audit log — append-only event log stored as JSONL + SQLite index.

Every external action the agent takes is recorded here.
The SQLite table is used for:
  - Dedup key lookups (fast)
  - Audit log queries in Streamlit (filterable)
The JSONL file is the raw immutable record.
"""

from __future__ import annotations

import os
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from core.models import AuditEntry


# ─────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────

def _db_path() -> str:
    return os.getenv("MEETING_DB_PATH", "data/audit.db")

def _log_path() -> str:
    return os.getenv("LOG_PATH", "data/audit_log.jsonl")

def _ensure_dirs():
    Path(_db_path()).parent.mkdir(parents=True, exist_ok=True)
    Path(_log_path()).parent.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────
# Schema setup
# ─────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT    NOT NULL,
    event        TEXT    NOT NULL,
    meeting_id   TEXT    NOT NULL,
    item_id      TEXT,
    title        TEXT,
    owner_email  TEXT,
    dedup_key    TEXT    UNIQUE,      -- NULL for non-creation events
    external_ref TEXT,
    approved_by  TEXT,
    approved_at  TEXT,
    skipped_reason TEXT,
    payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_dedup ON audit_log(dedup_key);
CREATE INDEX IF NOT EXISTS idx_meeting ON audit_log(meeting_id);
"""


def init_db() -> None:
    _ensure_dirs()
    conn = sqlite3.connect(_db_path())
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# Dedup check
# ─────────────────────────────────────────────

def is_duplicate(dedup_key: str) -> bool:
    """Returns True if this dedup_key already exists in the audit log."""
    try:
        conn   = sqlite3.connect(_db_path())
        cursor = conn.execute(
            "SELECT 1 FROM audit_log WHERE dedup_key = ? LIMIT 1",
            (dedup_key,)
        )
        result = cursor.fetchone()
        conn.close()
        return result is not None
    except Exception:
        return False  # On DB error, don't block creation


# ─────────────────────────────────────────────
# Write entry
# ─────────────────────────────────────────────

def log_entry(entry: AuditEntry) -> None:
    """Append one audit entry to both JSONL and SQLite."""
    _ensure_dirs()

    entry_dict = entry.model_dump(mode="json")

    # --- JSONL (raw immutable log) ---
    with open(_log_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry_dict) + "\n")

    # --- SQLite (indexed for queries) ---
    try:
        conn = sqlite3.connect(_db_path())
        conn.execute("""
            INSERT OR IGNORE INTO audit_log
            (timestamp, event, meeting_id, item_id, title, owner_email,
             dedup_key, external_ref, approved_by, approved_at, skipped_reason, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry.timestamp.isoformat(),
            entry.event,
            entry.meeting_id,
            entry.item_id,
            entry.title,
            entry.owner_email,
            entry.dedup_key,
            entry.external_ref,
            entry.approved_by,
            entry.approved_at.isoformat() if entry.approved_at else None,
            entry.skipped_reason,
            json.dumps(entry.payload) if entry.payload else None,
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        # JSONL is the source of truth — SQLite failure is non-fatal
        print(f"[audit_log] SQLite write failed: {e}")


# ─────────────────────────────────────────────
# Read for Streamlit viewer
# ─────────────────────────────────────────────

def get_all_entries(
    meeting_id: Optional[str] = None,
    event_filter: Optional[str] = None,
    limit: int = 500,
) -> list[dict]:
    """Fetch audit entries for the Streamlit audit log page."""
    try:
        conn  = sqlite3.connect(_db_path())
        conn.row_factory = sqlite3.Row

        query  = "SELECT * FROM audit_log WHERE 1=1"
        params = []

        if meeting_id:
            query  += " AND meeting_id = ?"
            params.append(meeting_id)
        if event_filter:
            query  += " AND event = ?"
            params.append(event_filter)

        query += f" ORDER BY id DESC LIMIT {limit}"

        rows   = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        # Fall back to reading JSONL
        return _read_jsonl(meeting_id, event_filter, limit)


def _read_jsonl(
    meeting_id: Optional[str],
    event_filter: Optional[str],
    limit: int,
) -> list[dict]:
    entries = []
    try:
        with open(_log_path(), "r", encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                    if meeting_id and e.get("meeting_id") != meeting_id:
                        continue
                    if event_filter and e.get("event") != event_filter:
                        continue
                    entries.append(e)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return entries[-limit:]


# ─────────────────────────────────────────────
# Convenience builders
# ─────────────────────────────────────────────

def log_issue_created(
    meeting_id: str,
    item_id: str,
    title: str,
    owner_email: str,
    dedup_key: str,
    issue_url: str,
    payload: dict,
    approved_by: str,
) -> AuditEntry:
    entry = AuditEntry(
        timestamp    = datetime.utcnow(),
        event        = "github_issue_created",
        meeting_id   = meeting_id,
        item_id      = item_id,
        title        = title,
        owner_email  = owner_email,
        dedup_key    = dedup_key,
        external_ref = issue_url,
        payload      = payload,
        approved_by  = approved_by,
        approved_at  = datetime.utcnow(),
    )
    log_entry(entry)
    return entry


def log_skipped_duplicate(
    meeting_id: str,
    item_id: str,
    title: str,
    dedup_key: str,
    existing_url: Optional[str] = None,
) -> AuditEntry:
    entry = AuditEntry(
        timestamp      = datetime.utcnow(),
        event          = "skipped_duplicate",
        meeting_id     = meeting_id,
        item_id        = item_id,
        title          = title,
        dedup_key      = dedup_key,
        external_ref   = existing_url,
        skipped_reason = "Dedup key already present in audit log",
    )
    log_entry(entry)
    return entry


def log_item_rejected(
    meeting_id: str,
    item_id: str,
    title: str,
    approved_by: str,
    reason: Optional[str] = None,
) -> AuditEntry:
    entry = AuditEntry(
        timestamp      = datetime.utcnow(),
        event          = "item_rejected",
        meeting_id     = meeting_id,
        item_id        = item_id,
        title          = title,
        approved_by    = approved_by,
        approved_at    = datetime.utcnow(),
        skipped_reason = reason or "Rejected by reviewer",
    )
    log_entry(entry)
    return entry
