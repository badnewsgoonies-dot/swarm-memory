#!/usr/bin/env python3
"""
mem-notify-daemon.py - Memory sync notification daemon

Polls memory.db for new task-related glyphs and emits JSON notifications.
Enables multiple agents/chats to stay in sync on task progress.

How to run:
    ./mem-notify-daemon.py                      # Poll continuously, emit to stdout
    ./mem-notify-daemon.py --once               # Single poll, then exit
    ./mem-notify-daemon.py --reset              # Clear state and re-emit all
    ./mem-notify-daemon.py --mailbox events.jsonl   # Also append to file (for Chat B)

Options:
    --once              Run one poll cycle and exit
    --reset             Clear state file and re-emit all notifications
    --mailbox FILE      Append JSON notifications to FILE (JSONL format)
                        File is created if it doesn't exist. Writes are flushed
                        immediately so other processes can tail the file.

Environment:
    MEMORY_DB    Path to memory.db (default: ./memory.db)

State file:
    notify_state.json   Stores {"last_id": N} to track progress

Output (one JSON object per line):
    {"event": "memory_update", "chunk_id": 1, "type": "R", "task_id": "vv-001", ...}

To stop:
    Ctrl+C or: touch notify_daemon.kill

Example - Chat B reads mailbox:
    # Terminal 1: Start daemon with mailbox
    ./mem-notify-daemon.py --mailbox /tmp/memory_events.jsonl

    # Terminal 2: Chat B tails the mailbox
    tail -f /tmp/memory_events.jsonl | jq .
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).parent.resolve()
DB_PATH = Path(os.environ.get("MEMORY_DB", SCRIPT_DIR / "memory.db"))
STATE_FILE = SCRIPT_DIR / "notify_state.json"
KILL_FILE = SCRIPT_DIR / "notify_daemon.kill"
POLL_INTERVAL = 2  # seconds
TASK_CONTEXT_SCRIPT = SCRIPT_DIR / "mem-task-context.sh"

# Glyph types we care about (TODO, ATTEMPT, RESULT, LESSON, PHASE)
TASK_TYPES = ('T', 'M', 'R', 'L', 'P')


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def load_state() -> dict:
    """Load last_id from state file, or return default."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log_warn(f"Failed to load state: {e}")
    return {"last_id": 0}


def save_state(state: dict):
    """Atomically save state to file (write-then-rename)."""
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f)
    tmp.rename(STATE_FILE)


# ---------------------------------------------------------------------------
# Logging (stderr, so stdout stays clean for JSON)
# ---------------------------------------------------------------------------

def log_info(msg: str):
    print(f"[INFO] {msg}", file=sys.stderr)


def log_warn(msg: str):
    print(f"[WARN] {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Database polling
# ---------------------------------------------------------------------------

def poll_chunks(last_id: int) -> list:
    """
    Query chunks with id > last_id, filter to task-related types with task_id.
    Handles DB locked errors with retry.
    """
    query = """
        SELECT id, anchor_type, task_id, anchor_topic, text
        FROM chunks
        WHERE id > ?
        ORDER BY id ASC
    """

    max_retries = 3
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=5)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query, (last_id,))
            rows = cursor.fetchall()
            conn.close()

            # Filter: task-related types with non-null task_id
            results = []
            for row in rows:
                if row["anchor_type"] in TASK_TYPES and row["task_id"]:
                    results.append(dict(row))
            return results

        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < max_retries - 1:
                log_warn(f"DB locked, retry {attempt + 1}/{max_retries}")
                time.sleep(0.5)
            else:
                raise

    return []


# ---------------------------------------------------------------------------
# Context generation
# ---------------------------------------------------------------------------

def get_task_context(task_id: str) -> str:
    """
    Call mem-task-context.sh to get context bundle.
    Returns empty string on failure.
    """
    if not TASK_CONTEXT_SCRIPT.exists():
        log_warn(f"Script not found: {TASK_CONTEXT_SCRIPT}")
        return ""

    try:
        result = subprocess.run(
            [str(TASK_CONTEXT_SCRIPT), "--task", task_id, "--limit", "20"],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        log_warn(f"Context timeout for task {task_id}")
        return ""
    except Exception as e:
        log_warn(f"Context failed for task {task_id}: {e}")
        return ""


# ---------------------------------------------------------------------------
# Notification emission
# ---------------------------------------------------------------------------

def emit_notification(chunk: dict, context: str, mailbox_file=None):
    """Print JSON notification to stdout, optionally also to mailbox file."""
    notification = {
        "event": "memory_update",
        "chunk_id": chunk["id"],
        "type": chunk["anchor_type"],
        "task_id": chunk["task_id"],
        "topic": chunk.get("anchor_topic") or "",
        "text": chunk.get("text") or "",
        "context": context
    }
    json_line = json.dumps(notification)

    # Always print to stdout
    print(json_line, flush=True)

    # Also write to mailbox file if provided
    if mailbox_file is not None:
        mailbox_file.write(json_line + "\n")
        mailbox_file.flush()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_daemon(once: bool = False, mailbox_path: str = None):
    """Main polling loop."""
    if not DB_PATH.exists():
        log_warn(f"Database not found: {DB_PATH}")
        sys.exit(1)

    state = load_state()
    last_id = state.get("last_id", 0)
    log_info(f"Starting, last_id={last_id}")

    # Open mailbox file if specified (append mode, create if needed)
    mailbox_file = None
    if mailbox_path:
        mailbox_file = open(mailbox_path, "a")
        log_info(f"Writing to mailbox: {mailbox_path}")

    try:
        while True:
            # Check kill switch
            if KILL_FILE.exists():
                log_info("Kill switch detected, exiting")
                KILL_FILE.unlink()
                break

            # Poll for new chunks
            chunks = poll_chunks(last_id)

            for chunk in chunks:
                task_id = chunk["task_id"]
                chunk_id = chunk["id"]

                # Get context bundle
                context = get_task_context(task_id)

                # Emit notification
                emit_notification(chunk, context, mailbox_file)
                log_info(f"Emitted chunk={chunk_id} type={chunk['anchor_type']} task={task_id}")

                # Update last_id
                last_id = chunk_id

            # Save state after each batch
            if chunks:
                state["last_id"] = last_id
                save_state(state)

            # Exit if --once mode
            if once:
                log_info("Single poll complete")
                break

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        log_info("Interrupted")
    finally:
        state["last_id"] = last_id
        save_state(state)
        if mailbox_file:
            mailbox_file.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Memory sync notification daemon",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run one poll cycle and exit"
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Clear state file and re-emit all notifications"
    )
    parser.add_argument(
        "--mailbox", metavar="FILE",
        help="Append JSON notifications to FILE (JSONL format)"
    )

    args = parser.parse_args()

    if args.reset and STATE_FILE.exists():
        STATE_FILE.unlink()
        log_info("State reset")

    run_daemon(once=args.once, mailbox_path=args.mailbox)


if __name__ == "__main__":
    main()
