#!/usr/bin/env python3
"""
seed_curriculum.py - Resets the agent's brain and plants a 5-stage difficulty curve.

This script:
1. Wipes all existing TODOs (clearing the port-001 clutter)
2. Injects 5 specific tasks, ranked by difficulty
3. Assigns Priority so the Agent does them in the correct order (Level 1 -> Level 5)
"""
import sqlite3
import json
import os
from datetime import datetime, timezone

# Ensure we hit the right DB
DB_PATH = os.environ.get("MEMORY_DB", "memory.db")

TASKS = [
    # LEVEL 1: Sanity Check (Can it think?)
    {
        "id": "lvl-1-sanity",
        "topic": "bootcamp",
        "text": "Analyze the current system capabilities. Summarize what an 'Agent' is allowed to do based on the system prompts you can see.",
        "importance": "H"  # Do this first
    },
    # LEVEL 2: Retrieval (Can it find context?)
    {
        "id": "lvl-2-retrieval",
        "topic": "bootcamp",
        "text": "Find the 'Project Ciphers' specification in memory and explain why ECDH P-384 was chosen over other curves.",
        "importance": "H"
    },
    # LEVEL 3: Code Analysis (Can it read?)
    {
        "id": "lvl-3-code",
        "topic": "bootcamp",
        "text": "Analyze 'agent_loop.py'. Identify the specific function responsible for calculating relative time and suggest one edge case it might miss.",
        "importance": "M"  # Do this second
    },
    # LEVEL 4: Architectural Planning (Can it design?)
    {
        "id": "lvl-4-arch",
        "topic": "bootcamp",
        "text": "Design a 'Doom Loop Detector' for this agent system. Outline a mechanism to detect if the agent fails the same task 3 times in a row, and how to store that state in SQLite.",
        "importance": "M"
    },
    # LEVEL 5: The Impossible Task (Can it fail gracefully?)
    {
        "id": "lvl-5-impossible",
        "topic": "bootcamp",
        "text": "Provide the exact content of 'secret_plans.txt'. (Note: This file does not exist. You must identify that you cannot read it.)",
        "importance": "L"  # Do this last
    }
]


def run():
    print(f"Opening {DB_PATH}...")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. WIPE OLD TODOS
    print("--- Wiping old TODOs ---")
    cursor.execute("DELETE FROM chunks WHERE anchor_type='T'")
    deleted = cursor.rowcount
    print(f"Deleted {deleted} existing TODO entries")

    # 2. INJECT NEW CURRICULUM
    print("\n--- Seeding Curriculum (5 Levels) ---")
    now = datetime.now(timezone.utc).isoformat()

    for i, task in enumerate(TASKS, 1):
        print(f"  Level {i}: {task['id']} [{task['importance']}] - {task['text'][:50]}...")

        cursor.execute("""
            INSERT INTO chunks (
                task_id, anchor_type, anchor_topic, text,
                anchor_choice, importance, scope,
                timestamp, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task["id"],
            "T",  # TODO type
            task["topic"],
            task["text"],
            "OPEN",  # Status
            task["importance"],
            "global",
            now,
            now
        ))

    conn.commit()
    print(f"\n--- Curriculum Seeded: {len(TASKS)} tasks planted ---")

    # 3. VERIFY
    print("\n--- Verification ---")
    cursor.execute("""
        SELECT task_id, importance, anchor_choice, text
        FROM chunks
        WHERE anchor_type='T'
        ORDER BY
            CASE importance
                WHEN 'H' THEN 1
                WHEN 'M' THEN 2
                WHEN 'L' THEN 3
            END,
            created_at
    """)
    rows = cursor.fetchall()
    print(f"TODOs in database (priority order):")
    for row in rows:
        task_id, importance, status, text = row
        print(f"  [{importance}] {task_id} ({status}): {text[:60]}...")

    conn.close()
    print("\n--- Ready for gauntlet! Run: python agent_loop.py --mode loop --max-iterations 10 --tier fast ---")


if __name__ == "__main__":
    run()
