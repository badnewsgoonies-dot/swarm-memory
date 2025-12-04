#!/usr/bin/env python3
"""
scheduler.py - Minimal long-running scheduler for planner + orchestrator lanes.

Features:
- Tick forever (or once with --once), reclaiming stale IN_PROGRESS tasks.
- Prefer orchestrator tasks whose TODO text starts with an ORCHESTRATE: prefix.
- Launch planner worker (agent_loop) for regular TODOs.
"""

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

from task_claims import reopen_stale_tasks

SCRIPT_DIR = Path(__file__).parent
DB_PATH = Path(os.environ.get("MEMORY_DB", SCRIPT_DIR / "memory.db"))


def parse_task_id(links: Optional[str], db_id: int, explicit: Optional[str] = None) -> str:
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
    return f"db-{db_id}"


def fetch_open_tasks(limit: int = 10) -> List[Dict[str, str]]:
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, anchor_topic, text, importance, links, task_id
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
        LIMIT ?
        """,
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()

    tasks: List[Dict[str, str]] = []
    for row in rows:
        db_id, topic, text, importance, links, explicit_task_id = row
        tasks.append(
            {
                "db_id": db_id,
                "task_id": parse_task_id(links, db_id, explicit_task_id),
                "topic": topic or "general",
                "text": text or "",
                "importance": importance or "M",
            }
        )
    return tasks


def find_orchestrate_candidate(prefix: str) -> Optional[Dict[str, str]]:
    prefix_upper = prefix.upper()
    for task in fetch_open_tasks(limit=20):
        if task["task_id"].startswith("db-"):
            continue  # mem-orchestrate looks up tasks by custom id; skip if missing
        if task["text"].strip().upper().startswith(prefix_upper):
            return task
    return None


def has_open_tasks() -> bool:
    if not DB_PATH.exists():
        return False
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT 1 FROM chunks
        WHERE anchor_type = 'T' AND anchor_choice = 'OPEN'
        LIMIT 1
        """
    )
    row = cursor.fetchone()
    conn.close()
    return bool(row)


def launch_planner(tier: str, dry_run: bool = False) -> int:
    cmd = [
        "python3",
        str(SCRIPT_DIR / "agent_loop.py"),
        "--mode",
        "worker",
        "--tier",
        tier,
    ]
    logging.info("Planner: %s", " ".join(cmd))
    if dry_run:
        return 0
    result = subprocess.run(cmd, cwd=SCRIPT_DIR)
    return result.returncode


def launch_orchestrator(task_id: str, args) -> subprocess.Popen:
    cmd = [str(SCRIPT_DIR / "mem-orchestrate.sh"), task_id]
    if args.repo_root:
        cmd.append(args.repo_root)
    cmd.extend(
        [
            "--max-iterations",
            str(args.orchestrator_iterations),
            "--llm",
            args.orchestrator_llm,
        ]
    )
    if args.restricted:
        cmd.append("--restricted")
    if args.verbose:
        cmd.append("--verbose")
    if args.dry_run:
        cmd.append("--dry-run")

    logging.info("Orchestrator: %s", " ".join(cmd))
    return subprocess.Popen(cmd, cwd=SCRIPT_DIR)


def main():
    parser = argparse.ArgumentParser(description="Minimal task scheduler")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between ticks")
    parser.add_argument("--planner-tier", default="smart", help="LLM tier for planner")
    parser.add_argument("--orchestrate-prefix", default="ORCHESTRATE:", help="Prefix marking orchestrator-owned tasks")
    parser.add_argument("--max-orchestrators", type=int, default=1, help="Max concurrent orchestrator processes")
    parser.add_argument("--orchestrator-iterations", type=int, default=50, help="Max iterations for mem-orchestrate")
    parser.add_argument("--orchestrator-llm", default="claude", help="LLM provider for mem-orchestrate")
    parser.add_argument("--repo-root", default="", help="Repo root to pass to mem-orchestrate.sh")
    parser.add_argument("--ttl-minutes", type=int, default=45, help="Minutes before reclaiming IN_PROGRESS tasks")
    parser.add_argument("--restricted", action="store_true", help="Run mem-orchestrate without --unrestricted")
    parser.add_argument("--verbose", action="store_true", help="Pass --verbose to mem-orchestrate")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without running them")
    parser.add_argument("--once", action="store_true", help="Run a single iteration then exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if not DB_PATH.exists():
        logging.error("Memory DB not found: %s", DB_PATH)
        return 1

    running_orchestrators: List[Dict[str, object]] = []

    try:
        while True:
            reopened = reopen_stale_tasks(DB_PATH, ttl_minutes=args.ttl_minutes)
            if reopened:
                logging.info("Reopened stale tasks: %s", ", ".join(reopened))

            # Prune finished orchestrator processes
            still_running: List[Dict[str, object]] = []
            for entry in running_orchestrators:
                proc: subprocess.Popen = entry["proc"]  # type: ignore[assignment]
                ret = proc.poll()
                if ret is None:
                    still_running.append(entry)
                else:
                    logging.info("Orchestrator for %s exited with code %s", entry["task_id"], ret)
            running_orchestrators = still_running

            # Prefer starting orchestrator tasks first
            orch_candidate = find_orchestrate_candidate(args.orchestrate_prefix)
            if orch_candidate:
                if len(running_orchestrators) < args.max_orchestrators:
                    logging.info("Dispatching orchestrator for %s (%s)", orch_candidate["task_id"], orch_candidate["topic"])
                    if not args.dry_run:
                        proc = launch_orchestrator(orch_candidate["task_id"], args)
                        running_orchestrators.append({"task_id": orch_candidate["task_id"], "proc": proc})
                    if args.once:
                        break
                    time.sleep(args.interval)
                    continue  # avoid planner claiming orchestrator-tagged tasks
                else:
                    logging.info("Orchestrator task %s pending; max orchestrators running", orch_candidate["task_id"])
                    if args.once:
                        break
                    time.sleep(args.interval)
                    continue

            # Planner lane
            if has_open_tasks():
                rc = launch_planner(args.planner_tier, dry_run=args.dry_run)
                if rc != 0:
                    logging.error("Planner exited with code %s", rc)
            else:
                logging.info("No OPEN tasks found.")

            if args.once:
                break

            time.sleep(args.interval)
    except KeyboardInterrupt:
        logging.info("Scheduler interrupted, exiting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
