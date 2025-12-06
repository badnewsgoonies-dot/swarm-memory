#!/usr/bin/env python3
"""
agent_loop.py - Minimal manager/worker orchestration loop

Modes:
  --mode worker   : Process one open TODO
  --mode manager  : Review recent results, create follow-up TODOs
  --mode loop     : Alternate worker/manager with caps

The WORKER agent (acts as PLANNER):
  1. Finds an OPEN TODO
  2. Builds context via mem-task-context.sh
  3. Calls LLM to ANALYZE and PLAN (not execute)
  4. Parses ATTEMPT/RESULT/LESSON from response
  5. Writes glyphs to memory, updates TODO status

  NOTE: The worker does NOT execute code or run tests. It produces
  analysis and actionable plans that a human or executor can follow.

The MANAGER agent:
  1. Queries recent RESULT glyphs
  2. Calls LLM to review and plan follow-ups
  3. Parses TODO/FEEDBACK from response
  4. Creates new TODOs and logs feedback

Usage:
  python agent_loop.py --mode worker [--tier smart] [--dry-run]
  python agent_loop.py --mode manager [--tier smart]
  python agent_loop.py --mode loop [--max-iterations 20] [--max-todos 5]
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any

# Add script directory to path for imports
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from llm_client import LLMClient, LLMResponse
from task_claims import TaskRecord, claim_next_open_todo

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

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
    new_todos: List[Dict[str, str]]  # {id, topic, text}
    feedback: List[Dict[str, str]]   # {task_id, text}
    raw_response: str

# =============================================================================
# SHELL COMMAND HELPERS
# =============================================================================

def run_shell(cmd: List[str], capture: bool = True) -> Tuple[int, str, str]:
    """Run shell command, return (returncode, stdout, stderr)"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=30,
            cwd=SCRIPT_DIR
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "Command timed out"
    except Exception as e:
        return 1, "", str(e)

def log_to_memory(msg_type: str, topic: str, text: str, source: str = "agent_loop"):
    """Log a glyph to memory via mem-db.sh"""
    cmd = [
        str(SCRIPT_DIR / "mem-db.sh"), "write",
        f"t={msg_type}",
        f"topic={topic}",
        f"source={source}",
        f"text={text}"
    ]
    run_shell(cmd)

# =============================================================================
# TODO OPERATIONS
# =============================================================================

def find_open_todo() -> Optional[TodoItem]:
    """
    Atomically claim one OPEN TODO and mark it IN_PROGRESS with ownership metadata.
    """
    db_path = os.environ.get("MEMORY_DB", str(SCRIPT_DIR / "memory.db"))
    claim_owner = os.environ.get("TODO_OWNER", "agent_loop")
    claim_role = os.environ.get("TODO_ROLE", "planner")
    claim_chat = os.environ.get("CHAT_ID") or f"{claim_role}-{os.getpid()}"
    ttl_minutes = int(os.environ.get("TODO_CLAIM_TTL_MINUTES", "45"))

    claimed: Optional[TaskRecord] = claim_next_open_todo(
        Path(db_path),
        owner=claim_owner,
        role=claim_role,
        chat_id=claim_chat,
        ttl_minutes=ttl_minutes
    )

    if not claimed:
        return None

    return TodoItem(
        task_id=claimed.task_id,
        topic=claimed.topic or "general",
        text=claimed.text or "",
        status=claimed.status or "IN_PROGRESS",
        importance=claimed.importance or "M",
        db_id=claimed.db_id
    )

def get_task_context(task_id: str, limit: int = 20) -> str:
    """Get context bundle for a task via mem-task-context.sh"""
    cmd = [
        str(SCRIPT_DIR / "mem-task-context.sh"),
        "--task", task_id,
        "--limit", str(limit)
    ]

    rc, stdout, stderr = run_shell(cmd)

    if rc != 0:
        logger.warning(f"mem-task-context.sh failed: {stderr}")
        return f"[No context available for task {task_id}]"

    return stdout.strip() or f"[No context found for task {task_id}]"

def get_topic_lessons(topic: str, limit: int = 5) -> str:
    """Get lessons for a topic via mem-lessons.sh"""
    cmd = [
        str(SCRIPT_DIR / "mem-lessons.sh"),
        "--topic", topic,
        "--limit", str(limit)
    ]

    rc, stdout, stderr = run_shell(cmd)

    if rc != 0 or not stdout.strip():
        return ""

    return stdout.strip()

def update_todo_status(task_id: str, status: str):
    """Update TODO status via mem-todo.sh"""
    cmd = [
        str(SCRIPT_DIR / "mem-todo.sh"),
        "update", task_id,
        "--status", status
    ]

    rc, stdout, stderr = run_shell(cmd)

    if rc != 0:
        logger.error(f"Failed to update TODO status: {stderr}")
    else:
        logger.info(f"Updated {task_id} -> {status}")

# =============================================================================
# LOGGING OPERATIONS (ATTEMPT, RESULT, LESSON)
# =============================================================================

def log_attempt(task_id: str, text: str, source: str = "worker"):
    """Log an ATTEMPT glyph via mem-log.sh"""
    cmd = [
        str(SCRIPT_DIR / "mem-log.sh"),
        "attempt",
        "--task", task_id,
        "--text", text,
        "--source", source
    ]

    rc, stdout, stderr = run_shell(cmd)

    if rc != 0:
        logger.error(f"Failed to log ATTEMPT: {stderr}")

def log_result(task_id: str, success: bool, text: str, source: str = "worker", metric: str = None):
    """Log a RESULT glyph via mem-log.sh"""
    cmd = [
        str(SCRIPT_DIR / "mem-log.sh"),
        "result",
        "--task", task_id,
        "--success", "true" if success else "false",
        "--text", text,
        "--source", source
    ]

    if metric:
        cmd.extend(["--metric", metric])

    rc, stdout, stderr = run_shell(cmd)

    if rc != 0:
        logger.error(f"Failed to log RESULT: {stderr}")

def log_lesson(topic: str, text: str, task_id: str = None, source: str = "worker"):
    """Log a LESSON glyph via mem-log.sh"""
    cmd = [
        str(SCRIPT_DIR / "mem-log.sh"),
        "lesson",
        "--topic", topic,
        "--text", text,
        "--source", source
    ]

    if task_id:
        cmd.extend(["--task", task_id])

    rc, stdout, stderr = run_shell(cmd)

    if rc != 0:
        logger.error(f"Failed to log LESSON: {stderr}")

def create_todo(task_id: str, topic: str, text: str, source: str = "manager", importance: str = "M"):
    """Create a new TODO via mem-todo.sh"""
    cmd = [
        str(SCRIPT_DIR / "mem-todo.sh"),
        "add",
        "--id", task_id,
        "--topic", topic,
        "--text", text,
        "--source", source,
        "--importance", importance
    ]

    rc, stdout, stderr = run_shell(cmd)

    if rc != 0:
        logger.error(f"Failed to create TODO: {stderr}")
    else:
        logger.info(f"Created TODO: {task_id}")

# =============================================================================
# WORKER PROMPT AND PARSING
# =============================================================================

WORKER_SYSTEM_PROMPT = """You are a PLANNER agent in a task analysis system.

CRITICAL CONSTRAINT: You CANNOT actually execute code, run tests, edit files, or perform any real actions.
You are a PLANNING and ANALYSIS agent only. Your job is to think deeply about the task and produce a
high-quality plan that a human or execution agent can follow.

You will be given:
- A TODO task with id, topic, and description
- A bundle of relevant memory context for this task
- Lessons learned from previous work on similar topics

Your job is to:
1. ANALYZE the TODO task thoroughly
2. REVIEW the memory context for relevant information and patterns
3. IDENTIFY what needs to be done, potential approaches, and trade-offs
4. PROPOSE a concrete, actionable plan with specific steps
5. SUGGEST commands, code changes, or tests that WOULD be run (hypothetically)
6. Be HONEST about uncertainty, missing information, or ambiguity

HONESTY RULES (VERY IMPORTANT):
- NEVER claim you "ran tests", "implemented", "fixed", "executed", or "completed" anything
- NEVER say "I added", "I modified", "I created" for code/files - you cannot do these things
- Instead use language like:
  - "I WOULD run: npm test -- --runTestsByPath ..."
  - "The suggested fix is: ..."
  - "The implementation plan is: ..."
  - "This SHOULD be changed to: ..."
  - "Proposed test cases: ..."
- Make clear the difference between ANALYSIS (what you did) and PROPOSED ACTIONS (what should be done)

CRITICAL: At the end of your response, you MUST output these three structured lines:

ATTEMPT <task_id>: <describe your reasoning approach and the analysis/planning work you performed>
RESULT <task_id>: success=<true|false> reason="<Did you produce a clear, actionable plan? If false, why - missing info, ambiguity, etc.>"
LESSON <topic>: <a distilled lesson about how to reason about or plan tasks like this in the future>

Example output endings for a PLANNER:
ATTEMPT vv-001: Analyzed component structure, identified 3 files needing type exports, proposed fix with code snippets
RESULT vv-001: success=true reason="Produced actionable plan with specific file paths and code changes"
LESSON VV: When seeing 'cannot find module' errors, first check index.ts barrel exports before deeper investigation

Example of UNSUCCESSFUL planning:
ATTEMPT vv-002: Analyzed test requirements but context lacks information about existing test framework
RESULT vv-002: success=false reason="Cannot produce specific plan - need to know which test runner (Jest/Vitest) is configured"
LESSON VV: Before planning test additions, verify the test framework and existing test patterns in the codebase

Do NOT omit these lines. They are required for tracking."""

def build_worker_prompt(todo: TodoItem, context: str, lessons: str = "") -> str:
    """Build the full prompt for the worker (planner) LLM.

    The worker acts as a PLANNER - it analyzes the TODO and context,
    then produces an actionable plan. It does NOT execute code.
    """
    parts = [
        "=" * 60,
        "TODO TASK",
        "=" * 60,
        f"ID: {todo.task_id}",
        f"Topic: {todo.topic}",
        f"Importance: {todo.importance}",
        f"Description: {todo.text}",
        "",
        "=" * 60,
        "MEMORY CONTEXT",
        "=" * 60,
        context,
    ]

    if lessons:
        parts.extend([
            "",
            "=" * 60,
            "LESSONS TO FOLLOW",
            "=" * 60,
            lessons,
        ])

    parts.extend([
        "",
        "=" * 60,
        "YOUR TASK (PLANNING ONLY)",
        "=" * 60,
        "Analyze the TODO and the context above. Your job is to PLAN, not execute.",
        "",
        "You should:",
        "- Understand what the task is asking for",
        "- Review the memory context for relevant patterns, decisions, and prior work",
        "- Apply any lessons that are relevant",
        "- Propose a concrete plan with specific steps, file paths, and code snippets",
        "- Suggest commands that WOULD be run (you cannot run them yourself)",
        "- Be honest about any gaps in information or uncertainty",
        "",
        "Do NOT claim you actually executed code or ran tests. You can only think and plan.",
        "",
        "End with ATTEMPT, RESULT, and LESSON lines as described in the system prompt.",
    ])

    return "\n".join(parts)

def parse_worker_response(response: str, todo: TodoItem) -> WorkerOutput:
    """
    Parse worker response to extract ATTEMPT, RESULT, LESSON lines.
    Returns WorkerOutput with extracted fields.
    """
    attempt_text = ""
    result_success = False
    result_reason = ""
    lesson_topic = todo.topic
    lesson_text = ""

    # Pattern: ATTEMPT <id>: <text>
    attempt_match = re.search(
        r'^ATTEMPT\s+[\w\-]+:\s*(.+)$',
        response,
        re.MULTILINE | re.IGNORECASE
    )
    if attempt_match:
        attempt_text = attempt_match.group(1).strip()

    # Pattern: RESULT <id>: success=<bool> reason="<text>"
    result_match = re.search(
        r'^RESULT\s+[\w\-]+:\s*success\s*=\s*(true|false)\s*(?:reason\s*=\s*"([^"]*)")?',
        response,
        re.MULTILINE | re.IGNORECASE
    )
    if result_match:
        result_success = result_match.group(1).lower() == "true"
        result_reason = result_match.group(2) or ""
    else:
        # Try alternative format: RESULT <id>: success=<bool> <text>
        result_match2 = re.search(
            r'^RESULT\s+[\w\-]+:\s*success\s*=\s*(true|false)\s+(.+)$',
            response,
            re.MULTILINE | re.IGNORECASE
        )
        if result_match2:
            result_success = result_match2.group(1).lower() == "true"
            result_reason = result_match2.group(2).strip()

    # Pattern: LESSON <topic>: <text>
    lesson_match = re.search(
        r'^LESSON\s+([\w\-]+):\s*(.+)$',
        response,
        re.MULTILINE | re.IGNORECASE
    )
    if lesson_match:
        lesson_topic = lesson_match.group(1).strip()
        lesson_text = lesson_match.group(2).strip()

    # Fallback: if no structured output, try to infer from response
    if not attempt_text:
        attempt_text = f"Processed task (no structured ATTEMPT line found)"

    if not result_reason:
        # Look for success/failure indicators
        if "error" in response.lower() or "failed" in response.lower():
            result_success = False
            result_reason = "Inferred failure from response content"
        elif "complete" in response.lower() or "done" in response.lower():
            result_success = True
            result_reason = "Inferred success from response content"
        else:
            result_reason = "No explicit result provided"

    if not lesson_text:
        lesson_text = "No specific lesson extracted"

    return WorkerOutput(
        attempt_text=attempt_text,
        result_success=result_success,
        result_reason=result_reason,
        lesson_topic=lesson_topic,
        lesson_text=lesson_text,
        raw_response=response
    )

# =============================================================================
# MANAGER PROMPT AND PARSING
# =============================================================================

MANAGER_SYSTEM_PROMPT = """You are the MANAGER agent in a task execution system.

You review recent task RESULTS and LESSONS from worker agents.
Your job is to:
1. Provide a brief summary of progress
2. Identify any follow-up tasks needed
3. Suggest improvements or new directions

For any new TODOs, use this format:
TODO <id> <topic>: <short description>

For feedback on specific tasks:
FEEDBACK <task_id>: <your feedback>

Example:
TODO vv-002 VV: Add unit tests for the new type exports
FEEDBACK vv-001: Good fix, but consider adding regression tests

Keep your response concise and actionable."""

def get_recent_results(limit: int = 10, hours: int = 24) -> str:
    """Query recent RESULT glyphs from memory"""
    cmd = [
        str(SCRIPT_DIR / "mem-db.sh"),
        "query",
        "t=R",
        f"recent={hours}h",
        f"limit={limit}"
    ]

    rc, stdout, stderr = run_shell(cmd)

    if rc != 0:
        logger.warning(f"Failed to query results: {stderr}")
        return ""

    return stdout.strip()

def get_recent_lessons(limit: int = 10, hours: int = 24) -> str:
    """Query recent LESSON glyphs from memory"""
    cmd = [
        str(SCRIPT_DIR / "mem-db.sh"),
        "query",
        "t=L",
        f"recent={hours}h",
        f"limit={limit}"
    ]

    rc, stdout, stderr = run_shell(cmd)

    if rc != 0:
        return ""

    return stdout.strip()

def build_manager_prompt(results: str, lessons: str) -> str:
    """Build the full prompt for the manager LLM"""
    parts = [
        "=" * 60,
        "RECENT TASK RESULTS",
        "=" * 60,
    ]

    if results:
        parts.append(results)
    else:
        parts.append("[No recent results to review]")

    parts.extend([
        "",
        "=" * 60,
        "RECENT LESSONS LEARNED",
        "=" * 60,
    ])

    if lessons:
        parts.append(lessons)
    else:
        parts.append("[No recent lessons]")

    parts.extend([
        "",
        "=" * 60,
        "YOUR TASK",
        "=" * 60,
        "Review the above results and lessons.",
        "Provide feedback and suggest any follow-up TODOs.",
        "Use TODO and FEEDBACK lines as shown in the system prompt.",
    ])

    return "\n".join(parts)

def parse_manager_response(response: str) -> ManagerOutput:
    """
    Parse manager response to extract TODO and FEEDBACK lines.
    """
    new_todos = []
    feedback = []

    # Pattern: TODO <id> <topic>: <text>
    for match in re.finditer(
        r'^TODO\s+([\w\-]+)\s+([\w\-]+):\s*(.+)$',
        response,
        re.MULTILINE | re.IGNORECASE
    ):
        new_todos.append({
            "id": match.group(1).strip(),
            "topic": match.group(2).strip(),
            "text": match.group(3).strip()
        })

    # Pattern: FEEDBACK <task_id>: <text>
    for match in re.finditer(
        r'^FEEDBACK\s+([\w\-]+):\s*(.+)$',
        response,
        re.MULTILINE | re.IGNORECASE
    ):
        feedback.append({
            "task_id": match.group(1).strip(),
            "text": match.group(2).strip()
        })

    return ManagerOutput(
        new_todos=new_todos,
        feedback=feedback,
        raw_response=response
    )

# =============================================================================
# MAIN MODES
# =============================================================================

def run_worker_step(
    client: LLMClient,
    tier: str = "smart",
    dry_run: bool = False,
    source: str = "worker"
) -> bool:
    """
    Run one worker step: find TODO, execute, log results.

    Returns True if a TODO was processed, False if none available.
    """
    logger.info("=" * 60)
    logger.info("WORKER STEP")
    logger.info("=" * 60)

    # 1. Find an open TODO
    todo = find_open_todo()

    if not todo:
        logger.info("No OPEN TODOs found. Worker step complete.")
        return False

    logger.info(f"Found TODO: {todo.task_id}")
    logger.info(f"  Topic: {todo.topic}")
    logger.info(f"  Text: {todo.text[:100]}...")

    # 2. Get context
    context = get_task_context(todo.task_id)
    lessons = get_topic_lessons(todo.topic)

    logger.info(f"Context length: {len(context)} chars")

    # 3. Build prompt
    prompt = build_worker_prompt(todo, context, lessons)

    if dry_run:
        logger.info("[DRY RUN] Would send prompt to LLM:")
        logger.info(f"Prompt length: {len(prompt)} chars")
        logger.info(f"Tier: {tier}")
        return True

    # 4. Call LLM
    logger.info(f"Calling LLM (tier={tier})...")

    response = client.complete(
        prompt,
        tier=tier,
        system_prompt=WORKER_SYSTEM_PROMPT
    )

    if not response.success:
        logger.error(f"LLM call failed: {response.error}")
        # Log the failure
        log_attempt(todo.task_id, f"LLM call failed: {response.error}", source)
        log_result(todo.task_id, False, f"LLM error: {response.error}", source)
        return True  # We tried, count as processed

    logger.info(f"LLM response: {len(response.text)} chars, {response.latency_ms}ms")

    # 5. Parse response
    output = parse_worker_response(response.text, todo)

    logger.info(f"Parsed output:")
    logger.info(f"  ATTEMPT: {output.attempt_text[:80]}...")
    logger.info(f"  RESULT: success={output.result_success}, reason={output.result_reason[:50]}...")
    logger.info(f"  LESSON: [{output.lesson_topic}] {output.lesson_text[:50]}...")

    # 6. Log to memory
    log_attempt(todo.task_id, output.attempt_text, source)
    log_result(todo.task_id, output.result_success, output.result_reason, source)

    if output.lesson_text and output.lesson_text != "No specific lesson extracted":
        log_lesson(output.lesson_topic, output.lesson_text, todo.task_id, source)

    # 7. Update TODO status
    if output.result_success:
        update_todo_status(todo.task_id, "DONE")
    else:
        update_todo_status(todo.task_id, "BLOCKED")

    # 8. Summary
    logger.info("-" * 40)
    logger.info(f"Worker step complete:")
    logger.info(f"  Task: {todo.task_id}")
    logger.info(f"  Status: {'DONE' if output.result_success else 'BLOCKED'}")
    logger.info(f"  Lesson: {output.lesson_text[:60]}...")

    # Also log a summary glyph
    log_to_memory(
        "a",  # action
        "agent_loop",
        f"Worker processed {todo.task_id}: {'success' if output.result_success else 'failure'} - {output.result_reason[:100]}",
        source
    )

    return True

def run_manager_step(
    client: LLMClient,
    tier: str = "smart",
    dry_run: bool = False,
    source: str = "manager"
) -> bool:
    """
    Run one manager step: review results, create follow-ups.

    Returns True if there were results to review, False otherwise.
    """
    logger.info("=" * 60)
    logger.info("MANAGER STEP")
    logger.info("=" * 60)

    # 1. Get recent results and lessons
    results = get_recent_results(limit=10, hours=24)
    lessons = get_recent_lessons(limit=5, hours=24)

    if not results:
        logger.info("No recent results to review. Manager step complete.")
        return False

    logger.info(f"Found recent results ({len(results)} chars)")

    # 2. Build prompt
    prompt = build_manager_prompt(results, lessons)

    if dry_run:
        logger.info("[DRY RUN] Would send prompt to LLM:")
        logger.info(f"Prompt length: {len(prompt)} chars")
        return True

    # 3. Call LLM
    logger.info(f"Calling LLM (tier={tier})...")

    response = client.complete(
        prompt,
        tier=tier,
        system_prompt=MANAGER_SYSTEM_PROMPT
    )

    if not response.success:
        logger.error(f"LLM call failed: {response.error}")
        return True

    logger.info(f"LLM response: {len(response.text)} chars, {response.latency_ms}ms")

    # 4. Parse response
    output = parse_manager_response(response.text)

    logger.info(f"Parsed {len(output.new_todos)} new TODOs, {len(output.feedback)} feedbacks")

    # 5. Create new TODOs
    for todo in output.new_todos:
        logger.info(f"  Creating TODO: {todo['id']} [{todo['topic']}] {todo['text'][:50]}...")
        create_todo(
            task_id=todo["id"],
            topic=todo["topic"],
            text=todo["text"],
            source=source
        )

    # 6. Log feedback
    for fb in output.feedback:
        logger.info(f"  Feedback for {fb['task_id']}: {fb['text'][:50]}...")
        log_to_memory(
            "n",  # note
            "feedback",
            f"[{fb['task_id']}] {fb['text']}",
            source
        )

    # 7. Log manager action
    log_to_memory(
        "a",
        "agent_loop",
        f"Manager reviewed results: created {len(output.new_todos)} TODOs, {len(output.feedback)} feedbacks",
        source
    )

    logger.info("-" * 40)
    logger.info("Manager step complete")

    return True

def run_loop(
    client: LLMClient,
    max_iterations: int = 20,
    max_todos: int = 5,
    tier: str = "smart",
    sleep_seconds: int = 5,
    dry_run: bool = False
):
    """
    Run loop mode: alternate worker/manager steps.

    Safety caps:
    - max_iterations: Total loop iterations
    - max_todos: Maximum TODOs to process
    """
    logger.info("=" * 60)
    logger.info("LOOP MODE")
    logger.info(f"  max_iterations: {max_iterations}")
    logger.info(f"  max_todos: {max_todos}")
    logger.info(f"  tier: {tier}")
    logger.info(f"  sleep: {sleep_seconds}s")
    logger.info("=" * 60)

    todos_processed = 0
    iterations = 0
    consecutive_idle = 0

    while iterations < max_iterations and todos_processed < max_todos:
        iterations += 1
        logger.info(f"\n--- Iteration {iterations}/{max_iterations} (processed {todos_processed}/{max_todos} TODOs) ---\n")

        # Worker step
        if run_worker_step(client, tier=tier, dry_run=dry_run):
            todos_processed += 1
            consecutive_idle = 0
        else:
            consecutive_idle += 1

        # Sleep between steps
        if not dry_run:
            time.sleep(sleep_seconds)

        # Manager step (every 3 iterations or when idle)
        if iterations % 3 == 0 or consecutive_idle > 0:
            run_manager_step(client, tier=tier, dry_run=dry_run)

        # Stop if idle too long
        if consecutive_idle >= 3:
            logger.info("No work available for 3 iterations. Stopping loop.")
            break

        # Sleep between iterations
        if not dry_run:
            time.sleep(sleep_seconds)

    logger.info("\n" + "=" * 60)
    logger.info("LOOP COMPLETE")
    logger.info(f"  Iterations: {iterations}")
    logger.info(f"  TODOs processed: {todos_processed}")
    logger.info("=" * 60)

    # Final manager review
    logger.info("\nRunning final manager review...")
    run_manager_step(client, tier=tier, dry_run=dry_run)

# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Manager/Worker orchestration loop",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process one TODO
  python agent_loop.py --mode worker --tier smart

  # Review results and create follow-ups
  python agent_loop.py --mode manager --tier smart

  # Run automated loop with caps
  python agent_loop.py --mode loop --max-iterations 10 --max-todos 3

  # Dry run to see what would happen
  python agent_loop.py --mode worker --dry-run
"""
    )

    parser.add_argument(
        "--mode",
        choices=["worker", "manager", "loop"],
        required=True,
        help="Operation mode"
    )

    parser.add_argument(
        "--tier",
        default="smart",
        help="LLM tier to use (fast, code, smart, claude, codex, max). Default: smart"
    )

    parser.add_argument(
        "--max-iterations",
        type=int,
        default=20,
        help="Maximum iterations in loop mode. Default: 20"
    )

    parser.add_argument(
        "--max-todos",
        type=int,
        default=5,
        help="Maximum TODOs to process in loop mode. Default: 5"
    )

    parser.add_argument(
        "--sleep",
        type=int,
        default=5,
        help="Seconds to sleep between steps. Default: 5"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually call LLM or modify DB"
    )

    parser.add_argument(
        "--source",
        default=None,
        help="Source tag for logging (default: worker/manager)"
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Initialize LLM client
    client = LLMClient()

    # Determine source
    if args.source:
        source = args.source
    else:
        source = f"{args.mode}_{args.tier}"

    # Run appropriate mode
    if args.mode == "worker":
        run_worker_step(client, tier=args.tier, dry_run=args.dry_run, source=source)

    elif args.mode == "manager":
        run_manager_step(client, tier=args.tier, dry_run=args.dry_run, source=source)

    elif args.mode == "loop":
        run_loop(
            client,
            max_iterations=args.max_iterations,
            max_todos=args.max_todos,
            tier=args.tier,
            sleep_seconds=args.sleep,
            dry_run=args.dry_run
        )

if __name__ == "__main__":
    main()
