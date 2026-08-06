"""
app.py — MeetingMind Streamlit Application

Three pages:
  1. Upload & Configure — drop transcript, set meeting date, enter roster
  2. Review & Approve   — review structured record, edit/approve/reject action items
  3. Audit Log          — view all past agent actions

Integrates with LangGraph via graph.py.
LangGraph pauses at the human_review node; Streamlit renders state and resumes it on Confirm.
"""

import os
import io
import json
import uuid
import tempfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from langgraph.types import Command

load_dotenv()

from graph import get_graph
from storage.audit_log import get_all_entries, init_db

# ─────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────

st.set_page_config(
    page_title="MeetingMind — Agentic Meeting Assistant",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────
# Styling
# ─────────────────────────────────────────────

st.markdown("""
<style>
    /* Main background */
    .stApp { background-color: #0f1117; color: #e0e0e0; }

    /* Card-like containers */
    .metric-card {
        background: #1e2130;
        border-radius: 10px;
        padding: 16px;
        border: 1px solid #2d3250;
        margin-bottom: 12px;
    }

    /* Status badges */
    .badge-approved { background:#1a3a2a; color:#4caf50; border-radius:4px; padding:2px 8px; font-size:0.8em; }
    .badge-rejected { background:#3a1a1a; color:#f44336; border-radius:4px; padding:2px 8px; font-size:0.8em; }
    .badge-flagged  { background:#3a2e1a; color:#ff9800; border-radius:4px; padding:2px 8px; font-size:0.8em; }
    .badge-pending  { background:#1a2a3a; color:#2196f3; border-radius:4px; padding:2px 8px; font-size:0.8em; }

    /* Confidence bar */
    .conf-high   { color: #4caf50; }
    .conf-medium { color: #ff9800; }
    .conf-low    { color: #f44336; }

    /* Section headers */
    .section-header {
        font-size: 1.1em;
        font-weight: 600;
        color: #7c9cfc;
        border-bottom: 1px solid #2d3250;
        padding-bottom: 4px;
        margin-top: 16px;
        margin-bottom: 10px;
    }

    /* Hide streamlit branding */
    #MainMenu, footer { visibility: hidden; }

    /* Sidebar */
    .css-1d391kg { background-color: #161b27; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────
# Session state helpers
# ─────────────────────────────────────────────

def _init_session():
    defaults = {
        "page":             "upload",
        "thread_id":        None,
        "graph_state":      None,     # raw state dict from graph
        "action_items":     [],
        "meeting_record":   None,
        "warnings":         [],
        "result":           None,
        "processing":       False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_session()


# ─────────────────────────────────────────────
# Sidebar navigation
# ─────────────────────────────────────────────

with st.sidebar:
    st.image("https://img.icons8.com/fluency/96/robot-2.png", width=60)
    st.markdown("## 🤖 MeetingMind")
    st.markdown("*Agentic AI Meeting Assistant*")
    st.divider()

    page = st.radio(
        "Navigate",
        options=["upload", "review", "audit"],
        format_func=lambda x: {
            "upload": "📁 Upload & Configure",
            "review": "✅ Review & Approve",
            "audit":  "📋 Audit Log",
        }[x],
        key="sidebar_page",
        index=["upload", "review", "audit"].index(st.session_state.page),
    )
    st.session_state.page = page
    st.divider()

    # Status indicators
    st.markdown("**Environment**")
    st.markdown("🟢 Groq API" if os.getenv("GROQ_API_KEY") else "🔴 Groq API (not set)")
    st.markdown("🟢 Deepgram"  if os.getenv("DEEPGRAM_API_KEY") else "🟡 Deepgram (text only)")
    st.markdown("🟢 GitHub"    if os.getenv("GITHUB_TOKEN") else "🔴 GitHub (not set)")
    st.markdown("🟢 Slack"     if os.getenv("SLACK_WEBHOOK_URL") else "⚫ Slack (optional)")


# ─────────────────────────────────────────────
# PAGE 1: Upload & Configure
# ─────────────────────────────────────────────

def page_upload():
    st.markdown("# 📁 Upload Meeting Transcript")
    st.markdown("Drop your transcript or audio file, configure participants, and process.")

    col1, col2 = st.columns([2, 1])

    with col1:
        st.markdown('<div class="section-header">Transcript File</div>', unsafe_allow_html=True)
        uploaded_file = st.file_uploader(
            "Upload transcript or audio",
            type=["txt", "vtt", "srt", "mp3", "mp4", "wav", "m4a"],
            help="Text formats (.txt, .vtt, .srt) are processed instantly. Audio files use Deepgram.",
            key="transcript_upload",
        )

        if uploaded_file:
            ext = Path(uploaded_file.name).suffix.lower()
            is_audio = ext in {".mp3", ".mp4", ".wav", ".m4a"}

            if is_audio:
                st.info(f"🎙️ Audio file detected ({uploaded_file.name}) — Deepgram will transcribe and diarize.")
            else:
                preview = uploaded_file.read(2000).decode("utf-8", errors="replace")
                uploaded_file.seek(0)
                st.markdown('<div class="section-header">Preview (first 2000 chars)</div>', unsafe_allow_html=True)
                st.code(preview, language=None)

        st.markdown('<div class="section-header">Meeting Date</div>', unsafe_allow_html=True)
        meeting_date = st.date_input(
            "Meeting date",
            value=date.today(),
            help="Used to resolve relative dates like 'by next Friday'",
        )

    with col2:
        st.markdown('<div class="section-header">Participant Roster</div>', unsafe_allow_html=True)
        st.markdown("*Used for owner resolution. Format: `name, email, github_username (optional)`*")

        roster_text = st.text_area(
            "Participants (one per line)",
            placeholder="Alice Chen, alice@company.com, alice-gh\nBob Kumar, bob@company.com\nPriya Sharma, priya@company.com, priya-sh",
            height=200,
            key="roster_input",
        )

        st.markdown('<div class="section-header">Reviewer Name</div>', unsafe_allow_html=True)
        reviewer = st.text_input(
            "Your name (for audit log)",
            value="demo_user",
            key="reviewer_name",
        )

    st.divider()

    # Process button
    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        process_btn = st.button(
            "⚡ Process Meeting",
            type="primary",
            disabled=uploaded_file is None,
            use_container_width=True,
        )

    if process_btn and uploaded_file:
        # Parse roster
        roster = _parse_roster(roster_text)

        # Save uploaded file to temp location
        suffix = Path(uploaded_file.name).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(uploaded_file.read())
            tmp_path = tmp.name

        # Build initial state
        initial_state = {
            "transcript_path":    tmp_path,
            "transcript_raw":     "",
            "meeting_date":       meeting_date.strftime("%Y-%m-%d"),
            "participant_roster": roster,
            "approved_by":        reviewer,
            "utterances":         [],
            "action_items":       [],
            "errors":             [],
            "warnings":           [],
        }

        thread_id = str(uuid.uuid4())
        st.session_state.thread_id = thread_id

        config = {"configurable": {"thread_id": thread_id}}
        graph  = get_graph()

        # Run graph until interrupt
        progress_bar = st.progress(0, text="Starting pipeline...")
        status_text  = st.empty()

        try:
            node_labels = {
                "ingest":       ("Ingesting transcript...", 25),
                "extract":      ("Extracting with Groq LLM...", 55),
                "resolve":      ("Resolving owners and dates...", 80),
                "add_warnings": ("Flagging ambiguous items...", 90),
                "human_review": ("Ready for your review!", 100),
            }

            final_state = None
            for event in graph.stream(initial_state, config=config, stream_mode="values"):
                final_state = event
                # Try to update progress from last completed node
                # (streaming gives us state snapshots)

            # Graph is now interrupted at human_review
            if final_state:
                st.session_state.graph_state    = final_state
                st.session_state.action_items   = final_state.get("action_items", [])
                st.session_state.meeting_record  = final_state.get("meeting_record_raw", {})
                st.session_state.warnings        = final_state.get("warnings", [])
                st.session_state.page            = "review"

                progress_bar.progress(100, text="✅ Processing complete!")
                st.success(f"✅ Found {len(st.session_state.action_items)} action items. Moving to review...")
                st.rerun()

        except Exception as e:
            st.error(f"❌ Pipeline error: {str(e)}")
            st.exception(e)


def _parse_roster(roster_text: str) -> list[dict]:
    """Parse roster text area into list of participant dicts."""
    roster = []
    for line in roster_text.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            p = {
                "name":  parts[0],
                "email": parts[1],
                "github_username": parts[2] if len(parts) > 2 else None,
                "aliases": [parts[0].split()[0]],  # first name as alias
            }
            roster.append(p)
    return roster


# ─────────────────────────────────────────────
# PAGE 2: Review & Approve
# ─────────────────────────────────────────────

def page_review():
    action_items    = st.session_state.get("action_items", [])
    meeting_record  = st.session_state.get("meeting_record", {})
    warnings        = st.session_state.get("warnings", [])

    if not action_items and not meeting_record:
        st.warning("No meeting processed yet. Go to **Upload & Configure** first.")
        return

    st.markdown("# ✅ Review & Approve Action Items")

    # Warnings banner
    if warnings:
        with st.expander(f"⚠️ {len(warnings)} items need attention", expanded=True):
            for w in warnings:
                st.warning(w)

    # ── Meeting record (left) + Action items (right) ──
    left_col, right_col = st.columns([1, 2])

    with left_col:
        _render_meeting_record(meeting_record)

    with right_col:
        _render_action_items_editor(action_items)

    # Sidebar preview + confirm button
    with st.sidebar:
        st.divider()
        approved_count = sum(1 for i in st.session_state.get("edited_items", action_items)
                             if i.get("status") == "approved")
        rejected_count = sum(1 for i in st.session_state.get("edited_items", action_items)
                             if i.get("status") == "rejected")

        st.markdown(f"""
        **What will happen:**
        - 🟢 Create **{approved_count}** GitHub Issues
        - 🔴 Reject **{rejected_count}** items
        - 📢 Post Slack recap: {'Yes' if os.getenv('SLACK_WEBHOOK_URL') else 'No'}
        """)
        st.divider()

        confirm_btn = st.button(
            "🚀 Confirm & Execute",
            type="primary",
            use_container_width=True,
            disabled=approved_count == 0,
        )

        if confirm_btn:
            _execute_approved()


def _render_meeting_record(record: dict):
    if not record:
        return

    st.markdown('<div class="section-header">📋 Summary</div>', unsafe_allow_html=True)
    st.markdown(record.get("summary", "_No summary_"))

    decisions = record.get("decisions", [])
    if decisions:
        st.markdown(f'<div class="section-header">✅ Decisions ({len(decisions)})</div>', unsafe_allow_html=True)
        for d in decisions:
            ts = f" `{d.get('timestamp')}`" if d.get("timestamp") else ""
            st.markdown(f"• {d.get('decision', '')}{ts}")

    questions = record.get("open_questions", [])
    if questions:
        st.markdown(f'<div class="section-header">❓ Open Questions ({len(questions)})</div>', unsafe_allow_html=True)
        for q in questions:
            st.markdown(f"• {q}")

    risks = record.get("risks", [])
    if risks:
        st.markdown(f'<div class="section-header">⚠️ Risks ({len(risks)})</div>', unsafe_allow_html=True)
        for r in risks:
            st.markdown(f"• {r}")


def _render_action_items_editor(action_items: list[dict]):
    st.markdown(f'<div class="section-header">🎯 Action Items ({len(action_items)})</div>',
                unsafe_allow_html=True)

    if not action_items:
        st.info("No action items extracted.")
        return

    # Get roster for owner dropdown
    roster  = st.session_state.get("graph_state", {}).get("participant_roster", [])
    owners  = [p.get("name", "") for p in roster] if roster else []

    edited_items = []
    for i, item in enumerate(action_items):
        with st.container():
            # Confidence color
            conf = item.get("confidence", 0)
            conf_color = "conf-high" if conf >= 0.85 else "conf-medium" if conf >= 0.6 else "conf-low"

            # Header row
            h_col1, h_col2, h_col3 = st.columns([4, 1, 1])
            with h_col1:
                st.markdown(f"**{item.get('title', 'Untitled')}**")
            with h_col2:
                st.markdown(f'<span class="{conf_color}">{int(conf*100)}% confident</span>',
                            unsafe_allow_html=True)
            with h_col3:
                status = st.selectbox(
                    "Status",
                    options=["pending", "approved", "rejected"],
                    index=["pending", "approved", "rejected"].index(item.get("status", "pending")),
                    key=f"status_{i}",
                    label_visibility="collapsed",
                )

            # Edit fields
            e_col1, e_col2, e_col3 = st.columns([2, 2, 1])

            with e_col1:
                if owners:
                    resolved_name = (item.get("resolved_owner") or {}).get("name", item.get("raw_owner", ""))
                    owner_idx = owners.index(resolved_name) if resolved_name in owners else 0
                    selected_owner = st.selectbox(
                        "Owner",
                        options=owners,
                        index=owner_idx,
                        key=f"owner_{i}",
                    )
                else:
                    selected_owner = st.text_input(
                        "Owner",
                        value=item.get("raw_owner", ""),
                        key=f"owner_{i}",
                    )

            with e_col2:
                raw_date = item.get("resolved_date")
                default_date = date.today()
                if raw_date:
                    try:
                        default_date = date.fromisoformat(str(raw_date))
                    except Exception:
                        pass

                if item.get("date_status") == "failed":
                    st.warning(f"⚠️ Due date unresolved: '{item.get('raw_due_date', 'none')}'")
                    selected_date = st.date_input("Set due date", value=default_date, key=f"date_{i}")
                else:
                    selected_date = st.date_input(
                        f"Due date (from: '{item.get('raw_due_date', '')}' )",
                        value=default_date,
                        key=f"date_{i}",
                    )

            with e_col3:
                priority = st.selectbox(
                    "Priority",
                    options=["high", "medium", "low"],
                    index=["high", "medium", "low"].index(item.get("priority", "medium")),
                    key=f"priority_{i}",
                )

            # Evidence (expandable)
            if item.get("evidence_quote"):
                with st.expander("🗣️ Evidence"):
                    ts = item.get("evidence_timestamp", "")
                    st.markdown(f"> *\"{item['evidence_quote']}\"*")
                    if ts:
                        st.markdown(f"📍 `{ts}`")

            # Build updated item
            updated = dict(item)
            updated["status"]   = status

            # Update resolved_owner name
            if updated.get("resolved_owner"):
                updated["resolved_owner"]["name"] = selected_owner
                # Try to find email from roster
                for p in roster:
                    if p.get("name") == selected_owner:
                        updated["resolved_owner"]["email"] = p.get("email", "")
                        updated["resolved_owner"]["github_username"] = p.get("github_username")
                        break
            else:
                updated["resolved_owner"] = {"name": selected_owner, "email": "", "github_username": None, "match_score": 1.0, "resolution_method": "manual"}

            updated["resolved_date"] = selected_date.isoformat()
            updated["priority"]      = priority
            edited_items.append(updated)

            st.divider()

    st.session_state["edited_items"] = edited_items


def _execute_approved():
    """Resume the LangGraph graph with the human's decisions."""
    edited_items = st.session_state.get("edited_items", [])
    thread_id   = st.session_state.get("thread_id")

    approved = [i for i in edited_items if i.get("status") == "approved"]
    rejected = [i for i in edited_items if i.get("status") == "rejected"]

    config = {"configurable": {"thread_id": thread_id}}
    graph  = get_graph()

    with st.spinner(f"Creating {len(approved)} GitHub Issues..."):
        try:
            # Resume graph from interrupt with human decisions
            final_state = graph.invoke(
                Command(resume={"approved": approved, "rejected": rejected}),
                config=config,
            )
            st.session_state.result = final_state
            created = final_state.get("created_issues", [])
            skipped = final_state.get("skipped_duplicates", [])

            st.success(f"✅ Done! Created {len(created)} issues, skipped {len(skipped)} duplicates.")

            if created:
                st.markdown("**Created Issues:**")
                for issue in created:
                    st.markdown(f"- [{issue.get('title')}]({issue.get('issue_url')})")

        except Exception as e:
            st.error(f"Execution failed: {str(e)}")
            st.exception(e)


# ─────────────────────────────────────────────
# PAGE 3: Audit Log
# ─────────────────────────────────────────────

def page_audit():
    st.markdown("# 📋 Audit Log")
    st.markdown("Every action the agent has taken — immutable, queryable, full context.")

    init_db()

    # Filters
    f_col1, f_col2, f_col3 = st.columns(3)
    with f_col1:
        meeting_filter = st.text_input("Filter by Meeting ID", placeholder="Leave blank for all")
    with f_col2:
        event_filter = st.selectbox(
            "Filter by Event",
            options=["", "github_issue_created", "skipped_duplicate", "item_rejected"],
            format_func=lambda x: x or "All events",
        )
    with f_col3:
        limit = st.number_input("Max rows", min_value=10, max_value=1000, value=100, step=10)

    entries = get_all_entries(
        meeting_id   = meeting_filter or None,
        event_filter = event_filter or None,
        limit        = limit,
    )

    if not entries:
        st.info("No audit entries found. Process a meeting to see the log here.")
        return

    # Summary metrics
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Events", len(entries))
    m2.metric("Issues Created", sum(1 for e in entries if e.get("event") == "github_issue_created"))
    m3.metric("Duplicates Skipped", sum(1 for e in entries if e.get("event") == "skipped_duplicate"))
    m4.metric("Items Rejected", sum(1 for e in entries if e.get("event") == "item_rejected"))

    st.divider()

    # Table
    df = pd.DataFrame(entries)
    display_cols = ["timestamp", "event", "meeting_id", "title", "owner_email", "external_ref", "approved_by"]
    display_cols = [c for c in display_cols if c in df.columns]

    st.dataframe(
        df[display_cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "external_ref": st.column_config.LinkColumn("GitHub Issue"),
            "timestamp":    st.column_config.DatetimeColumn("Time", format="YYYY-MM-DD HH:mm:ss"),
        }
    )

    # Raw JSON download
    st.download_button(
        "⬇️ Download as JSON",
        data=json.dumps(entries, indent=2, default=str),
        file_name="meetingmind_audit_log.json",
        mime="application/json",
    )


# ─────────────────────────────────────────────
# Router
# ─────────────────────────────────────────────

page = st.session_state.page

if page == "upload":
    page_upload()
elif page == "review":
    page_review()
elif page == "audit":
    page_audit()
