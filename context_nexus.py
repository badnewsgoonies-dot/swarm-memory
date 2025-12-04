#!/usr/bin/env python3
"""
Context Nexus - Unified context bundle builder for swarm agents.

This module provides a centralized, task-centric, time-aware view of memory
for every agent step. It implements:

1. ContextBundle: Structured dataclass organizing memories into:
   - tasks: Active + nearby TODOs/GOALs
   - mandates: Non-negotiable Decisions / rules
   - task_memories: Attempts/results/lessons for active task
   - global_lessons: Cross-task lessons with high importance
   - misc: Lower-priority context

2. Unified scoring function combining:
   - Importance (H > M > L)
   - Time decay (with immortality for H importance)
   - Task alignment (huge boost for active task)
   - Mandate bonus (for decisions)

3. HUD banner rendering for consistent agent prompts

Usage:
    from context_nexus import build_context, render_hud

    context = build_context(
        db_path="memory.db",
        active_task_id="vv-001",
        topic="VV2-port",
        now=datetime.utcnow(),
        max_memories=32
    )
    hud_text = render_hud(context, active_task_id="vv-001")
"""

from __future__ import annotations

import json
import math
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class ContextMemory:
    """A single memory entry with all relevant metadata."""
    id: int
    text: str
    anchor_type: str        # F/D/L/M/R/T/G/P/etc
    anchor_topic: str
    anchor_choice: Optional[str] = None
    task_id: Optional[str] = None
    importance: Optional[str] = None
    timestamp: Optional[datetime] = None
    due: Optional[str] = None
    metric: Optional[str] = None
    links: Optional[str] = None

    # Computed fields (set during scoring)
    score: float = 0.0
    age_days: float = 0.0
    is_immortal: bool = False


@dataclass
class ContextBundle:
    """Organized context for an agent step."""
    now: datetime
    active_task_id: Optional[str] = None
    active_task: Optional[ContextMemory] = None

    # Partitioned memories
    tasks: List[ContextMemory] = field(default_factory=list)         # TODOs/GOALs
    mandates: List[ContextMemory] = field(default_factory=list)      # Binding decisions
    task_memories: List[ContextMemory] = field(default_factory=list) # ATTEMPTs/RESULTs/LESSONs for active task
    global_lessons: List[ContextMemory] = field(default_factory=list)# Cross-task lessons
    misc: List[ContextMemory] = field(default_factory=list)          # Other context

    # Metadata
    total_scored: int = 0
    topic: Optional[str] = None


# =============================================================================
# TIMESTAMP PARSING
# =============================================================================

def parse_timestamp(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp string to an aware datetime."""
    if not ts:
        return None
    ts = ts.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(ts)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (ValueError, AttributeError):
        return None


def format_relative_time(ts: Optional[datetime], now: datetime) -> str:
    """Format a datetime as relative time string."""
    if not ts:
        return "?"

    delta = now - ts
    total_seconds = delta.total_seconds()

    if total_seconds < 0:
        return "future"
    if total_seconds < 60:
        return f"{int(total_seconds)}s ago"
    elif total_seconds < 3600:
        return f"{int(total_seconds / 60)}m ago"
    elif total_seconds < 86400:
        return f"{int(total_seconds / 3600)}h ago"
    elif total_seconds < 2592000:  # 30 days
        return f"{int(total_seconds / 86400)}d ago"
    else:
        return ts.strftime("%Y-%m-%d")


# =============================================================================
# SCORING FUNCTION
# =============================================================================

def score_memory(
    mem: ContextMemory,
    active_task_id: Optional[str],
    now: datetime,
    tau_days: float = 7.0
) -> float:
    """
    Compute a unified priority score for a memory entry.

    Combines:
    1. Importance: H=3.0, M=2.0, L=1.0, None=1.5
    2. Time factor: exp decay, but immortal (boost) for H importance
    3. Task alignment: huge boost if tied to active task
    4. Mandate bonus: strong bonus for decisions/lessons

    Args:
        mem: The memory entry to score
        active_task_id: Currently active task ID (if any)
        now: Current timestamp for age calculations
        tau_days: Time decay constant (default 7 days)

    Returns:
        Composite score (higher = more relevant)
    """
    # 1. Importance scoring
    imp_label = (mem.importance or "").upper()
    importance_scores = {
        "H": 3.0, "HIGH": 3.0, "CRITICAL": 3.0,
        "M": 2.0, "MED": 2.0, "MEDIUM": 2.0,
        "L": 1.0, "LOW": 1.0,
    }
    imp = importance_scores.get(imp_label, 1.5)  # Default to mid-low

    # Check if immortal (high importance doesn't decay)
    is_immortal = imp_label in ("H", "HIGH", "CRITICAL")
    mem.is_immortal = is_immortal

    # 2. Time factor
    if mem.timestamp:
        age_days = max((now - mem.timestamp).total_seconds() / 86400.0, 0.0)
        mem.age_days = age_days
    else:
        age_days = 0.0
        mem.age_days = 0.0

    if is_immortal:
        # Immortal memories get slight boost instead of decay
        time_factor = 1.5
    else:
        # Exponential decay
        time_factor = math.exp(-age_days / tau_days)

    # 3. Task alignment: huge boost if tied to active task
    task_align = 0.0
    if active_task_id and mem.task_id:
        if mem.task_id == active_task_id:
            task_align = 5.0  # Massive boost for direct task link
        elif mem.anchor_topic and active_task_id.startswith(mem.anchor_topic):
            task_align = 2.0  # Moderate boost for topic alignment

    # 4. Mandate/decision bonus
    mem_type = (mem.anchor_type or "").lower()
    mandate_bonus = 0.0
    if mem_type in ("d", "decision"):
        mandate_bonus = 3.0  # Decisions are binding
    elif mem_type in ("l", "lesson"):
        mandate_bonus = 2.5  # Lessons are learned constraints

    # 5. Urgency from due dates
    urgency = 0.0
    if mem.due:
        try:
            due_dt = parse_timestamp(mem.due)
            if due_dt:
                days_until = (due_dt - now).total_seconds() / 86400.0
                if days_until <= 0:
                    urgency = 2.0  # Overdue
                elif days_until <= 1:
                    urgency = 1.5  # Due today/tomorrow
                elif days_until <= 7:
                    urgency = 1.0  # Due this week
        except:
            pass

    # Combine components
    # Formula: weighted sum ensuring task alignment dominates when present
    score = (
        imp * time_factor * 0.4 +  # Base importance with time
        task_align * 0.35 +         # Task alignment
        mandate_bonus * 0.15 +      # Mandate bonus
        urgency * 0.1               # Urgency
    )

    mem.score = score
    return score


# =============================================================================
# MEMORY RETRIEVAL
# =============================================================================

def fetch_all_memories(
    db_path: str,
    limit: int = 100,
    recent_hours: int = 48
) -> List[ContextMemory]:
    """
    Fetch memories from database with full metadata.

    Args:
        db_path: Path to SQLite database
        limit: Maximum entries to fetch
        recent_hours: How far back to look

    Returns:
        List of ContextMemory objects
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Calculate cutoff
        cutoff = datetime.now(timezone.utc) - timedelta(hours=recent_hours)
        cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

        # Fetch with all relevant fields
        cursor.execute("""
            SELECT
                id, text, anchor_type, anchor_topic, anchor_choice,
                task_id, importance, timestamp, due, metric, links
            FROM chunks
            WHERE (timestamp >= ? OR importance IN ('H', 'HIGH', 'CRITICAL'))
              AND (status IS NULL OR status = 'active')
            ORDER BY timestamp DESC
            LIMIT ?
        """, (cutoff_str, limit))

        rows = cursor.fetchall()
        conn.close()

        memories = []
        for row in rows:
            (id_, text, anchor_type, anchor_topic, anchor_choice,
             task_id, importance, timestamp, due, metric, links) = row

            mem = ContextMemory(
                id=id_,
                text=text or "",
                anchor_type=anchor_type or "n",
                anchor_topic=anchor_topic or "",
                anchor_choice=anchor_choice,
                task_id=task_id,
                importance=importance,
                timestamp=parse_timestamp(timestamp),
                due=due,
                metric=metric,
                links=links
            )
            memories.append(mem)

        return memories

    except Exception as e:
        # Log error but return empty list to avoid breaking the daemon
        import logging
        logging.getLogger(__name__).warning(f"Failed to fetch memories: {e}")
        return []


def fetch_task_by_id(db_path: str, task_id: str) -> Optional[ContextMemory]:
    """Fetch a specific TODO/GOAL by its task_id (stored in links JSON)."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # TODOs store their ID in the links JSON field as {"id": "xxx"}
        cursor.execute("""
            SELECT
                id, text, anchor_type, anchor_topic, anchor_choice,
                task_id, importance, timestamp, due, metric, links
            FROM chunks
            WHERE anchor_type IN ('T', 'G')
              AND links LIKE ?
            ORDER BY timestamp DESC
            LIMIT 1
        """, (f'%"id":"{task_id}"%',))

        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        (id_, text, anchor_type, anchor_topic, anchor_choice,
         task_id_col, importance, timestamp, due, metric, links) = row

        return ContextMemory(
            id=id_,
            text=text or "",
            anchor_type=anchor_type or "T",
            anchor_topic=anchor_topic or "",
            anchor_choice=anchor_choice,
            task_id=task_id,  # Use the searched task_id
            importance=importance,
            timestamp=parse_timestamp(timestamp),
            due=due,
            metric=metric,
            links=links
        )

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to fetch task {task_id}: {e}")
        return None


def fetch_open_todos(db_path: str, limit: int = 10) -> List[ContextMemory]:
    """Fetch OPEN TODOs/GOALs for the task queue."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                id, text, anchor_type, anchor_topic, anchor_choice,
                task_id, importance, timestamp, due, metric, links
            FROM chunks
            WHERE anchor_type IN ('T', 'G')
              AND (anchor_choice = 'OPEN' OR anchor_choice IS NULL)
              AND (status IS NULL OR status = 'active')
            ORDER BY
                CASE importance
                    WHEN 'H' THEN 1
                    WHEN 'HIGH' THEN 1
                    WHEN 'CRITICAL' THEN 1
                    WHEN 'M' THEN 2
                    WHEN 'MED' THEN 2
                    ELSE 3
                END,
                timestamp DESC
            LIMIT ?
        """, (limit,))

        rows = cursor.fetchall()
        conn.close()

        todos = []
        for row in rows:
            (id_, text, anchor_type, anchor_topic, anchor_choice,
             task_id, importance, timestamp, due, metric, links) = row

            # Extract task_id from links JSON if not in task_id column
            actual_task_id = task_id
            if links and not actual_task_id:
                try:
                    links_data = json.loads(links)
                    actual_task_id = links_data.get("id", "")
                except:
                    pass

            mem = ContextMemory(
                id=id_,
                text=text or "",
                anchor_type=anchor_type or "T",
                anchor_topic=anchor_topic or "",
                anchor_choice=anchor_choice,
                task_id=actual_task_id,
                importance=importance,
                timestamp=parse_timestamp(timestamp),
                due=due,
                metric=metric,
                links=links
            )
            todos.append(mem)

        return todos

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to fetch open todos: {e}")
        return []


# =============================================================================
# CONTEXT BUNDLE BUILDER
# =============================================================================

def build_context(
    db_path: str,
    active_task_id: Optional[str],
    topic: Optional[str],
    now: datetime,
    max_memories: int = 32,
    recent_hours: int = 48
) -> ContextBundle:
    """
    Build a structured context bundle for an agent step.

    This is the main entry point. It:
    1. Fetches recent memories from the database
    2. Scores each memory using the unified scoring function
    3. Partitions memories into meaningful categories
    4. Returns a ContextBundle ready for HUD rendering

    Args:
        db_path: Path to memory.db
        active_task_id: Currently active task ID (from ORCHESTRATE prefix)
        topic: Topic filter (optional)
        now: Current timestamp
        max_memories: Maximum total memories to include
        recent_hours: How far back to fetch memories

    Returns:
        ContextBundle with partitioned, scored memories
    """
    bundle = ContextBundle(
        now=now,
        active_task_id=active_task_id,
        topic=topic
    )

    # 1. Fetch the active task if specified
    if active_task_id:
        active_task = fetch_task_by_id(db_path, active_task_id)
        if active_task:
            bundle.active_task = active_task

    # 2. Fetch open TODOs for the queue
    open_todos = fetch_open_todos(db_path, limit=10)

    # 3. Fetch all recent memories
    all_memories = fetch_all_memories(db_path, limit=max_memories * 2, recent_hours=recent_hours)
    bundle.total_scored = len(all_memories)

    # 4. Score all memories
    for mem in all_memories:
        score_memory(mem, active_task_id, now)

    # 5. Sort by score (descending)
    all_memories.sort(key=lambda m: m.score, reverse=True)

    # 6. Partition memories into categories
    seen_ids = set()

    # Active task memories (directly linked)
    for mem in all_memories:
        if mem.id in seen_ids:
            continue
        if active_task_id and mem.task_id == active_task_id:
            mem_type = (mem.anchor_type or "").upper()
            if mem_type in ("M", "R", "L", "P", "A"):  # Attempts, Results, Lessons, Phases, Actions
                bundle.task_memories.append(mem)
                seen_ids.add(mem.id)
                if len(bundle.task_memories) >= 10:
                    break

    # Mandates (decisions with H importance or containing MANDATE keyword)
    for mem in all_memories:
        if mem.id in seen_ids:
            continue
        mem_type = (mem.anchor_type or "").lower()
        imp = (mem.importance or "").upper()
        text_lower = (mem.text or "").lower()

        is_mandate = (
            mem_type == "d" and (imp in ("H", "HIGH", "CRITICAL") or "mandate" in text_lower)
        )
        if is_mandate:
            bundle.mandates.append(mem)
            seen_ids.add(mem.id)
            if len(bundle.mandates) >= 5:
                break

    # Global lessons (L type, not linked to active task, H importance)
    for mem in all_memories:
        if mem.id in seen_ids:
            continue
        mem_type = (mem.anchor_type or "").upper()
        imp = (mem.importance or "").upper()

        is_global_lesson = (
            mem_type == "L" and
            (not active_task_id or mem.task_id != active_task_id) and
            imp in ("H", "HIGH", "CRITICAL", "M", "MED", "MEDIUM")
        )
        if is_global_lesson:
            bundle.global_lessons.append(mem)
            seen_ids.add(mem.id)
            if len(bundle.global_lessons) >= 5:
                break

    # Tasks (TODOs/GOALs from open queue, excluding active)
    for todo in open_todos:
        if todo.id in seen_ids:
            continue
        if active_task_id and todo.task_id == active_task_id:
            continue  # Skip active task, already shown
        bundle.tasks.append(todo)
        seen_ids.add(todo.id)
        if len(bundle.tasks) >= 5:
            break

    # Misc (remaining high-scoring memories)
    remaining_slots = max_memories - (
        len(bundle.task_memories) + len(bundle.mandates) +
        len(bundle.global_lessons) + len(bundle.tasks) + 1  # +1 for active task
    )

    for mem in all_memories:
        if mem.id in seen_ids:
            continue
        if remaining_slots <= 0:
            break
        bundle.misc.append(mem)
        seen_ids.add(mem.id)
        remaining_slots -= 1

    return bundle


# =============================================================================
# HUD RENDERING
# =============================================================================

# Type labels for display
TYPE_LABELS = {
    'd': 'DECISION', 'D': 'DECISION',
    'q': 'QUESTION', 'Q': 'QUESTION',
    'a': 'ACTION', 'A': 'ACTION',
    'f': 'FACT', 'F': 'FACT',
    'n': 'NOTE', 'N': 'NOTE',
    'c': 'CONV', 'C': 'CONV',
    'T': 'TODO', 'G': 'GOAL',
    'M': 'ATTEMPT', 'R': 'RESULT',
    'L': 'LESSON', 'P': 'PHASE'
}


def _format_memory_line(mem: ContextMemory, now: datetime, show_score: bool = False) -> str:
    """Format a single memory as a compact line."""
    type_label = TYPE_LABELS.get(mem.anchor_type, mem.anchor_type or "?")
    topic = mem.anchor_topic or "general"
    ts_rel = format_relative_time(mem.timestamp, now)

    # Truncate text
    text = (mem.text or "").replace("\n", " ").strip()
    if len(text) > 80:
        text = text[:77] + "..."

    # Build line
    parts = [f"[{type_label}]"]
    if topic:
        parts.append(f"[{topic}]")
    if mem.importance and mem.importance.upper() in ("H", "HIGH", "CRITICAL"):
        parts.append("[H]")
    parts.append(f"[{ts_rel}]")
    if show_score:
        parts.append(f"[s={mem.score:.2f}]")
    parts.append(text)

    return " ".join(parts)


def _extract_task_id_from_links(links: Optional[str]) -> Optional[str]:
    """Extract task ID from links JSON."""
    if not links:
        return None
    try:
        data = json.loads(links)
        return data.get("id")
    except:
        return None


def render_hud(context: ContextBundle, active_task_id: Optional[str] = None) -> str:
    """
    Render the HUD banner for agent prompts.

    The HUD provides a consistent, time-aware view including:
    - Current time
    - Active task details
    - Task queue
    - Mandates/decisions
    - Relevant lessons

    Args:
        context: ContextBundle from build_context()
        active_task_id: Override for active task ID (optional)

    Returns:
        Formatted HUD string ready for prompt injection
    """
    now = context.now
    task_id = active_task_id or context.active_task_id

    lines = []

    # Header separator
    sep = "=" * 78

    # TIME section
    lines.append(sep)
    lines.append("🕒 TIME")
    lines.append(sep)
    lines.append(f"Now: {now.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    lines.append("")

    # OFFICIAL TASK HUD section
    lines.append(sep)
    lines.append("📋 OFFICIAL TASK HUD")
    lines.append(sep)

    # Active task
    if context.active_task:
        task = context.active_task
        task_topic = task.anchor_topic or "general"
        task_imp = task.importance or "M"
        task_status = task.anchor_choice or "OPEN"
        task_text = (task.text or "").replace("\n", " ").strip()
        if len(task_text) > 100:
            task_text = task_text[:97] + "..."

        lines.append(f"[ACTIVE] {task_id} | topic={task_topic} | importance={task_imp} | status={task_status}")
        lines.append(f"        {task_text}")
    elif task_id:
        lines.append(f"[ACTIVE] {task_id} | (task details not found)")
    else:
        lines.append("[ACTIVE] None - no active task")

    lines.append("")

    # Task queue
    if context.tasks:
        lines.append("[QUEUE]")
        for todo in context.tasks[:5]:
            todo_id = todo.task_id or _extract_task_id_from_links(todo.links) or f"#{todo.id}"
            todo_topic = todo.anchor_topic or "general"
            todo_imp = todo.importance or "M"
            todo_text = (todo.text or "").replace("\n", " ").strip()
            if len(todo_text) > 60:
                todo_text = todo_text[:57] + "..."
            lines.append(f"[ ] {todo_id} | {todo_topic} | {todo_imp} | {todo_text}")
    else:
        lines.append("[QUEUE] (empty)")

    lines.append("")

    # MANDATES section
    lines.append(sep)
    lines.append("🧱 MANDATES / DECISIONS")
    lines.append(sep)

    if context.mandates:
        for mandate in context.mandates[:5]:
            topic = mandate.anchor_topic or "general"
            text = (mandate.text or "").replace("\n", " ").strip()
            if len(text) > 80:
                text = text[:77] + "..."
            choice = f" [{mandate.anchor_choice}]" if mandate.anchor_choice else ""
            lines.append(f"- [D] {topic}:{choice} {text}")
    else:
        lines.append("(no active mandates)")

    lines.append("")

    # RELEVANT LESSONS section
    lines.append(sep)
    lines.append("📚 RELEVANT LESSONS")
    lines.append(sep)

    # Combine task-specific lessons with global lessons
    all_lessons = []
    for mem in context.task_memories:
        if (mem.anchor_type or "").upper() == "L":
            all_lessons.append(("task", mem))
    for mem in context.global_lessons:
        all_lessons.append(("global", mem))

    if all_lessons:
        for source, lesson in all_lessons[:5]:
            topic = lesson.anchor_topic or "general"
            text = (lesson.text or "").replace("\n", " ").strip()
            if len(text) > 80:
                text = text[:77] + "..."
            marker = "[TASK]" if source == "task" else ""
            lines.append(f"- [L] {topic}: {marker} {text}")
    else:
        lines.append("(no relevant lessons)")

    lines.append("")

    # TASK HISTORY section (if active task has attempts/results)
    task_history = [m for m in context.task_memories if (m.anchor_type or "").upper() in ("M", "R", "P")]
    if task_history:
        lines.append(sep)
        lines.append("📊 TASK HISTORY (Recent Attempts/Results)")
        lines.append(sep)
        for mem in task_history[:5]:
            lines.append(_format_memory_line(mem, now))
        lines.append("")

    # ADDITIONAL CONTEXT section (misc memories if space allows)
    if context.misc and len(context.misc) > 0:
        lines.append(sep)
        lines.append("📝 ADDITIONAL CONTEXT")
        lines.append(sep)
        for mem in context.misc[:5]:
            lines.append(_format_memory_line(mem, now))
        lines.append("")

    return "\n".join(lines)


# =============================================================================
# CLI INTERFACE
# =============================================================================

def main():
    """CLI for testing context nexus."""
    import argparse

    parser = argparse.ArgumentParser(description="Context Nexus - Build and render agent context")
    parser.add_argument("--db", default="memory.db", help="Path to memory database")
    parser.add_argument("--task", "-t", help="Active task ID")
    parser.add_argument("--topic", help="Topic filter")
    parser.add_argument("--limit", type=int, default=32, help="Max memories")
    parser.add_argument("--hours", type=int, default=48, help="Recent hours to fetch")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)

    # Build context
    context = build_context(
        db_path=args.db,
        active_task_id=args.task,
        topic=args.topic,
        now=now,
        max_memories=args.limit,
        recent_hours=args.hours
    )

    if args.json:
        # Output as JSON for programmatic use
        output = {
            "now": now.isoformat(),
            "active_task_id": context.active_task_id,
            "active_task": context.active_task.__dict__ if context.active_task else None,
            "tasks": [t.__dict__ for t in context.tasks],
            "mandates": [m.__dict__ for m in context.mandates],
            "task_memories": [m.__dict__ for m in context.task_memories],
            "global_lessons": [l.__dict__ for l in context.global_lessons],
            "misc": [m.__dict__ for m in context.misc],
            "total_scored": context.total_scored
        }
        # Convert datetimes to strings
        def serialize_dt(obj):
            if isinstance(obj, datetime):
                return obj.isoformat()
            return obj
        print(json.dumps(output, default=serialize_dt, indent=2))
    else:
        # Render HUD
        hud = render_hud(context, args.task)
        print(hud)


if __name__ == "__main__":
    main()
