#!/usr/bin/env python3
"""
agent_loop.py - Minimal manager/worker orchestration loop (Windows Compatible)

Modes:
  --mode worker   : Process one open TODO
  --mode manager  : Review recent results, create follow-up TODOs
  --mode loop     : Alternate worker/manager with caps

The WORKER agent (acts as PLANNER):
  1. Finds an OPEN TODO
  2. Builds context via native SQLite queries (no shell scripts)
  3. Calls LLM to ANALYZE and PLAN (not execute)
  4. Parses ATTEMPT/RESULT/LESSON from response
  5. Writes glyphs to memory, updates TODO status

The MANAGER agent:
  1. Queries recent RESULT glyphs
  2. Calls LLM to review and plan follow-ups
  3. Creates new TODOs and logs feedback
"""

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime, timezone

# Add script directory to path for imports
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

# Fix OLLAMA_HOST if it's set to 0.0.0.0 (common server-side config)
# or if it's not set at all
if os.environ.get("OLLAMA_HOST") == "0.0.0.0":
    os.environ["OLLAMA_HOST"] = "http://localhost:11434"
elif "OLLAMA_HOST" not in os.environ:
    os.environ["OLLAMA_HOST"] = "http://localhost:11434"

from llm_client import LLMClient, LLMResponse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("MEMORY_DB", SCRIPT_DIR / "memory.db"))

# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class TodoItem:
    """Represents a TODO from the database"""
    task_id: str
    topic: str
    text: str
    status: str
    importance: str = "M"
    db_id: Optional[int] = None

@dataclass
class WorkerOutput:
    """Parsed output from worker LLM"""
    attempt_text: str
    result_success: bool
    result_reason: str
    lesson_topic: str
    lesson_text: str
    raw_response: str

@dataclass
class ManagerOutput:
    """Parsed output from manager LLM"""
    new_todos: List[Dict[str, str]]
    feedback: List[Dict[str, str]]
    raw_response: str

# =============================================================================
# DATABASE HELPERS (Native Python)
# =============================================================================

def get_db_connection():
    """Get a connection to the memory database"""
    return sqlite3.connect(DB_PATH)

def format_relative_time(ts_str):
    """Convert ISO timestamp to relative time string"""
    if not ts_str:
        return "?"
    try:
        ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        seconds = delta.total_seconds()
        if seconds < 60:
            return f"{int(seconds)}s ago"
        if seconds < 3600:
            return f"{int(seconds/60)}m ago"
        if seconds < 86400:
            return f"{int(seconds/3600)}h ago"
        return f"{int(seconds/86400)}d ago"
    except:
        return "?"

def write_chunk(anchor_type: str, text: str, topic: str = None,
                choice: str = None, task_id: str = None,
                source: str = "agent_loop", links: str = None,
                importance: str = None):
    """Write a memory chunk directly to SQLite"""
    conn = get_db_connection()
    cursor = conn.cursor()

    ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    cursor.execute("""
        INSERT INTO chunks (
            bucket, timestamp, text, anchor_type, anchor_topic,
            anchor_choice, anchor_source, task_id, links, importance
        ) VALUES (
            'anchor', ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
    """, (ts, text, anchor_type, topic, choice, source, task_id, links, importance))

    new_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return new_id

# =============================================================================
# CONTEXT BUILDING (Native Python - replaces mem-task-context.sh)
# =============================================================================

# Stopwords to filter out from keyword extraction
STOPWORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of',
    'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been', 'be',
    'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could',
    'should', 'may', 'might', 'must', 'shall', 'can', 'this', 'that', 'these',
    'those', 'it', 'its', 'you', 'your', 'we', 'our', 'they', 'their', 'what',
    'which', 'who', 'when', 'where', 'why', 'how', 'all', 'each', 'every',
    'both', 'few', 'more', 'most', 'other', 'some', 'such', 'no', 'not', 'only',
    'same', 'than', 'too', 'very', 'just', 'also', 'now', 'here', 'there',
    'then', 'so', 'if', 'about', 'into', 'over', 'after', 'before', 'between',
    'under', 'again', 'further', 'once', 'during', 'while', 'find', 'analyze',
    'identify', 'explain', 'describe', 'summarize', 'provide', 'specific',
    'function', 'responsible', 'based', 'allowed', 'current', 'system', 'agent'
}

def extract_keywords(text: str, min_len: int = 4, max_keywords: int = 5) -> List[str]:
    """Extract meaningful keywords from task text for context search"""
    # Remove punctuation and split
    words = re.findall(r"[a-zA-Z0-9_-]+", text.lower())
    # Filter: not stopwords, long enough, quoted phrases get priority
    keywords = []

    # Extract quoted phrases first (e.g., 'Project Ciphers')
    quoted = re.findall(r"'([^']+)'", text)
    keywords.extend(quoted)

    # Then add individual significant words
    for word in words:
        if word not in STOPWORDS and len(word) >= min_len:
            if word not in keywords:
                keywords.append(word)

    return keywords[:max_keywords]

def get_task_context(task_id: str, limit: int = 20) -> str:
    """Gather context for a task (TODO + related memories)"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Find the TODO itself
    cursor.execute("""
        SELECT id, anchor_type, anchor_topic, text, anchor_choice, timestamp
        FROM chunks
        WHERE anchor_type IN ('T', 'G') AND (links LIKE ? OR task_id = ?)
        ORDER BY timestamp DESC LIMIT 1
    """, (f'%"id":"{task_id}"%', task_id))

    todo_row = cursor.fetchone()
    if not todo_row:
        conn.close()
        return f"[Task {task_id} not found]"

    db_id, _, topic, text, status, ts = todo_row

    lines = []
    lines.append(f"[TODO][id={task_id}][topic={topic or '?'}] {text}")

    # 2. Find related entries (Topic matches or Task ID matches)
    query = """
        SELECT anchor_type, anchor_topic, text, anchor_choice, timestamp, task_id
        FROM chunks
        WHERE (anchor_topic = ? AND anchor_type IN ('d','f','n','a') AND id != ?)
           OR (task_id = ? AND anchor_type IN ('M','R','L','P'))
        ORDER BY timestamp DESC
        LIMIT ?
    """
    cursor.execute(query, (topic, db_id, task_id, limit))
    rows = cursor.fetchall()

    # 3. Extract keywords from task text and search for relevant context
    # This helps find context even when topics don't match
    keywords = extract_keywords(text)
    if keywords:
        kw_query = """
            SELECT anchor_type, anchor_topic, text, anchor_choice, timestamp, task_id
            FROM chunks
            WHERE anchor_type IN ('d','f','n','a','c')
              AND id != ?
              AND ({})
            ORDER BY timestamp DESC
            LIMIT ?
        """.format(" OR ".join(["text LIKE ?" for _ in keywords]))
        params = [db_id] + [f"%{kw}%" for kw in keywords] + [limit]
        cursor.execute(kw_query, params)
        kw_rows = cursor.fetchall()
        # Add keyword matches that aren't already in rows
        seen_texts = {r[2] for r in rows}
        for r in kw_rows:
            if r[2] not in seen_texts:
                rows.append(r)
                seen_texts.add(r[2])

    conn.close()

    type_map = {
        'd': 'DECISION', 'f': 'FACT', 'n': 'NOTE', 'a': 'ACTION',
        'M': 'ATTEMPT', 'R': 'RESULT', 'L': 'LESSON', 'P': 'PHASE'
    }

    for r in rows:
        atype, atopic, atext, achoice, ats, atask = r
        label = type_map.get(atype, atype)

        meta = f"[{label}][topic={atopic or '?'}]"
        if achoice:
            meta += f"[{achoice}]"
        if atask:
            meta += f"[task={atask}]"

        lines.append(f"{meta} {atext.replace(chr(10), ' ').strip()}")

    return "\n".join(lines)

def get_recent_lessons(topic: str = None, limit: int = 5) -> str:
    """Get recent lessons, optionally filtered by topic"""
    conn = get_db_connection()
    cursor = conn.cursor()

    if topic:
        cursor.execute("""
            SELECT anchor_topic, text FROM chunks
            WHERE anchor_type='L' AND anchor_topic = ?
            ORDER BY timestamp DESC LIMIT ?
        """, (topic, limit))
    else:
        cursor.execute("""
            SELECT anchor_topic, text FROM chunks
            WHERE anchor_type='L'
            ORDER BY timestamp DESC LIMIT ?
        """, (limit,))

    rows = cursor.fetchall()
    conn.close()

    lines = []
    for r in rows:
        lines.append(f"[LESSON][topic={r[0] or '?'}] {r[1].strip()}")

    return "\n".join(lines) if lines else "(No lessons found)"

def get_recent_results(hours: int = 24, limit: int = 10) -> str:
    """Get recent RESULT glyphs for manager review"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Calculate cutoff time
    cutoff = datetime.now(timezone.utc) - __import__('datetime').timedelta(hours=hours)
    cutoff_str = cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')

    cursor.execute("""
        SELECT task_id, anchor_choice, text, timestamp, anchor_topic
        FROM chunks
        WHERE anchor_type='R' AND timestamp > ?
        ORDER BY timestamp DESC LIMIT ?
    """, (cutoff_str, limit))

    rows = cursor.fetchall()
    conn.close()

    lines = []
    for r in rows:
        task_id, choice, text, ts, topic = r
        status = "SUCCESS" if choice == "success" else "FAILURE"
        time_str = format_relative_time(ts)
        lines.append(f"[{status}][task={task_id}][topic={topic}][{time_str}] {text[:100]}")

    return "\n".join(lines) if lines else "(No recent results)"

# =============================================================================
# DOOM LOOP DETECTOR
# =============================================================================

DOOM_LOOP_THRESHOLD = 3  # Auto-BLOCK after this many consecutive failures

def get_consecutive_failures(task_id: str) -> int:
    """
    Count consecutive failures for a task by checking recent RESULT glyphs.
    Returns the number of consecutive 'failure' results (most recent first).
    Stops counting at the first 'success'.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get recent RESULT glyphs for this task, ordered by most recent first
    cursor.execute("""
        SELECT anchor_choice FROM chunks
        WHERE anchor_type='R' AND task_id = ?
        ORDER BY timestamp DESC
        LIMIT 10
    """, (task_id,))

    rows = cursor.fetchall()
    conn.close()

    consecutive_failures = 0
    for (choice,) in rows:
        if choice == "failure":
            consecutive_failures += 1
        else:
            break  # Stop at first success

    return consecutive_failures

def check_doom_loop(task_id: str, current_failure: bool) -> tuple:
    """
    Check if task is in a doom loop after the current result.

    Returns:
        (should_block: bool, failure_count: int, message: str)
    """
    if not current_failure:
        return (False, 0, "Task succeeded")

    # Count failures including the one we're about to log
    consecutive = get_consecutive_failures(task_id) + 1  # +1 for current failure

    if consecutive >= DOOM_LOOP_THRESHOLD:
        return (True, consecutive, f"DOOM LOOP: {consecutive} consecutive failures, auto-blocking")
    else:
        remaining = DOOM_LOOP_THRESHOLD - consecutive
        return (False, consecutive, f"Failure {consecutive}/{DOOM_LOOP_THRESHOLD}, {remaining} retries remaining")

# =============================================================================
# TODO MANAGEMENT (Native Python)
# =============================================================================

def find_open_todo() -> Optional[TodoItem]:
    """Find the highest priority open TODO"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Priority: H > M > L, then Oldest first (FIFO)
    cursor.execute("""
        SELECT id, text, anchor_topic, importance, links, task_id
        FROM chunks
        WHERE anchor_type='T' AND anchor_choice='OPEN'
        ORDER BY
            CASE importance WHEN 'H' THEN 1 WHEN 'M' THEN 2 ELSE 3 END,
            timestamp ASC
        LIMIT 1
    """)

    row = cursor.fetchone()
    if not row:
        conn.close()
        return None

    db_id, text, topic, imp, links, stored_task_id = row

    # Use stored task_id if available, otherwise extract from links or use db_id
    task_id = stored_task_id or f"db-{db_id}"
    if not stored_task_id:
        try:
            if links:
                data = json.loads(links)
                task_id = data.get("id", task_id)
        except:
            pass

    # Mark IN_PROGRESS
    cursor.execute("UPDATE chunks SET anchor_choice='IN_PROGRESS' WHERE id=?", (db_id,))
    conn.commit()
    conn.close()

    return TodoItem(task_id, topic or "", text, "IN_PROGRESS", imp or "M", db_id)

def update_todo_status(task_id: str, status: str):
    """Update TODO status by task_id"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Handle both db-ID format and custom IDs via links
    if task_id.startswith("db-"):
        db_id = int(task_id.replace("db-", ""))
        cursor.execute("UPDATE chunks SET anchor_choice=? WHERE id=?", (status, db_id))
    else:
        cursor.execute("""
            UPDATE chunks SET anchor_choice=?
            WHERE anchor_type='T' AND links LIKE ?
        """, (status, f'%"id":"{task_id}"%'))

    conn.commit()
    conn.close()

def create_todo(task_id: str, topic: str, text: str, importance: str = "M", source: str = "manager"):
    """Create a new TODO"""
    links = json.dumps({"id": task_id})
    write_chunk("T", text, topic=topic, choice="OPEN", links=links,
                importance=importance, source=source)
    logger.info(f"Created TODO: {task_id}")

# =============================================================================
# LOGGING (Native Python - replaces mem-log.sh)
# =============================================================================

def log_attempt(task_id: str, text: str, source: str = "worker"):
    """Log an ATTEMPT glyph"""
    write_chunk("M", text, task_id=task_id, source=source)
    logger.debug(f"Logged ATTEMPT for {task_id}")

def log_result(task_id: str, success: bool, text: str, source: str = "worker"):
    """Log a RESULT glyph"""
    choice = "success" if success else "failure"
    write_chunk("R", text, choice=choice, task_id=task_id, source=source)
    logger.debug(f"Logged RESULT ({choice}) for {task_id}")

def log_lesson(topic: str, text: str, task_id: str = None, source: str = "worker"):
    """Log a LESSON glyph"""
    write_chunk("L", text, topic=topic, task_id=task_id, source=source)
    logger.debug(f"Logged LESSON for topic={topic}")

def log_feedback(text: str, source: str = "manager"):
    """Log manager feedback as a NOTE"""
    write_chunk("n", text, topic="manager-feedback", source=source)

# =============================================================================
# LLM PROMPTS
# =============================================================================

WORKER_SYSTEM_PROMPT = """You are a PLANNER agent.
You CANNOT execute code. You can only PLAN.

Your job:
1. Analyze the TODO and context.
2. Search memory patterns.
3. Propose a concrete plan.

OUTPUT FORMAT:
ATTEMPT: [What you analyzed/tried]
RESULT: success=True/False reason=[why you succeeded or failed]
LESSON: [What was learned that should be remembered]
PLAN: [Step-by-step implementation plan if successful]
"""

MANAGER_SYSTEM_PROMPT = """You are a MANAGER agent.
You review completed work and create follow-up tasks.

Your job:
1. Review recent RESULT glyphs
2. Identify patterns (repeated failures, blockers)
3. Create follow-up TODOs if needed
4. Provide strategic feedback

OUTPUT FORMAT:
REVIEW: [Summary of what you observed]
TODO: id=<unique-id> topic=<topic> importance=<H|M|L> text=<task description>
TODO: ... (can have multiple)
FEEDBACK: [Strategic observations for the team]
"""

def build_worker_prompt(todo: TodoItem, context: str, lessons: str = "") -> str:
    """Build the prompt for worker mode"""
    return f"""
TASK: {todo.task_id}
TOPIC: {todo.topic}
DESC: {todo.text}

CONTEXT:
{context}

RECENT LESSONS:
{lessons}

Analyze and plan this task. Use the specified output format.
"""

def build_manager_prompt(results: str, lessons: str = "") -> str:
    """Build the prompt for manager mode"""
    return f"""
RECENT RESULTS:
{results}

RECENT LESSONS:
{lessons}

Review these results and create follow-up TODOs if needed.
"""

def parse_worker_response(response: str, todo: TodoItem) -> WorkerOutput:
    """Parse worker LLM response into structured output"""
    result_success = "success=true" in response.lower()

    # Extract sections
    attempt = re.search(r'ATTEMPT:(.*?)(?=RESULT|LESSON|PLAN|$)', response, re.DOTALL | re.IGNORECASE)
    reason = re.search(r'reason=([^\n]+)', response, re.IGNORECASE)
    lesson = re.search(r'LESSON:(.*?)(?=PLAN|$)', response, re.DOTALL | re.IGNORECASE)

    return WorkerOutput(
        attempt_text=attempt.group(1).strip()[:500] if attempt else "Analyzed task",
        result_success=result_success,
        result_reason=reason.group(1).strip()[:200] if reason else "Completed",
        lesson_topic=todo.topic,
        lesson_text=lesson.group(1).strip()[:500] if lesson else "",
        raw_response=response
    )

def parse_manager_response(response: str) -> ManagerOutput:
    """Parse manager LLM response into structured output"""
    new_todos = []
    feedback = []

    # Extract TODOs: TODO: id=xxx topic=yyy importance=H text=zzz
    todo_pattern = r'TODO:\s*id=(\S+)\s+topic=(\S+)\s+importance=([HML])\s+text=(.+?)(?=TODO:|FEEDBACK:|$)'
    for match in re.finditer(todo_pattern, response, re.DOTALL | re.IGNORECASE):
        new_todos.append({
            "id": match.group(1).strip(),
            "topic": match.group(2).strip(),
            "importance": match.group(3).strip().upper(),
            "text": match.group(4).strip()
        })

    # Extract FEEDBACK
    feedback_match = re.search(r'FEEDBACK:(.*?)$', response, re.DOTALL | re.IGNORECASE)
    if feedback_match:
        feedback.append({"text": feedback_match.group(1).strip()})

    return ManagerOutput(new_todos=new_todos, feedback=feedback, raw_response=response)

# =============================================================================
# WORKER MODE
# =============================================================================

def run_worker_step(client: LLMClient, tier: str = "fast", dry_run: bool = False, source: str = "worker") -> bool:
    """Run one worker step: claim task, analyze, log results"""
    logger.info("=" * 50)
    logger.info("WORKER STEP")
    logger.info("=" * 50)

    # 1. Find and claim an open TODO
    todo = find_open_todo()
    if not todo:
        logger.info("No OPEN tasks found.")
        return False

    logger.info(f"Claimed Task: {todo.task_id}")
    logger.info(f"  Topic: {todo.topic}")
    logger.info(f"  Text: {todo.text[:80]}...")

    # 2. Build context
    context = get_task_context(todo.task_id)
    lessons = get_recent_lessons(todo.topic)
    prompt = build_worker_prompt(todo, context, lessons)

    logger.info(f"Context length: {len(context)} chars")

    if dry_run:
        logger.info("Dry run - skipping LLM call")
        update_todo_status(todo.task_id, "OPEN")  # Release task
        return True

    # 3. Call LLM
    logger.info(f"Calling LLM (tier={tier})...")
    response = client.complete(prompt, tier=tier, system_prompt=WORKER_SYSTEM_PROMPT, timeout=120)

    if not response.success:
        logger.error(f"LLM call failed: {response.error}")
        update_todo_status(todo.task_id, "OPEN")  # Release task
        return True

    logger.info(f"LLM response: {len(response.text)} chars, {response.latency_ms}ms")

    # 4. Parse response
    output = parse_worker_response(response.text, todo)
    logger.info("Parsed output:")
    logger.info(f"  ATTEMPT: {output.attempt_text[:80]}...")
    logger.info(f"  RESULT: success={output.result_success}, reason={output.result_reason[:50]}...")
    logger.info(f"  LESSON: [{output.lesson_topic}] {output.lesson_text[:50]}...")

    # 5. Log to memory
    log_attempt(todo.task_id, output.attempt_text, source=source)
    log_result(todo.task_id, output.result_success, output.result_reason, source=source)
    if output.lesson_text:
        log_lesson(output.lesson_topic, output.lesson_text, task_id=todo.task_id, source=source)

    # 6. Update TODO status with doom loop detection
    if output.result_success:
        status = "DONE"
        doom_msg = ""
    else:
        # Check doom loop before deciding status
        should_block, failure_count, doom_msg = check_doom_loop(todo.task_id, True)
        if should_block:
            status = "BLOCKED"
            logger.warning(doom_msg)
        else:
            status = "OPEN"  # Allow retry
            logger.info(doom_msg)

    update_todo_status(todo.task_id, status)

    logger.info("-" * 40)
    logger.info("Worker step complete:")
    logger.info(f"  Task: {todo.task_id}")
    logger.info(f"  Status: {status}")
    if doom_msg and status == "OPEN":
        logger.info(f"  Doom Loop: {doom_msg}")
    if output.lesson_text:
        logger.info(f"  Lesson: {output.lesson_text[:80]}...")

    return True

# =============================================================================
# MANAGER MODE
# =============================================================================

def run_manager_step(client: LLMClient, tier: str = "fast", dry_run: bool = False, source: str = "manager") -> bool:
    """Run one manager step: review results, create follow-ups"""
    logger.info("=" * 50)
    logger.info("MANAGER STEP")
    logger.info("=" * 50)

    # 1. Get recent results
    results = get_recent_results(hours=24, limit=10)
    lessons = get_recent_lessons(limit=5)

    if results == "(No recent results)":
        logger.info("No recent results to review.")
        return False

    logger.info(f"Reviewing {results.count('[')//2} recent results")

    prompt = build_manager_prompt(results, lessons)

    if dry_run:
        logger.info("Dry run - skipping LLM call")
        return True

    # 2. Call LLM
    logger.info(f"Calling LLM (tier={tier})...")
    response = client.complete(prompt, tier=tier, system_prompt=MANAGER_SYSTEM_PROMPT, timeout=120)

    if not response.success:
        logger.error(f"LLM call failed: {response.error}")
        return True

    logger.info(f"LLM response: {len(response.text)} chars, {response.latency_ms}ms")

    # 3. Parse response
    output = parse_manager_response(response.text)

    # 4. Create new TODOs
    for todo_data in output.new_todos:
        create_todo(
            task_id=todo_data["id"],
            topic=todo_data["topic"],
            text=todo_data["text"],
            importance=todo_data["importance"],
            source=source
        )

    # 5. Log feedback
    for fb in output.feedback:
        log_feedback(fb["text"], source=source)

    logger.info("-" * 40)
    logger.info("Manager step complete:")
    logger.info(f"  New TODOs created: {len(output.new_todos)}")
    logger.info(f"  Feedback logged: {len(output.feedback)}")

    return True

# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Manager/Worker orchestration loop (Windows Compatible)")
    parser.add_argument("--mode", choices=["worker", "manager", "loop"], required=True,
                        help="Operation mode")
    parser.add_argument("--tier", default="fast",
                        help="LLM tier to use (fast, code, smart, claude, codex, max). Default: fast")
    parser.add_argument("--max-iterations", type=int, default=20,
                        help="Maximum iterations in loop mode. Default: 20")
    parser.add_argument("--max-todos", type=int, default=5,
                        help="Maximum TODOs to process in loop mode. Default: 5")
    parser.add_argument("--sleep", type=int, default=5,
                        help="Seconds to sleep between steps. Default: 5")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't actually call LLM or modify DB")
    parser.add_argument("--source", default=None,
                        help="Source tag for logging (default: worker/manager)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable verbose logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Create LLM client (uses OLLAMA_HOST env var)
    client = LLMClient()
    logger.info(f"Ollama host: {client.ollama_host}")

    source = args.source or args.mode

    if args.mode == "worker":
        run_worker_step(client, tier=args.tier, dry_run=args.dry_run, source=source)

    elif args.mode == "manager":
        run_manager_step(client, tier=args.tier, dry_run=args.dry_run, source=source)

    elif args.mode == "loop":
        iterations = 0
        todos_processed = 0

        while iterations < args.max_iterations and todos_processed < args.max_todos:
            iterations += 1
            logger.info(f"\n>>> Loop iteration {iterations}/{args.max_iterations}")

            # Run worker step
            if run_worker_step(client, tier=args.tier, dry_run=args.dry_run, source=source):
                todos_processed += 1
            else:
                logger.info("No tasks. Running manager...")
                run_manager_step(client, tier=args.tier, dry_run=args.dry_run, source=source)

            if iterations < args.max_iterations:
                logger.info(f"Sleeping {args.sleep}s...")
                time.sleep(args.sleep)

        logger.info(f"\nLoop complete: {iterations} iterations, {todos_processed} TODOs processed")

if __name__ == "__main__":
    main()
