"""
Resolution node — resolves raw owner names and due date strings
into concrete, actionable values.

Three-layer approach:
  1. Deterministic (RapidFuzz / dateparser) — fast, no API cost
  2. LLM fallback (Groq) — for ambiguous cases with transcript context
  3. Human review — when both layers fail (flagged in UI)
"""

from __future__ import annotations

import os
import json
import hashlib
from datetime import date, datetime, timedelta
from typing import Optional

import dateparser
from rapidfuzz import process, fuzz
from groq import Groq

from core.models import (
    ActionItem, ActionItemRaw, MeetingRecordRaw,
    ResolvedOwner, ResolutionStatus, Priority, ItemStatus
)


# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────

FUZZY_CONFIDENT_THRESHOLD = 85   # >= this → resolved cleanly
FUZZY_ATTEMPT_THRESHOLD   = 55   # >= this → try LLM fallback
                                 # <  this → straight to human review

MODEL = "llama-3.3-70b-versatile"


# ─────────────────────────────────────────────
# Owner resolution
# ─────────────────────────────────────────────

def _build_candidate_list(roster: list[dict]) -> list[tuple[str, dict]]:
    """Expand roster into (alias, participant) pairs for fuzzy matching."""
    candidates = []
    for p in roster:
        name    = p.get("name", "")
        email   = p.get("email", "")
        aliases = p.get("aliases", [])
        github  = p.get("github_username")

        if name:
            candidates.append((name, p))
        for alias in aliases:
            if alias:
                candidates.append((alias, p))
        # First name as alias
        first_name = name.split()[0] if name else ""
        if first_name and first_name != name:
            candidates.append((first_name, p))

    return candidates


def _resolve_owner_fuzzy(
    raw_owner: str,
    roster: list[dict],
) -> tuple[Optional[dict], float]:
    """
    Returns (participant_dict, score).
    Score is 0.0–100.0 (RapidFuzz scale).
    """
    if not roster:
        return None, 0.0

    candidates = _build_candidate_list(roster)
    if not candidates:
        return None, 0.0

    candidate_names = [c[0] for c in candidates]
    result = process.extractOne(
        raw_owner,
        candidate_names,
        scorer=fuzz.WRatio
    )

    if result is None:
        return None, 0.0

    best_name, score, idx = result
    matched_participant = candidates[idx][1]
    return matched_participant, score


def _resolve_owner_llm(
    raw_owner: str,
    roster: list[dict],
    transcript_excerpt: str,
    client: Groq,
) -> Optional[dict]:
    """Ask Groq to pick the best match using transcript context."""
    if not roster:
        return None

    roster_lines = "\n".join(
        f"- {p.get('name','')} ({p.get('email','')})"
        for p in roster
    )

    prompt = f"""Given these meeting participants:
{roster_lines}

And this transcript excerpt for context:
"{transcript_excerpt}"

The action item owner was referred to as: "{raw_owner}"

Which participant is most likely the owner? 
Reply with ONLY a JSON object: {{"name": "Full Name", "email": "email@..."}}
If you cannot determine with confidence, reply: {{"name": null, "email": null}}"""

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=100,
        )
        result = json.loads(response.choices[0].message.content)
        if not result.get("name"):
            return None

        # Find the matching participant in roster
        for p in roster:
            if p.get("name") == result.get("name") or p.get("email") == result.get("email"):
                return p
        return None
    except Exception:
        return None


def resolve_owner(
    raw_owner: str,
    roster: list[dict],
    transcript_excerpt: str = "",
    client: Optional[Groq] = None,
) -> tuple[Optional[ResolvedOwner], ResolutionStatus]:
    """
    Main owner resolution entry point.
    Returns (ResolvedOwner | None, ResolutionStatus)
    """
    raw_owner = raw_owner.strip()

    # Layer 1: Fuzzy match
    participant, score = _resolve_owner_fuzzy(raw_owner, roster)

    if participant and score >= FUZZY_CONFIDENT_THRESHOLD:
        return ResolvedOwner(
            name=participant.get("name", ""),
            email=participant.get("email", ""),
            github_username=participant.get("github_username"),
            match_score=score / 100.0,
            resolution_method="fuzzy",
        ), ResolutionStatus.CLEAN

    # Layer 2: LLM fallback (if we have a client and a partial match)
    if client and score >= FUZZY_ATTEMPT_THRESHOLD:
        llm_participant = _resolve_owner_llm(raw_owner, roster, transcript_excerpt, client)
        if llm_participant:
            return ResolvedOwner(
                name=llm_participant.get("name", ""),
                email=llm_participant.get("email", ""),
                github_username=llm_participant.get("github_username"),
                match_score=0.75,
                resolution_method="llm",
            ), ResolutionStatus.FLAGGED   # LLM resolved — still show to human for confirmation

    # Layer 3: Human review
    return None, ResolutionStatus.FAILED


# ─────────────────────────────────────────────
# Date resolution
# ─────────────────────────────────────────────

QUARTER_ENDS = {
    1: (3, 31), 2: (3, 31), 3: (3, 31),
    4: (6, 30), 5: (6, 30), 6: (6, 30),
    7: (9, 30), 8: (9, 30), 9: (9, 30),
    10: (12, 31), 11: (12, 31), 12: (12, 31),
}

UNRESOLVABLE_PHRASES = {
    "asap", "as soon as possible", "soon", "urgent",
    "immediately", "at some point", "eventually", "later"
}

CUSTOM_PHRASE_MAP = {
    # Quarter boundaries
    "end of quarter":         lambda d: date(d.year, *QUARTER_ENDS[d.month]),
    "before end of quarter":  lambda d: date(d.year, *QUARTER_ENDS[d.month]),
    "end of q1":              lambda d: date(d.year, 3, 31),
    "end of q2":              lambda d: date(d.year, 6, 30),
    "end of q3":              lambda d: date(d.year, 9, 30),
    "end of q4":              lambda d: date(d.year, 12, 31),

    # Sprint / month
    "end of sprint":          lambda d: d + timedelta(weeks=2),
    "next sprint":            lambda d: d + timedelta(weeks=2),
    "end of month":           lambda d: date(d.year, d.month, _last_day_of_month(d)),
    "end of next month":      lambda d: date(d.year, (d.month % 12) + 1, _last_day_of_month(
                                             date(d.year + (1 if d.month == 12 else 0), (d.month % 12) + 1, 1))),

    # Week boundaries
    "end of week":            lambda d: _next_weekday(d, 4),   # Friday
    "end of this week":       lambda d: _next_weekday(d, 4),
    "by end of week":         lambda d: _next_weekday(d, 4),
    "eow":                    lambda d: _next_weekday(d, 4),
    "this friday":            lambda d: _next_weekday(d, 4),
    "by friday":              lambda d: _next_weekday(d, 4),
    "next friday":            lambda d: _next_weekday(d, 4),
    "by next friday":         lambda d: _next_weekday(d, 4),

    # Next week
    "beginning of next week": lambda d: _next_weekday(d, 0),  # Monday
    "start of next week":     lambda d: _next_weekday(d, 0),
    "next monday":            lambda d: _next_weekday(d, 0),
    "end of next week":       lambda d: _next_weekday(d, 4) + timedelta(weeks=1),
    "by end of next week":    lambda d: _next_weekday(d, 4) + timedelta(weeks=1),

    # Same day
    "eod":                    lambda d: d,
    "today":                  lambda d: d,
    "by eod":                 lambda d: d,
    "by end of day":          lambda d: d,

    # Tomorrow
    "tomorrow":               lambda d: d + timedelta(days=1),

    # Relative weeks
    "in two weeks":           lambda d: d + timedelta(weeks=2),
    "in a week":              lambda d: d + timedelta(weeks=1),
    "next week":              lambda d: d + timedelta(weeks=1),
}


def _last_day_of_month(d: date) -> int:
    import calendar
    return calendar.monthrange(d.year, d.month)[1]


def _next_weekday(d: date, weekday: int) -> date:
    """Returns the next occurrence of weekday (0=Mon, 6=Sun) after d."""
    days_ahead = weekday - d.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    return d + timedelta(days=days_ahead)


def _is_exact_date(raw: str) -> Optional[date]:
    """
    Fast check for unambiguous exact date strings only.
    Examples: "2026-08-14", "August 14", "14th August", "Aug 14 2026"
    Does NOT handle relative phrases — those go to LLM.
    """
    import re
    # Already ISO format
    iso_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", raw.strip())
    if iso_match:
        try:
            return date(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3)))
        except ValueError:
            pass

    # Try dateparser strictly for absolute date patterns only
    # (disable relative date interpretation)
    parsed = dateparser.parse(raw, settings={
        "PREFER_DATES_FROM":    "future",
        "PARSERS":              ["absolute-time"],   # only absolute dates
        "RETURN_AS_TIMEZONE_AWARE": False,
    })
    return parsed.date() if parsed else None


def resolve_date(
    raw_due_date: Optional[str],
    meeting_date_str: str,
    transcript_excerpt: str = "",
    client: Optional[Groq] = None,
) -> tuple[Optional[date], str, ResolutionStatus]:
    """
    Date resolution — LLM is the PRIMARY resolver for all ambiguous phrases.

    Flow:
      1. Immediately fail on clearly unresolvable phrases (ASAP, soon, later)
      2. Fast-path: exact/ISO dates handled without any API call
      3. Custom phrase map: zero-cost resolution for dead-obvious phrases
      4. LLM (PRIMARY for everything ambiguous): Groq reasons with full context
      5. Human review: LLM also failed or no client available

    Returns (resolved_date | None, method, ResolutionStatus)
    """
    if not raw_due_date:
        return None, "none", ResolutionStatus.FAILED

    raw_lower = raw_due_date.strip().lower()

    # ── Step 1: Immediately unresolvable ─────────────────────────────
    if any(phrase == raw_lower or phrase in raw_lower
           for phrase in UNRESOLVABLE_PHRASES):
        return None, "unresolvable_phrase", ResolutionStatus.FAILED

    try:
        meeting_date = datetime.strptime(meeting_date_str, "%Y-%m-%d").date()
    except ValueError:
        meeting_date = date.today()

    # ── Step 2: Exact/ISO date (no LLM needed) ────────────────────────
    exact = _is_exact_date(raw_due_date)
    if exact and exact >= meeting_date:
        return exact, "exact_date", ResolutionStatus.CLEAN

    # ── Step 3: Custom phrase map (zero-cost, obvious phrases) ────────
    for phrase, resolver in CUSTOM_PHRASE_MAP.items():
        if phrase in raw_lower:
            try:
                resolved = resolver(meeting_date)
                return resolved, "custom_rule", ResolutionStatus.CLEAN
            except Exception:
                continue

    # ── Step 4: LLM (PRIMARY for all ambiguous dates) ─────────────────
    if client:
        resolved_str = _resolve_date_llm(
            raw_due_date     = raw_due_date,
            meeting_date_str = meeting_date_str,
            transcript_excerpt = transcript_excerpt,
            client           = client,
        )
        if resolved_str:
            try:
                resolved = datetime.strptime(resolved_str, "%Y-%m-%d").date()
                if resolved >= meeting_date:
                    # LLM is primary resolver — CLEAN if high confidence, FLAGGED if low
                    return resolved, "llm", ResolutionStatus.CLEAN
                else:
                    return resolved, "llm_past", ResolutionStatus.FLAGGED
            except ValueError:
                pass

    # Layer 3: Human must set it
    return None, "unresolved", ResolutionStatus.FAILED


def _resolve_date_llm(
    raw_due_date: str,
    meeting_date_str: str,
    transcript_excerpt: str,
    client: Groq,
) -> Optional[str]:
    """
    Primary date resolver — asks Groq to interpret ambiguous date language
    with full meeting context.

    Returns ISO date string "YYYY-MM-DD" or None if it cannot determine.
    """
    try:
        meeting_date = datetime.strptime(meeting_date_str, "%Y-%m-%d").date()
        day_of_week  = meeting_date.strftime("%A")  # e.g. "Wednesday"
    except ValueError:
        meeting_date = date.today()
        day_of_week  = meeting_date.strftime("%A")

    prompt = f"""You are resolving a due date from a meeting transcript.

MEETING DATE: {meeting_date_str} ({day_of_week})

RAW DUE DATE PHRASE: "{raw_due_date}"

TRANSCRIPT CONTEXT:
"{transcript_excerpt}"

INSTRUCTIONS:
- Interpret the due date phrase relative to the meeting date above
- "next Friday" means the Friday AFTER the meeting date
- "end of week" means the Friday of the same week as the meeting
- "end of quarter" = last day of the current calendar quarter
- "by EOD" = the meeting date itself
- If the phrase is truly ambiguous or cannot be determined, return null
- Do NOT return dates in the past relative to the meeting date
- Return ONLY a JSON object, no explanation

Return format:
{{"date": "YYYY-MM-DD", "confidence": "high|medium|low", "reasoning": "one sentence"}}

If you cannot determine: {{"date": null, "confidence": "low", "reasoning": "..."}}"""

    try:
        response = client.chat.completions.create(
            model   = MODEL,
            messages = [{"role": "user", "content": prompt}],
            response_format = {"type": "json_object"},
            temperature = 0.0,
            max_tokens  = 120,
        )
        result = json.loads(response.choices[0].message.content)
        return result.get("date")   # Returns "YYYY-MM-DD" or null
    except Exception:
        return None


# ─────────────────────────────────────────────
# Dedup key
# ─────────────────────────────────────────────

def compute_dedup_key(meeting_id: str, title: str, owner_email: str) -> str:
    content = f"{meeting_id}|{title.lower().strip()}|{owner_email.lower().strip()}"
    return hashlib.sha256(content.encode()).hexdigest()


# ─────────────────────────────────────────────
# Full resolution pipeline for one action item
# ─────────────────────────────────────────────

def resolve_action_item(
    raw: ActionItemRaw,
    roster: list[dict],
    meeting_date_str: str,
    meeting_id: str,
    transcript_text: str,
    client: Optional[Groq] = None,
) -> ActionItem:
    """Resolve owner and date for a single action item."""

    # --- Owner resolution ---
    resolved_owner, owner_status = resolve_owner(
        raw_owner=raw.raw_owner,
        roster=roster,
        transcript_excerpt=raw.evidence_quote or transcript_text[:500],
        client=client,
    )

    # --- Date resolution ---
    resolved_date, date_method, date_status = resolve_date(
        raw_due_date=raw.raw_due_date,
        meeting_date_str=meeting_date_str,
        transcript_excerpt=raw.evidence_quote or "",
        client=client,
    )

    # --- Compute dedup key ---
    owner_email = resolved_owner.email if resolved_owner else "unknown"
    dedup_key   = compute_dedup_key(meeting_id, raw.title, owner_email)

    return ActionItem(
        id                 = raw.id,
        title              = raw.title,
        description        = raw.description,
        raw_owner          = raw.raw_owner,
        resolved_owner     = resolved_owner,
        owner_status       = owner_status,
        raw_due_date       = raw.raw_due_date,
        resolved_date      = resolved_date,
        date_method        = date_method,
        date_status        = date_status,
        priority           = raw.priority,
        confidence         = raw.confidence,
        evidence_quote     = raw.evidence_quote,
        evidence_timestamp = raw.evidence_timestamp,
        status             = ItemStatus.PENDING,
        dedup_key          = dedup_key,
    )


# ─────────────────────────────────────────────
# LangGraph node
# ─────────────────────────────────────────────

def resolve_node(state: dict) -> dict:
    """
    LangGraph node: resolve.
    Reads meeting_record_raw from state.
    Writes action_items and has_flags to state.
    """
    raw_record_dict = state.get("meeting_record_raw")
    if not raw_record_dict:
        return {**state, "errors": state.get("errors", []) + ["No extraction record to resolve"]}

    roster       = state.get("participant_roster", [])
    meeting_date = state.get("meeting_date", date.today().strftime("%Y-%m-%d"))
    meeting_id   = state.get("_meeting_id", "unknown")
    utterances   = state.get("utterances", [])
    transcript_text = " ".join(u.get("text", "") for u in utterances)

    # Init Groq client for LLM fallback
    api_key = os.getenv("GROQ_API_KEY")
    client  = Groq(api_key=api_key) if api_key else None

    raw_record = MeetingRecordRaw(**raw_record_dict)

    resolved_items = []
    has_flags = False

    for raw_item in raw_record.action_items:
        resolved = resolve_action_item(
            raw=raw_item,
            roster=roster,
            meeting_date_str=meeting_date,
            meeting_id=meeting_id,
            transcript_text=transcript_text,
            client=client,
        )
        resolved_items.append(resolved.model_dump())

        # Flag if owner or date couldn't be resolved
        if (resolved.owner_status in [ResolutionStatus.FAILED, ResolutionStatus.FLAGGED] or
                resolved.date_status == ResolutionStatus.FAILED):
            has_flags = True

    return {
        **state,
        "action_items": resolved_items,
        "has_flags":    has_flags,
    }


def add_warnings_node(state: dict) -> dict:
    """
    LangGraph node: add_warnings.
    Adds human-readable warnings for flagged items before human review.
    """
    items    = state.get("action_items", [])
    warnings = list(state.get("warnings", []))

    for item in items:
        name = item.get("title", "?")
        if item.get("owner_status") == ResolutionStatus.FAILED.value:
            warnings.append(f"⚠️  '{name}': owner '{item.get('raw_owner')}' could not be resolved — please assign manually")
        elif item.get("owner_status") == ResolutionStatus.FLAGGED.value:
            owner = item.get("resolved_owner", {})
            warnings.append(f"❓ '{name}': owner resolved to '{owner.get('name','')}' via LLM — please confirm")

        if item.get("date_status") == ResolutionStatus.FAILED.value:
            raw_date = item.get("raw_due_date", "none")
            warnings.append(f"⚠️  '{name}': due date '{raw_date}' could not be resolved — please set manually")

    return {**state, "warnings": warnings}
