# ⚙️ MeetingMind — Configuration Guide

All configuration is done through environment variables loaded from a `.env` file at the project root.

```bash
# One-time setup
copy .env.example .env
# Then edit .env with your values
```

---

## Quick Reference

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GROQ_API_KEY` | ✅ Yes | — | LLM extraction, owner resolution, date resolution |
| `DEEPGRAM_API_KEY` | Audio only | — | Transcription + diarization for audio/video files |
| `GITHUB_TOKEN` | ✅ Yes | — | Creates issues in your target repo |
| `GITHUB_REPO` | ✅ Yes | — | Target repo (`username/repo-name`) |
| `SLACK_WEBHOOK_URL` | No | — | Post recap message after execution |
| `MEETING_DB_PATH` | No | `data/audit.db` | SQLite audit log + dedup index |
| `LOG_PATH` | No | `data/audit_log.jsonl` | Append-only JSONL event log |

---

## Variable Details

### `GROQ_API_KEY` — Required

Used for three things in the pipeline:
1. **Extraction** — `llama-3.3-70b-versatile` reads the full transcript and returns structured JSON (summary, decisions, action items)
2. **Owner resolution fallback** — when fuzzy match score is between 55–84%, Groq is asked to resolve with transcript context
3. **Date resolution (primary)** — all ambiguous date phrases (`"by next Friday"`, `"end of sprint"`, `"before the compliance deadline"`) are resolved by Groq using meeting date + day of week context

**Get your key**: [console.groq.com](https://console.groq.com) — free tier is generous (100+ requests/day)

**Model used**: `llama-3.3-70b-versatile`
- Context window: 128k tokens (chunking only needed for very long meetings)
- Speed: ~100 tokens/second — a 45-min meeting processes in under 90 seconds
- JSON mode enabled for all structured output calls

```
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx
```

---

### `DEEPGRAM_API_KEY` — Required only for audio/video input

Used when uploading `.mp3`, `.mp4`, `.wav`, or `.m4a` files.
**Not needed** if you upload `.txt`, `.vtt`, or `.srt` files — those are parsed natively.

**Get your key**: [console.deepgram.com](https://console.deepgram.com) — $200 free credit on signup

**Model used**: `nova-3`
- Features enabled: transcription + speaker diarization (`diarize=True`)
- Output: utterance-level segments with speaker labels
- Speaker labels (`Speaker 0`, `Speaker 1`) are mapped to real names using the participant roster you provide in the UI

```
DEEPGRAM_API_KEY=your_deepgram_api_key_here
```

---

### `GITHUB_TOKEN` — Required

A GitHub Personal Access Token used to create issues in your target repository.

**Generate at**: [github.com/settings/tokens](https://github.com/settings/tokens) → *Generate new token (classic)*

**Scopes needed**: `repo` (full repository access — needed to create issues and labels)

The token is used to:
- Create issues (`repo.create_issue(...)`)
- Create labels if they don't exist (`priority:high`, `priority:medium`, `priority:low`, `meeting-action-item`)
- Search for existing issues (dedup check)

```
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
```

---

### `GITHUB_REPO` — Required

The repository where action item issues will be created.

**Format**: `username/repo-name` or `org-name/repo-name`

**Recommendation**: Create a dedicated empty private repo for the demo — e.g. `yourname/meeting-assistant-demo`. This keeps action items isolated and gives judges a clean view.

```
GITHUB_REPO=yourname/meeting-assistant-demo
```

---

### `SLACK_WEBHOOK_URL` — Optional

If set, MeetingMind posts a recap message to your Slack channel after execution, listing all created GitHub issue links.

**Setup**:
1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → *From scratch*
2. Under *Features*, enable **Incoming Webhooks**
3. Click **Add New Webhook to Workspace** → choose your channel
4. Copy the webhook URL

If not set, Slack posting is silently skipped — no errors.

```
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR_WORKSPACE_ID/YOUR_CHANNEL_ID/YOUR_SECRET_TOKEN
```

---

### `MEETING_DB_PATH` — Optional

Path to the SQLite database file used for:
- **Dedup index**: fast `O(1)` lookup of `dedup_key` before creating any issue
- **Audit log queries**: the Streamlit audit log page reads from this DB

**Default**: `data/audit.db` (created automatically in the project directory)

Change this if you want to persist data outside the project folder or share it across machines.

```
MEETING_DB_PATH=data/audit.db
```

---

### `LOG_PATH` — Optional

Path to the append-only JSONL event log — the **immutable source of truth** for all agent actions.

Every `github_issue_created`, `skipped_duplicate`, and `item_rejected` event is written here immediately. Even if the SQLite database is lost, the full history can be reconstructed from this file.

**Default**: `data/audit_log.jsonl` (created automatically)

```
LOG_PATH=data/audit_log.jsonl
```

---

## Enabling Additional Connectors

MeetingMind uses a **connector registry** — connectors are enabled automatically when their API key is present in `.env`.

| Connector | Enable by setting | Status |
|---|---|---|
| GitHub Issues | `GITHUB_TOKEN` + `GITHUB_REPO` | ✅ Built |
| Slack | `SLACK_WEBHOOK_URL` | ✅ Built |
| Linear | `LINEAR_API_KEY` | 🔧 Add `integrations/linear_connector.py` |
| Jira | `JIRA_TOKEN` + `JIRA_URL` | 🔧 Add `integrations/jira_connector.py` |

To add a new connector — see `integrations/base.py` for the `TaskConnector` protocol.

---

## Minimum Working Setup

To run the full pipeline (upload transcript → extract → review → create GitHub issues):

```
GROQ_API_KEY=...
GITHUB_TOKEN=...
GITHUB_REPO=yourname/meeting-assistant-demo
```

That's all. Everything else is optional.

---

## Running the App

```powershell
# Activate virtual environment
.\venv\Scripts\activate

# Test pipeline without writing to GitHub (no API keys needed for ingestion/resolution)
python dry_run.py

# Launch the full Streamlit app
streamlit run app.py
```

The app will open at `http://localhost:8501`.
