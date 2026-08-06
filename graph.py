"""
graph.py — LangGraph StateGraph wiring for MeetingMind.

Defines all nodes, edges, and the compiled graph with:
  - SqliteSaver checkpointer (state persistence across Streamlit reruns)
  - interrupt_before=["human_review"] (human-in-the-loop gate)
  - Conditional routing for flagged items
  - Async execution node for parallel GitHub issue creation
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import interrupt, Command

from core.ingestion   import ingest_node
from core.extraction  import extract_node
from core.resolution  import resolve_node, add_warnings_node
from core.models      import ActionItem, ItemStatus
from storage.audit_log import (
    init_db, is_duplicate,
    log_issue_created, log_skipped_duplicate, log_item_rejected
)


# ─────────────────────────────────────────────
# Human review node (interrupt point)
# ─────────────────────────────────────────────

def human_review_node(state: dict) -> dict:
    """
    LangGraph node: human_review.
    This node calls interrupt() — the graph pauses here.
    Streamlit renders the current state for the human to review.
    The graph resumes when Streamlit calls:
        graph.invoke(Command(resume={"approved": [...], "rejected": [...]}), config)
    """
    # The interrupt call suspends the graph and returns state to the caller
    review_result = interrupt({
        "action_items": state.get("action_items", []),
        "meeting_record_raw": state.get("meeting_record_raw", {}),
        "warnings": state.get("warnings", []),
    })

    # When resumed, review_result contains the human's decisions
    approved_ids = {item["id"] for item in review_result.get("approved", [])}
    rejected_ids = {item["id"] for item in review_result.get("rejected", [])}

    # Apply human edits to items
    all_items       = review_result.get("approved", []) + review_result.get("rejected", [])
    approved_items  = [i for i in all_items if i.get("id") in approved_ids]
    rejected_items  = [i for i in all_items if i.get("id") in rejected_ids]

    return {
        **state,
        "approved_items": approved_items,
        "rejected_items": rejected_items,
    }


# ─────────────────────────────────────────────
# Dedup check node
# ─────────────────────────────────────────────

def dedup_check_node(state: dict) -> dict:
    """
    LangGraph node: dedup_check.
    Filters out items whose dedup_key already exists in the audit log.
    Also checks each enabled connector's task_exists() as a secondary safety net.
    """
    from integrations.base import get_enabled_connectors

    approved     = state.get("approved_items", [])
    meeting_id   = state.get("_meeting_id", "unknown")
    skipped_duds = list(state.get("skipped_duplicates", []))
    connectors   = get_enabled_connectors()

    to_create = []
    for item_dict in approved:
        dedup_key = item_dict.get("dedup_key")
        title     = item_dict.get("title", "")

        # Check audit log (primary dedup — fast, no API call)
        if dedup_key and is_duplicate(dedup_key):
            skipped_duds.append(item_dict.get("id"))
            log_skipped_duplicate(
                meeting_id = meeting_id,
                item_id    = item_dict.get("id", ""),
                title      = title,
                dedup_key  = dedup_key,
            )
            continue

        # Check each connector's task_exists() (secondary safety net)
        existing_url = None
        try:
            item_obj = ActionItem(**item_dict)
            for connector in connectors:
                existing_url = connector.task_exists(item_obj)
                if existing_url:
                    break
        except Exception:
            pass

        if existing_url:
            skipped_duds.append(item_dict.get("id"))
            log_skipped_duplicate(
                meeting_id   = meeting_id,
                item_id      = item_dict.get("id", ""),
                title        = title,
                dedup_key    = dedup_key or "",
                existing_url = existing_url,
            )
            continue

        to_create.append(item_dict)

    return {
        **state,
        "approved_items":     to_create,
        "skipped_duplicates": skipped_duds,
    }


# ─────────────────────────────────────────────
# Execute tools node (GitHub + Slack)
# ─────────────────────────────────────────────

def execute_tools_node(state: dict) -> dict:
    """
    LangGraph node: execute_tools.

    Uses the connector registry — calls ALL enabled connectors.
    Adding a new connector (Linear, Jira, etc.) requires ZERO changes here.
    Just add the connector file and register it in integrations/base.py.
    """
    from integrations.base import get_enabled_connectors

    items_to_create = state.get("approved_items", [])
    rejected_items  = state.get("rejected_items", [])
    meeting_date    = state.get("meeting_date", datetime.today().strftime("%Y-%m-%d"))
    meeting_id      = state.get("_meeting_id", "unknown")
    approved_by     = state.get("approved_by", "user")

    # Convert dicts back to ActionItem objects
    action_items = []
    for item_dict in items_to_create:
        try:
            action_items.append(ActionItem(**item_dict))
        except Exception:
            continue

    # Log rejected items
    for item_dict in rejected_items:
        log_item_rejected(
            meeting_id  = meeting_id,
            item_id     = item_dict.get("id", ""),
            title       = item_dict.get("title", ""),
            approved_by = approved_by,
        )

    connectors     = get_enabled_connectors()
    created_issues = []
    errors         = list(state.get("errors", []))

    # Run each connector for each action item (async within executor)
    async def _run_all():
        loop = asyncio.get_event_loop()
        all_results = []

        for item in action_items:
            item_results = []
            for connector in connectors:
                # Only task-creating connectors (not recap-only like Slack)
                if connector.name == "Slack":
                    continue
                try:
                    result = await loop.run_in_executor(
                        None, connector.create_task, item, meeting_date
                    )
                    item_results.append(result)
                except Exception as e:
                    item_results.append({
                        "connector": connector.name,
                        "item_id":   item.id,
                        "success":   False,
                        "error":     str(e),
                        "task_url":  None,
                    })
            all_results.extend(item_results)
        return all_results

    if action_items and connectors:
        try:
            loop    = asyncio.new_event_loop()
            results = loop.run_until_complete(_run_all())
            loop.close()

            for result in results:
                if result.get("success") and result.get("task_url"):
                    # Find the action item
                    item = next((i for i in action_items if i.id == result["item_id"]), None)
                    if item:
                        log_issue_created(
                            meeting_id  = meeting_id,
                            item_id     = item.id,
                            title       = item.title,
                            owner_email = item.resolved_owner.email if item.resolved_owner else "",
                            dedup_key   = item.dedup_key or "",
                            issue_url   = result["task_url"],
                            payload     = {
                                "connector": result.get("connector"),
                                "title":     item.title,
                                "due_date":  str(item.resolved_date) if item.resolved_date else None,
                            },
                            approved_by = approved_by,
                        )
                    created_issues.append(result)
                elif result.get("error"):
                    errors.append(f"[{result.get('connector')}] {result.get('error')}")

        except Exception as e:
            errors.append(f"Execution error: {str(e)}")

    # Run post_recap on all connectors (e.g. Slack sends recap)
    slack_posted = False
    for connector in connectors:
        try:
            posted = connector.post_recap(created_issues, meeting_date)
            if posted:
                slack_posted = True
        except Exception as e:
            errors.append(f"[{connector.name}] recap failed: {str(e)}")

    return {
        **state,
        "created_issues": created_issues,
        "slack_posted":   slack_posted,
        "errors":         errors,
    }


# ─────────────────────────────────────────────
# Audit log node
# ─────────────────────────────────────────────

def audit_log_node(state: dict) -> dict:
    """
    LangGraph node: audit_log.
    Final node — state is already fully logged inside execute_tools_node.
    This node exists to make the audit step explicit in the graph
    and to store the final summary state.
    """
    # Audit entries were already written inside execute_tools_node per-item.
    # Here we just ensure the DB is initialized and return state unchanged.
    init_db()
    return state


# ─────────────────────────────────────────────
# Conditional routing
# ─────────────────────────────────────────────

def route_after_resolve(state: dict) -> str:
    """Route to add_warnings if there are flagged items, else straight to human_review."""
    if state.get("has_flags"):
        return "has_flags"
    return "clean"


# ─────────────────────────────────────────────
# Graph compilation
# ─────────────────────────────────────────────

def build_graph():
    """Build and compile the MeetingMind LangGraph."""
    init_db()

    builder = StateGraph(dict)

    # Register nodes
    builder.add_node("ingest",         ingest_node)
    builder.add_node("extract",        extract_node)
    builder.add_node("resolve",        resolve_node)
    builder.add_node("add_warnings",   add_warnings_node)
    builder.add_node("human_review",   human_review_node)
    builder.add_node("dedup_check",    dedup_check_node)
    builder.add_node("execute_tools",  execute_tools_node)
    builder.add_node("audit_log",      audit_log_node)

    # Entry point
    builder.set_entry_point("ingest")

    # Sequential edges
    builder.add_edge("ingest",   "extract")
    builder.add_edge("extract",  "resolve")

    # Conditional edge after resolve
    builder.add_conditional_edges(
        "resolve",
        route_after_resolve,
        {
            "has_flags": "add_warnings",
            "clean":     "human_review",
        }
    )
    builder.add_edge("add_warnings",  "human_review")

    # Post-review sequential nodes
    builder.add_edge("human_review",  "dedup_check")
    builder.add_edge("dedup_check",   "execute_tools")
    builder.add_edge("execute_tools", "audit_log")
    builder.add_edge("audit_log",     END)

    # Compile with SQLite checkpointer for state persistence
    db_path      = os.getenv("MEETING_DB_PATH", "data/audit.db")
    checkpointer = SqliteSaver.from_conn_string(db_path)

    graph = builder.compile(
        checkpointer     = checkpointer,
        interrupt_before = ["human_review"],  # pause before human review node
    )

    return graph


# Singleton graph instance
_graph = None

def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
