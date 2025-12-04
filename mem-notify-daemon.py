#!/usr/bin/env python3
"""
mem-notify-daemon.py - Memory sync notification daemon

Polls memory.db for new task-related glyphs and emits JSON notifications
with context bundles. Enables Chat B to stay in sync with Chat A's work.

Usage:
    ./mem-notify-daemon.py                          # Poll stdout, default 2s interval
    ./mem-notify-daemon.py --interval 5             # 5 second polling
    ./mem-notify-daemon.py --output file:events.jsonl
    ./mem-notify-daemon.py --output http://localhost:8080/notify
    ./mem-notify-daemon.py --once                   # Single poll, then exit
    ./mem-notify-daemon.py --task-filter vv-001     # Only notify for specific task

Output modes:
    stdout              Print JSON lines to stdout (default)
    file:<path>         Append JSON lines to file
    http:<url>          POST each notification to URL
    unix:<socket>       Write to Unix domain socket
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_DB = SCRIPT_DIR / "memory.db"
DEFAULT_STATE = SCRIPT_DIR / "notify_state.json"
DEFAULT_INTERVAL = 2.0

# Task-centric glyph types we care about
TASK_TYPES = ('T', 'M', 'R', 'L', 'P')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
log = logging.getLogger(__name__)


# =============================================================================
# STATE MANAGEMENT
# =============================================================================

def load_state(state_file: Path) -> dict:
    """Load daemon state from JSON file."""
    if state_file.exists():
        try:
            with open(state_file) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            log.warning(f"Failed to load state: {e}, starting fresh")
    return {"last_seen_id": 0}


def save_state(state_file: Path, state: dict):
    """Atomically save state to JSON file."""
    tmp_file = state_file.with_suffix('.tmp')
    with open(tmp_file, 'w') as f:
        json.dump(state, f)
    tmp_file.rename(state_file)


# =============================================================================
# DATABASE POLLING
# =============================================================================

def poll_new_chunks(db_path: Path, last_seen_id: int, task_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Poll for new task-related chunks since last_seen_id.

    Returns list of dicts with: id, anchor_type, task_id, anchor_topic, text, timestamp
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Build query with optional task_id filter
    query = """
        SELECT id, anchor_type, task_id, anchor_topic, text, timestamp
        FROM chunks
        WHERE id > ?
          AND anchor_type IN ('T', 'M', 'R', 'L', 'P')
          AND task_id IS NOT NULL
    """
    params = [last_seen_id]

    if task_filter:
        query += " AND task_id = ?"
        params.append(task_filter)

    query += " ORDER BY id ASC"

    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


# =============================================================================
# CONTEXT GENERATION
# =============================================================================

def get_task_context(task_id: str, limit: int = 20) -> str:
    """Call mem-task-context.sh to get context bundle for a task."""
    script = SCRIPT_DIR / "mem-task-context.sh"
    if not script.exists():
        log.warning(f"mem-task-context.sh not found at {script}")
        return ""

    try:
        result = subprocess.run(
            [str(script), "--task", task_id, "--limit", str(limit)],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        log.warning(f"Context generation timed out for task {task_id}")
        return ""
    except Exception as e:
        log.warning(f"Context generation failed for task {task_id}: {e}")
        return ""


# =============================================================================
# OUTPUT HANDLERS
# =============================================================================

class OutputHandler:
    """Base class for notification output."""
    def emit(self, notification: dict):
        raise NotImplementedError

    def close(self):
        pass


class StdoutHandler(OutputHandler):
    """Write JSON lines to stdout."""
    def emit(self, notification: dict):
        print(json.dumps(notification), flush=True)


class FileHandler(OutputHandler):
    """Append JSON lines to file."""
    def __init__(self, path: str):
        self.path = Path(path)
        self.file = open(self.path, 'a')

    def emit(self, notification: dict):
        self.file.write(json.dumps(notification) + '\n')
        self.file.flush()

    def close(self):
        self.file.close()


class HttpHandler(OutputHandler):
    """POST notifications to HTTP endpoint."""
    def __init__(self, url: str):
        self.url = url
        try:
            import requests
            self.requests = requests
        except ImportError:
            log.error("requests library required for HTTP output: pip install requests")
            sys.exit(1)

    def emit(self, notification: dict):
        try:
            resp = self.requests.post(
                self.url,
                json=notification,
                timeout=5,
                headers={"Content-Type": "application/json"}
            )
            if resp.status_code >= 400:
                log.warning(f"HTTP POST failed: {resp.status_code}")
        except Exception as e:
            log.warning(f"HTTP POST failed: {e}")


class UnixSocketHandler(OutputHandler):
    """Write to Unix domain socket."""
    def __init__(self, socket_path: str):
        import socket
        self.socket_path = socket_path
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(socket_path)

    def emit(self, notification: dict):
        msg = json.dumps(notification) + '\n'
        self.sock.sendall(msg.encode('utf-8'))

    def close(self):
        self.sock.close()


def create_output_handler(output_spec: str) -> OutputHandler:
    """Create appropriate output handler from spec string."""
    if output_spec == "stdout":
        return StdoutHandler()
    elif output_spec.startswith("file:"):
        return FileHandler(output_spec[5:])
    elif output_spec.startswith("http:") or output_spec.startswith("https:"):
        return HttpHandler(output_spec)
    elif output_spec.startswith("unix:"):
        return UnixSocketHandler(output_spec[5:])
    else:
        log.error(f"Unknown output spec: {output_spec}")
        sys.exit(1)


# =============================================================================
# NOTIFICATION GENERATION
# =============================================================================

def build_notification(chunk: Dict[str, Any], context: str) -> dict:
    """Build notification dict from chunk and context."""
    return {
        "event": "memory_update",
        "chunk_id": chunk["id"],
        "type": chunk["anchor_type"],
        "task_id": chunk["task_id"],
        "topic": chunk.get("anchor_topic") or "",
        "text": chunk.get("text") or "",
        "timestamp": chunk.get("timestamp") or "",
        "context": context,
        "emitted_at": datetime.now(timezone.utc).isoformat()
    }


# =============================================================================
# MAIN DAEMON LOOP
# =============================================================================

def run_daemon(
    db_path: Path,
    state_file: Path,
    output: OutputHandler,
    interval: float,
    task_filter: Optional[str],
    once: bool,
    verbose: bool
):
    """Main daemon loop."""
    if verbose:
        log.setLevel(logging.DEBUG)

    # Load state
    state = load_state(state_file)
    last_seen_id = state.get("last_seen_id", 0)
    log.info(f"Starting daemon, last_seen_id={last_seen_id}")

    # Kill switch
    kill_file = SCRIPT_DIR / "notify_daemon.kill"

    try:
        while True:
            # Check kill switch
            if kill_file.exists():
                log.info("Kill switch detected, exiting")
                kill_file.unlink()
                break

            # Poll for new chunks
            chunks = poll_new_chunks(db_path, last_seen_id, task_filter)

            if chunks:
                log.debug(f"Found {len(chunks)} new chunks")

                for chunk in chunks:
                    task_id = chunk["task_id"]
                    chunk_id = chunk["id"]

                    # Get context bundle
                    context = get_task_context(task_id)

                    # Build and emit notification
                    notification = build_notification(chunk, context)
                    output.emit(notification)

                    log.info(f"Emitted: chunk={chunk_id} type={chunk['anchor_type']} task={task_id}")

                    # Update last_seen_id
                    last_seen_id = chunk_id

                # Persist state after batch
                state["last_seen_id"] = last_seen_id
                save_state(state_file, state)

            if once:
                log.info("Single poll complete, exiting")
                break

            time.sleep(interval)

    except KeyboardInterrupt:
        log.info("Interrupted, saving state")
    finally:
        state["last_seen_id"] = last_seen_id
        save_state(state_file, state)
        output.close()


# =============================================================================
# CLI
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Memory sync notification daemon",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--db", type=Path, default=DEFAULT_DB,
        help=f"Path to memory.db (default: {DEFAULT_DB})"
    )
    parser.add_argument(
        "--state-file", type=Path, default=DEFAULT_STATE,
        help=f"State file path (default: {DEFAULT_STATE})"
    )
    parser.add_argument(
        "--output", "-o", type=str, default="stdout",
        help="Output mode: stdout, file:<path>, http:<url>, unix:<socket>"
    )
    parser.add_argument(
        "--interval", "-i", type=float, default=DEFAULT_INTERVAL,
        help=f"Poll interval in seconds (default: {DEFAULT_INTERVAL})"
    )
    parser.add_argument(
        "--task-filter", "-t", type=str, default=None,
        help="Only notify for specific task_id"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run one poll cycle and exit"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging"
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Reset state (re-emit all notifications from beginning)"
    )

    args = parser.parse_args()

    # Validate database exists
    if not args.db.exists():
        log.error(f"Database not found: {args.db}")
        sys.exit(1)

    # Handle reset
    if args.reset and args.state_file.exists():
        args.state_file.unlink()
        log.info("State reset")

    # Create output handler
    output = create_output_handler(args.output)

    # Run daemon
    run_daemon(
        db_path=args.db,
        state_file=args.state_file,
        output=output,
        interval=args.interval,
        task_filter=args.task_filter,
        once=args.once,
        verbose=args.verbose
    )


if __name__ == "__main__":
    main()
