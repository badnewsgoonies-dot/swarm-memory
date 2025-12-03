#!/usr/bin/env python3
"""
mem-briefing.py - Generate session briefing from memory

Creates a concise context snapshot for new sessions including:
- Recent decisions (last 24h)
- Active questions (unresolved)
- Key facts about current projects
- Recent actions
- Infrastructure state

Output is optimized for LLM context injection.
"""

import sqlite3
import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR / "memory.db"

def get_db():
    return sqlite3.connect(str(DB_PATH))

def format_time_ago(ts_str):
    """Convert ISO timestamp to relative time."""
    if not ts_str:
        return "?"
    ts_str = ts_str.replace('Z', '+00:00')
    try:
        ts = datetime.fromisoformat(ts_str)
    except (ValueError, AttributeError):
        return "?"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = now - ts
    secs = delta.total_seconds()
    if secs < 60:
        return f"{int(secs)}s"
    elif secs < 3600:
        return f"{int(secs/60)}m"
    elif secs < 86400:
        return f"{int(secs/3600)}h"
    elif secs < 604800:
        return f"{int(secs/86400)}d"
    else:
        return ts_str[:10]

def query_entries(cursor, anchor_type=None, topic=None, limit=10, hours=None, exclude_trivial=True):
    """Query memory entries with filters."""
    where = []
    params = {}

    if anchor_type:
        where.append("anchor_type = :type")
        params['type'] = anchor_type
    if topic:
        where.append("anchor_topic = :topic")
        params['topic'] = topic
    if hours:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')
        where.append("timestamp >= :cutoff")
        params['cutoff'] = cutoff
    if exclude_trivial:
        # Exclude conversation entries and very short entries
        where.append("anchor_type != 'c'")
        where.append("length(text) > 30")

    where_clause = " AND ".join(where) if where else "1=1"

    cursor.execute(f"""
        SELECT anchor_type, anchor_topic, text, anchor_choice, timestamp
        FROM chunks
        WHERE {where_clause}
        ORDER BY timestamp DESC
        LIMIT :limit
    """, {**params, 'limit': limit})

    return cursor.fetchall()

def generate_briefing(format='text', project=None):
    """Generate session briefing."""
    if not DB_PATH.exists():
        return "Memory database not initialized."

    conn = get_db()
    cursor = conn.cursor()

    sections = []

    # Recent decisions (24h)
    decisions = query_entries(cursor, anchor_type='d', limit=5, hours=24)
    if decisions:
        lines = ["## Recent Decisions (24h)"]
        for t, topic, text, choice, ts in decisions:
            time_ago = format_time_ago(ts)
            text_short = text[:150].replace('\n', ' ') if text else ""
            choice_str = f" → {choice}" if choice else ""
            lines.append(f"- [{topic}] {text_short}{choice_str} ({time_ago})")
        sections.append("\n".join(lines))

    # Key facts about infrastructure
    infra_facts = query_entries(cursor, anchor_type='f', topic='infrastructure', limit=5)
    if infra_facts:
        lines = ["## Infrastructure"]
        for t, topic, text, choice, ts in infra_facts:
            time_ago = format_time_ago(ts)
            text_short = text[:200].replace('\n', ' ') if text else ""
            lines.append(f"- {text_short} ({time_ago})")
        sections.append("\n".join(lines))

    # Open questions
    cursor.execute("""
        SELECT anchor_topic, text, timestamp
        FROM chunks
        WHERE anchor_type = 'q'
          AND (anchor_choice IS NULL OR anchor_choice NOT IN ('resolved', 'answered'))
        ORDER BY timestamp DESC
        LIMIT 5
    """)
    questions = cursor.fetchall()
    if questions:
        lines = ["## Open Questions"]
        for topic, text, ts in questions:
            time_ago = format_time_ago(ts)
            text_short = text[:150].replace('\n', ' ') if text else ""
            lines.append(f"- [{topic}] {text_short} ({time_ago})")
        sections.append("\n".join(lines))

    # Recent actions (6h)
    actions = query_entries(cursor, anchor_type='a', limit=5, hours=6)
    if actions:
        lines = ["## Recent Actions (6h)"]
        for t, topic, text, choice, ts in actions:
            time_ago = format_time_ago(ts)
            text_short = text[:150].replace('\n', ' ') if text else ""
            lines.append(f"- [{topic}] {text_short} ({time_ago})")
        sections.append("\n".join(lines))

    # Project-specific context if provided
    if project:
        project_entries = query_entries(cursor, topic=project, limit=5)
        if project_entries:
            lines = [f"## Project: {project}"]
            for t, topic, text, choice, ts in project_entries:
                time_ago = format_time_ago(ts)
                type_label = {'d': 'DECISION', 'f': 'FACT', 'a': 'ACTION', 'n': 'NOTE'}.get(t, t)
                text_short = text[:150].replace('\n', ' ') if text else ""
                lines.append(f"- [{type_label}] {text_short} ({time_ago})")
            sections.append("\n".join(lines))

    # Database stats
    cursor.execute("SELECT COUNT(*) FROM chunks")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM chunks WHERE timestamp > datetime('now', '-24 hours')")
    recent = cursor.fetchone()[0]

    stats = f"## Memory Stats\n- Total entries: {total}\n- Last 24h: {recent} new entries"
    sections.append(stats)

    conn.close()

    if format == 'json':
        return json.dumps({
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'sections': sections
        })
    else:
        header = f"# Session Briefing\n_Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}_\n"
        return header + "\n\n".join(sections)

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Generate session briefing')
    parser.add_argument('--format', choices=['text', 'json'], default='text')
    parser.add_argument('--project', help='Focus on specific project/topic')
    parser.add_argument('--output', help='Write to file instead of stdout')
    args = parser.parse_args()

    briefing = generate_briefing(format=args.format, project=args.project)

    if args.output:
        Path(args.output).write_text(briefing)
        print(f"Briefing written to {args.output}")
    else:
        print(briefing)

if __name__ == '__main__':
    main()
