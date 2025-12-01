#!/usr/bin/env python3
"""
mem-consolidate.py - Memory consolidation using LLM decisions

Compares new/target entries against similar existing entries and decides:
- ADD: New unique information, keep as-is
- UPDATE: Refines existing entry, merge and mark old as superseded
- DELETE: Supersedes existing entry entirely, mark old as deprecated
- NOOP: Duplicate of existing, discard new entry

Usage:
    ./mem-consolidate.py --db memory.db --id 123       # Consolidate specific entry
    ./mem-consolidate.py --db memory.db --recent       # Consolidate most recent entry
    ./mem-consolidate.py --db memory.db --all          # Consolidate all pending entries
    ./mem-consolidate.py --db memory.db --dry-run      # Preview without changes
"""

import argparse
import sqlite3
import subprocess
import json
import sys
import struct
from datetime import datetime

def get_embedding(conn, chunk_id):
    """Get embedding for a chunk as numpy-compatible list"""
    cursor = conn.cursor()
    cursor.execute("SELECT embedding, embedding_dim FROM chunks WHERE id = ?", (chunk_id,))
    row = cursor.fetchone()
    if not row or not row[0]:
        return None
    blob, dim = row
    return list(struct.unpack(f'{dim}f', blob))

def cosine_similarity(a, b):
    """Compute cosine similarity between two vectors"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)

def find_similar(conn, chunk_id, top_k=5, threshold=0.7):
    """Find top-k similar chunks to the given chunk"""
    target_emb = get_embedding(conn, chunk_id)
    if not target_emb:
        return []

    cursor = conn.cursor()
    # Get all other active chunks with embeddings
    cursor.execute("""
        SELECT id, anchor_type, anchor_topic, text, anchor_choice, embedding, embedding_dim
        FROM chunks
        WHERE id != ? AND embedding IS NOT NULL AND (status IS NULL OR status = 'active')
    """, (chunk_id,))

    candidates = []
    for row in cursor.fetchall():
        cid, ctype, ctopic, ctext, cchoice, blob, dim = row
        if not blob:
            continue
        emb = list(struct.unpack(f'{dim}f', blob))
        sim = cosine_similarity(target_emb, emb)
        if sim >= threshold:
            candidates.append({
                'id': cid,
                'type': ctype,
                'topic': ctopic,
                'text': ctext,
                'choice': cchoice,
                'similarity': sim
            })

    # Sort by similarity, return top-k
    candidates.sort(key=lambda x: x['similarity'], reverse=True)
    return candidates[:top_k]

def format_glyph(entry):
    """Format entry as glyph for LLM"""
    t = {'d': 'D', 'q': 'Q', 'a': 'A', 'f': 'F', 'n': 'N'}.get(entry.get('type', '?'), '?')
    topic = entry.get('topic') or 'general'
    text = (entry.get('text') or '').replace('\n', ' ')[:200]
    choice = entry.get('choice', '')
    choice_str = f"[choice={choice}]" if choice else ""
    return f"[{t}][topic={topic}]{choice_str} {text}"

def call_llm(prompt):
    """Call Claude CLI with prompt, return response"""
    try:
        result = subprocess.run(
            ['claude', '-p', prompt],
            capture_output=True,
            text=True,
            timeout=60
        )
        return result.stdout.strip()
    except Exception as e:
        print(f"LLM call failed: {e}", file=sys.stderr)
        return None

def decide_consolidation(target, similar_entries):
    """Ask LLM to decide consolidation action"""
    if not similar_entries:
        return {'action': 'ADD', 'reason': 'No similar entries found'}

    prompt = f"""You are a memory consolidation agent. Given a NEW entry and SIMILAR existing entries, decide what to do.

NEW ENTRY:
{format_glyph(target)}

SIMILAR EXISTING ENTRIES:
"""
    for i, entry in enumerate(similar_entries, 1):
        prompt += f"{i}. (id={entry['id']}, sim={entry['similarity']:.2f}) {format_glyph(entry)}\n"

    prompt += """
DECISION OPTIONS:
- ADD: New entry has unique info not in existing entries. Keep it.
- UPDATE <id>: New entry refines/improves entry #id. Merge and supersede old.
- DELETE <id>: New entry supersedes entry #id entirely. Mark old as deprecated.
- NOOP: New entry is duplicate of existing. Discard new entry.

Respond with EXACTLY one line in format: ACTION [id] | reason
Examples:
  ADD | New topic not covered by existing entries
  UPDATE 42 | More specific version of entry 42
  DELETE 15 | Entry 15 is now obsolete
  NOOP | Duplicate of entry 23

Your decision:"""

    response = call_llm(prompt)
    if not response:
        return {'action': 'ADD', 'reason': 'LLM call failed, defaulting to ADD'}

    # Parse response
    parts = response.split('|', 1)
    action_part = parts[0].strip().upper()
    reason = parts[1].strip() if len(parts) > 1 else ""

    if action_part.startswith('ADD'):
        return {'action': 'ADD', 'reason': reason}
    elif action_part.startswith('NOOP'):
        return {'action': 'NOOP', 'reason': reason}
    elif action_part.startswith('UPDATE'):
        tokens = action_part.split()
        target_id = int(tokens[1]) if len(tokens) > 1 else None
        return {'action': 'UPDATE', 'target_id': target_id, 'reason': reason}
    elif action_part.startswith('DELETE'):
        tokens = action_part.split()
        target_id = int(tokens[1]) if len(tokens) > 1 else None
        return {'action': 'DELETE', 'target_id': target_id, 'reason': reason}
    else:
        return {'action': 'ADD', 'reason': f'Unparseable response: {response}'}

def execute_decision(conn, new_id, decision, dry_run=False):
    """Execute the consolidation decision"""
    try:
        from datetime import UTC
        now = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
    except ImportError:
        now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
    action = decision['action']
    reason = decision.get('reason', '')
    target_id = decision.get('target_id')

    if action == 'ADD':
        print(f"ADD: Keeping entry {new_id} as-is. Reason: {reason}")
        return True

    elif action == 'NOOP':
        print(f"NOOP: Discarding entry {new_id} (duplicate). Reason: {reason}")
        if not dry_run:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE chunks SET status = 'duplicate', superseded_at = ?
                WHERE id = ?
            """, (now, new_id))
            conn.commit()
        return True

    elif action == 'UPDATE' and target_id:
        print(f"UPDATE: Entry {new_id} updates entry {target_id}. Reason: {reason}")
        if not dry_run:
            cursor = conn.cursor()
            # Mark old entry as superseded
            cursor.execute("""
                UPDATE chunks SET status = 'superseded', superseded_by = ?, superseded_at = ?
                WHERE id = ?
            """, (new_id, now, target_id))
            conn.commit()
        return True

    elif action == 'DELETE' and target_id:
        print(f"DELETE: Entry {new_id} supersedes entry {target_id}. Reason: {reason}")
        if not dry_run:
            cursor = conn.cursor()
            # Mark old entry as deprecated
            cursor.execute("""
                UPDATE chunks SET status = 'deprecated', superseded_by = ?, superseded_at = ?
                WHERE id = ?
            """, (new_id, now, target_id))
            conn.commit()
        return True

    else:
        print(f"Unknown action: {action}", file=sys.stderr)
        return False

def consolidate_entry(conn, chunk_id, dry_run=False, verbose=False):
    """Consolidate a single entry"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, anchor_type, anchor_topic, text, anchor_choice
        FROM chunks WHERE id = ?
    """, (chunk_id,))
    row = cursor.fetchone()
    if not row:
        print(f"Entry {chunk_id} not found", file=sys.stderr)
        return False

    target = {
        'id': row[0],
        'type': row[1],
        'topic': row[2],
        'text': row[3],
        'choice': row[4]
    }

    if verbose:
        print(f"Consolidating: {format_glyph(target)}")

    # Find similar entries
    similar = find_similar(conn, chunk_id, top_k=5, threshold=0.6)
    if verbose:
        print(f"Found {len(similar)} similar entries (threshold 0.6)")
        for s in similar:
            print(f"  - {s['id']} (sim={s['similarity']:.2f}): {s['text'][:60]}...")

    # Get LLM decision
    decision = decide_consolidation(target, similar)
    if verbose:
        print(f"Decision: {decision}")

    # Execute
    return execute_decision(conn, chunk_id, decision, dry_run)

def main():
    parser = argparse.ArgumentParser(description='Memory consolidation')
    parser.add_argument('--db', required=True, help='Path to memory database')
    parser.add_argument('--id', type=int, help='Consolidate specific entry ID')
    parser.add_argument('--recent', action='store_true', help='Consolidate most recent entry')
    parser.add_argument('--all', action='store_true', help='Consolidate all unprocessed entries')
    parser.add_argument('--dry-run', action='store_true', help='Preview without changes')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)

    if args.id:
        consolidate_entry(conn, args.id, args.dry_run, args.verbose)
    elif args.recent:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM chunks ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        if row:
            consolidate_entry(conn, row[0], args.dry_run, args.verbose)
        else:
            print("No entries found", file=sys.stderr)
    elif args.all:
        cursor = conn.cursor()
        # Find entries without consolidation status
        cursor.execute("""
            SELECT id FROM chunks
            WHERE status IS NULL OR status = 'active'
            ORDER BY id ASC
        """)
        ids = [row[0] for row in cursor.fetchall()]
        print(f"Consolidating {len(ids)} entries...")
        for chunk_id in ids:
            consolidate_entry(conn, chunk_id, args.dry_run, args.verbose)
    else:
        parser.print_help()
        sys.exit(1)

    conn.close()

if __name__ == '__main__':
    main()
