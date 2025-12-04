#!/usr/bin/env python3
"""
task_claims.py - Shared helpers for claiming and reclaiming TODO/GOAL tasks.

Responsibilities:
- Atomically claim the next OPEN task by marking it IN_PROGRESS with ownership
  metadata (agent_role/chat_id/anchor_session).
- Guard against orphaned claims by reopening stale IN_PROGRESS tasks whose last
  activity is older than a configured TTL.
"""

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TaskRecord:
    db_id: int
    task_id: str
    topic: str
    text: str
    importance: str
    status: str
    last_activity: str


def _utc_now() -> datetime:
    try:
        return datetime.now(timezone.utc)
    except Exception:
        return datetime.utcnow().replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(ts_str: Optional[str]) -> Optional[datetime]:
    if not ts_str:
        return None
    ts_str = ts_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _extract_task_id(links: Optional[str], fallback: str, explicit: Optional[str] = None) -> str:
    """Prefer explicit task_id, else JSON links id/task, else fallback."""
    if explicit:
        return explicit
    if links:
        try:
            obj = json.loads(links)
            for key in ("id", "task"):
                if key in obj and isinstance(obj[key], str):
                    return obj[key]
        except json.JSONDecodeError:
            pass
    return fallback


def _last_activity_ts(conn: sqlite3.Connection, task_id: str) -> Optional[str]:
    cursor = conn.execute(
        """
        SELECT MAX(timestamp) FROM chunks
        WHERE task_id = :task_id
           OR links LIKE :id_link
           OR links LIKE :task_link
        """,
        {
            "task_id": task_id,
            "id_link": f'%\"id\":\"{task_id}\"%',
            "task_link": f'%\"task\":\"{task_id}\"%',
        },
    )
    row = cursor.fetchone()
    return row[0] if row and row[0] else None


def _reclaim_stale_in_progress(
    conn: sqlite3.Connection, cutoff: datetime
) -> List[str]:
    """Reopen stale IN_PROGRESS tasks whose last activity is older than cutoff."""
    reopened: List[str] = []
    rows = conn.execute(
        """
        SELECT id, anchor_topic, text, links, task_id, timestamp
        FROM chunks
        WHERE anchor_type = 'T' AND anchor_choice = 'IN_PROGRESS'
        """
    ).fetchall()

    for row in rows:
        db_id, topic, text, links, explicit_task_id, row_ts = row
        task_id = _extract_task_id(links, f"db-{db_id}", explicit_task_id)

        last_ts = _last_activity_ts(conn, task_id) or row_ts
        last_dt = _parse_ts(last_ts)

        if not last_dt or last_dt < cutoff:
            conn.execute(
                """
                UPDATE chunks
                SET anchor_choice = 'OPEN',
                    anchor_session = NULL,
                    agent_role = NULL,
                    chat_id = NULL
                WHERE id = ?
                """,
                (db_id,),
            )
            reopened.append(task_id)
            logger.info(
                "Reopened stale task %s (topic=%s, last_activity=%s)",
                task_id,
                topic or "general",
                last_ts or "unknown",
            )

    return reopened


def claim_next_open_todo(
    db_path: Path,
    owner: str = "agent_loop",
    role: str = "planner",
    chat_id: Optional[str] = None,
    ttl_minutes: int = 45,
) -> Optional[TaskRecord]:
    """
    Atomically claim the next OPEN TODO.

    Steps:
    - Open a write transaction (BEGIN IMMEDIATE) to avoid double-claims.
    - Reopen stale IN_PROGRESS tasks older than ttl_minutes.
    - Select the next OPEN TODO (importance then timestamp).
    - Mark it IN_PROGRESS with ownership metadata and refreshed timestamp.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        logger.error("Memory DB not found: %s", db_path)
        return None

    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=30)
    conn.execute("PRAGMA busy_timeout = 5000")

    now = _utc_now()
    claim_time = _iso(now)
    cutoff = now - timedelta(minutes=ttl_minutes)
    chat_id = chat_id or f"{role}-{os.getpid()}"
    session_tag = f"{chat_id}-{int(time.time())}"

    try:
        conn.execute("BEGIN IMMEDIATE")

        _reclaim_stale_in_progress(conn, cutoff)

        row = conn.execute(
            """
            SELECT id, anchor_topic, text, anchor_choice, importance, links, task_id
            FROM chunks
            WHERE anchor_type = 'T' AND anchor_choice = 'OPEN'
            ORDER BY
                CASE importance
                    WHEN 'H' THEN 1
                    WHEN 'M' THEN 2
                    WHEN 'L' THEN 3
                    ELSE 4
                END,
                timestamp ASC
            LIMIT 1
            """
        ).fetchone()

        if not row:
            conn.commit()
            return None

        db_id, topic, text, status, importance, links, explicit_task_id = row
        task_id = _extract_task_id(links, f"db-{db_id}", explicit_task_id)

        update = conn.execute(
            """
            UPDATE chunks
            SET anchor_choice = 'IN_PROGRESS',
                anchor_source = COALESCE(anchor_source, :owner),
                agent_role = :role,
                chat_id = :chat_id,
                anchor_session = :session,
                task_id = COALESCE(task_id, :task_id),
                timestamp = :claimed_at
            WHERE id = :id AND anchor_choice = 'OPEN'
            """,
            {
                "owner": owner,
                "role": role,
                "chat_id": chat_id,
                "session": session_tag,
                "task_id": task_id,
                "claimed_at": claim_time,
                "id": db_id,
            },
        )

        if update.rowcount == 0:
            conn.commit()
            return None

        conn.commit()
        return TaskRecord(
            db_id=db_id,
            task_id=task_id,
            topic=topic or "general",
            text=text or "",
            importance=importance or "M",
            status="IN_PROGRESS",
            last_activity=claim_time,
        )
    except sqlite3.Error as exc:
        logger.error("Failed to claim TODO: %s", exc)
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        return None
    finally:
        conn.close()


def reopen_stale_tasks(db_path: Path, ttl_minutes: int = 45) -> List[str]:
    """
    Reopen IN_PROGRESS tasks that have been idle longer than ttl_minutes.

    Returns a list of task_ids that were reopened.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        logger.error("Memory DB not found: %s", db_path)
        return []

    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=30)
    conn.execute("PRAGMA busy_timeout = 5000")

    now = _utc_now()
    cutoff = now - timedelta(minutes=ttl_minutes)

    try:
        conn.execute("BEGIN IMMEDIATE")
        reopened = _reclaim_stale_in_progress(conn, cutoff)
        conn.commit()
        return reopened
    except sqlite3.Error as exc:
        logger.error("Failed to reopen stale tasks: %s", exc)
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        return []
    finally:
        conn.close()
