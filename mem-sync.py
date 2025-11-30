#!/usr/bin/env python3
"""
mem-sync.py - Incremental sync from anchors.jsonl to memory.db

Syncs entries from anchors.jsonl to SQLite database, tracking progress
to enable incremental updates.

Usage:
    ./mem-sync.py                    # Default: sync anchors.jsonl to memory.db
    ./mem-sync.py --db path/to.db    # Custom database path
    ./mem-sync.py --source path.jsonl # Custom source file
    ./mem-sync.py --dry-run          # Show what would be synced without writing
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def get_script_dir():
    """Get the directory containing this script."""
    return Path(__file__).parent.resolve()


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Sync anchors.jsonl to memory.db incrementally'
    )
    parser.add_argument(
        '--db',
        default=str(get_script_dir() / 'memory.db'),
        help='Path to SQLite database (default: ./memory.db)'
    )
    parser.add_argument(
        '--source',
        default=str(get_script_dir() / 'anchors.jsonl'),
        help='Path to source JSONL file (default: ./anchors.jsonl)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be synced without writing to database'
    )
    return parser.parse_args()


def get_last_synced_line(conn, source_file):
    """Get the last synced line number for the source file."""
    cursor = conn.cursor()
    cursor.execute(
        'SELECT last_line FROM sync_state WHERE source_file = ?',
        (source_file,)
    )
    row = cursor.fetchone()
    return row[0] if row else 0


def update_sync_state(conn, source_file, last_line):
    """Update the sync state for the source file."""
    cursor = conn.cursor()
    now = datetime.utcnow().isoformat() + 'Z'
    cursor.execute(
        '''
        INSERT INTO sync_state (source_file, last_line, last_sync)
        VALUES (?, ?, ?)
        ON CONFLICT(source_file) DO UPDATE SET
            last_line = excluded.last_line,
            last_sync = excluded.last_sync
        ''',
        (source_file, last_line, now)
    )
    conn.commit()


def parse_anchor_line(line_num, line_text):
    """
    Parse a line from anchors.jsonl.

    Returns:
        tuple: (parsed_data, error_message) where parsed_data is a dict or None
    """
    # Skip comment lines
    if line_text.startswith('#'):
        return None, None

    # Skip empty lines
    if not line_text.strip():
        return None, None

    try:
        data = json.loads(line_text)
    except json.JSONDecodeError as e:
        return None, f"Malformed JSON at line {line_num}: {e}"

    # Validate it's a list with expected structure
    if not isinstance(data, list):
        return None, f"Expected array at line {line_num}, got {type(data).__name__}"

    # Map array indices to field names
    # [type, topic, text, choice, rationale, timestamp, session, source]
    if len(data) < 8:
        return None, f"Expected 8 fields at line {line_num}, got {len(data)}"

    anchor_type = data[0] if data[0] else None
    anchor_topic = data[1] if data[1] else None
    text = data[2] if data[2] else None
    anchor_choice = data[3] if data[3] else None
    anchor_rationale = data[4] if data[4] else None
    timestamp = data[5] if data[5] else None
    anchor_session = data[6] if data[6] else None
    anchor_source = data[7] if data[7] else None

    # Require text field
    if not text:
        return None, f"Empty text field at line {line_num}, skipping"

    return {
        'bucket': 'anchor',
        'timestamp': timestamp,
        'text': text,
        'anchor_type': anchor_type,
        'anchor_topic': anchor_topic,
        'anchor_choice': anchor_choice,
        'anchor_rationale': anchor_rationale,
        'anchor_session': anchor_session,
        'anchor_source': anchor_source,
        'source_line': line_num,
    }, None


def insert_chunk(conn, chunk_data):
    """Insert a chunk into the database."""
    cursor = conn.cursor()
    cursor.execute(
        '''
        INSERT INTO chunks (
            bucket, timestamp, text,
            anchor_type, anchor_topic, anchor_choice, anchor_rationale,
            anchor_session, anchor_source, source_line
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            chunk_data['bucket'],
            chunk_data['timestamp'],
            chunk_data['text'],
            chunk_data['anchor_type'],
            chunk_data['anchor_topic'],
            chunk_data['anchor_choice'],
            chunk_data['anchor_rationale'],
            chunk_data['anchor_session'],
            chunk_data['anchor_source'],
            chunk_data['source_line'],
        )
    )


def sync_anchors(db_path, source_path, dry_run=False):
    """
    Sync anchors from JSONL file to database.

    Returns:
        tuple: (new_entries_count, warnings)
    """
    # Check if source file exists
    if not os.path.exists(source_path):
        print(f"ERROR: Source file not found: {source_path}", file=sys.stderr)
        sys.exit(1)

    # Check if database exists
    if not os.path.exists(db_path):
        print(f"ERROR: Database not found: {db_path}", file=sys.stderr)
        print("Run './mem-db.sh init' to create the database first.", file=sys.stderr)
        sys.exit(1)

    # Connect to database
    conn = sqlite3.connect(db_path)

    # Get last synced line
    source_file_name = os.path.basename(source_path)
    last_synced_line = get_last_synced_line(conn, source_file_name)

    print(f"Source: {source_path}")
    print(f"Database: {db_path}")
    print(f"Last synced line: {last_synced_line}")

    if dry_run:
        print("\nDRY RUN MODE - No changes will be written to database\n")

    # Read and process new lines
    new_entries = []
    warnings = []
    current_line = 0

    with open(source_path, 'r', encoding='utf-8') as f:
        for line in f:
            current_line += 1

            # Skip already-synced lines
            if current_line <= last_synced_line:
                continue

            # Parse the line
            chunk_data, error = parse_anchor_line(current_line, line)

            if error:
                warnings.append(error)
                continue

            if chunk_data is None:
                # Comment or empty line, silently skip
                continue

            new_entries.append((current_line, chunk_data))

    # Insert new entries
    if new_entries and not dry_run:
        for line_num, chunk_data in new_entries:
            insert_chunk(conn, chunk_data)
        conn.commit()

        # Update sync state
        update_sync_state(conn, source_file_name, current_line)

    conn.close()

    # Print results
    print(f"\nNew entries synced: {len(new_entries)}")

    if warnings:
        print(f"\nWarnings ({len(warnings)}):")
        for warning in warnings:
            print(f"  - {warning}")

    if dry_run and new_entries:
        print(f"\nWould sync {len(new_entries)} entries:")
        for line_num, chunk_data in new_entries[:5]:  # Show first 5
            print(f"  Line {line_num}: [{chunk_data['anchor_type']}] {chunk_data['anchor_topic']} - {chunk_data['text'][:60]}...")
        if len(new_entries) > 5:
            print(f"  ... and {len(new_entries) - 5} more")

    return len(new_entries), warnings


def main():
    """Main entry point."""
    args = parse_args()

    try:
        new_count, warnings = sync_anchors(args.db, args.source, args.dry_run)

        # Exit with error code if there were warnings
        if warnings:
            sys.exit(1)

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
