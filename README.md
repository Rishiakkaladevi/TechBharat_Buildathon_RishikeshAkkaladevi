# 🤖 MeetingMind — Agentic AI Meeting Assistant

> *Transcripts capture words. MeetingMind captures commitments — and acts on them.*

---

## What is this?

Most meeting tools stop at transcription. A transcript is just the meeting again in text form — nobody reads it.

**MeetingMind** closes the gap between what was *said* and what gets *done*. It reads a meeting transcript, extracts every commitment made by a named person, resolves who owns it and when it's due, and creates real tasks in GitHub — after a human reviews and approves every single one.

It is an **agent**, not a summarizer. The difference: it takes action in the real world.

---

## The Full Workflow

```
You drop in a transcript (.srt / .vtt / .txt / audio)
        │
        ▼
┌───────────────────┐
│  1. INGESTION     │  Parse file → [{speaker, timestamp, text}]
└────────┬──────────┘  Deepgram for audio. Native parsers for text.
         │
         ▼
┌───────────────────┐
│  2. EXTRACTION    │  Groq LLM (llama-3.3-70b) reads transcript
└────────┬──────────┘  → summary, decisions, action items, risks, questions
         │              Each item has: owner, due date, confidence, evidence quote
         │
         ▼
┌───────────────────┐
│  3. RESOLUTION    │  "Alice" → alice@company.com  (RapidFuzz)
└────────┬──────────┘  "by next Friday" → 2026-08-08  (LLM primary resolver)
         │              Unresolvable → flagged for human, never silently guessed
         │
         ▼
┌───────────────────┐
│  4. HUMAN REVIEW  │  ⚡ LangGraph INTERRUPT — everything pauses here
└────────┬──────────┘  Streamlit UI: edit owners, dates, priorities
         │  [You press Confirm → graph resumes]
         │
         ▼
┌───────────────────┐
│  5. DEDUP CHECK   │  sha256(meeting_id + title + owner_email) lookup
└────────┬──────────┘  Already exists in audit log → skipped, not duplicated
         │
         ▼
┌───────────────────┐
│  6. EXECUTION     │  Connector registry calls all enabled integrations
└────────┬──────────┘  GitHub Issues created in parallel (asyncio)
         │              Slack recap posted. Runs same file again → zero duplicates.
         │
         ▼
┌───────────────────┐
│  7. AUDIT LOG     │  Every action: what, who approved, when, GitHub URL
└───────────────────┘  JSONL (immutable) + SQLite (queryable)
```

---

## LangGraph Architecture

The pipeline is a compiled `StateGraph`. Each stage is a typed node, edges are explicit, and `interrupt_before=["human_review"]` is the human-approval gate.

```
START
  │
  ▼
[ingest] ──→ [extract] ──→ [resolve]
                                │
                    ┌───────────┴───────────┐
                    │  conditional edge     │
               has_flags?                clean?
                    │                       │
                    ▼                       │
            [add_warnings] ────────────────▶│
                                            ▼
                                    [human_review]  ← ⚡ INTERRUPT
                                            │
                                    [dedup_check]
                                            │
                                    [execute_tools]  ← calls all enabled connectors
                                            │
                                     [audit_log]
                                            │
                                          END
```

| LangGraph Feature | How we use it |
|---|---|
| **`StateGraph`** | Typed `dict` state flows through every node |
| **`interrupt_before`** | Graph pauses before `human_review`, resumes on `Command(resume=...)` |
| **Conditional edges** | Route flagged items (unresolved owner/date) through `add_warnings` |
| **`SqliteSaver` checkpointer** | App restart mid-review? Graph resumes where it left off |
| **`graph.stream()`** | Streamlit shows real-time per-node progress |

---

## Connector Architecture

Every integration implements the `TaskConnector` protocol. The `execute_tools` node calls **all enabled connectors** — no code change needed to add a new one.

```
integrations/
├── base.py              ← TaskConnector Protocol + Registry
├── github_connector.py  ← GitHub Issues (enabled if GITHUB_TOKEN set)
├── slack_connector.py   ← Slack recap (enabled if SLACK_WEBHOOK_URL set)
└── [future]
    ├── linear_connector.py
    └── jira_connector.py
```

**Adding a new connector = 3 steps:**
1. Create `integrations/your_connector.py` implementing `TaskConnector`
2. Register it in `integrations/base.py` (one line)
3. Set the API key in `.env`

Zero changes to `graph.py`, `app.py`, or anything else.

---

## Resolution Pipeline

### Owner Resolution (3-layer)

```
"Alice"             → fuzzy match score 97  → Alice Chen <alice@company.com>  [CLEAN]
"Priya S"           → fuzzy match score 88  → Priya Sharma <priya@company.com> [CLEAN]
"the backend team"  → fuzzy match score 42  → LLM fallback with transcript context [FLAGGED]
"someone from legal"→ LLM also fails       → Human must assign [FAILED → UI warning]
```

| Layer | Method | Threshold |
|---|---|---|
| 1 | RapidFuzz `WRatio` | score ≥ 85 → CLEAN |
| 2 | Groq LLM with transcript context | score 55–84 → try LLM → FLAGGED |
| 3 | Human review | score < 55 or LLM fails → FAILED |

### Date Resolution (4-layer)

```
"ASAP" / "soon"      → immediately FAILED (no LLM wasted)
"2026-08-14"         → exact ISO date, no API call needed
"end of quarter"     → custom phrase map → 2026-09-30 [CLEAN]
"by next Friday"     → LLM PRIMARY: meeting date Wed Aug 6 → 2026-08-08 [CLEAN]
"in the near future" → LLM fails → Human sets manually [FAILED]
```

**LLM is the primary resolver for all ambiguous dates.** It receives:
- Meeting date and day of week (e.g. `"2026-08-06 (Wednesday)"`)
- The raw phrase (`"by next Friday"`)
- Transcript context (evidence quote)

This correctly handles: `"by next friday"`, `"end of this week"`, `"before end of quarter"`, `"next sprint"`, etc.

---

## Step-by-Step Breakdown

### Step 1 — Ingestion

| Input | Parser |
|---|---|
| `.srt` | `parse_srt()` — SubRip timestamps + `"Speaker: text"` labels |
| `.vtt` | `parse_vtt()` — WebVTT cues with `<v Speaker>` tags |
| `.txt` | `parse_txt()` — multiple pattern matchers for common formats |
| `.mp3/.mp4/.wav` | Deepgram `nova-3` model — transcription + diarization |

Output is a normalized `[{speaker, timestamp, text}]` list. Consecutive utterances from the same speaker are merged.

---

### Step 2 — LLM Extraction

**Single Groq call** with JSON mode for deterministic structured output.

Extracts:
- `summary` — 3–5 sentence executive summary
- `decisions` — what was decided, with context + timestamp
- `open_questions` — raised but unresolved
- `risks` — blockers mentioned
- `action_items` — every genuine commitment with confidence score + evidence quote

**Confidence scoring**:
| Score | Meaning |
|---|---|
| ≥ 0.9 | Strong explicit commitment: *"I will do X by Friday"* |
| 0.7–0.9 | Clear commitment with minor ambiguity |
| 0.5–0.7 | Possible commitment, not fully confirmed |
| < 0.5 | Not extracted |

**Long transcripts**: Chunked at 28k tokens with 1.5k overlap. Chunks merged with RapidFuzz dedup (title similarity > 85% → duplicate).

---

### Step 3 — Resolution

See [Resolution Pipeline](#resolution-pipeline) above.

All resolution happens before human review. Flagged items surface as warnings in the UI — they are not silently dropped or guessed.

---

### Step 4 — Human Review (Streamlit)

Three-page Streamlit app:

**Page 1 — Upload & Configure**
- File upload (drag & drop)
- Meeting date picker
- Participant roster (paste CSV: `name, email, github_username`)
- Reviewer name (logged in audit trail)

**Page 2 — Review & Approve**
- Left: meeting summary, decisions, open questions, risks
- Right: per-item editor
  - Owner dropdown (from roster)
  - Due date picker (pre-filled from resolution)
  - Priority selector
  - Evidence quote (expandable)
  - Status: `pending / approved / rejected`
- Sidebar: live preview of what will happen ("Will create 3 GitHub Issues")
- **Confirm & Execute** — nothing runs before this button

**Page 3 — Audit Log**
- Full filterable history of all agent actions
- Clickable GitHub issue links
- JSON download

---

### Step 5 — Dedup Check

Before any issue is created:

```python
dedup_key = sha256(meeting_id + title.lower() + owner_email.lower())
```

1. Check internal audit log (SQLite) — instant
2. Check GitHub directly via search API — safety net

Same transcript run twice → zero new issues created.

---

### Step 6 — Execution

All approved items run **in parallel** (`asyncio.gather`) across all enabled connectors.

For each approved item per connector:
- GitHub: creates issue with rich body, assignee, labels (`priority:high`, `meeting-action-item`)
- Slack: posts recap after all items processed
- Returns `{connector, item_id, task_url, success, error}`

All results logged to audit trail immediately.

---

### Step 7 — Audit Log

Dual-write: **JSONL** (append-only, immutable source of truth) + **SQLite** (indexed for queries).

Every entry records:
```
event        → "github_issue_created" | "skipped_duplicate" | "item_rejected"
meeting_id   → sha256 of transcript content
item_id      → "ai_001"
title        → action item title
owner_email  → resolved email
dedup_key    → sha256 idempotency key
external_ref → GitHub issue URL
approved_by  → reviewer name
approved_at  → UTC timestamp
payload      → exact data sent to GitHub
```

---

## Project Structure

```
meeting-assistant/
│
├── app.py                    ← Streamlit entry point (3 pages)
├── graph.py                  ← LangGraph StateGraph + all node wiring
├── config.py                 ← Env var loader + validation
├── dry_run.py                ← Pipeline test script (no GitHub writes)
│
├── core/
│   ├── models.py             ← All Pydantic schemas (MeetingState, ActionItem, etc.)
│   ├── ingestion.py          ← .srt/.vtt/.txt parsers + Deepgram audio
│   ├── extraction.py         ← Groq LLM call, chunking, merging
│   └── resolution.py         ← Owner fuzzy match + LLM date resolver
│
├── integrations/
│   ├── base.py               ← TaskConnector Protocol + get_enabled_connectors()
│   ├── github_connector.py   ← GitHub Issues (implements TaskConnector)
│   └── slack_connector.py    ← Slack recap (implements TaskConnector)
│
├── storage/
│   └── audit_log.py          ← JSONL + SQLite dual-write audit log
│
├── tests/
│   └── sample_meeting.srt    ← Sample transcript for dry run
│
├── data/                     ← Auto-created at runtime
│   ├── audit.db              ← SQLite (dedup + queries)
│   └── audit_log.jsonl       ← Immutable event log
│
├── venv/                     ← Python virtual environment
├── .env                      ← Your API keys (gitignored)
├── .env.example              ← Template — copy this
├── .gitignore
└── requirements.txt
```

---

## Tech Stack

| Component | Technology | Why |
|---|---|---|
| **Agent orchestration** | LangGraph 1.2 | Typed state graph, native HITL interrupt, SQLite checkpointing |
| **LLM** | Groq `llama-3.3-70b-versatile` | 100+ tok/s free inference — 45-min meeting in < 90s |
| **Transcription** | Deepgram `nova-3` | Diarization + speaker labels out of the box |
| **Review UI** | Streamlit 1.61 | Python-native, inline editors, rapid iteration |
| **Task tracker** | GitHub Issues (PyGithub) | Single token, 1 line to create, issue URL in response |
| **Notifications** | Slack Webhooks | No OAuth, zero friction |
| **Owner matching** | RapidFuzz | Fast fuzzy string matching with confidence scores |
| **Date resolution** | Groq LLM (primary) + custom rules | LLM handles all ambiguous relative dates |
| **Schemas** | Pydantic v2 | Type-safe state across all nodes |
| **Audit storage** | JSONL + SQLite | Portable, no infra, append-only guarantee |
| **Checkpointing** | `langgraph-checkpoint-sqlite` | Resume graph if app restarts mid-review |
| **Token counting** | tiktoken | Groq chunk-size guard before sending |

---

## Key Design Decisions

### 1. No unapproved side effects — ever
The LangGraph `interrupt_before=["human_review"]` guarantees the graph **cannot reach** `execute_tools` without human input. Not just a UI convention — enforced at the graph level.

### 2. LLM for dates, not just dateparser
Relative date language (`"by next sprint"`, `"before the compliance deadline"`, `"let's aim for Q3"`) is fundamentally ambiguous and context-dependent. The LLM gets the meeting date, day of week, and full transcript context — dateparser cannot.

### 3. Fail loudly, never silently
Owner unresolvable → flagged with warning, not assigned to a random person. Date unresolvable → shown as unset in the UI, not defaulted to today. Every failure is visible.

### 4. Connector registry, not hardcoded integrations
`execute_tools` calls `get_enabled_connectors()` — it does not know or care which connectors exist. Adding Linear tomorrow requires zero changes to the graph or app.

### 5. Idempotency as a first-class guarantee
`sha256(meeting_id + title + owner_email)` is computed before any write. The same meeting processed twice produces exactly zero additional issues. Logged either way.

### 6. Audit log is the source of truth
JSONL is append-only. SQLite is just an index. Even if the database is corrupted, the full history can be reconstructed from the JSONL file.

---

## Running the Project

```powershell
# 1. Clone and set up venv
git clone <repo>
cd meeting-assistant
python -m venv venv
.\venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure API keys
copy .env.example .env
# Edit .env — fill in GROQ_API_KEY, GITHUB_TOKEN, GITHUB_REPO

# 4. Dry run (no API keys needed for ingestion + resolution test)
python dry_run.py

# 5. Run the full app
streamlit run app.py
```

---

## Success Metrics (from brief)

| Metric | Target | Our approach |
|---|---|---|
| Action item recall | ≥ 80% | Groq LLM with confidence-scored extraction + evidence quotes |
| Action item precision | ≥ 75% | Confidence threshold (≥ 0.5) + human approval gate |
| Owner accuracy | ≥ 85% | RapidFuzz 3-layer resolution, fail-loud below threshold |
| Date resolution | 90% of relative dates | LLM primary resolver with meeting-date + day-of-week context |
| End-to-end latency | < 3 min for 45-min meeting | Groq 100+ tok/s, parallel issue creation (asyncio) |
| Unapproved actions | Exactly zero | `interrupt_before` at graph level, not just UI |
| Duplicate suppression | Zero on re-run | sha256 dedup key checked before every write |
