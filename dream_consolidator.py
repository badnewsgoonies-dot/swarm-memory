#!/usr/bin/env python3
"""
dream_consolidator.py - Turns Memories into Training Data

Extracts LESSON glyphs, groups them by topic, and formats them for LLM Fine-Tuning.

Usage:
    python dream_consolidator.py                     # Generate training_data.jsonl
    python dream_consolidator.py --topic port-ui    # Only lessons from specific topic
    python dream_consolidator.py --stats            # Show lesson statistics
    python dream_consolidator.py --onboard          # Generate onboarding prompts
"""
import sqlite3
import json
import os
import argparse
from datetime import datetime
from collections import defaultdict

# Configuration
DB_PATH = os.environ.get("MEMORY_DB", "memory.db")
OUTPUT_FILE = "training_data.jsonl"
ONBOARD_DIR = "onboard"


def get_db_connection():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database not found at {DB_PATH}")
        return None
    return sqlite3.connect(DB_PATH)


def fetch_lessons(topic_filter: str = None):
    """Fetch lessons from database, optionally filtered by topic"""
    conn = get_db_connection()
    if not conn:
        return []

    cursor = conn.cursor()

    if topic_filter:
        cursor.execute("""
            SELECT anchor_topic, text, timestamp, task_id
            FROM chunks
            WHERE anchor_type='L' AND anchor_topic LIKE ?
            ORDER BY anchor_topic, timestamp ASC
        """, (f"%{topic_filter}%",))
    else:
        cursor.execute("""
            SELECT anchor_topic, text, timestamp, task_id
            FROM chunks
            WHERE anchor_type='L'
            ORDER BY anchor_topic, timestamp ASC
        """)

    rows = cursor.fetchall()
    conn.close()
    return rows


def format_for_training(topic: str, lessons: list) -> dict:
    """
    Formats a set of lessons into a training example.
    Format: User asks for advice on [Topic], Assistant provides compiled wisdom.
    """
    # Construct the "Wisdom" block
    wisdom_lines = []
    for lesson in lessons:
        # Clean the lesson text
        text = lesson.replace("[LESSON]", "").strip()
        if text:
            wisdom_lines.append(f"- {text}")

    wisdom = "\n".join(wisdom_lines)

    # Create a chat-style training example (Llama 3 / OpenAI format)
    return {
        "messages": [
            {
                "role": "system",
                "content": "You are an expert software architect. Provide best practices based on historical project lessons."
            },
            {
                "role": "user",
                "content": f"What are the key lessons learned regarding '{topic}'?"
            },
            {
                "role": "assistant",
                "content": wisdom
            }
        ]
    }


def generate_training_data(topic_filter: str = None, output_file: str = None):
    """Generate training data JSONL file from lessons"""
    output_file = output_file or OUTPUT_FILE

    print(f"--- Dreaming (Consolidating Memories) ---")
    print(f"Database: {DB_PATH}")

    rows = fetch_lessons(topic_filter)

    if not rows:
        print("No lessons found. Run more agents!")
        return

    # Group by Topic
    topic_map = defaultdict(list)
    for topic, text, ts, task_id in rows:
        t = topic or "general"
        topic_map[t].append(text)

    print(f"Found {len(rows)} lessons across {len(topic_map)} topics")

    # Generate training examples
    examples = []
    for topic, lessons in topic_map.items():
        if len(lessons) >= 2:  # Only topics with 2+ lessons
            example = format_for_training(topic, lessons)
            examples.append(example)
            print(f"  [{topic}] {len(lessons)} lessons")

    # Write to JSONL
    with open(output_file, 'w', encoding='utf-8') as f:
        for example in examples:
            f.write(json.dumps(example) + "\n")

    print(f"\n--- Training data written to {output_file} ---")
    print(f"    {len(examples)} training examples generated")


def generate_onboarding_prompts():
    """
    Generate topic-specific onboarding prompts for new agents.
    Creates prompts that inject historical wisdom into new sessions.
    """
    print(f"--- Generating Onboarding Prompts ---")

    rows = fetch_lessons()
    if not rows:
        print("No lessons found.")
        return

    # Group by topic
    topic_map = defaultdict(list)
    for topic, text, ts, task_id in rows:
        t = topic or "general"
        clean_text = text.replace("[LESSON]", "").strip()
        if clean_text:
            topic_map[t].append(clean_text)

    # Create onboard directory
    os.makedirs(ONBOARD_DIR, exist_ok=True)

    for topic, lessons in topic_map.items():
        if len(lessons) < 1:
            continue

        # Create onboarding prompt
        prompt_lines = [
            f"# Onboarding: {topic}",
            "",
            "Before starting work on this topic, review these historical lessons:",
            ""
        ]

        for i, lesson in enumerate(lessons[-10:], 1):  # Last 10 lessons
            prompt_lines.append(f"{i}. {lesson}")

        prompt_lines.extend([
            "",
            "Apply these lessons to avoid repeating past mistakes.",
            ""
        ])

        # Write to file
        safe_topic = topic.replace("/", "-").replace("\\", "-")
        filepath = os.path.join(ONBOARD_DIR, f"onboard_{safe_topic}.md")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write("\n".join(prompt_lines))

        print(f"  Created: {filepath} ({len(lessons)} lessons)")

    print(f"\n--- Onboarding prompts written to {ONBOARD_DIR}/ ---")


def show_stats():
    """Show statistics about lessons in the database"""
    print(f"--- Lesson Statistics ---")
    print(f"Database: {DB_PATH}")

    rows = fetch_lessons()
    if not rows:
        print("No lessons found.")
        return

    # Group by topic
    topic_map = defaultdict(list)
    for topic, text, ts, task_id in rows:
        t = topic or "general"
        topic_map[t].append((text, ts, task_id))

    print(f"\nTotal lessons: {len(rows)}")
    print(f"Topics: {len(topic_map)}\n")

    # Sort by lesson count
    sorted_topics = sorted(topic_map.items(), key=lambda x: len(x[1]), reverse=True)

    print("Top topics by lesson count:")
    for topic, lessons in sorted_topics[:15]:
        task_ids = set(l[2] for l in lessons if l[2])
        print(f"  [{topic:20}] {len(lessons):3} lessons ({len(task_ids)} tasks)")

    # Recent lessons
    print("\nMost recent lessons:")
    all_lessons = [(t, text, ts) for t, lessons in topic_map.items() for text, ts, _ in lessons]
    all_lessons.sort(key=lambda x: x[2] or "", reverse=True)

    for topic, text, ts in all_lessons[:5]:
        short_text = text[:60].replace("\n", " ")
        print(f"  [{ts[:16]}] [{topic}] {short_text}...")


def main():
    parser = argparse.ArgumentParser(description="Consolidate lessons into training data")
    parser.add_argument("--topic", help="Filter by topic")
    parser.add_argument("--output", "-o", help="Output file (default: training_data.jsonl)")
    parser.add_argument("--stats", action="store_true", help="Show lesson statistics")
    parser.add_argument("--onboard", action="store_true", help="Generate onboarding prompts")

    args = parser.parse_args()

    if args.stats:
        show_stats()
    elif args.onboard:
        generate_onboarding_prompts()
    else:
        generate_training_data(topic_filter=args.topic, output_file=args.output)


if __name__ == "__main__":
    main()
