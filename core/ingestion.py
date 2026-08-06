"""
Ingestion node — parses transcript files and audio into a clean list of Utterances.

Supports:
  - .txt  (plain text with "Speaker: text" patterns)
  - .vtt  (WebVTT with timestamps and <v Speaker> cues)
  - .srt  (SubRip with timestamps, speaker inferred from labels)
  - audio (.mp3, .mp4, .wav, .m4a) via Deepgram API
"""

from __future__ import annotations

import os
import re
import hashlib
from pathlib import Path
from typing import Optional

from core.models import Utterance, MeetingState


# ─────────────────────────────────────────────
# Format detection
# ─────────────────────────────────────────────

AUDIO_EXTENSIONS = {".mp3", ".mp4", ".wav", ".m4a", ".ogg", ".flac", ".webm"}
TEXT_EXTENSIONS  = {".txt", ".vtt", ".srt"}


def detect_format(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    if ext == ".vtt":
        return "vtt"
    if ext == ".srt":
        return "srt"
    return "txt"


# ─────────────────────────────────────────────
# .VTT parser
# ─────────────────────────────────────────────

def parse_vtt(content: str) -> list[Utterance]:
    """Parse WebVTT format — handles both <v Speaker> cues and plain text blocks."""
    utterances = []
    # Remove BOM and header
    content = content.lstrip("\ufeff").strip()
    lines = content.splitlines()

    timestamp_re = re.compile(
        r"(\d{2}:\d{2}:\d{2}[.,]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[.,]\d{3})"
    )
    speaker_tag_re = re.compile(r"<v ([^>]+)>(.+)")

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Skip WEBVTT header, NOTE blocks, cue ids
        if line.startswith("WEBVTT") or line.startswith("NOTE") or line == "":
            i += 1
            continue

        ts_match = timestamp_re.match(line)
        if ts_match:
            start_ts = _normalize_timestamp(ts_match.group(1))
            i += 1
            # Collect text lines until blank line
            text_lines = []
            while i < len(lines) and lines[i].strip() != "":
                text_lines.append(lines[i].strip())
                i += 1

            full_text = " ".join(text_lines)

            # Check for <v Speaker> tag
            speaker = "UNKNOWN"
            cleaned_text = full_text
            v_match = speaker_tag_re.match(full_text)
            if v_match:
                speaker     = v_match.group(1).strip()
                cleaned_text = _strip_vtt_tags(v_match.group(2).strip())
            else:
                cleaned_text = _strip_vtt_tags(full_text)

            if cleaned_text:
                utterances.append(Utterance(
                    speaker=speaker,
                    timestamp=start_ts,
                    text=cleaned_text
                ))
        else:
            i += 1

    return _merge_consecutive_speakers(utterances)


def _strip_vtt_tags(text: str) -> str:
    """Remove <c>, <b>, <i>, timestamp tags from VTT text."""
    return re.sub(r"<[^>]+>", "", text).strip()


# ─────────────────────────────────────────────
# .SRT parser
# ─────────────────────────────────────────────

def parse_srt(content: str) -> list[Utterance]:
    """Parse SubRip (.srt) format. Speaker labels inferred from 'Name: text' patterns."""
    utterances = []
    content = content.lstrip("\ufeff").strip()

    # SRT blocks: index \n timestamp \n text... \n\n
    blocks = re.split(r"\n\n+", content)
    speaker_prefix_re = re.compile(r"^([A-Z][a-zA-Z\s]{1,30}):\s(.+)$")

    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if len(lines) < 2:
            continue

        # First line: sequence number (skip)
        # Second line: timestamps
        ts_match = re.match(
            r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})",
            lines[1] if lines[0].isdigit() else lines[0]
        )
        if not ts_match:
            continue

        start_ts = _normalize_timestamp(ts_match.group(1))
        text_lines = lines[2:] if lines[0].isdigit() else lines[1:]
        full_text = " ".join(text_lines)

        # Try to detect speaker from "Name: text" pattern
        speaker = "UNKNOWN"
        sp_match = speaker_prefix_re.match(full_text)
        if sp_match:
            speaker   = sp_match.group(1).strip()
            full_text = sp_match.group(2).strip()

        if full_text:
            utterances.append(Utterance(
                speaker=speaker,
                timestamp=start_ts,
                text=full_text
            ))

    return _merge_consecutive_speakers(utterances)


# ─────────────────────────────────────────────
# .TXT parser
# ─────────────────────────────────────────────

def parse_txt(content: str) -> list[Utterance]:
    """
    Parse plain text transcripts.
    Handles common formats:
      - "Speaker: text"
      - "[Speaker] text"
      - "[00:03:12] Speaker: text"
      - "Speaker (00:03:12): text"
    """
    utterances = []
    lines = [l.strip() for l in content.splitlines() if l.strip()]

    # Patterns in order of specificity
    patterns = [
        # [00:03:12] Speaker: text
        re.compile(r"\[(\d{2}:\d{2}:\d{2})\]\s+([^:]+):\s+(.+)"),
        # Speaker (00:03:12): text
        re.compile(r"([^(]+)\((\d{2}:\d{2}:\d{2})\):\s+(.+)"),
        # Speaker: text  (no timestamp)
        re.compile(r"^([A-Z][a-zA-Z\s]{1,30}):\s+(.+)$"),
        # [Speaker] text
        re.compile(r"^\[([A-Z][a-zA-Z\s]{1,30})\]\s+(.+)$"),
    ]

    ts_counter = 0  # synthetic timestamp counter when no timestamps present

    for line in lines:
        matched = False
        for pat in patterns:
            m = pat.match(line)
            if m:
                groups = m.groups()
                if len(groups) == 3:
                    # Has timestamp
                    if re.match(r"\d{2}:\d{2}:\d{2}", groups[0]):
                        ts, speaker, text = groups
                    else:
                        speaker, ts, text = groups
                    utterances.append(Utterance(
                        speaker=speaker.strip(),
                        timestamp=_normalize_timestamp(ts),
                        text=text.strip()
                    ))
                else:
                    speaker, text = groups
                    # Generate synthetic timestamp
                    utterances.append(Utterance(
                        speaker=speaker.strip(),
                        timestamp=f"00:{ts_counter // 60:02d}:{ts_counter % 60:02d}",
                        text=text.strip()
                    ))
                    ts_counter += 5
                matched = True
                break

        if not matched and utterances:
            # Continuation line — append to last utterance
            utterances[-1] = utterances[-1].model_copy(
                update={"text": utterances[-1].text + " " + line}
            )

    return _merge_consecutive_speakers(utterances)


# ─────────────────────────────────────────────
# Deepgram audio parser
# ─────────────────────────────────────────────

def parse_audio_deepgram(file_path: str, speaker_map: Optional[dict] = None) -> list[Utterance]:
    """
    Transcribe and diarize audio/video using Deepgram.
    speaker_map: {"Speaker 0": "Alice", "Speaker 1": "Bob"}
    """
    from deepgram import DeepgramClient, PrerecordedOptions, FileSource

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        raise ValueError("DEEPGRAM_API_KEY not set in environment")

    client = DeepgramClient(api_key)

    with open(file_path, "rb") as f:
        buffer_data = f.read()

    payload: FileSource = {"buffer": buffer_data}

    options = PrerecordedOptions(
        model="nova-3",
        smart_format=True,
        diarize=True,           # Speaker attribution
        punctuate=True,
        utterances=True,        # Return utterance-level segments
        language="en",
    )

    response = client.listen.rest.v("1").transcribe_file(payload, options)
    result   = response.to_dict()

    return _parse_deepgram_response(result, speaker_map)


def _parse_deepgram_response(
    result: dict,
    speaker_map: Optional[dict] = None
) -> list[Utterance]:
    """Convert Deepgram JSON response into our Utterance list."""
    utterances_out = []
    speaker_map = speaker_map or {}

    # Use utterance-level results if available (cleaner)
    raw_utterances = (
        result.get("results", {})
              .get("utterances", [])
    )

    if raw_utterances:
        for u in raw_utterances:
            speaker_label = f"Speaker {u.get('speaker', 0)}"
            speaker_name  = speaker_map.get(speaker_label, speaker_label)
            start_seconds = u.get("start", 0)
            timestamp     = _seconds_to_timestamp(start_seconds)
            text          = u.get("transcript", "").strip()

            if text:
                utterances_out.append(Utterance(
                    speaker=speaker_name,
                    timestamp=timestamp,
                    text=text
                ))
    else:
        # Fall back to word-level grouping
        words = (
            result.get("results", {})
                  .get("channels", [{}])[0]
                  .get("alternatives", [{}])[0]
                  .get("words", [])
        )
        utterances_out = _group_words_by_speaker(words, speaker_map)

    return utterances_out


def _group_words_by_speaker(words: list[dict], speaker_map: dict) -> list[Utterance]:
    """Group word-level Deepgram output into speaker turns."""
    utterances = []
    if not words:
        return utterances

    current_speaker = None
    current_words   = []
    current_start   = 0.0

    for word in words:
        speaker_id    = word.get("speaker", 0)
        speaker_label = f"Speaker {speaker_id}"
        speaker_name  = speaker_map.get(speaker_label, speaker_label)

        if speaker_name != current_speaker:
            if current_words and current_speaker:
                utterances.append(Utterance(
                    speaker=current_speaker,
                    timestamp=_seconds_to_timestamp(current_start),
                    text=" ".join(current_words)
                ))
            current_speaker = speaker_name
            current_words   = [word["word"]]
            current_start   = word.get("start", 0.0)
        else:
            current_words.append(word["word"])

    # Flush last group
    if current_words and current_speaker:
        utterances.append(Utterance(
            speaker=current_speaker,
            timestamp=_seconds_to_timestamp(current_start),
            text=" ".join(current_words)
        ))

    return utterances


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _normalize_timestamp(ts: str) -> str:
    """Convert 00:03:12.000 or 00:03:12,000 → 00:03:12"""
    return re.sub(r"[,.][\d]+$", "", ts).strip()


def _seconds_to_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _merge_consecutive_speakers(utterances: list[Utterance]) -> list[Utterance]:
    """Merge consecutive utterances from the same speaker into one turn."""
    if not utterances:
        return []

    merged = [utterances[0].model_copy()]
    for u in utterances[1:]:
        if u.speaker == merged[-1].speaker:
            merged[-1] = merged[-1].model_copy(
                update={"text": merged[-1].text + " " + u.text}
            )
        else:
            merged.append(u.model_copy())

    return merged


def _compute_meeting_id(content: str) -> str:
    """Deterministic meeting ID from transcript content — used for dedup."""
    return hashlib.sha256(content.encode()).hexdigest()[:16]


# ─────────────────────────────────────────────
# LangGraph node
# ─────────────────────────────────────────────

def ingest_node(state: dict) -> dict:
    """
    LangGraph node: ingest.
    Reads transcript_raw or transcript_path from state.
    Writes utterances to state.
    """
    path    = state.get("transcript_path")
    content = state.get("transcript_raw", "")
    roster  = state.get("participant_roster", [])

    # Build speaker map from roster aliases
    speaker_map = {}
    for i, p in enumerate(roster):
        speaker_map[f"Speaker {i}"] = p.get("name", f"Speaker {i}")

    try:
        if path:
            fmt = detect_format(path)

            if fmt == "audio":
                utterances = parse_audio_deepgram(path, speaker_map)
                # Also store raw transcript text for meeting ID
                content = " ".join(u.text for u in utterances)

            else:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()

                if fmt == "vtt":
                    utterances = parse_vtt(content)
                elif fmt == "srt":
                    utterances = parse_srt(content)
                else:
                    utterances = parse_txt(content)

        elif content:
            # Plain text content passed directly (e.g. from Streamlit text area)
            utterances = parse_txt(content)

        else:
            return {**state, "errors": state.get("errors", []) + ["No transcript provided"]}

        if not utterances:
            return {**state, "errors": state.get("errors", []) + ["Ingestion produced no utterances — check file format"]}

        # Compute meeting ID from content
        meeting_id = _compute_meeting_id(content)

        return {
            **state,
            "utterances":   [u.model_dump() for u in utterances],
            "transcript_raw": content,
            "_meeting_id":  meeting_id,       # temp key, picked up by extract node
        }

    except Exception as e:
        return {**state, "errors": state.get("errors", []) + [f"Ingestion error: {str(e)}"]}
