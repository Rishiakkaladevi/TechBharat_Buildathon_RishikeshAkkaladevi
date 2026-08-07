# MeetingMind — AI-Powered Meeting Action Tracker

MeetingMind is an intelligent, end-to-end meeting assistant that bridges the gap between raw meeting transcripts and structured engineering workflows. It ingests transcripts in multiple formats, extracts action items, key decisions, and open questions using state-of-the-art LLMs, and automatically syncs the approved results directly to your GitHub repository as formatted issues — all through a beautifully designed review dashboard.

---

## Table of Contents

- [Features](#features)
- [Tech Stack & Libraries](#tech-stack--libraries)
- [Project Structure](#project-structure)
- [How It Works — The Pipeline](#how-it-works--the-pipeline)
- [Setup & Installation](#setup--installation)
- [Environment Variables & API Keys](#environment-variables--api-keys)
- [Running the App](#running-the-app)
- [Using the Dashboard](#using-the-dashboard)

---

## Features

- **Intelligent Extraction** — Uses Groq's LLaMA 3.3 70B to accurately extract Action Items, Key Decisions, and Open Questions from messy raw transcripts in one structured LLM call.
- **Speaker Discovery & Mapping** — Automatically detects speakers from the transcript (e.g. `Speaker 0`) and lets you map them to real GitHub collaborators and rename them before action items are finalized.
- **Smart Two-Layer Deduplication** — Prevents duplicate GitHub issues by checking both a local SHA-256 audit log (SQLite) and live GitHub search using fuzzy title matching (`RapidFuzz`).
- **Human-in-the-Loop Review** — Before anything is committed, you get a full review dashboard to approve, reject, or edit every item, including assignee, due date, and priority.
- **Toggle Approve/Reject Buttons** — Clickable, stateful per-item approve/reject buttons with orange highlight on selection. Clicking an active button reverts the item back to pending.
- **Calendar Widget** — A compact visual calendar showing task deadlines as colored dots (by priority), with hover tooltips showing task titles and assignees.
- **GitHub Issue Creation** — Approved items are pushed directly to GitHub as cleanly formatted issues with all relevant metadata (assignee, due date, priority, description, evidence quote).
- **Open Questions Attribution** — Open questions extracted from the meeting are displayed with the name of the person who asked them.
- **Markdown Report Export** — Download a full meeting report (action items, decisions, open questions) as a portable `.md` file.
- **Audit Log** — Every successfully created GitHub issue is recorded in a local SQLite database with timestamps and item IDs.

---

## Tech Stack & Libraries

| Layer | Library | Purpose |
|---|---|---|
| **Frontend** | [Streamlit](https://streamlit.io/) | Interactive dashboard, file upload, UI rendering |
| **Pipeline Orchestration** | [LangGraph](https://python.langchain.com/docs/langgraph) | Stateful, agentic workflow management |
| **LLM Inference** | [Groq](https://groq.com/) (`llama-3.3-70b-versatile`) | Fast structured extraction from transcripts |
| **Data Validation** | [Pydantic](https://docs.pydantic.dev/) | Strict typing and schema enforcement for extracted data |
| **GitHub Integration** | [PyGitHub](https://pygithub.readthedocs.io/) | Creating issues and reading repository collaborators |
| **Fuzzy Matching** | [RapidFuzz](https://rapidfuzz.github.io/RapidFuzz/) | Deduplication via fuzzy title similarity scoring |
| **Token Counting** | [tiktoken](https://github.com/openai/tiktoken) | Chunking transcripts that exceed LLM context windows |
| **Local Storage** | SQLite (via `sqlite3`) | Audit log for tracking all previously synced items |
| **Date Parsing** | [python-dateutil](https://dateutil.readthedocs.io/) | Converting relative dates ("next Friday") to ISO format |

---

## Project Structure

```text
meeting-assistant/
│
├── app.py                      # Main Streamlit Frontend — UI, review dashboard, execution
├── graph.py                    # LangGraph pipeline — defines nodes and state transitions
├── requirements.txt            # All Python dependencies
├── .env                        # Secrets & configuration (NOT committed to Git)
│
├── core/                       # Core intelligence layer
│   ├── models.py               # Pydantic schemas: MeetingRecordRaw, ActionItem,
│   │                           #   Decision, OpenQuestion, MeetingState, etc.
│   ├── ingestion.py            # File reader — handles .txt, .vtt, .srt formats,
│   │                           #   strips formatting, normalizes transcript text
│   └── extraction.py          # Groq LLM caller — prompts, chunking, JSON parsing,
│                               #   and merging results from multiple chunks
│
├── integrations/               # External service connectors
│   ├── base.py                 # Abstract TaskConnector Protocol defining create_task()
│   │                           #   and task_exists() interfaces
│   └── github_connector.py    # GitHub implementation — creates issues, checks for
│                               #   duplicates using fuzzy search, formats issue bodies,
│                               #   manages labels, and records to audit log
│
└── .streamlit/
    └── config.toml             # Custom Streamlit theme — warm parchment color palette,
                                #   Space Grotesk font, dark orange primary accent
```

---

## How It Works — The Pipeline

When you upload a transcript and click **"Process Transcript"**, MeetingMind runs the following LangGraph state machine. Each stage is a discrete node that updates a shared `MeetingState` Pydantic object.

### Stage 1 — Ingestion (`core/ingestion.py`)
Reads the uploaded file and normalizes it into a clean, flat text transcript. For `.vtt` and `.srt` subtitle files, it strips all timestamps, formatting tags, and metadata, leaving only the raw spoken dialogue. The resulting text is stored in `MeetingState.transcript_raw`.

### Stage 2 — Extraction (`core/extraction.py`)
Passes the cleaned transcript to **Groq** (`llama-3.3-70b-versatile`) in **JSON mode**, ensuring the LLM output strictly conforms to our Pydantic schema. The model is prompted to extract:
- **Action Items** — with raw owner name, due date, priority, confidence score, and a verbatim evidence quote
- **Key Decisions** — one-line summaries of decisions made in the meeting
- **Open Questions** — unresolved questions, tagged with who asked them

For long transcripts that exceed the context window, the ingestion is automatically chunked using `tiktoken`, and results from each chunk are merged with deduplication.

### Stage 3 — Resolution (`graph.py`)
Enriches the raw extracted data by resolving fuzzy natural language dates (`"end of Q3"`, `"next Monday"`) into concrete `YYYY-MM-DD` ISO dates using `python-dateutil`. It also maps raw speaker labels from the transcript (e.g. `Speaker 0`) against the participant roster.

### Stage 4 — Deduplication (`integrations/github_connector.py`)
Before the review step, each action item is checked against two sources to see if it already exists:
1. **Local SHA-256 Audit Log (SQLite)** — a hash of the item's content is checked against previously synced items to catch exact re-runs.
2. **Live GitHub Search** — queries `repo:<owner>/<repo> is:issue is:open <title>` and uses `RapidFuzz` with a `WRatio > 85%` threshold to catch near-duplicate manually created issues.

Items flagged as potential duplicates are surfaced as warnings in the review dashboard.

### Stage 5 — Human Review (`app.py`)
The pipeline pauses. The Streamlit frontend displays:
- A **Participant Mapper** panel where you can rename raw speakers and link them to GitHub collaborators
- A **Summary section** showing decisions and open questions extracted from the meeting
- **Action Item cards** for every extracted item, each showing the title, assignee dropdown, priority, due date, and per-item Approve/Reject buttons

### Stage 6 — Execution (`integrations/github_connector.py`)
When you click **"Confirm & Execute"**, every approved item is formatted and pushed to GitHub as a new Issue with:
- Title from the extraction
- A body containing: Issue Name, Assignee, Submission Date, Due Date, Priority, Confidence, Description, and Evidence Quote
- Correct GitHub assignee set via `@username`
- Labels auto-created if they don't exist (`priority:high`, `meeting-action-item`, etc.)
- A final entry written to the local SQLite audit log

---

## Setup & Installation

### Prerequisites
- Python **3.10** or higher
- Git

### Step 1 — Clone & Create a Virtual Environment

```bash
git clone https://github.com/YourUsername/meeting-assistant.git
cd meeting-assistant

# Create a virtual environment
python -m venv venv

# Activate it
venv\Scripts\activate        # Windows
source venv/bin/activate     # macOS / Linux
```

### Step 2 — Install Dependencies

```bash
pip install -r requirements.txt
```

> **Note:** The virtual environment will use approximately **415 MB** of disk space due to ML and NLP libraries.

---

## Environment Variables & API Keys

MeetingMind requires a `.env` file in the root directory. Create one and fill in the following:

```ini
# ─── Groq (LLM Inference) ─────────────────────────────────────────────────────
# Used for all AI extraction (action items, decisions, open questions)
# Get your free key at: https://console.groq.com/keys
GROQ_API_KEY="gsk_your_groq_api_key_here"

# ─── GitHub Integration ───────────────────────────────────────────────────────
# A Personal Access Token (Classic) with `repo` scope.
# This is needed to:
#   - Read the list of repository collaborators (for participant mapping)
#   - Create Issues with correct assignees and labels
# Create one at: https://github.com/settings/tokens → Generate new token (classic)
# Required scopes: repo (full)
GITHUB_TOKEN="ghp_your_github_token_here"

# The target repository where issues will be created (format: owner/repo)
GITHUB_REPO="YourUsername/YourRepoName"

# ─── Optional: LangSmith Tracing (for debugging LangGraph) ────────────────────
# Uncomment these if you want to trace pipeline runs in LangSmith
# LANGCHAIN_TRACING_V2="true"
# LANGCHAIN_API_KEY="ls__your_langsmith_key"
# LANGCHAIN_PROJECT="MeetingMind"
```

### Where to Get Each Key

| Key | Where to Get It | Required? |
|---|---|---|
| `GROQ_API_KEY` | [console.groq.com/keys](https://console.groq.com/keys) — Free tier available | **Yes** |
| `GITHUB_TOKEN` | [github.com/settings/tokens](https://github.com/settings/tokens) — Classic token, `repo` scope | **Yes** |
| `GITHUB_REPO` | Your repository's `owner/repo` path (e.g. `acme-corp/backend`) | **Yes** |
| `LANGCHAIN_API_KEY` | [smith.langchain.com](https://smith.langchain.com) — For tracing/debugging | No |

---

## Running the App

Once your `.env` is configured and dependencies are installed:

```bash
streamlit run app.py
```

The dashboard will open automatically at `http://localhost:8501`.

---

## Using the Dashboard

1. **Upload a transcript** — Drag and drop a `.txt`, `.vtt`, or `.srt` file (e.g. a Zoom or Deepgram export).
2. **Set the meeting date** — Select the actual meeting date using the date picker.
3. **Click "Process Transcript"** — The AI pipeline runs. A live status indicator walks you through each stage.
4. **Map Participants** — In the "Participant → GitHub Mapping" panel, rename raw speaker labels (e.g. `Speaker 0`) to real names, and link them to GitHub collaborators via dropdown.
5. **Review Action Items** — Check each extracted card. Edit the assignee or due date if needed. Click **Approve** (turns orange) or **Reject** on each item. Clicking again deselects and returns it to Pending.
6. **Click "Confirm & Execute"** — All approved items are pushed to GitHub as Issues. A summary of what was created appears at the top of the page.
7. **Download Report** — Export the full meeting summary as a `.md` file using the Download button.

---

## Model Configuration

MeetingMind relies on specific AI models for transcription and data extraction. These are hardcoded to optimal defaults, but can be easily swapped if you prefer different providers or models.

### 1. LLM Extraction (Groq)
By default, the pipeline uses Groq's **`llama-3.3-70b-versatile`** model for extracting action items, decisions, and questions from the transcript. This model was chosen for its 32k context window and lightning-fast JSON generation.

To change the LLM model:
1. Open `core/extraction.py`.
2. Locate the `client.chat.completions.create` function calls (used in both chunked extraction and merging).
3. Change the `model="llama-3.3-70b-versatile"` parameter to your preferred Groq model (e.g., `llama3-8b-8192` or `mixtral-8x7b-32768`).

### 2. Audio Transcription (Deepgram)
When you upload raw audio or video files (`.mp3`, `.mp4`, `.wav`), the app uses Deepgram's **`nova-3`** model with `smart_format` and `diarize` enabled. 

To adjust the transcription model or language:
1. Open `core/ingestion.py`.
2. Locate the `_transcribe_audio_deepgram` function.
3. Modify the API payload options:
   ```python
   payload = {
       "model": "nova-3",    # Swap to "nova-2" or "whisper"
       "smart_format": True,
       "diarize": True,      # Required for speaker mapping
       # "language": "es",   # Uncomment to force a specific language
   }
   ```

