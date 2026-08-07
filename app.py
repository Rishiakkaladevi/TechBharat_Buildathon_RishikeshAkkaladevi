"""
app.py — MeetingMind  |  Single-page Light Mode Glassmorphism
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import time
import uuid
from datetime import date, datetime
from pathlib import Path
import calendar
from collections import defaultdict

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from langgraph.types import Command

load_dotenv()

from graph import get_graph
from storage.audit_log import get_all_entries, init_db
from core.ingestion import set_status_callback, clear_status_callback
from integrations.github_client import get_repo_collaborators

# ── Page config ─────────────────────────────────────────────────
st.set_page_config(
    page_title="MeetingMind",
    page_icon="✨",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Global CSS (Space Grotesk + Warm Parchment Theme) ──────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=Space+Mono:wght@400;700&display=swap');

/* ── Base font ── */
html, body, .stApp, [class*="css"] {
    font-family: 'Space Grotesk', sans-serif !important;
}
code, pre, .stCode, [data-testid="stCode"] * {
    font-family: 'Space Mono', monospace !important;
}

*, *::before, *::after { box-sizing: border-box; }

/* ── App background ── */
.stApp {
    background-color: #f0ece3 !important;
    color: #1a1008 !important;
    min-height: 100vh;
}

/* Hide Streamlit chrome */
#MainMenu, footer, header { visibility: hidden !important; }
[data-testid="collapsedControl"], .stDeployButton,
[data-testid="stToolbar"], section[data-testid="stSidebar"] { display: none !important; }
.block-container { padding: 2rem 2.5rem !important; max-width: 1200px !important; margin: 0 auto; }

/* ── Cards (bordered containers) ── */
div[data-testid="stVerticalBlockBorderWrapper"] {
    background: #faf8f4 !important;
    border: 1px solid #d6cfc4 !important;
    border-radius: 12px !important;
    padding: 20px 22px !important;
    transition: border-color 0.2s, box-shadow 0.2s !important;
    box-shadow: 0 2px 8px rgba(26,16,8,0.04) !important;
}
div[data-testid="stVerticalBlockBorderWrapper"]:hover {
    border-color: #c1440e !important;
    box-shadow: 0 4px 16px rgba(193,68,14,0.08) !important;
}

/* ── Primary buttons ── */
.stButton > button[kind="primary"] {
    background: #c1440e !important;
    border: none !important;
    border-radius: 8px !important;
    padding: 10px 24px !important;
    font-family: 'Space Grotesk', sans-serif !important;
    font-size: 0.9em !important;
    font-weight: 600 !important;
    color: #fff !important;
    letter-spacing: 0.01em !important;
    box-shadow: 0 2px 10px rgba(193,68,14,0.25) !important;
    transition: all 0.18s !important;
    width: 100% !important;
}
.stButton > button[kind="primary"]:hover {
    background: #a83a0c !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 18px rgba(193,68,14,0.35) !important;
}

/* ── Secondary buttons ── */
.stButton > button[kind="secondary"] {
    background: #faf8f4 !important;
    border: 1px solid #d6cfc4 !important;
    border-radius: 8px !important;
    color: #4a3728 !important;
    font-family: 'Space Grotesk', sans-serif !important;
    font-weight: 500 !important;
    transition: all 0.18s !important;
}
.stButton > button[kind="secondary"]:hover {
    background: #fff !important;
    border-color: #c1440e !important;
    color: #c1440e !important;
}

/* ── Input wrappers — strip Streamlit default borders ── */
div[data-baseweb="input"],
div[data-baseweb="base-input"],
div[data-baseweb="select"] > div,
[data-testid="stTextInputRootElement"],
[data-testid="stTextInputRootElement"] > div,
[data-testid="stTextInputRootElement"] > div > div,
[data-testid="stTextInput"] > div > div {
    border: none !important;
    border-color: transparent !important;
    background-color: transparent !important;
    box-shadow: none !important;
}

/* ── Inputs & textareas ── */
[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stSelectbox"] > div > div,
[data-testid="stDateInput"] input {
    background: #fff !important;
    border: 1px solid #d6cfc4 !important;
    border-radius: 8px !important;
    color: #1a1008 !important;
    font-family: 'Space Grotesk', sans-serif !important;
    font-size: 0.9em !important;
}
[data-testid="stTextInput"] input:focus,
[data-testid="stTextArea"] textarea:focus,
[data-testid="stSelectbox"] > div > div:focus,
[data-testid="stDateInput"] input:focus {
    border-color: #c1440e !important;
    box-shadow: 0 0 0 3px rgba(193,68,14,0.12) !important;
    outline: none !important;
}

/* ── File uploader ── */
[data-testid="stFileUploader"] {
    border: 2px dashed #c1440e !important;
    border-radius: 10px !important;
    background: rgba(193,68,14,0.02) !important;
    transition: all 0.2s !important;
}
[data-testid="stFileUploader"]:hover {
    background: rgba(193,68,14,0.05) !important;
}

/* ── Metrics ── */
[data-testid="stMetric"] {
    background: #faf8f4 !important;
    border: 1px solid #d6cfc4 !important;
    border-radius: 10px !important;
    padding: 14px !important;
}
[data-testid="stMetricValue"] { color: #c1440e !important; font-weight: 700 !important; font-family: 'Space Grotesk', sans-serif !important; }
[data-testid="stMetricLabel"] { color: #7a6254 !important; font-size: 0.82em !important; }

/* ── Progress bar ── */
[data-testid="stProgressBar"] > div > div {
    background: #c1440e !important;
    border-radius: 4px !important;
}

/* ── Expander ── */
[data-testid="stExpander"] details {
    border: 1px solid #d6cfc4 !important;
    border-radius: 10px !important;
    background: #faf8f4 !important;
}
[data-testid="stExpander"] summary {
    background: transparent !important;
    border-radius: 10px !important;
    border: none !important;
    color: #4a3728 !important;
    font-weight: 600 !important;
    font-family: 'Space Grotesk', sans-serif !important;
}

/* ── Badges ── */
.badge {
    display: inline-block; padding: 2px 9px; border-radius: 6px;
    font-size: 0.7em; font-weight: 700; letter-spacing: 0.05em;
    font-family: 'Space Mono', monospace;
}
.badge-high    { background: rgba(220,38,38,0.1);   color: #dc2626; }
.badge-medium  { background: rgba(193,68,14,0.12);  color: #c1440e; }
.badge-low     { background: rgba(22,163,74,0.1);   color: #16a34a; }
.badge-pending { background: rgba(193,68,14,0.1);   color: #c1440e; }
.badge-approved{ background: rgba(22,163,74,0.1);   color: #16a34a; }
.badge-rejected{ background: rgba(220,38,38,0.1);   color: #dc2626; }
.badge-flagged { background: rgba(202,138,4,0.1);   color: #ca8a04; }

/* ── Stepper ── */
.step-row { display: flex; align-items: flex-start; gap: 14px; padding-bottom: 18px; position: relative; }
.step-row:not(:last-child)::after {
    content: ''; position: absolute; left: 15px; top: 34px;
    width: 2px; height: calc(100% - 14px);
    background: #d6cfc4;
}
.step-dot {
    width: 32px; height: 32px; border-radius: 50%; flex-shrink: 0;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.75em; font-weight: 700; z-index: 1; margin-top: 2px;
    font-family: 'Space Mono', monospace;
}
.step-dot.done   { background: #16a34a; color: #fff; }
.step-dot.active { background: #c1440e; color: #fff; animation: pulse-dot 2s infinite; }
.step-dot.wait   { background: #faf8f4; color: #a08070; border: 1px solid #d6cfc4; }
@keyframes pulse-dot { 0%,100%{box-shadow:0 0 0 0 rgba(193,68,14,0.3)} 50%{box-shadow:0 0 0 6px rgba(193,68,14,0)} }
.step-name { font-size: 0.88em; font-weight: 600; color: #1a1008; padding-top: 4px; }
.step-desc { font-size: 0.76em; color: #7a6254; margin-top: 2px; }

/* ── Confidence bar ── */
.conf-bar { background: #e8e4db; border-radius: 4px; height: 4px; overflow: hidden; margin: 8px 0 4px; }
.conf-fill { height: 4px; border-radius: 4px; background: #c1440e; }

/* ── Divider ── */
hr { border-color: #d6cfc4 !important; margin: 28px 0 !important; }

/* ── Dataframe ── */
[data-testid="stDataFrame"] { border-radius: 10px !important; overflow: hidden !important; border: 1px solid #d6cfc4 !important; }

/* ── Headings ── */
h1, h2, h3, h4 { color: #1a1008 !important; font-weight: 700 !important; font-family: 'Space Grotesk', sans-serif !important; }

/* ── Download button ── */
[data-testid="stDownloadButton"] button {
    background: #faf8f4 !important;
    border: 1px solid #d6cfc4 !important;
    border-radius: 8px !important;
    color: #4a3728 !important;
    font-family: 'Space Grotesk', sans-serif !important;
    font-weight: 600 !important;
    transition: all 0.18s !important;
}
[data-testid="stDownloadButton"] button:hover {
    border-color: #c1440e !important;
    color: #c1440e !important;
}

/* ── Action Item Cards ── */
.ac-card {
    background: #faf8f4;
    border: 1px solid #d6cfc4;
    border-radius: 12px;
    overflow: hidden;
    margin-bottom: 4px;
    transition: box-shadow 0.2s, border-color 0.2s;
}
.ac-card:hover {
    border-color: #c1440e;
    box-shadow: 0 6px 20px rgba(26,16,8,0.08);
}
.ac-header {
    display: flex;
    align-items: stretch;
    border-bottom: 1px solid #e8e4db;
}
.ac-header-body {
    flex: 1;
    padding: 14px 18px;
}
.ac-title {
    font-size: 1em;
    font-weight: 700;
    color: #1a1008;
    line-height: 1.3;
    margin-bottom: 8px;
    font-family: 'Space Grotesk', sans-serif;
}
.ac-meta {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
    align-items: center;
}
.ac-chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 0.78em;
    font-weight: 500;
    color: #7a6254;
    background: #edeae4;
    border-radius: 20px;
    padding: 3px 10px;
    font-family: 'Space Grotesk', sans-serif;
}
.ac-badges {
    display: flex;
    flex-direction: column;
    gap: 5px;
    align-items: flex-end;
    justify-content: center;
    padding: 14px 16px;
    flex-shrink: 0;
}
.ac-body {
    padding: 14px 18px 10px 23px;
}
.ac-desc {
    font-size: 0.88em;
    color: #3d2c20;
    line-height: 1.65;
    padding: 10px 14px;
    background: rgba(193,68,14,0.04);
    border-radius: 6px;
    border-left: 3px solid rgba(193,68,14,0.3);
    margin-bottom: 12px;
}
.ac-conf-row {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 6px;
}
.ac-conf-track {
    flex: 1;
    height: 4px;
    background: #e8e4db;
    border-radius: 4px;
    overflow: hidden;
}
.ac-conf-fill {
    height: 4px;
    border-radius: 4px;
    background: #c1440e;
}
.ac-conf-label {
    font-size: 0.72em;
    color: #a08070;
    font-family: 'Space Mono', monospace;
    flex-shrink: 0;
}
.ac-controls-label {
    font-size: 0.65em;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #a08070;
    margin-bottom: 4px;
    font-family: 'Space Grotesk', sans-serif;
    padding: 8px 0 2px 0;
    border-top: 1px solid #e8e4db;
}
</style>
""", unsafe_allow_html=True)


# ── Session state ────────────────────────────────────────────────
def _init_state():
    defaults = {
        "thread_id": None, "graph_state": None,
        "action_items": [], "meeting_record": None, "warnings": [],
        "result": None, "pipeline_step": 0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_state()


# ── Helpers ──────────────────────────────────────────────────────
def _norm(val, fb=""):
    if val is None: return fb
    return val.value if hasattr(val, "value") else str(val)

def _parse_roster(text: str) -> list:
    out = []
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            out.append({
                "name": parts[0], "email": parts[1],
                "github_username": parts[2] if len(parts) > 2 else None,
                "aliases": [parts[0].split()[0]],
            })
    return out

def _stepper_html(active: int) -> str:
    steps = [
        ("Ingest",   "Parse transcript"),
        ("Extract",  "Groq LLM"),
        ("Resolve",  "Owners & dates"),
        ("Review",   "Human gate"),
        ("Execute",  "GitHub Issues"),
    ]
    rows = ""
    for i, (name, desc) in enumerate(steps):
        if i < active:    cls, icon = "done",   ""
        elif i == active: cls, icon = "active", str(i+1)
        else:             cls, icon = "wait",   str(i+1)
        rows += (
            f'<div class="step-row">'
            f'  <div class="step-dot {cls}">{icon}</div>'
            f'  <div><div class="step-name">{name}</div>'
            f'      <div class="step-desc">{desc}</div></div>'
            f'</div>'
        )
    return rows

def _build_markdown_report(meeting_record: dict, action_items: list, result: dict | None) -> str:
    mr      = meeting_record or {}
    date_s  = mr.get("meeting_date", str(date.today()))
    created = (result or {}).get("created_issues", [])
    skipped = (result or {}).get("skipped_duplicates", [])

    lines = [
        f"# MeetingMind Report — {date_s}",
        f"\n*Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}*\n",
        "---",
        "\n##Meeting Summary\n",
        mr.get("summary", "_No summary available._"),
    ]

    decisions = mr.get("decisions", [])
    if decisions:
        lines += ["\n##Key Decisions\n"]
        for d in decisions:
            text = d.get("decision", str(d)) if isinstance(d, dict) else str(d)
            ctx  = d.get("context", "") if isinstance(d, dict) else ""
            lines.append(f"- **{text}**")
            if ctx:
                lines.append(f"  > {ctx}")

    risks = mr.get("risks", [])
    if risks:
        lines += ["\n##Risks & Blockers\n"]
        for r in risks:
            lines.append(f"-  {r}")

    questions = mr.get("open_questions", [])
    if questions:
        lines += ["\n##Open Questions\n"]
        for q in questions:
            q_text = q.question if hasattr(q, "question") else (q.get("question", str(q)) if isinstance(q, dict) else str(q))
            asked_by = q.asked_by if hasattr(q, "asked_by") else (q.get("asked_by") if isinstance(q, dict) else None)
            
            if asked_by:
                lines.append(f"-  **{q_text}** *(asked by {asked_by})*")
            else:
                lines.append(f"-  {q_text}")

    if action_items:
        lines += ["\n##Action Items\n"]
        for item in action_items:
            status   = _norm(item.get("status", "pending"))
            priority = _norm(item.get("priority", "medium"))
            owner    = (item.get("resolved_owner") or {}).get("name") or item.get("raw_owner", "—")
            due      = str(item.get("resolved_date") or item.get("raw_due_date") or "Not set")
            desc     = item.get("description", "")
            evidence = item.get("evidence_quote", "")

            lines.append(f"\n### {item.get('title','Untitled')}")
            lines.append(f"**Status:** {status} | **Priority:** {priority} | **Owner:** {owner} | **Due:** {due}")
            if desc:
                lines.append(f"\n{desc}")
            if evidence:
                lines.append(f'\n>  *"{evidence}"*')
                if item.get("evidence_timestamp"):
                    lines.append(f'> *(@ {item["evidence_timestamp"]})*')

    if created:
        lines += ["\n##  GitHub Issues Created\n"]
        for i in created:
            url   = i.get("task_url") or i.get("issue_url", "#")
            title = i.get("title", "Issue")
            lines.append(f"- [{title}]({url})")

    lines += ["\n---", "\n*Report generated by MeetingMind*"]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# MAIN APP FLOW
# ═══════════════════════════════════════════════════════════════

def main():
    st.markdown(
        '<div style="text-align:center;margin-bottom:20px">'
        '<span style="font-size:2.4em;font-weight:900;letter-spacing:-0.03em;background:linear-gradient(135deg,#c1440e,#d4602a);'
        '-webkit-background-clip:text;-webkit-text-fill-color:transparent">MeetingMind</span>'
        '</div>',
        unsafe_allow_html=True,
    )
    # ── SECTION 1: Upload & Config ──

    left, right = st.columns([3, 2], gap="large")

    with left:
        with st.container(border=True):
            st.markdown(
                '<span style="font-size:0.7em;font-weight:800;letter-spacing:0.12em;'
                'text-transform:uppercase;color:#c1440e">1. Transcript File</span>',
                unsafe_allow_html=True,
            )
            uploaded_file = st.file_uploader(
                " ",
                type=["txt", "vtt", "srt", "mp3", "mp4", "wav", "m4a"],
                label_visibility="collapsed",
                key="transcript_upload",
            )
            
            if uploaded_file is not None:
                st.session_state["saved_file"] = uploaded_file

            active_file = uploaded_file or st.session_state.get("saved_file")

            if active_file:
                if uploaded_file is None:
                    st.info(f"💾 Retained file: **{active_file.name}**")
                    if st.button("Clear saved file", key="clear_file"):
                        st.session_state["saved_file"] = None
                        st.rerun()

                ext = Path(active_file.name).suffix.lower()
                if ext in {".mp3", ".mp4", ".wav", ".m4a"}:
                    st.info(" Audio detected — Deepgram will transcribe & diarize")
                else:
                    preview = active_file.read(1600).decode("utf-8", errors="replace")
                    active_file.seek(0)
                    with st.expander("Preview transcript (first 1600 chars)"):
                        st.code(preview, language=None)

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        dc, rc = st.columns(2)
        with dc:
            with st.container(border=True):
                st.markdown(
                    '<span style="font-size:0.68em;font-weight:800;letter-spacing:0.1em;'
                    'text-transform:uppercase;color:#c1440e">Meeting Date</span>',
                    unsafe_allow_html=True,
                )
                meeting_date = st.date_input(
                    "Date", value=date.today(), label_visibility="collapsed", key="meeting_date_inp"
                )
        with rc:
            with st.container(border=True):
                st.markdown(
                    '<span style="font-size:0.68em;font-weight:800;letter-spacing:0.1em;'
                    'text-transform:uppercase;color:#c1440e">Reviewer</span>',
                    unsafe_allow_html=True,
                )
                reviewer = st.text_input(
                    "Name", value="demo_user", label_visibility="collapsed", key="reviewer_name"
                )

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        with st.container(border=True):
            st.markdown(
                '<span style="font-size:0.68em;font-weight:800;letter-spacing:0.1em;'
                'text-transform:uppercase;color:#c1440e">Participant Roster</span>',
                unsafe_allow_html=True,
            )
            st.caption("Participants will be auto-discovered from the transcript after processing. You can map them to GitHub accounts in the Review section below.")

        st.markdown("<div style='height:18px'></div>", unsafe_allow_html=True)
        btn_col, _, _ = st.columns([2,1,1])
        with btn_col:
            st.button(
                "Process Meeting",
                type="primary",
                disabled=st.session_state.get("saved_file") is None and uploaded_file is None,
                key="process_btn",
            )

    # ── Pipeline Status & Last Run ──
    with right:
        # Derive step from actual session state so it's always accurate
        _result     = st.session_state.get("result")
        _has_record = bool(st.session_state.get("meeting_record"))
        _has_ai     = bool(st.session_state.get("action_items"))
        if _result:
            _derived_step = 5          # Execute done
        elif _has_record or _has_ai:
            _derived_step = 3          # Review
        else:
            _derived_step = st.session_state.get("pipeline_step", 0)
        step = _derived_step
        with st.container(border=True):
            st.markdown(
                '<span style="font-size:0.68em;font-weight:800;letter-spacing:0.1em;'
                'text-transform:uppercase;color:#c1440e">Pipeline</span>',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<div style="margin-top:14px">{_stepper_html(step)}</div>',
                unsafe_allow_html=True,
            )

        result = st.session_state.get("result")
        if result:
            created = result.get("created_issues", [])
            skipped = result.get("skipped_duplicates", [])
            st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
            with st.container(border=True):
                st.markdown(
                    '<span style="font-size:0.68em;font-weight:800;letter-spacing:0.1em;'
                    'text-transform:uppercase;color:#16a34a">Last Run</span>',
                    unsafe_allow_html=True,
                )
                mc1, mc2 = st.columns(2)
                mc1.metric("Issues Created", len(created))
                mc2.metric("Skipped Dupes",  len(skipped))
                for i in created:
                    url   = i.get("task_url") or i.get("issue_url", "#")
                    title = i.get("title", "Issue")
                    st.markdown(
                        f'<a href="{url}" target="_blank" style="font-size:0.83em;color:#c1440e;'
                        f'text-decoration:none">→ {title}</a>',
                        unsafe_allow_html=True,
                    )

        # Download report button
        mr = st.session_state.get("meeting_record") or {}
        ai = st.session_state.get("action_items", [])
        if mr or ai:
            st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
            md_report = _build_markdown_report(
                mr if isinstance(mr, dict) else (mr.model_dump() if hasattr(mr, "model_dump") else {}),
                ai, result,
            )
            st.download_button(
                "Download Markdown Report",
                data=md_report.encode("utf-8"),
                file_name=f"meetingmind_{date.today()}.md",
                mime="text/markdown",
                use_container_width=True,
            )

    # ── Processor Logic ──
    active_file = st.session_state.get("saved_file") or uploaded_file
    _should_process = st.session_state.get("process_btn") and active_file

    if _should_process:
        # Clear old session state to prevent previous meeting data from bleeding over
        for key in ["participant_roster_final", "action_items", "meeting_record", "graph_state", "warnings"]:
            st.session_state.pop(key, None)
            
        roster = []
        suffix = Path(active_file.name).suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            active_file.seek(0)
            tmp.write(active_file.read())
            tmp_path = tmp.name

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

        with st.status("Starting pipeline…", expanded=True) as status:
            def _st_callback(msg: str):
                status.update(label=msg, state="running")
            set_status_callback(_st_callback)

            try:
                final_state = None
                node_labels = [
                    "Ingesting transcript…",
                    "Extracting action items with Groq…",
                    "Resolving owners & due dates…",
                    "Preparing human review…",
                ]
                for n, event in enumerate(graph.stream(
                    initial_state, config=config, stream_mode="values"
                )):
                    final_state = event
                    errs = event.get("errors", [])
                    if errs:
                        status.update(label=f"Error: {errs[-1]}", state="error", expanded=True)
                        st.error(f"Pipeline error: {errs[-1]}")
                        break

                    if n < len(node_labels):
                        status.update(label=node_labels[n], state="running")
                    st.session_state.pipeline_step = min(n + 1, 4)

                if final_state and not final_state.get("errors"):
                    mr_raw = final_state.get("meeting_record_raw")
                    st.session_state.graph_state    = final_state
                    st.session_state.action_items   = final_state.get("action_items", [])
                    st.session_state.warnings       = final_state.get("warnings", [])
                    st.session_state.meeting_record = (
                        mr_raw.model_dump() if hasattr(mr_raw, "model_dump") else (mr_raw or {})
                    )
                    st.session_state.pipeline_step  = 3
                    status.update(label="Ready for review!", state="complete", expanded=False)
                    time.sleep(1.2)
                    st.rerun()

            except Exception as e:
                status.update(label=f"Pipeline error: {e}", state="error", expanded=True)
                st.exception(e)
            finally:
                clear_status_callback()

    # ── SECTION 2: Review (Conditional) ──
    mr = st.session_state.get("meeting_record")
    ai = st.session_state.get("action_items", [])

    if mr or ai:
        st.divider()
        st.markdown("## Review & Execute")
        
        warnings = st.session_state.get("warnings", [])
        if warnings:
            with st.expander(f" {len(warnings)} flagged items — click to expand"):
                for w in warnings:
                    st.warning(w)

        # ── Participant → GitHub Mapping panel ──────────────────────
        _render_participant_mapper()

        # Flat view: Summary -> Items
        _render_summary(mr, ai)
        
        st.markdown("<div style='height:24px'></div>", unsafe_allow_html=True)
        edited_items = _render_items(ai)
        
        approved = [i for i in edited_items if i.get("status") == "approved"]
        rejected = [i for i in edited_items if i.get("status") == "rejected"]
        pending  = len(ai) - len(approved) - len(rejected)

        st.markdown(
            "<hr style='margin:30px 0; border:none; border-top:1px solid #e8e4db'>", 
            unsafe_allow_html=True
        )
        scol, bcol = st.columns([3, 1])
        with scol:
            st.markdown(
                f'<div style="display:flex;gap:28px;align-items:center;height:100%;padding-top:4px">'
                f'  <div style="font-size:0.9em;font-weight:800;color:#1a6b55;letter-spacing:0.05em;text-transform:uppercase"><span style="font-size:1.3em">{len(approved)}</span> Approved</div>'
                f'  <div style="font-size:0.9em;font-weight:800;color:#c1440e;letter-spacing:0.05em;text-transform:uppercase"><span style="font-size:1.3em">{len(rejected)}</span> Rejected</div>'
                f'  <div style="font-size:0.9em;font-weight:700;color:#a05c00;letter-spacing:0.05em;text-transform:uppercase"><span style="font-size:1.3em">{pending}</span> Pending</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            with bcol:
                if st.button(
                    "Confirm & Execute",
                    type="primary",
                    disabled=len(approved) == 0,
                    key="confirm_btn",
                    use_container_width=True,
                ):
                    _execute(approved, rejected)

    # ── SECTION 3: Audit Log (Expander) ──
    st.divider()
    with st.expander("📝 Audit Log", expanded=False):
        _render_audit_log()


def _render_participant_mapper():
    """
    Renders the Participant → GitHub Mapper panel in the Review section.
    Shows each unique speaker discovered in the transcript with a dropdown
    of GitHub repo collaborators, auto-fuzzy-matched. User can override.
    Saves the final mapping to st.session_state['participant_roster_final'].
    """
    from rapidfuzz import process as fuzz_process, fuzz

    graph_state = st.session_state.get("graph_state") or {}
    utterances  = graph_state.get("utterances", [])

    if not utterances:
        return

    # Collect unique speaker labels from utterances
    speakers = list(dict.fromkeys(
        u.get("speaker", "Unknown") for u in utterances
        if u.get("speaker")
    ))

    if not speakers:
        return

    # Fetch GitHub collaborators (cached per session to avoid repeated API calls)
    if "github_collaborators_cache" not in st.session_state:
        with st.spinner("Fetching GitHub collaborators…"):
            st.session_state["github_collaborators_cache"] = get_repo_collaborators()

    collabs = st.session_state["github_collaborators_cache"]
    collab_options = ["(Not a collaborator)"] + [
        f"{c['name']} (@{c['login']})" for c in collabs
    ]
    collab_login_map = {
        f"{c['name']} (@{c['login']})": c["login"] for c in collabs
    }
    collab_name_map = {
        f"{c['name']} (@{c['login']})": c["name"] for c in collabs
    }

    def _best_match(speaker_label: str) -> int:
        """Returns index into collab_options for best fuzzy match, or 0 (Not a collaborator)."""
        if not collabs:
            return 0
        candidate_labels = [f"{c['name']} (@{c['login']})" for c in collabs]
        result = fuzz_process.extractOne(speaker_label, candidate_labels, scorer=fuzz.WRatio)
        if result and result[1] >= 60:
            matched_label = result[0]
            return collab_options.index(matched_label)
        return 0

    with st.container(border=True):
        st.markdown(
            '<span style="font-size:0.7em;font-weight:800;letter-spacing:0.12em;'
            'text-transform:uppercase;color:#16a34a">Participant → GitHub Mapping</span>',
            unsafe_allow_html=True,
        )
        if not collabs:
            st.caption("GitHub not configured or no collaborators found — mapping unavailable.")
        else:
            st.caption(
                f"Found **{len(speakers)}** speaker(s) in transcript · **{len(collabs)}** collaborator(s) in repo. "
                "Confirm or override auto-detected assignments below."
            )

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

        roster = []
        for speaker in speakers:
            col_label, col_select = st.columns([2, 3])
            with col_label:
                edited_speaker_name = st.text_input(
                    f"Name for {speaker}",
                    value=speaker,
                    key=f"edit_speaker_{speaker}",
                    label_visibility="collapsed"
                )
            with col_select:
                default_idx = _best_match(speaker) if collabs else 0
                chosen = st.selectbox(
                    f"Map {speaker}",
                    options=collab_options,
                    index=default_idx,
                    key=f"collab_map_{speaker}",
                    label_visibility="collapsed",
                )

            # Build roster entry from selection
            # We must keep the original `speaker` in aliases so LLM extracted raw_owners still map correctly
            if chosen == "(Not a collaborator)":
                roster.append({
                    "name":            edited_speaker_name,
                    "email":           "",
                    "github_username": None,
                    "aliases":         [speaker, edited_speaker_name.split()[0]] if speaker else [],
                })
            else:
                roster.append({
                    "name":            collab_name_map.get(chosen, edited_speaker_name),
                    "email":           "",
                    "github_username": collab_login_map.get(chosen),
                    "aliases":         [speaker, edited_speaker_name, speaker.split()[0]] if speaker else [],
                })

        # Persist the final mapped roster to session state so the pipeline and execute step can use it
        st.session_state["participant_roster_final"] = roster

        # Also update graph_state so _render_items owner dropdowns show correct names
        if st.session_state.get("graph_state"):
            st.session_state["graph_state"]["participant_roster"] = roster

        # ── KEY FIX: Push github_username into each action item immediately ──────
        # The pipeline ran before the mapping existed, so resolved_owner.github_username
        # is None for all items. We now backfill it from the roster using raw_owner
        # matching (the speaker label from the transcript) so that Confirm & Execute
        # sends the correct assignee to GitHub and triggers their notification.
        action_items = st.session_state.get("action_items", [])
        for item in action_items:
            raw_owner = (item.get("raw_owner") or "").strip()
            if not raw_owner:
                continue
            for entry in roster:
                # Match against aliases (which include the original speaker label, e.g. "Eggomelette")
                all_names = [entry.get("name", "")] + entry.get("aliases", [])
                if any(raw_owner.lower() == n.lower() for n in all_names if n):
                    if item.get("resolved_owner") is None:
                        item["resolved_owner"] = {
                            "name": entry.get("name", raw_owner),
                            "email": entry.get("email", ""),
                            "github_username": entry.get("github_username"),
                            "match_score": 1.0,
                            "resolution_method": "participant_map",
                        }
                    else:
                        item["resolved_owner"]["github_username"] = entry.get("github_username")
                        item["resolved_owner"]["name"] = entry.get("name", raw_owner)
                    break
        st.session_state["action_items"] = action_items



def _render_summary(record: dict, action_items: list):
    if not record and not action_items:
        return

    summary   = record.get("summary", "")
    decisions = record.get("decisions", [])
    risks     = record.get("risks", [])
    questions = record.get("open_questions", [])

    with st.container(border=True):
        st.markdown(
            '<span style="font-size:0.7em;font-weight:800;letter-spacing:0.12em;'
            'text-transform:uppercase;color:#c1440e">Meeting Summary</span>',
            unsafe_allow_html=True,
        )
        if summary:
            st.markdown(
                f'<p style="color:#3d2c20;font-size:0.95em;line-height:1.75;margin-top:10px">{summary}</p>',
                unsafe_allow_html=True,
            )

    if decisions or risks or questions:
        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        d_col, r_col = st.columns(2)
        with d_col:
            if decisions:
                # ── Key Decisions card ──────────────────────────────
                dec_html = """
<div class="ac-card" style="margin-bottom:12px">
  <div class="ac-header" style="border-bottom:none">
    <div class="ac-header-body" style="padding-bottom:0">
      <div style="font-size:0.65em;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:#1a6b55;margin-bottom:10px">Key Decisions</div>"""
                for d in decisions:
                    text = d.get("decision", str(d)) if isinstance(d, dict) else str(d)
                    ctx  = d.get("context", "") if isinstance(d, dict) else ""
                    dec_html += f"""
      <div style="display:flex;align-items:flex-start;gap:10px;padding:9px 0;border-bottom:1px solid #e8e4db;">
        <div style="width:8px;height:8px;border-radius:50%;background:#1a6b55;flex-shrink:0;margin-top:5px"></div>
        <div>
          <div style="font-size:0.88em;font-weight:600;color:#1a1008;line-height:1.4">{text}</div>
          {"" if not ctx else f'<div style=\"font-size:0.78em;color:#7a6254;margin-top:3px\">{ctx}</div>'}
        </div>
      </div>"""
                dec_html += "\n    </div>\n  </div>\n</div>"
                st.markdown(dec_html, unsafe_allow_html=True)

            if risks:
                st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
                # ── Risks & Blockers card ────────────────────────────
                risk_html = """
<div class="ac-card" style="margin-bottom:12px">
  <div class="ac-header" style="border-bottom:none">
    <div class="ac-header-body" style="padding-bottom:0">
      <div style="font-size:0.65em;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:#a05c00;margin-bottom:10px">Risks &amp; Blockers</div>"""
                for r in risks:
                    risk_html += f"""
      <div style="display:flex;align-items:flex-start;gap:10px;padding:9px 0;border-bottom:1px solid #e8e4db;">
        <div style="width:8px;height:8px;border-radius:2px;background:#a05c00;flex-shrink:0;margin-top:5px;transform:rotate(45deg)"></div>
        <div style="font-size:0.88em;font-weight:600;color:#1a1008;line-height:1.4">⚠️ {r}</div>
      </div>"""
                risk_html += "\n    </div>\n  </div>\n</div>"
                st.markdown(risk_html, unsafe_allow_html=True)

            if questions:
                st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
                # ── Open Questions card ──────────────────────────────
                q_html = """
<div class="ac-card" style="margin-bottom:12px">
  <div class="ac-header" style="border-bottom:none">
    <div class="ac-header-body" style="padding-bottom:0">
      <div style="font-size:0.65em;font-weight:700;letter-spacing:0.12em;text-transform:uppercase;color:#7c5070;margin-bottom:10px">Open Questions</div>"""
                for q in questions:
                    q_text = q.question if hasattr(q, "question") else (q.get("question", str(q)) if isinstance(q, dict) else str(q))
                    asked_by = q.asked_by if hasattr(q, "asked_by") else (q.get("asked_by") if isinstance(q, dict) else None)
                    asked_html = f'<span style="color:#7c5070;font-weight:700;font-size:0.85em;margin-left:6px;background:#f4eef2;padding:2px 6px;border-radius:4px">{asked_by}</span>' if asked_by else ""
                    
                    q_html += f"""
      <div style="display:flex;align-items:flex-start;gap:10px;padding:9px 0;border-bottom:1px solid #e8e4db;">
        <div style="width:8px;height:8px;border-radius:50%;background:none;border:2px solid #7c5070;flex-shrink:0;margin-top:5px"></div>
        <div style="font-size:0.88em;font-weight:600;color:#1a1008;line-height:1.4">❓ {q_text}{asked_html}</div>
      </div>"""
                q_html += "\n    </div>\n  </div>\n</div>"
                st.markdown(q_html, unsafe_allow_html=True)

        with r_col:
            # Deadlines (Custom Glassmorphic Layout)
            if action_items:
                st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

                # Parse and sort events
                events = []
                for item in action_items:
                    due = item.get("resolved_date") or item.get("raw_due_date")
                    if due:
                        try:
                            d_obj = date.fromisoformat(str(due).split("T")[0].split(" ")[0])
                            events.append({
                                "title": item.get("title", "Action Item"),
                                "date":  d_obj,
                                "owner": (item.get("resolved_owner") or {}).get("name") or item.get("raw_owner", "—"),
                                "prio":  _norm(item.get("priority","medium"),"medium").lower(),
                            })
                        except:
                            pass

                if not events:
                    st.caption("No action items with dates to display.")
                else:
                    events.sort(key=lambda x: x["date"])
                    events_by_date = defaultdict(list)
                    for ev in events:
                        events_by_date[ev["date"]].append(ev)

                    cal_date   = events[0]["date"]
                    cal        = calendar.Calendar(firstweekday=6)
                    month_days = cal.monthdayscalendar(cal_date.year, cal_date.month)
                    month_name = calendar.month_name[cal_date.month]
                    today      = date.today()

                    PRIO_DOT = {"high": "#c1440e", "medium": "#a05c00", "low": "#1a6b55"}

                    # ── Styles embedded in the HTML ──
                    # Priority ring color: use highest priority event on that date
                    PRIO_DOT  = {"high": "#c1440e", "medium": "#a05c00", "low": "#1a6b55"}
                    PRIO_RANK = {"high": 0, "medium": 1, "low": 2}

                    cal_html = f"""
<style>
.cal-wrap {{
    background: #faf8f4;
    border: 1px solid #d6cfc4;
    border-radius: 12px;
    padding: 16px 20px;
    margin: 8px auto 0;
    font-family: 'Space Grotesk', sans-serif;
    max-width: 380px;
    width: 100%;
}}
.cal-header {{
    text-align: center;
    margin-bottom: 12px;
}}
.cal-month {{
    font-size: 0.95em;
    font-weight: 700;
    color: #1a1008;
    letter-spacing: 0.06em;
    text-transform: uppercase;
}}
.cal-grid {{
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    gap: 4px;
    text-align: center;
}}
.cal-dow {{
    font-size: 0.75em;
    font-weight: 600;
    color: #a08070;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    padding-bottom: 6px;
}}
.cal-day {{
    position: relative;
    aspect-ratio: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    border-radius: 6px;
    cursor: default;
}}
.cal-day-num {{
    display: flex;
    align-items: center;
    justify-content: center;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    font-size: 0.88em;
    font-weight: 500;
    color: #a08070;
}}
.cal-day.has-events .cal-day-num {{
    font-weight: 800;
    color: #1a1008;
    cursor: pointer;
}}
.cal-day.has-events:hover {{
    background: rgba(193, 68, 14, 0.08);
}}
.cal-day.is-today .cal-day-num {{
    background: #c1440e;
    color: #fff;
    font-weight: 800;
    border: none;
    box-shadow: 0 2px 6px rgba(193,68,14,0.3);
}}
.cal-dots {{
    display: flex;
    gap: 4px;
    margin-top: 3px;
    justify-content: center;
    height: 8px;
}}
.cal-dot {{
    width: 5px;
    height: 5px;
    border-radius: 50%;
    flex-shrink: 0;
}}
/* Hover tooltip */
.cal-tooltip {{
    display: none;
    position: absolute;
    bottom: calc(100% + 6px);
    left: 50%;
    transform: translateX(-50%);
    background: #1a1008;
    color: #f0ece3;
    border-radius: 8px;
    padding: 7px 10px;
    width: max-content;
    max-width: 190px;
    font-size: 0.68em;
    line-height: 1.5;
    z-index: 100;
    white-space: normal;
    box-shadow: 0 4px 16px rgba(26,16,8,0.28);
    pointer-events: none;
}}
.cal-tooltip::after {{
    content: '';
    position: absolute;
    top: 100%;
    left: 50%;
    transform: translateX(-50%);
    border: 5px solid transparent;
    border-top-color: #1a1008;
}}
.cal-day.has-events:hover .cal-tooltip {{
    display: block;
}}
.cal-section-label {{
    font-size: 0.6em;
    font-weight: 700;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #9b3a10;
    margin-bottom: 8px;
}}
</style>

<div class="cal-wrap">
  <div class="cal-section-label">Deadlines Timeline</div>
  <div class="cal-header">
    <div class="cal-month">{month_name} {cal_date.year}</div>
  </div>
  <div class="cal-grid">
"""
                    for dow in ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"]:
                        cal_html += f'<div class="cal-dow">{dow}</div>\n'

                    for week in month_days:
                        for day in week:
                            if day == 0:
                                cal_html += '<div></div>\n'
                                continue

                            current_d  = date(cal_date.year, cal_date.month, day)
                            day_events = events_by_date.get(current_d, [])
                            has_ev     = len(day_events) > 0
                            is_today   = (current_d == today)

                            classes = "cal-day"
                            if is_today:  classes += " is-today"
                            if has_ev:  classes += " has-events"

                            cal_html += f'<div class="{classes}">\n'
                            cal_html += f'  <div class="cal-day-num">{day}</div>\n'

                            if has_ev:
                                # Dots below date
                                cal_html += '  <div class="cal-dots">\n'
                                for ev in day_events[:3]: # Max 3 dots so it fits
                                    dot_color = PRIO_DOT.get(ev.get("prio", "medium"), "#a05c00")
                                    cal_html += f'    <div class="cal-dot" style="background:{dot_color}"></div>\n'
                                cal_html += '  </div>\n'

                                # Tooltip
                                tooltip_lines = ""
                                for ev in day_events:
                                    rc = PRIO_DOT.get(ev.get("prio","medium"), "#a05c00")
                                    tooltip_lines += (
                                        f'<div style="display:flex;align-items:flex-start;gap:6px;margin-bottom:4px;">'
                                        f'<div style="width:6px;height:6px;border-radius:50%;border:1.5px solid {rc};flex-shrink:0;margin-top:4px"></div>'
                                        f'<div><div style="font-weight:700;color:#f0ece3;font-size:1em">{ev["title"]}</div>'
                                        f'<div style="color:#c8b8ac;font-size:0.88em">👤 {ev["owner"]}</div></div>'
                                        f'</div>'
                                    )
                                cal_html += f'  <div class="cal-tooltip">{tooltip_lines}</div>\n'

                            cal_html += '</div>\n'

                    cal_html += "  </div>\n</div>"
                    st.markdown(cal_html, unsafe_allow_html=True)

def _render_items(action_items: list) -> list:
    if not action_items:
        return []

    roster = (st.session_state.get("graph_state") or {}).get("participant_roster", [])
    owners = [p.get("name", "") for p in roster] if roster else []

    PRIO_COLOR = {"high": "#c1440e", "medium": "#a05c00", "low": "#1a6b55"}
    STATUS_ICON = {"approved": "✅", "rejected": "❌", "pending": "⏳"}

    st.markdown(
        f'<div style="font-size:0.78em;font-weight:700;letter-spacing:0.12em;'
        f'text-transform:uppercase;color:#c1440e;margin-bottom:14px">'
        f'Action Items &nbsp;<span style="background:#c1440e;color:#fff;border-radius:20px;'
        f'padding:2px 9px;font-size:0.95em">{len(action_items)}</span></div>',
        unsafe_allow_html=True,
    )

    edited = []
    for i, item in enumerate(action_items):
        conf       = item.get("confidence", 0)
        status_val = _norm(item.get("status", "pending"), "pending")
        raw_prio   = _norm(item.get("priority", "medium"), "medium").lower()
        if raw_prio not in ["high", "medium", "low"]:
            raw_prio = "medium"
        owner_name  = (item.get("resolved_owner") or {}).get("name") or item.get("raw_owner", "—")
        date_disp   = str(item.get("resolved_date") or item.get("raw_due_date") or "Not set")
        description = item.get("description", "")
        evidence    = item.get("evidence_quote", "")
        ev_ts       = item.get("evidence_timestamp", "")
        prio_color  = PRIO_COLOR.get(raw_prio, "#a05c00")
        status_icon = STATUS_ICON.get(status_val, "⏳")
        title       = item.get("title", "Untitled")

        # ── Card header (full custom HTML) ──────────────────────
        header_html = f"""
<div class="ac-card">
  <div class="ac-header">
    <div class="ac-header-body">
      <div class="ac-title">{title}</div>
      <div class="ac-meta">
        <span class="ac-chip">👤 {owner_name}</span>
        <span class="ac-chip">📅 {date_disp}</span>
      </div>
    </div>
    <div class="ac-badges">
      <span class="badge badge-{raw_prio}">{raw_prio.upper()}</span>
      <span class="badge badge-{status_val}">{status_icon} {status_val.upper()}</span>
    </div>
  </div>
  <div class="ac-body">"""

        if description:
            header_html += f"""
    <div class="ac-desc">{description}</div>"""

        header_html += f"""
    <div class="ac-conf-row">
      <div class="ac-conf-track"><div class="ac-conf-fill" style="width:{int(conf*100)}%"></div></div>
      <span class="ac-conf-label">{int(conf*100)}% confidence</span>
    </div>"""

        header_html += """
    <div class="ac-controls-label">Edit</div>
  </div>
</div>"""

        st.markdown(header_html, unsafe_allow_html=True)

        # ── Native edit controls (must be Streamlit widgets) ─────
        c1, c2, c3, c4 = st.columns([1.8, 1.8, 1, 1.6])
        with c1:
            if owners:
                idx = owners.index(owner_name) if owner_name in owners else 0
                sel_owner = st.selectbox(
                    "Owner", owners, index=idx, key=f"owner_{i}", label_visibility="collapsed"
                )
            else:
                sel_owner = st.text_input(
                    "Owner", value=owner_name, key=f"owner_{i}", label_visibility="collapsed"
                )
        with c2:
            raw_date = item.get("resolved_date")
            dflt = date.today()
            if raw_date:
                try: dflt = date.fromisoformat(str(raw_date))
                except: pass
            sel_date = st.date_input("Due", value=dflt, key=f"date_{i}", label_visibility="collapsed")
        with c3:
            sel_prio = st.selectbox(
                "Prio", ["high", "medium", "low"],
                index=["high", "medium", "low"].index(raw_prio),
                key=f"prio_{i}", label_visibility="collapsed",
            )
        with c4:
            bc1, bc2 = st.columns(2)
            
            def set_status(idx, new_status):
                current = st.session_state["action_items"][idx].get("status", "pending")
                st.session_state["action_items"][idx]["status"] = "pending" if current == new_status else new_status

            with bc1:
                st.button(
                    "Approved" if status_val == "approved" else "Approve", 
                    on_click=set_status, 
                    args=(i, "approved"), 
                    key=f"btn_app_{i}", 
                    type="primary" if status_val == "approved" else "secondary",
                    use_container_width=True
                )
            with bc2:
                st.button(
                    "Rejected" if status_val == "rejected" else "Reject", 
                    on_click=set_status, 
                    args=(i, "rejected"), 
                    key=f"btn_rej_{i}",
                    type="primary" if status_val == "rejected" else "secondary",
                    use_container_width=True
                )

        # Transcript evidence (collapsible)
        if evidence:
            with st.expander("📜 Transcript evidence"):
                st.markdown(f'> *"{evidence}"*')
                if ev_ts:
                    st.caption(f"@ {ev_ts}")

        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

        updated = dict(item)
        updated.update({
            "status": status_val, "priority": sel_prio,
            "resolved_date": sel_date.isoformat(),
        })
        if updated.get("resolved_owner"):
            updated["resolved_owner"]["name"] = sel_owner
            for p in roster:
                if p.get("name") == sel_owner:
                    updated["resolved_owner"].update({
                        "email": p.get("email", ""),
                        "github_username": p.get("github_username"),
                    })
                    break
        else:
            updated["resolved_owner"] = {
                "name": sel_owner, "email": "", "github_username": None,
                "match_score": 1.0, "resolution_method": "manual",
            }
        edited.append(updated)
    return edited

def _execute(approved: list, rejected: list):
    config = {"configurable": {"thread_id": st.session_state.get("thread_id")}}
    with st.spinner(f"Creating {len(approved)} GitHub Issue(s)…"):
        try:
            final = get_graph().invoke(
                Command(resume={"approved": approved, "rejected": rejected}),
                config=config,
            )
            st.session_state.result = final
            created = final.get("created_issues", [])
            skipped = final.get("skipped_duplicates", [])
            st.success(f" Done! {len(created)} issues created · {len(skipped)} duplicates skipped")
            st.session_state.pipeline_step = 5
            time.sleep(1.5)
            st.rerun()
        except Exception as e:
            st.error(f"❌ {e}")
            st.exception(e)

def _render_audit_log():
    init_db()
    with st.container(border=True):
        fc1, fc2, fc3 = st.columns([2, 2, 1])
        with fc1:
            mf = st.text_input("Filter by Meeting ID", placeholder="Leave blank for all")
        with fc2:
            ef = st.selectbox(
                "Filter by Event",
                ["", "github_issue_created", "skipped_duplicate", "item_rejected",
                 "decision_recorded", "risk_identified", "open_question"],
                format_func=lambda x: x or "All events",
            )
        with fc3:
            lim = st.number_input("Max rows", 10, 2000, 200, 50)

    entries = get_all_entries(meeting_id=mf or None, event_filter=ef or None, limit=lim)

    if not entries:
        st.info("No audit logs yet.")
        return

    mc = st.columns(5)
    mc[0].metric("Total Logs", len(entries))
    mc[1].metric("Issues",     sum(1 for e in entries if e.get("event") == "github_issue_created"))
    mc[2].metric("Decisions",  sum(1 for e in entries if e.get("event") == "decision_recorded"))
    mc[3].metric("Risks",      sum(1 for e in entries if e.get("event") == "risk_identified"))
    mc[4].metric("Questions",  sum(1 for e in entries if e.get("event") == "open_question"))

    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
    df = pd.DataFrame(entries)
    cols = [c for c in ["timestamp", "event", "title", "owner_email", "external_ref", "approved_by"] if c in df.columns]
    
    st.dataframe(
        df[cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "external_ref": st.column_config.LinkColumn("Link"),
            "timestamp":    st.column_config.DatetimeColumn("Time", format="YYYY-MM-DD HH:mm"),
            "event":        st.column_config.TextColumn("Event"),
            "title":        st.column_config.TextColumn("Title", width="large"),
        },
    )

    st.download_button(
        "Download JSON Audit Log",
        data=json.dumps(entries, indent=2, default=str).encode("utf-8"),
        file_name=f"meetingmind_audit_{date.today()}.json",
        mime="application/json",
    )


if __name__ == "__main__":
    main()


