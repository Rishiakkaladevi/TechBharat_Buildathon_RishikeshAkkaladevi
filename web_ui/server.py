"""
web_ui/server.py — FastAPI backend for MeetingMind glassmorphism UI

Shares all business logic (core/, integrations/, graph.py) with the Streamlit app.
Session state is kept in-memory per session_id for demo purposes.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, AsyncGenerator

# ── Make sure parent directory is importable ──────────────────────────────────
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env")

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from pydantic import BaseModel

from graph import get_graph
from langgraph.types import Command
from integrations.github_client import get_repo_collaborators


# ─────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────

app = FastAPI(title="MeetingMind Web UI")

_WEB_UI = Path(__file__).parent
app.mount("/static", StaticFiles(directory=_WEB_UI / "static"), name="static")
templates = Jinja2Templates(directory=_WEB_UI / "templates")


# ─────────────────────────────────────────────
# In-memory session store
# ─────────────────────────────────────────────

# session_id → {
#   "config": {"configurable": {"thread_id": "..."}},
#   "state": {...},            ← last graph state
#   "action_items": [...],
#   "meeting_record": {...},
#   "meeting_date": "YYYY-MM-DD",
#   "approved_by": "...",
#   "tmp_path": "...",
#   "pipeline_events": [],     ← SSE event buffer
#   "pipeline_done": False,
#   "result": None,
# }
SESSIONS: dict[str, dict] = {}


def _get_session(session_id: str) -> dict:
    if session_id not in SESSIONS:
        raise HTTPException(status_code=404, detail="Session not found")
    return SESSIONS[session_id]


# ─────────────────────────────────────────────
# Pages
# ─────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


# ─────────────────────────────────────────────
# API — Upload & start pipeline
# ─────────────────────────────────────────────

@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    meeting_date: str = Form(default=""),
    reviewer: str = Form(default="user"),
):
    session_id = str(uuid.uuid4())
    suffix = Path(file.filename).suffix if file.filename else ".txt"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    content = await file.read()
    tmp.write(content)
    tmp.close()

    meeting_date = meeting_date or date.today().isoformat()

    SESSIONS[session_id] = {
        "config":          {"configurable": {"thread_id": str(uuid.uuid4())}},
        "state":           {},
        "action_items":    [],
        "meeting_record":  {},
        "meeting_date":    meeting_date,
        "approved_by":     reviewer,
        "tmp_path":        tmp.name,
        "pipeline_events": [],
        "pipeline_done":   False,
        "participant_map": {},   # speaker_label → github_login
        "result":          None,
        "errors":          [],
    }

    # Kick off pipeline in background
    asyncio.create_task(_run_pipeline(session_id))

    return {"session_id": session_id, "filename": file.filename}


async def _run_pipeline(session_id: str):
    """Run the LangGraph pipeline up to the human_review interrupt."""
    sess = SESSIONS[session_id]

    def _emit(event_type: str, data: dict):
        sess["pipeline_events"].append({"type": event_type, "data": data})

    try:
        initial_state = {
            "transcript_path":    sess["tmp_path"],
            "transcript_raw":     "",
            "meeting_date":       sess["meeting_date"],
            "participant_roster": [],
            "approved_by":        sess["approved_by"],
            "utterances":         [],
            "action_items":       [],
            "errors":             [],
            "warnings":           [],
        }

        graph = get_graph()
        config = sess["config"]

        step_labels = [
            ("ingest",   "Ingesting transcript…"),
            ("extract",  "Extracting action items…"),
            ("resolve",  "Resolving owners & dates…"),
            ("review",   "Ready for review"),
        ]

        _emit("step", {"step": 0, "label": "Starting pipeline…"})

        final_state = None
        for n, event in enumerate(graph.stream(initial_state, config=config, stream_mode="values")):
            final_state = event

            errs = event.get("errors", [])
            if errs:
                _emit("error", {"message": errs[-1]})
                sess["errors"] = errs
                break

            if n < len(step_labels):
                key, label = step_labels[n]
                _emit("step", {"step": n + 1, "label": label, "key": key})

            # Small yield so async loop can breathe
            await asyncio.sleep(0)

        if final_state and not final_state.get("errors"):
            mr_raw = final_state.get("meeting_record_raw")
            mr = mr_raw.model_dump() if hasattr(mr_raw, "model_dump") else (mr_raw or {})
            sess["state"]          = final_state
            sess["action_items"]   = final_state.get("action_items", [])
            sess["meeting_record"] = mr
            sess["warnings"]       = final_state.get("warnings", [])

            # Collect unique speakers
            utterances = final_state.get("utterances", [])
            speakers = list(dict.fromkeys(
                u.get("speaker", "") for u in utterances if u.get("speaker")
            ))
            sess["speakers"] = speakers

            _emit("complete", {
                "action_items":   [_serialize_item(i) for i in sess["action_items"]],
                "meeting_record": mr,
                "speakers":       speakers,
                "warnings":       sess["warnings"],
            })

    except Exception as e:
        _emit("error", {"message": str(e)})
        sess["errors"] = [str(e)]
    finally:
        sess["pipeline_done"] = True


def _serialize_item(item) -> dict:
    """Safely convert an ActionItem or dict to a JSON-serialisable dict."""
    if hasattr(item, "model_dump"):
        d = item.model_dump()
    elif isinstance(item, dict):
        d = dict(item)
    else:
        d = {}

    # Normalise enums / dates
    for k in ["status", "priority"]:
        if hasattr(d.get(k), "value"):
            d[k] = d[k].value
    for k in ["resolved_date"]:
        if d.get(k) and not isinstance(d[k], str):
            d[k] = str(d[k])
    return d


# ─────────────────────────────────────────────
# API — SSE pipeline progress
# ─────────────────────────────────────────────

@app.get("/api/pipeline/{session_id}")
async def pipeline_events(session_id: str):
    sess = _get_session(session_id)

    async def _generator() -> AsyncGenerator[str, None]:
        sent = 0
        while True:
            events = sess["pipeline_events"]
            while sent < len(events):
                ev = events[sent]
                yield f"data: {json.dumps(ev)}\n\n"
                sent += 1
            if sess["pipeline_done"]:
                yield "data: {\"type\": \"done\"}\n\n"
                break
            await asyncio.sleep(0.3)

    return StreamingResponse(_generator(), media_type="text/event-stream",
                              headers={"Cache-Control": "no-cache",
                                       "X-Accel-Buffering": "no"})


# ─────────────────────────────────────────────
# API — Session state (polling fallback)
# ─────────────────────────────────────────────

@app.get("/api/state/{session_id}")
async def get_state(session_id: str):
    sess = _get_session(session_id)
    return {
        "action_items":   [_serialize_item(i) for i in sess.get("action_items", [])],
        "meeting_record": sess.get("meeting_record", {}),
        "speakers":       sess.get("speakers", []),
        "warnings":       sess.get("warnings", []),
        "pipeline_done":  sess.get("pipeline_done", False),
        "errors":         sess.get("errors", []),
    }


# ─────────────────────────────────────────────
# API — GitHub collaborators
# ─────────────────────────────────────────────

@app.get("/api/collaborators")
async def collaborators():
    collabs = get_repo_collaborators()
    return {"collaborators": collabs}


# ─────────────────────────────────────────────
# API — Save participant mapping
# ─────────────────────────────────────────────

class MappingPayload(BaseModel):
    session_id: str
    mapping: dict[str, str | None]   # speaker_label → github_login or null


@app.post("/api/confirm-mapping")
async def confirm_mapping(payload: MappingPayload):
    sess = _get_session(payload.session_id)
    sess["participant_map"] = payload.mapping

    # Backfill github_username into action items immediately
    mapping = payload.mapping   # { "Eggomelette": "eggo-gh", ... }
    items = sess.get("action_items", [])
    for item in items:
        raw_owner = (item.get("raw_owner") or "").strip()
        if not raw_owner:
            continue
        # Case-insensitive match
        for speaker, login in mapping.items():
            if raw_owner.lower() == speaker.lower():
                if item.get("resolved_owner") is None:
                    item["resolved_owner"] = {
                        "name": speaker,
                        "email": "",
                        "github_username": login,
                        "match_score": 1.0,
                        "resolution_method": "participant_map",
                    }
                else:
                    item["resolved_owner"]["github_username"] = login
                break

    sess["action_items"] = items
    return {"ok": True}


# ─────────────────────────────────────────────
# API — Execute (create GitHub issues)
# ─────────────────────────────────────────────

class ExecutePayload(BaseModel):
    session_id: str
    approved: list[dict]
    rejected: list[dict]


@app.post("/api/execute")
async def execute(payload: ExecutePayload):
    sess = _get_session(payload.session_id)
    config = sess["config"]

    try:
        graph = get_graph()
        final = graph.invoke(
            Command(resume={"approved": payload.approved, "rejected": payload.rejected}),
            config=config,
        )
        sess["result"] = final
        created = final.get("created_issues", [])
        skipped = final.get("skipped_duplicates", [])
        return {
            "ok":      True,
            "created": created,
            "skipped": skipped,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─────────────────────────────────────────────
# API — Download Markdown report
# ─────────────────────────────────────────────

@app.get("/api/report/{session_id}")
async def download_report(session_id: str):
    sess = _get_session(session_id)
    mr   = sess.get("meeting_record", {})
    ai   = sess.get("action_items", [])
    result = sess.get("result") or {}
    date_s = sess.get("meeting_date", str(date.today()))

    lines = [
        f"# MeetingMind Report — {date_s}",
        f"\n*Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}*\n",
        "---",
        "\n## Meeting Summary\n",
        mr.get("summary", "_No summary available._"),
    ]

    for decision in mr.get("decisions", []):
        text = decision.get("decision", str(decision)) if isinstance(decision, dict) else str(decision)
        lines.append(f"- **{text}**")

    created = result.get("created_issues", [])
    if created:
        lines += ["\n## GitHub Issues Created\n"]
        for i in created:
            url   = i.get("task_url") or i.get("issue_url", "#")
            title = i.get("title", "Issue")
            lines.append(f"- [{title}]({url})")

    lines += ["\n---", "\n*Report generated by MeetingMind*"]
    content = "\n".join(lines)

    from fastapi.responses import Response
    return Response(
        content=content.encode("utf-8"),
        media_type="text/markdown",
        headers={"Content-Disposition": f"attachment; filename=meetingmind_{date_s}.md"},
    )
