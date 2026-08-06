"""
dry_run.py — Pipeline dry run test

Tests each stage of the pipeline independently.
Ingestion and resolution run without any API keys.
Extraction requires GROQ_API_KEY.

Usage:
    .\\venv\\Scripts\\activate
    python dry_run.py
"""

import sys
import os
import json
from datetime import date
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Rich output for nice terminal display ──
try:
    from rich.console import Console
    from rich.panel   import Panel
    from rich.table   import Table
    from rich         import print as rprint
    console = Console()
    RICH = True
except ImportError:
    console = None
    RICH = False

def header(text):
    if RICH:
        console.rule(f"[bold cyan]{text}[/bold cyan]")
    else:
        print(f"\n{'='*60}\n{text}\n{'='*60}")

def ok(text):
    print(f"  [OK] {text}")

def warn(text):
    print(f"  [!!] {text}")

def err(text):
    print(f"  [XX] {text}")

def info(text):
    print(f"  [..] {text}")


TRANSCRIPT_PATH = Path(__file__).parent / "tests" / "sample_meeting.srt"
MEETING_DATE    = "2026-08-06"
ROSTER = [
    {"name": "Alice Chen",  "email": "alice@company.com",  "github_username": "alice-gh",  "aliases": ["Alice", "Al"]},
    {"name": "Bob Kumar",   "email": "bob@company.com",    "github_username": "bob-gh",    "aliases": ["Bob", "Bobby"]},
    {"name": "Priya Sharma","email": "priya@company.com",  "github_username": "priya-gh",  "aliases": ["Priya", "PS"]},
    {"name": "Rahul Mehta", "email": "rahul@company.com",  "github_username": "rahul-gh",  "aliases": ["Rahul"]},
]


# ─────────────────────────────────────────────
# Stage 1: Ingestion
# ─────────────────────────────────────────────

def test_ingestion():
    header("Stage 1: Ingestion (no API needed)")

    from core.ingestion import parse_srt, _compute_meeting_id

    if not TRANSCRIPT_PATH.exists():
        err(f"Transcript not found: {TRANSCRIPT_PATH}")
        return None, None

    content    = TRANSCRIPT_PATH.read_text(encoding="utf-8")
    utterances = parse_srt(content)
    meeting_id = _compute_meeting_id(content)

    if not utterances:
        err("Ingestion returned 0 utterances!")
        return None, None

    ok(f"Parsed {len(utterances)} utterances from {TRANSCRIPT_PATH.name}")
    ok(f"Meeting ID: {meeting_id}")
    print()

    for u in utterances[:5]:
        print(f"    [{u.timestamp}] {u.speaker}: {u.text[:70]}...")
    if len(utterances) > 5:
        info(f"  ... and {len(utterances) - 5} more")

    return [u.model_dump() for u in utterances], meeting_id


# ─────────────────────────────────────────────
# Stage 2: Extraction (requires GROQ_API_KEY)
# ─────────────────────────────────────────────

def test_extraction(utterances: list[dict], meeting_id: str):
    header("Stage 2: Extraction (Groq LLM)")

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        warn("GROQ_API_KEY not set — skipping extraction stage")
        warn("Set it in your .env file to test this stage")
        return None

    try:
        from core.extraction import extract
        record = extract(
            utterances         = utterances,
            meeting_date       = MEETING_DATE,
            participant_roster = ROSTER,
            meeting_id         = meeting_id,
        )

        ok(f"Summary: {record.summary[:120]}...")
        ok(f"Decisions found:      {len(record.decisions)}")
        ok(f"Action items found:   {len(record.action_items)}")
        ok(f"Open questions:       {len(record.open_questions)}")
        ok(f"Risks flagged:        {len(record.risks)}")

        print()
        print("  Action Items:")
        for item in record.action_items:
            conf_bar = "++" if item.confidence >= 0.85 else "--" if item.confidence >= 0.6 else "!!"
            print(f"    {conf_bar} [{item.id}] {item.title}")
            print(f"       Owner: '{item.raw_owner}' | Due: '{item.raw_due_date}' | Conf: {item.confidence:.0%}")
            if item.evidence_quote:
                print(f"       Evidence @ {item.evidence_timestamp}: \"{item.evidence_quote[:60]}...\"")
            print()

        return record

    except Exception as e:
        err(f"Extraction failed: {e}")
        import traceback; traceback.print_exc()
        return None


# ─────────────────────────────────────────────
# Stage 3: Resolution (no API needed for fuzzy, Groq for LLM fallback)
# ─────────────────────────────────────────────

def test_resolution(record):
    header("Stage 3: Resolution (owner + date)")

    if record is None:
        warn("No extraction record — using mock action items")
        from core.models import ActionItemRaw, Priority
        mock_items = [
            ActionItemRaw(id="ai_001", title="Write API spec", raw_owner="Alice",
                          raw_due_date="by next Friday", priority=Priority.HIGH,
                          confidence=0.95, evidence_quote="I'll have a draft ready by next Friday"),
            ActionItemRaw(id="ai_002", title="Follow up with Rahul on infra", raw_owner="Priya",
                          raw_due_date="today", priority=Priority.HIGH,
                          confidence=0.90, evidence_quote="I'll ping him right after this call"),
            ActionItemRaw(id="ai_003", title="Send deprecation notice", raw_owner="Priya",
                          raw_due_date="end of this week", priority=Priority.MEDIUM,
                          confidence=0.88, evidence_quote="I'll do that by end of this week"),
            ActionItemRaw(id="ai_004", title="Book security audit", raw_owner="Bob",
                          raw_due_date="before end of quarter", priority=Priority.MEDIUM,
                          confidence=0.82, evidence_quote="I'll reach out to the security team"),
            ActionItemRaw(id="ai_005", title="Confirm legal sign-off for payment flow", raw_owner="someone from legal",
                          raw_due_date="ASAP", priority=Priority.LOW,
                          confidence=0.55, evidence_quote="I'm not sure who owns that"),
        ]
        items = mock_items
        meeting_id = "dryrun_mock_001"
    else:
        items = record.action_items
        meeting_id = record.meeting_id

    from core.resolution import resolve_action_item
    transcript_text = "Alice will handle API spec by next Friday. Priya will follow up with Rahul."

    print()
    for raw in items:
        resolved = resolve_action_item(
            raw            = raw,
            roster         = ROSTER,
            meeting_date_str = MEETING_DATE,
            meeting_id     = meeting_id,
            transcript_text = transcript_text,
            client         = None,   # no LLM fallback in dry run
        )

        owner_icon = "[OK]" if resolved.owner_status.value == "clean" else "[??]" if resolved.owner_status.value == "flagged" else "[XX]"
        date_icon  = "[OK]" if resolved.date_status.value  == "clean" else "[??]" if resolved.date_status.value  == "flagged" else "[XX]"

        owner_str = f"{resolved.resolved_owner.name} <{resolved.resolved_owner.email}>" if resolved.resolved_owner else f"UNRESOLVED ('{raw.raw_owner}')"
        date_str  = str(resolved.resolved_date) if resolved.resolved_date else f"UNRESOLVED ('{raw.raw_due_date}')"

        print(f"  [{raw.id}] {raw.title}")
        print(f"    {owner_icon} Owner: {owner_str}")
        print(f"    {date_icon} Date:  {date_str}")
        print(f"    [KEY] Dedup key: {resolved.dedup_key[:20]}...")
        print()


# ─────────────────────────────────────────────
# Stage 4: Connector status
# ─────────────────────────────────────────────

def test_connectors():
    header("Stage 4: Connector Status")

    from integrations.base import get_connector_status
    status = get_connector_status()

    if not status:
        warn("No connectors found in registry")
        return

    for name, enabled in status.items():
        icon = "[ON] " if enabled else "[OFF]"
        state = "ENABLED" if enabled else "disabled (no API key)"
        print(f"  {icon} {name:20s} {state}")

    print()
    info("Set API keys in .env to enable connectors")


# ─────────────────────────────────────────────
# Run all stages
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print()
    if RICH:
        console.print(Panel.fit(
            "[bold cyan]MeetingMind Dry Run[/bold cyan]\n"
            "[dim]Testing pipeline stages end-to-end[/dim]",
            border_style="cyan"
        ))
    else:
        print("=" * 60)
        print("  MeetingMind — Dry Run Test")
        print("=" * 60)

    print()

    # Run stages
    utterances, meeting_id = test_ingestion()

    record = None
    if utterances:
        record = test_extraction(utterances, meeting_id)

    test_resolution(record)
    test_connectors()

    header("Dry Run Complete")
    ok("Ingestion:   working")
    ok("Resolution:  working")
    if os.getenv("GROQ_API_KEY"):
        ok("Extraction:  working (Groq connected)")
    else:
        warn("Extraction:  skipped (add GROQ_API_KEY to .env)")

    if os.getenv("GITHUB_TOKEN"):
        ok("GitHub:      connected")
    else:
        warn("GitHub:      not connected (add GITHUB_TOKEN to .env)")

    print()
    print("  Run the full app with:")
    print("  .\\venv\\Scripts\\activate && streamlit run app.py")
    print()
