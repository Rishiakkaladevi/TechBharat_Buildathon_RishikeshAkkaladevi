# 🚀 MeetingMind — How to Run

> All commands must be run from the **`meeting-assistant/`** folder, not the parent `buildathon/` folder.

---

## Step 1 — Navigate to the Project Folder

```powershell
cd "C:\Users\Rishikesh Akkaladevi\Desktop\Desktop\coding\buildathon\meeting-assistant"
```

Confirm you're in the right place:
```powershell
ls
# You should see: app.py, graph.py, core/, integrations/, venv/, requirements.txt, etc.
```

---

## Step 2 — Activate the Virtual Environment

```powershell
.\venv\Scripts\activate
```

Your terminal prompt will change to show `(venv)` — this means you're using the project's isolated Python, not your system Python.

```
# Before:  PS C:\...\meeting-assistant>
# After:   (venv) PS C:\...\meeting-assistant>
```

---

## Step 3 — Set Up API Keys (one-time)

```powershell
copy .env.example .env
```

Then open `.env` and fill in your keys:

```
GROQ_API_KEY=gsk_...          ← from console.groq.com (free)
GITHUB_TOKEN=ghp_...          ← from github.com/settings/tokens → repo scope
GITHUB_REPO=yourname/repo     ← create an empty private repo for this demo
```

Deepgram and Slack are optional — leave them blank if not using audio/Slack.

---

## Step 4 — Dry Run (no GitHub writes, tests pipeline locally)

```powershell
python dry_run.py
```

**What this checks:**
- `[OK] Ingestion` — SRT parser works, 18 utterances extracted from sample transcript
- `[OK] Resolution` — fuzzy owner matching + date resolving work
- `[!!] Extraction` — skipped until GROQ_API_KEY is set
- `[OFF] github / slack` — shown as disabled until API keys are set

This is safe — it writes nothing to GitHub.

---

## Step 5 — Run the Full App

```powershell
streamlit run app.py
```

Browser opens automatically at `http://localhost:8501`.

---

## Step 6 — Use the App (3-page workflow)

### Page 1 — Upload & Configure

1. Click **Browse files** → upload a transcript
   - Use `tests/sample_meeting.srt` to test with the demo transcript
   - Or upload any real `.txt`, `.vtt`, `.srt` file
   - Audio `.mp3/.mp4/.wav` requires `DEEPGRAM_API_KEY`

2. Set the **Meeting Date** (used to resolve relative dates like "next Friday")

3. Paste your **Participant Roster** — one person per line:
   ```
   Alice Chen, alice@company.com, alice-gh
   Bob Kumar, bob@company.com, bob-gh
   Priya Sharma, priya@company.com, priya-sh
   ```

4. Enter your **Reviewer Name** (logged in the audit trail)

5. Click **⚡ Process Meeting**

   > The pipeline now runs: Ingestion → Extraction (Groq LLM) → Resolution
   > Progress is shown in the UI. This takes ~30–90 seconds depending on transcript length.

---

### Page 2 — Review & Approve

The graph has **paused** and is waiting for your decision. Nothing has been written to GitHub yet.

**Left panel** — the full meeting record:
- Executive summary
- Decisions made
- Open questions
- Risks flagged

**Right panel** — one card per action item:
- Owner: pre-filled from resolution, editable dropdown
- Due date: pre-filled, editable date picker
- Priority: `high / medium / low`
- Evidence: expandable quote from the transcript
- Status: set each item to `approved ✅` or `rejected ❌`

**Sidebar** shows a live preview:
> *"Will create 3 GitHub Issues · Reject 1 · Skip 0 duplicates"*

Click **🚀 Confirm & Execute** when ready.

> **Nothing happens until this button is clicked.** The LangGraph graph is interrupted at the `human_review` node and cannot proceed without your input.

---

### After Execution

- GitHub Issues are created in parallel for all approved items
- Each issue has: title, description, owner (assignee), due date label, priority label, evidence quote, and a link back to the meeting date
- If Slack is configured, a recap is posted to your channel
- The Streamlit UI shows the list of created issue URLs

---

### Page 3 — Audit Log

- Full history of every agent action (creates, skips, rejects)
- Filterable by meeting ID, event type
- Clickable GitHub issue links
- Download as JSON

---

## Running the Same Transcript Again (Dedup Test)

Process the exact same file again:
1. Upload → Process → Approve everything

**Expected result**: 0 new GitHub issues created, all items show as "Skipped (duplicate)"

This is the idempotency guarantee — `sha256(meeting_id + title + owner_email)` is checked before every write.

---

## Common Issues

| Problem | Fix |
|---|---|
| `GROQ_API_KEY not set` | Make sure `.env` exists in `meeting-assistant/`, not `buildathon/` |
| `copy .env.example` fails | You're in the wrong folder — run `cd meeting-assistant` first |
| `venv\Scripts\activate` not found | Recreate venv: `python -m venv venv` |
| Port 8501 already in use | `streamlit run app.py --server.port 8502` |
| Import errors | Make sure venv is activated — prompt should show `(venv)` |

---

## Full Command Sequence (copy-paste)

```powershell
# Navigate to project
cd "C:\Users\Rishikesh Akkaladevi\Desktop\Desktop\coding\buildathon\meeting-assistant"

# Activate venv
.\venv\Scripts\activate

# One-time: copy config template and fill in keys
copy .env.example .env
# (Open .env and add your GROQ_API_KEY, GITHUB_TOKEN, GITHUB_REPO)

# Test pipeline locally (no API keys needed for basic test)
python dry_run.py

# Run the app
streamlit run app.py
```
