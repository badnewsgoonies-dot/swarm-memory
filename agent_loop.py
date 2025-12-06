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
# PHASE-SPECIFIC TIER ROUTING (Bounty Hunter "Dream Team" Configuration)
# =============================================================================
#
# The Bounty Hunter service uses THREE premium CLI tools on flat-rate plans:
#   1. copilot -p   → GitHub Copilot (GPT-5.1) - Fast reader/analyzer
#   2. claude -p    → Claude Opus - Best planner/teacher
#   3. codex exec   → GPT-5.1-Codex-Max - Only reliable code writer
#
# Phase routing (hard-coded for maximum quality):
#   ANALYZE  → Copilot  (reads issue + repo context)
#   PLAN     → Claude   (creates architectural strategy)
#   EXECUTE  → Codex    (writes the actual code)
#   REVIEW   → Claude   (extracts lessons for memory)

BOUNTY_HUNTER_TIERS = {
    "analyze": "copilot",       # Reader: fast read, large context
    "plan": "claude",           # Philosopher: deep thought, robust plans
    "execute": "codex-max-low", # Speed Demon: 2.5s execution loop
    "review": "gpt5.1",         # Balanced Pro: GPT-5.1 high-effort for lessons
}

# Legacy mapping (can be overridden via env vars for non-bounty-hunter modes)
PHASE_TIERS = {
    "analyst": os.environ.get("TIER_ANALYST", "copilot"),
    "architect": os.environ.get("TIER_ARCHITECT", "claude"),
    "coder": os.environ.get("TIER_CODER", "codex-max"),
    "teacher": os.environ.get("TIER_TEACHER", "claude"),
}

def get_tier_for_phase(phase: str, bounty_hunter: bool = False, fallback: str = "fast") -> str:
    """Get the configured tier for a specific workflow phase"""
    if bounty_hunter:
        return BOUNTY_HUNTER_TIERS.get(phase.lower(), fallback)
    return PHASE_TIERS.get(phase.lower(), fallback)

# =============================================================================
# PERSONA / ROLE RULES SYSTEM
# =============================================================================
# Each persona injects domain expertise into the agent based on task topic.
# Personas live in prompts/ directory and are selected by topic keywords.

PROMPTS_DIR = SCRIPT_DIR / "prompts"

# Map topic keywords to persona files
PERSONA_MAP = {
    # Architecture/Planning keywords
    "architect": "persona_architect.txt",
    "plan": "persona_architect.txt",
    "structure": "persona_architect.txt",
    "api": "persona_architect.txt",
    "schema": "persona_architect.txt",

    # Coding keywords
    "code": "persona_coder.txt",
    "implement": "persona_coder.txt",
    "fix": "persona_coder.txt",
    "bug": "persona_coder.txt",
    "refactor": "persona_coder.txt",
    "function": "persona_coder.txt",
    "port": "persona_coder.txt",

    # UI/UX keywords
    "ui": "persona_designer.txt",
    "ux": "persona_designer.txt",
    "design": "persona_designer.txt",
    "frontend": "persona_designer.txt",
    "css": "persona_designer.txt",
    "layout": "persona_designer.txt",
    "component": "persona_designer.txt",

    # QA keywords
    "test": "persona_qa.txt",
    "qa": "persona_qa.txt",
    "verify": "persona_qa.txt",
    "audit": "persona_qa.txt",
    "check": "persona_qa.txt",
    "validate": "persona_qa.txt",

    # Product keywords
    "product": "persona_product.txt",
    "feature": "persona_product.txt",
    "requirement": "persona_product.txt",
    "priority": "persona_product.txt",
    "scope": "persona_product.txt",
}

# Default persona if no match found
DEFAULT_PERSONA = "persona_coder.txt"

# Onboarding directory (generated by dream_consolidator.py --onboard)
ONBOARD_DIR = SCRIPT_DIR / "onboard"


def load_onboarding(topic: str) -> str:
    """
    Load onboarding prompt for a topic from onboard/ directory.

    Onboarding files contain historical lessons for a topic, generated by
    dream_consolidator.py --onboard. Format: onboard/onboard_{topic}.md

    Returns:
        Onboarding content if found, empty string otherwise.
    """
    if not topic:
        return ""

    # Try exact match first
    safe_topic = topic.replace("/", "-").replace("\\", "-")
    filepath = ONBOARD_DIR / f"onboard_{safe_topic}.md"

    if filepath.exists():
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                logger.debug(f"Loaded onboarding: {filepath} ({len(content)} chars)")
                return content
        except Exception as e:
            logger.error(f"Error loading onboarding {filepath}: {e}")
            return ""

    # Try partial match (topic might be 'daemon-lessons' but file is 'onboard_daemon.md')
    if ONBOARD_DIR.exists():
        for candidate in ONBOARD_DIR.glob("onboard_*.md"):
            candidate_topic = candidate.stem.replace("onboard_", "")
            if candidate_topic in topic or topic in candidate_topic:
                try:
                    with open(candidate, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                        logger.debug(f"Loaded onboarding (partial match): {candidate} ({len(content)} chars)")
                        return content
                except Exception as e:
                    logger.error(f"Error loading onboarding {candidate}: {e}")

    return ""


def load_persona(persona_file: str) -> str:
    """Load a persona file from the prompts directory"""
    path = PROMPTS_DIR / persona_file
    if not path.exists():
        logger.warning(f"Persona file not found: {path}")
        return ""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except Exception as e:
        logger.error(f"Error loading persona {persona_file}: {e}")
        return ""


def get_persona_for_topic(topic: str, task_text: str = "") -> str:
    """
    Select and load the appropriate persona based on topic and task text.

    Priority:
    1. Exact topic match in PERSONA_MAP
    2. Keyword match in topic or task_text
    3. Default persona (coder)
    """
    combined = f"{topic} {task_text}".lower()

    # Check for keyword matches
    for keyword, persona_file in PERSONA_MAP.items():
        if keyword in combined:
            persona = load_persona(persona_file)
            if persona:
                logger.debug(f"Selected persona: {persona_file} (matched '{keyword}')")
                return persona

    # Fall back to default
    logger.debug(f"Using default persona: {DEFAULT_PERSONA}")
    return load_persona(DEFAULT_PERSONA)


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

# =============================================================================
# BOUNTY HUNTER PHASE PROMPTS (Dream Team Configuration)
# =============================================================================

ANALYST_SYSTEM_PROMPT = """You are the ANALYST agent (Phase 1 of 4).
Your tool: GitHub Copilot (GPT-5.1) - fast, excellent at reading large contexts.

Your ONLY job is to UNDERSTAND the problem:
1. Read the GitHub issue/bug report
2. Identify the affected files and functions
3. Understand the expected vs actual behavior
4. Note any reproduction steps or error messages

OUTPUT FORMAT:
ISSUE_SUMMARY: [One-paragraph summary of the bug/feature]
AFFECTED_FILES: [List of files that likely need changes]
ROOT_CAUSE: [Your hypothesis about what's wrong]
REPRODUCTION: [How to trigger/verify the issue]
READY_FOR_PLANNING: yes/no

DO NOT:
- Propose solutions (that's the Architect's job)
- Write any code (that's the Coder's job)
- Create lessons (that's the Teacher's job)
"""

ARCHITECT_SYSTEM_PROMPT = """You are the ARCHITECT agent (Phase 2 of 4).
Your tool: Claude Opus - best at architectural thinking and edge case detection.

Your ONLY job is to DESIGN the solution:
1. Review the Analyst's findings
2. Design a step-by-step implementation plan
3. Identify edge cases and potential pitfalls
4. Specify exact file changes needed

OUTPUT FORMAT:
STRATEGY: [High-level approach in 1-2 sentences]
STEPS:
1. [First concrete step with file:function target]
2. [Second step...]
3. [Continue as needed...]
EDGE_CASES: [List potential issues to watch for]
TEST_PLAN: [How to verify the fix works]
READY_FOR_CODING: yes/no

DO NOT:
- Write actual code (that's the Coder's job)
- Re-analyze the issue (trust the Analyst)
- Extract lessons (that's the Teacher's job)
"""

CODER_SYSTEM_PROMPT = """You are the CODER agent (Phase 3 of 4).
Your tool: Codex GPT-5.1-Max - only model trusted to write compilable code.

Your ONLY job is to IMPLEMENT the plan:
1. Follow the Architect's step-by-step plan EXACTLY
2. Write clean, minimal, working code
3. Make only the changes specified
4. Output code in diff format for easy review

OUTPUT FORMAT:
IMPLEMENTING: [Which step you're working on]
FILE: [path/to/file]
```diff
- old line
+ new line
```
RESULT: success=True/False reason=[compilation status]

DO NOT:
- Re-analyze the issue (trust the Analyst)
- Redesign the solution (trust the Architect)
- Add features not in the plan
- Write lessons (that's the Teacher's job)
"""

TEACHER_SYSTEM_PROMPT = """You are the TEACHER agent (Phase 4 of 4).
Your tool: Claude Opus - best at extracting clean, memorable lessons.

Your ONLY job is to REVIEW and LEARN:
1. Review what was attempted
2. Verify if the fix was successful
3. Extract a reusable lesson for the memory database
4. Suggest follow-up tasks if needed

OUTPUT FORMAT:
REVIEW: [What was done, was it successful?]
LESSON: [topic] [A clear, reusable insight in 1-2 sentences]
FOLLOW_UP: [Any remaining work needed, or "none"]
RESULT: success=True/False reason=[why the overall bounty succeeded or failed]

The LESSON should be:
- Specific enough to be useful
- General enough to apply to similar problems
- Written for a future agent who knows nothing about this issue
"""

def build_worker_prompt(todo: TodoItem, context: str, lessons: str = "", onboarding: str = "") -> str:
    """Build the prompt for worker mode"""
    onboarding_section = ""
    if onboarding:
        onboarding_section = f"ONBOARDING (Historical wisdom):\n{onboarding}\n\n"

    return f"""
TASK: {todo.task_id}
TOPIC: {todo.topic}
DESC: {todo.text}

CONTEXT:
{context}

RECENT LESSONS:
{lessons}

{onboarding_section}Analyze and plan this task. Use the specified output format.
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
    onboarding = load_onboarding(todo.topic)
    prompt = build_worker_prompt(todo, context, lessons, onboarding)
    
    if onboarding:
        logger.info(f"Loaded onboarding for topic '{todo.topic}' ({len(onboarding)} chars)")

    logger.info(f"Context length: {len(context)} chars")

    # 2b. Load persona based on topic/task
    persona = get_persona_for_topic(todo.topic, todo.text)
    if persona:
        logger.info(f"Persona loaded: {len(persona)} chars")

    if dry_run:
        logger.info("Dry run - skipping LLM call")
        update_todo_status(todo.task_id, "OPEN")  # Release task
        return True

    # 3. Call LLM with persona-enhanced system prompt
    system_prompt = WORKER_SYSTEM_PROMPT
    if persona:
        system_prompt = f"{persona}\n\n---\n\n{WORKER_SYSTEM_PROMPT}"

    logger.info(f"Calling LLM (tier={tier})...")
    response = client.complete(prompt, tier=tier, system_prompt=system_prompt, timeout=120)

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
# BOUNTY HUNTER MODE (Dream Team 4-Phase Workflow)
# =============================================================================

@dataclass
class BountyHunterState:
    """State passed between bounty hunter phases"""
    task_id: str
    topic: str
    issue_text: str
    # Phase outputs
    analysis: Optional[str] = None
    plan: Optional[str] = None
    implementation: Optional[str] = None
    review: Optional[str] = None
    # Status
    phase: str = "analyze"
    success: bool = False
    error: Optional[str] = None


def run_bounty_hunter_step(client: LLMClient, repo_root: str = None, dry_run: bool = False) -> bool:
    """
    Run the Bounty Hunter 4-phase workflow:
      Phase 1: ANALYZE (Copilot)  - Understand the issue
      Phase 2: PLAN (Claude)      - Design the solution
      Phase 3: EXECUTE (Codex)    - Write the code
      Phase 4: REVIEW (Claude)    - Extract lessons

    Each phase uses its optimal model from BOUNTY_HUNTER_TIERS.
    """
    logger.info("=" * 60)
    logger.info("BOUNTY HUNTER - Dream Team Workflow")
    logger.info("=" * 60)
    logger.info("  Phase 1: ANALYZE  → Copilot (GPT-5.1)")
    logger.info("  Phase 2: PLAN     → Claude Opus")
    logger.info("  Phase 3: EXECUTE  → Codex Max")
    logger.info("  Phase 4: REVIEW   → Claude Opus")
    logger.info("=" * 60)

    # 1. Find and claim an open TODO
    todo = find_open_todo()
    if not todo:
        logger.info("No OPEN tasks found.")
        return False

    state = BountyHunterState(
        task_id=todo.task_id,
        topic=todo.topic,
        issue_text=todo.text
    )

    logger.info(f"Claimed Bounty: {state.task_id}")
    logger.info(f"  Topic: {state.topic}")
    logger.info(f"  Issue: {state.issue_text[:100]}...")

    # Get context for all phases
    context = get_task_context(state.task_id)
    lessons = get_recent_lessons(state.topic)
    onboarding = load_onboarding(state.topic)
    
    if onboarding:
        logger.info(f"Loaded onboarding for topic '{state.topic}' ({len(onboarding)} chars)")

    # =========================================================================
    # PHASE 1: ANALYZE (Copilot)
    # =========================================================================
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 1: ANALYZE (Copilot)")
    logger.info("-" * 50)

    tier = get_tier_for_phase("analyze", bounty_hunter=True)
    logger.info(f"Using tier: {tier}")

    # Build onboarding section separately (avoids f-string multiline issues)
    onboarding_section = ""
    if onboarding:
        onboarding_section = f"ONBOARDING (Historical wisdom for this topic):\n{onboarding}\n\n"

    analyze_prompt = f"""
GITHUB ISSUE:
{state.issue_text}

CODEBASE CONTEXT:
{context}

RECENT LESSONS:
{lessons}

{onboarding_section}Analyze this issue and identify what needs to be fixed.
"""

    if not dry_run:
        response = client.complete(analyze_prompt, tier=tier, system_prompt=ANALYST_SYSTEM_PROMPT, timeout=180)
        if not response.success:
            logger.error(f"ANALYZE failed: {response.error}")
            update_todo_status(state.task_id, "OPEN")  # Release for retry
            return False
        state.analysis = response.text
        logger.info(f"Analysis complete ({response.latency_ms}ms)")
        logger.info(f"  {state.analysis[:200]}...")

        # Log the analysis as an ATTEMPT
        log_attempt(state.task_id, f"[ANALYZE] {state.analysis[:500]}", source="bounty-analyst")
    else:
        state.analysis = "[DRY RUN - Analysis skipped]"

    # Check if ready to proceed
    if "READY_FOR_PLANNING: no" in (state.analysis or "").upper():
        logger.warning("Analyst says not ready for planning. Needs more info.")
        update_todo_status(state.task_id, "BLOCKED")
        log_result(state.task_id, False, "Analysis incomplete - needs more information", source="bounty-analyst")
        return True

    # =========================================================================
    # PHASE 2: PLAN (Claude Opus)
    # =========================================================================
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 2: PLAN (Claude Opus)")
    logger.info("-" * 50)

    tier = get_tier_for_phase("plan", bounty_hunter=True)
    logger.info(f"Using tier: {tier}")

    plan_prompt = f"""
ANALYST'S FINDINGS:
{state.analysis}

ORIGINAL ISSUE:
{state.issue_text}

Based on the analyst's findings, design a step-by-step implementation plan.
"""

    if not dry_run:
        response = client.complete(plan_prompt, tier=tier, system_prompt=ARCHITECT_SYSTEM_PROMPT, timeout=300)
        if not response.success:
            logger.error(f"PLAN failed: {response.error}")
            log_result(state.task_id, False, f"Planning failed: {response.error}", source="bounty-architect")
            update_todo_status(state.task_id, "OPEN")  # Release for retry
            return True
        state.plan = response.text
        logger.info(f"Plan complete ({response.latency_ms}ms)")
        logger.info(f"  {state.plan[:200]}...")

        # Log the plan
        log_attempt(state.task_id, f"[PLAN] {state.plan[:500]}", source="bounty-architect")
    else:
        state.plan = "[DRY RUN - Plan skipped]"

    # Check if ready to proceed
    if "READY_FOR_CODING: no" in (state.plan or "").upper():
        logger.warning("Architect says not ready for coding.")
        update_todo_status(state.task_id, "BLOCKED")
        log_result(state.task_id, False, "Plan incomplete - architecture needs revision", source="bounty-architect")
        return True

    # =========================================================================
    # PHASE 3: EXECUTE (Codex Max)
    # =========================================================================
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 3: EXECUTE (Codex Max)")
    logger.info("-" * 50)

    tier = get_tier_for_phase("execute", bounty_hunter=True)
    logger.info(f"Using tier: {tier}")

    execute_prompt = f"""
ARCHITECT'S PLAN:
{state.plan}

ORIGINAL ISSUE:
{state.issue_text}

REPOSITORY: {repo_root or 'current directory'}

Implement the plan step by step. Output the code changes needed.
"""

    if not dry_run:
        response = client.complete(execute_prompt, tier=tier, system_prompt=CODER_SYSTEM_PROMPT, timeout=600)
        if not response.success:
            logger.error(f"EXECUTE failed: {response.error}")
            log_result(state.task_id, False, f"Execution failed: {response.error}", source="bounty-coder")
            update_todo_status(state.task_id, "OPEN")  # Release for retry
            return True
        state.implementation = response.text
        logger.info(f"Implementation complete ({response.latency_ms}ms)")
        logger.info(f"  {state.implementation[:200]}...")

        # Log the implementation
        log_attempt(state.task_id, f"[EXECUTE] {state.implementation[:500]}", source="bounty-coder")
    else:
        state.implementation = "[DRY RUN - Execution skipped]"

    # =========================================================================
    # PHASE 4: REVIEW (Claude Opus)
    # =========================================================================
    logger.info("\n" + "-" * 50)
    logger.info("PHASE 4: REVIEW (Claude Opus)")
    logger.info("-" * 50)

    tier = get_tier_for_phase("review", bounty_hunter=True)
    logger.info(f"Using tier: {tier}")

    review_prompt = f"""
ORIGINAL ISSUE:
{state.issue_text}

ANALYST'S FINDINGS:
{state.analysis[:1000] if state.analysis else 'N/A'}

ARCHITECT'S PLAN:
{state.plan[:1000] if state.plan else 'N/A'}

CODER'S IMPLEMENTATION:
{state.implementation[:2000] if state.implementation else 'N/A'}

Review the entire bounty workflow and extract lessons for the memory database.
"""

    if not dry_run:
        response = client.complete(review_prompt, tier=tier, system_prompt=TEACHER_SYSTEM_PROMPT, timeout=300)
        if not response.success:
            logger.error(f"REVIEW failed: {response.error}")
            # Still mark as done if execution succeeded
            state.review = "Review failed but implementation may have succeeded"
        else:
            state.review = response.text
            logger.info(f"Review complete ({response.latency_ms}ms)")
            logger.info(f"  {state.review[:200]}...")
    else:
        state.review = "[DRY RUN - Review skipped]"

    # =========================================================================
    # FINALIZE
    # =========================================================================
    logger.info("\n" + "-" * 50)
    logger.info("FINALIZING BOUNTY")
    logger.info("-" * 50)

    # Parse success from review
    overall_success = "success=true" in (state.review or "").lower()

    # Extract and log lesson
    lesson_match = re.search(r'LESSON:\s*\[?([^\]]+)\]?\s*(.+?)(?=FOLLOW_UP|RESULT|$)', state.review or "", re.DOTALL | re.IGNORECASE)
    if lesson_match:
        lesson_topic = lesson_match.group(1).strip()
        lesson_text = lesson_match.group(2).strip()
        log_lesson(lesson_topic, lesson_text, task_id=state.task_id, source="bounty-teacher")
        logger.info(f"Lesson logged: [{lesson_topic}] {lesson_text[:80]}...")

    # Log final result
    log_result(state.task_id, overall_success, state.review[:500] if state.review else "Bounty completed", source="bounty-hunter")

    # Update TODO status with doom loop detection
    if overall_success:
        status = "DONE"
    else:
        should_block, failure_count, doom_msg = check_doom_loop(state.task_id, True)
        if should_block:
            status = "BLOCKED"
            logger.warning(doom_msg)
        else:
            status = "OPEN"  # Allow retry
            logger.info(doom_msg)

    update_todo_status(state.task_id, status)

    logger.info("=" * 60)
    logger.info("BOUNTY HUNTER COMPLETE")
    logger.info(f"  Task: {state.task_id}")
    logger.info(f"  Status: {status}")
    logger.info(f"  Success: {overall_success}")
    logger.info("=" * 60)

    return True


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Manager/Worker orchestration loop (Windows Compatible)")
    parser.add_argument("--mode", choices=["worker", "manager", "loop", "bounty-hunter"], required=True,
                        help="Operation mode: worker, manager, loop, or bounty-hunter")
    parser.add_argument("--tier", default="fast",
                        help="LLM tier to use (fast, code, smart, claude, codex, max). Default: fast")
    parser.add_argument("--repo-root", default=None,
                        help="Repository root for bounty-hunter mode (default: current directory)")
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

    elif args.mode == "bounty-hunter":
        # Bounty Hunter Mode: 4-phase Dream Team workflow
        # Ignores --tier flag, uses BOUNTY_HUNTER_TIERS:
        #   ANALYZE  → Copilot
        #   PLAN     → Claude Opus
        #   EXECUTE  → Codex Max
        #   REVIEW   → Claude Opus
        logger.info("=" * 60)
        logger.info("BOUNTY HUNTER SERVICE")
        logger.info("=" * 60)
        logger.info("Dream Team Configuration:")
        for phase, tier in BOUNTY_HUNTER_TIERS.items():
            logger.info(f"  {phase.upper():10} → {tier}")
        logger.info("=" * 60)

        iterations = 0
        bounties_processed = 0

        while iterations < args.max_iterations and bounties_processed < args.max_todos:
            iterations += 1
            logger.info(f"\n>>> Bounty iteration {iterations}/{args.max_iterations}")

            if run_bounty_hunter_step(client, repo_root=args.repo_root, dry_run=args.dry_run):
                bounties_processed += 1
            else:
                logger.info("No bounties available.")
                break

            if iterations < args.max_iterations:
                logger.info(f"Sleeping {args.sleep}s...")
                time.sleep(args.sleep)

        logger.info(f"\nBounty Hunter complete: {iterations} iterations, {bounties_processed} bounties processed")

if __name__ == "__main__":
    main()
