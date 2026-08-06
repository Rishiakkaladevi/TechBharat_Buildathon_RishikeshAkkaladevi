"""
Extraction node — sends the clean transcript to Groq (llama-3.3-70b)
and returns a fully structured meeting record in one LLM call.

Handles:
  - JSON mode for reliable structured output
  - Chunking for transcripts > 28k tokens
  - Pydantic validation with one retry on malformed output
"""

from __future__ import annotations

import os
import json
import hashlib
from datetime import datetime

from groq import Groq
import tiktoken

from core.models import (
    MeetingRecordRaw, ActionItemRaw, Decision,
    Priority, Utterance
)


# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────

MODEL          = "llama-3.3-70b-versatile"
MAX_TOKENS     = 28_000      # leave headroom below Groq's 32k limit
CHUNK_OVERLAP  = 1_500       # token overlap between chunks

TOKENIZER = tiktoken.get_encoding("cl100k_base")

def _count_tokens(text: str) -> int:
    return len(TOKENIZER.encode(text))


# ─────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert meeting analyst. Your job is to extract a structured record from a meeting transcript.

You must return ONLY valid JSON — no explanation, no markdown, no preamble.

Be conservative with action items: only extract genuine, specific commitments made by named individuals.
Vague statements like "someone should look into this" are NOT action items.

For each action item, assign a confidence score:
  0.9+ = Strong explicit commitment ("I will do X by Friday")
  0.7–0.9 = Clear commitment with minor ambiguity
  0.5–0.7 = Possible commitment, not fully confirmed
  < 0.5 = Speculation — do not include

Only include action items with confidence >= 0.5."""

def _build_extraction_prompt(
    transcript: str,
    meeting_date: str,
    participants: list[str],
) -> str:
    participants_str = ", ".join(participants) if participants else "Unknown"
    return f"""MEETING DATE: {meeting_date}
KNOWN PARTICIPANTS: {participants_str}

TRANSCRIPT:
{transcript}

Return a JSON object with exactly this structure:
{{
  "summary": "3-5 sentence executive summary",
  "decisions": [
    {{
      "decision": "what was decided",
      "context": "why or how it was decided",
      "timestamp": "HH:MM:SS or null"
    }}
  ],
  "open_questions": ["question 1", "question 2"],
  "risks": ["risk or blocker 1", "risk or blocker 2"],
  "action_items": [
    {{
      "id": "ai_001",
      "title": "concise action title",
      "description": "more detail if available",
      "raw_owner": "exactly as named in transcript",
      "raw_due_date": "exactly as stated, or null if not mentioned",
      "priority": "high or medium or low",
      "confidence": 0.95,
      "evidence_quote": "exact words from transcript",
      "evidence_timestamp": "HH:MM:SS or null"
    }}
  ]
}}"""


# ─────────────────────────────────────────────
# Transcript formatting
# ─────────────────────────────────────────────

def _utterances_to_text(utterances: list[dict]) -> str:
    """Convert utterance dicts to readable transcript string for the prompt."""
    lines = []
    for u in utterances:
        ts      = u.get("timestamp", "")
        speaker = u.get("speaker", "UNKNOWN")
        text    = u.get("text", "")
        lines.append(f"[{ts}] {speaker}: {text}")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────

def _chunk_utterances(utterances: list[dict], max_tokens: int, overlap_tokens: int) -> list[list[dict]]:
    """Split utterances into chunks that fit within max_tokens."""
    chunks   = []
    current  = []
    cur_toks = 0

    for u in utterances:
        u_text = f"[{u.get('timestamp','')}] {u.get('speaker','')}: {u.get('text','')}"
        u_toks = _count_tokens(u_text)

        if cur_toks + u_toks > max_tokens and current:
            chunks.append(current)
            # Keep overlap: last N tokens worth of utterances
            overlap  = []
            ov_toks  = 0
            for prev_u in reversed(current):
                prev_text = f"[{prev_u.get('timestamp','')}] {prev_u.get('speaker','')}: {prev_u.get('text','')}"
                prev_toks = _count_tokens(prev_text)
                if ov_toks + prev_toks > overlap_tokens:
                    break
                overlap.insert(0, prev_u)
                ov_toks += prev_toks
            current  = overlap + [u]
            cur_toks = ov_toks + u_toks
        else:
            current.append(u)
            cur_toks += u_toks

    if current:
        chunks.append(current)

    return chunks


# ─────────────────────────────────────────────
# Groq API call
# ─────────────────────────────────────────────

def _call_groq(prompt: str, client: Groq) -> dict:
    """Single Groq call with JSON mode. Returns parsed dict."""
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,       # low temp for deterministic structured output
        max_tokens=4096,
    )
    return json.loads(response.choices[0].message.content)


# ─────────────────────────────────────────────
# Merge results from multiple chunks
# ─────────────────────────────────────────────

def _merge_chunk_results(chunk_results: list[dict]) -> dict:
    """
    Merge extraction results from multiple transcript chunks.
    Deduplicates action items by title similarity.
    """
    from rapidfuzz import fuzz

    merged_decisions      = []
    merged_open_questions = []
    merged_risks          = []
    merged_action_items   = []
    seen_titles           = []

    for result in chunk_results:
        merged_decisions      += result.get("decisions", [])
        merged_open_questions += result.get("open_questions", [])
        merged_risks          += result.get("risks", [])

        for item in result.get("action_items", []):
            title = item.get("title", "")
            # Check for duplicate using fuzzy match
            is_dup = any(
                fuzz.ratio(title.lower(), seen.lower()) > 85
                for seen in seen_titles
            )
            if not is_dup:
                merged_action_items.append(item)
                seen_titles.append(title)

    # Re-index action item IDs
    for i, item in enumerate(merged_action_items):
        item["id"] = f"ai_{i+1:03d}"

    # Use summary from first chunk (consolidation pass would be ideal but adds latency)
    summary = chunk_results[0].get("summary", "") if chunk_results else ""

    return {
        "summary":        summary,
        "decisions":      merged_decisions,
        "open_questions": list(set(merged_open_questions)),
        "risks":          list(set(merged_risks)),
        "action_items":   merged_action_items,
    }


# ─────────────────────────────────────────────
# Pydantic validation + normalisation
# ─────────────────────────────────────────────

def _validate_and_build(raw: dict, meeting_id: str, meeting_date: str) -> MeetingRecordRaw:
    """Parse LLM output dict into a validated MeetingRecordRaw."""

    decisions = [
        Decision(
            decision  = d.get("decision", ""),
            context   = d.get("context"),
            timestamp = d.get("timestamp"),
        )
        for d in raw.get("decisions", [])
        if d.get("decision")
    ]

    action_items = []
    for i, a in enumerate(raw.get("action_items", [])):
        try:
            priority_str = a.get("priority", "medium").lower()
            priority = Priority(priority_str) if priority_str in Priority._value2member_map_ else Priority.MEDIUM

            item = ActionItemRaw(
                id                 = a.get("id", f"ai_{i+1:03d}"),
                title              = a.get("title", "Untitled action"),
                description        = a.get("description"),
                raw_owner          = a.get("raw_owner", "UNKNOWN"),
                raw_due_date       = a.get("raw_due_date"),
                priority           = priority,
                confidence         = float(a.get("confidence", 0.7)),
                evidence_quote     = a.get("evidence_quote"),
                evidence_timestamp = a.get("evidence_timestamp"),
            )
            action_items.append(item)
        except Exception:
            continue  # skip malformed items, don't crash

    return MeetingRecordRaw(
        meeting_id     = meeting_id,
        meeting_date   = meeting_date,
        summary        = raw.get("summary", ""),
        decisions      = decisions,
        open_questions = raw.get("open_questions", []),
        risks          = raw.get("risks", []),
        action_items   = action_items,
    )


# ─────────────────────────────────────────────
# Main extraction function
# ─────────────────────────────────────────────

def extract(
    utterances: list[dict],
    meeting_date: str,
    participant_roster: list[dict],
    meeting_id: str,
) -> MeetingRecordRaw:
    """
    Full extraction pipeline:
    1. Check token count
    2. Single call if fits, chunked if not
    3. Merge if chunked
    4. Validate with Pydantic
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set in environment")

    client = Groq(api_key=api_key)

    participants = [p.get("name", "") for p in participant_roster]
    transcript   = _utterances_to_text(utterances)
    token_count  = _count_tokens(transcript)

    if token_count <= MAX_TOKENS:
        # Single call — fast path
        prompt = _build_extraction_prompt(transcript, meeting_date, participants)
        try:
            raw = _call_groq(prompt, client)
        except json.JSONDecodeError:
            # Retry once with explicit reminder
            raw = _call_groq(prompt + "\n\nIMPORTANT: Return ONLY valid JSON.", client)

    else:
        # Chunked path — split, extract, merge
        chunks = _chunk_utterances(utterances, MAX_TOKENS, CHUNK_OVERLAP)
        chunk_results = []

        for chunk in chunks:
            chunk_transcript = _utterances_to_text(chunk)
            prompt = _build_extraction_prompt(chunk_transcript, meeting_date, participants)
            try:
                result = _call_groq(prompt, client)
                chunk_results.append(result)
            except Exception:
                continue  # skip failed chunks

        raw = _merge_chunk_results(chunk_results)

    return _validate_and_build(raw, meeting_id, meeting_date)


# ─────────────────────────────────────────────
# LangGraph node
# ─────────────────────────────────────────────

def extract_node(state: dict) -> dict:
    """
    LangGraph node: extract.
    Reads utterances from state.
    Writes meeting_record_raw to state.
    """
    utterances = state.get("utterances", [])
    if not utterances:
        return {**state, "errors": state.get("errors", []) + ["No utterances to extract from"]}

    meeting_date    = state.get("meeting_date", datetime.today().strftime("%Y-%m-%d"))
    participant_roster = state.get("participant_roster", [])
    meeting_id      = state.get("_meeting_id", hashlib.sha256(
        "".join(u.get("text","") for u in utterances).encode()
    ).hexdigest()[:16])

    try:
        record = extract(utterances, meeting_date, participant_roster, meeting_id)
        return {
            **state,
            "meeting_record_raw": record.model_dump(),
            "_meeting_id":        meeting_id,
        }
    except Exception as e:
        return {**state, "errors": state.get("errors", []) + [f"Extraction error: {str(e)}"]}
