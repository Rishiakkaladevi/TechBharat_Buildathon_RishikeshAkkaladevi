"""
cli.py — MeetingMind command-line runner.

Run the full pipeline (ingest → extract → resolve → auto-approve → GitHub push)
without needing Streamlit.

Usage:
    python cli.py <path/to/file> [options]

Examples:
    python cli.py tests/sample_meeting.srt
    python cli.py meeting.mp4 --date 2024-08-06 --reviewer alice --roster "Alice,alice@co.com,alice-gh"
    python cli.py meeting.mp4 --dry-run       # skip GitHub push
    python cli.py meeting.mp4 --extract-only  # stop before human review / GitHub
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


# ─────────────────────────────────────────────
# Coloured terminal output helpers
# ─────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
CYAN   = "\033[36m"
RED    = "\033[31m"
DIM    = "\033[2m"


def _h(text: str) -> str:
    return f"{BOLD}{CYAN}{text}{RESET}"


def _ok(text: str) -> str:
    return f"{GREEN}[OK] {text}{RESET}"


def _warn(text: str) -> str:
    return f"{YELLOW}[!] {text}{RESET}"


def _err(text: str) -> str:
    return f"{RED}[X] {text}{RESET}"


def _info(text: str) -> str:
    return f"{DIM}  {text}{RESET}"


def _progress(msg: str):
    """Live progress callback — wired into ingest_node / Deepgram."""
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"  [{ts}] {msg}", flush=True)


# ─────────────────────────────────────────────
# Argument parsing
# ─────────────────────────────────────────────

def _parse_args():
    p = argparse.ArgumentParser(
        description="MeetingMind CLI — run the full meeting-to-GitHub pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("file", help="Path to transcript/audio/video file")
    p.add_argument("--date",         default=str(date.today()),
                   help="Meeting date (YYYY-MM-DD). Default: today")
    p.add_argument("--reviewer",     default="cli_user",
                   help="Reviewer / approved-by name. Default: cli_user")
    p.add_argument("--roster",       default="",
                   help='Participant roster, semicolon-separated: "Name,email,github;Name2,email2,github2"')
    p.add_argument("--dry-run",      action="store_true",
                   help="Skip GitHub push and Slack recap. Just print what WOULD be created.")
    p.add_argument("--extract-only", action="store_true",
                   help="Stop after extraction. Do not auto-approve or push to GitHub.")
    p.add_argument("--output",       default=None,
                   help="Write final JSON state to this file path.")
    return p.parse_args()


def _parse_roster(roster_str: str) -> list:
    out = []
    for entry in roster_str.split(";"):
        parts = [p.strip() for p in entry.split(",")]
        if len(parts) >= 2:
            out.append({
                "name":            parts[0],
                "email":           parts[1],
                "github_username": parts[2] if len(parts) > 2 else None,
                "aliases":         [parts[0].split()[0]],
            })
    return out


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    args = _parse_args()
    file_path = Path(args.file).resolve()

    if not file_path.exists():
        print(_err(f"File not found: {file_path}"), file=sys.stderr)
        sys.exit(1)

    print()
    print(_h("=" * 60))
    print(_h("  MeetingMind CLI"))
    print(_h("=" * 60))
    print(f"  File     : {file_path}")
    print(f"  Date     : {args.date}")
    print(f"  Reviewer : {args.reviewer}")
    print(f"  Dry-run  : {args.dry_run}")
    print()

    # ── Import graph ──
    try:
        from graph import get_graph
        from langgraph.types import Command
        from core.ingestion import set_status_callback, clear_status_callback
    except ImportError as e:
        print(_err(f"Could not import graph: {e}"))
        print(_info("Make sure you are running from the meeting-assistant directory with venv active."))
        sys.exit(1)

    roster = _parse_roster(args.roster)
    graph  = get_graph()

    # Copy file to a temp location (graph expects a path it can open)
    suffix = file_path.suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_path.read_bytes())
        tmp_path = tmp.name

    thread_id = str(uuid.uuid4())
    config    = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "transcript_path":    tmp_path,
        "transcript_raw":     "",
        "meeting_date":       args.date,
        "participant_roster": roster,
        "approved_by":        args.reviewer,
        "utterances":         [],
        "action_items":       [],
        "errors":             [],
        "warnings":           [],
    }

    # ── Phase 1: Run graph up to human_review interrupt ──
    print(_h("Phase 1 — Ingestion + Extraction + Resolution"))
    print(_info("Streaming graph nodes…"))
    try:
        final_state = None
        node_names  = ["ingest", "extract", "resolve", "add_warnings"]

        set_status_callback(_progress)
        for n, event in enumerate(graph.stream(initial_state, config=config, stream_mode="values")):
            final_state = event
            errs = event.get("errors", [])
            if errs:
                print()
                for e in errs:
                    print(_err(e))
                print()
                print(_warn("Pipeline stopped due to errors."))
                sys.exit(1)

        print()
        print(_ok("Ingestion + extraction complete"))
        clear_status_callback()

    except Exception as e:
        clear_status_callback()
        print(_err(f"Pipeline error: {e}"))
        import traceback; traceback.print_exc()
        sys.exit(1)

    if not final_state:
        print(_err("No state returned from graph."))
        sys.exit(1)

    # ── Print extraction results ──
    action_items = final_state.get("action_items", [])
    mr_raw       = final_state.get("meeting_record_raw") or {}
    if hasattr(mr_raw, "model_dump"):
        mr_raw = mr_raw.model_dump()

    print()
    print(_h("-" * 60))
    print(_h("  Meeting Summary"))
    print(_h("-" * 60))
    summary = mr_raw.get("summary", "(no summary)")
    print(f"\n{summary}\n")

    decisions = mr_raw.get("decisions", [])
    if decisions:
        print(_h("  Key Decisions"))
        for d in decisions:
            text = d.get("decision", str(d)) if isinstance(d, dict) else str(d)
            print(f"  • {text}")
        print()

    risks = mr_raw.get("risks", [])
    if risks:
        print(_h("  Risks & Blockers"))
        for r in risks:
            print(_warn(str(r)))
        print()

    print(_h("-" * 60))
    print(_h(f"  Action Items ({len(action_items)} extracted)"))
    print(_h("-" * 60))
    for i, item in enumerate(action_items, 1):
        status   = item.get("status", "pending")
        priority = item.get("priority", "medium")
        owner    = (item.get("resolved_owner") or {}).get("name") or item.get("raw_owner", "—")
        due      = str(item.get("resolved_date") or item.get("raw_due_date") or "Not set")
        title    = item.get("title", "Untitled")
        conf     = int(item.get("confidence", 0) * 100)
        print(f"\n  {i}. {BOLD}{title}{RESET}")
        print(f"     Priority: {priority} | Owner: {owner} | Due: {due} | Confidence: {conf}%")
        desc = item.get("description", "")
        if desc:
            print(f"     {DIM}{desc[:120]}{'…' if len(desc) > 120 else ''}{RESET}")
    print()

    # ── Extract-only mode — stop here ──
    if args.extract_only:
        print(_ok("--extract-only flag set. Stopping before GitHub push."))
        _dump_output(args, final_state, mr_raw, action_items)
        return

    # ── Phase 2: Auto-approve all items and execute ──
    print(_h("Phase 2 — Auto-approve all items & push to GitHub"))
    print()

    # Mark all items as approved for CLI run
    approved_items = []
    for item in action_items:
        item_copy = dict(item)
        item_copy["status"] = "approved"
        approved_items.append(item_copy)

    if args.dry_run:
        print(_warn("DRY RUN — would create these GitHub issues:"))
        for item in approved_items:
            print(f"  • {item.get('title', 'Untitled')} → {item.get('raw_owner', '—')}")
        print()
        print(_ok("Dry run complete. No issues were created."))
        _dump_output(args, final_state, mr_raw, action_items)
        return

    try:
        print(_info("Resuming graph with approved items…"))
        final2 = graph.invoke(
            Command(resume={"approved": approved_items, "rejected": []}),
            config=config,
        )

        created = final2.get("created_issues", [])
        skipped = final2.get("skipped_duplicates", [])
        errors2 = final2.get("errors", [])

        print()
        print(_ok(f"Done! {len(created)} issue(s) created, {len(skipped)} duplicate(s) skipped"))
        print()

        if created:
            print(_h("  Created Issues"))
            for issue in created:
                url   = issue.get("task_url") or issue.get("issue_url", "#")
                title = issue.get("title", "Issue")
                print(f"  → {title}")
                print(f"    {CYAN}{url}{RESET}")
            print()

        if errors2:
            print(_h("  Errors"))
            for e in errors2:
                print(_warn(str(e)))
            print()

        _dump_output(args, final2, mr_raw, action_items)

    except Exception as e:
        print(_err(f"Execution error: {e}"))
        import traceback; traceback.print_exc()
        sys.exit(1)


def _dump_output(args, state: dict, mr_raw: dict, action_items: list):
    """Write JSON output file if requested."""
    if not args.output:
        return

    # Serialise — strip unpicklable callback
    clean = {k: v for k, v in state.items() if k != "_status_callback"}

    output = {
        "meeting_record": mr_raw,
        "action_items":   action_items,
        "created_issues": clean.get("created_issues", []),
        "errors":         clean.get("errors", []),
        "generated_at":   datetime.utcnow().isoformat(),
    }

    out_path = Path(args.output)
    out_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(_ok(f"Results written to {out_path}"))


if __name__ == "__main__":
    main()
