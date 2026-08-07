"""
test_graph.py — Tests graph.stream() directly to expose any silent errors.
"""
import os, sys, traceback
from datetime import date
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

TRANSCRIPT_PATH = Path("tests/sample_meeting.srt")
ROSTER = [
    {"name": "Alice Chen",  "email": "alice@company.com",  "github_username": "alice-gh",  "aliases": ["Alice"]},
    {"name": "Bob Kumar",   "email": "bob@company.com",    "github_username": "bob-gh",    "aliases": ["Bob"]},
    {"name": "Priya Sharma","email": "priya@company.com",  "github_username": "priya-gh",  "aliases": ["Priya"]},
]

import uuid, tempfile, shutil
from graph import get_graph

# Copy SRT to temp file (simulating Streamlit upload)
tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".srt")
shutil.copy(str(TRANSCRIPT_PATH), tmp.name)
tmp.close()

initial_state = {
    "transcript_path":    tmp.name,
    "transcript_raw":     "",
    "meeting_date":       date.today().strftime("%Y-%m-%d"),
    "participant_roster": ROSTER,
    "approved_by":        "test_user",
    "utterances":         [],
    "action_items":       [],
    "errors":             [],
    "warnings":           [],
}

thread_id = str(uuid.uuid4())
config    = {"configurable": {"thread_id": thread_id}}
graph     = get_graph()

print("\n[1] Running graph.stream() until interrupt...\n")
try:
    final_state = None
    for i, event in enumerate(graph.stream(initial_state, config=config, stream_mode="values")):
        final_state = event
        keys = [k for k, v in event.items() if v]
        print(f"  Event {i}: state keys with data = {keys}")

    print("\n[2] Stream completed.")
    if final_state:
        items = final_state.get("action_items", [])
        print(f"  action_items count: {len(items)}")
        for item in items:
            print(f"    - [{item.get('id')}] {item.get('title')} | owner_status={item.get('owner_status')} | date_status={item.get('date_status')}")
        errors = final_state.get("errors", [])
        if errors:
            print(f"\n  ERRORS IN STATE: {errors}")
    else:
        print("  final_state is None — stream produced no events!")

except Exception as e:
    print(f"\n[ERROR] Stream failed:")
    traceback.print_exc()
