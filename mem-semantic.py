#!/usr/bin/env python3
"""
mem-semantic.py - Semantic search with hybrid scoring.

Combines vector similarity with temporal decay:
    score = α·similarity + β·exp(-Δt/τ)

Usage:
    ./mem-semantic.py "query"                    # Semantic search
    ./mem-semantic.py "query" --limit 10         # Limit results
    ./mem-semantic.py "query" --alpha 1.0        # Similarity weight
    ./mem-semantic.py "query" --beta 0.3         # Decay weight
    ./mem-semantic.py "query" --tau 7            # Decay time constant (days)
    ./mem-semantic.py "query" --json             # JSON output

Requires:
    OPENAI_API_KEY environment variable
    Chunks with embeddings (run ./mem-db.sh embed first)
"""

import argparse
import json
import os
import sqlite3
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

# Import temporal decay from sibling module
from temporal_decay import temporal_decay_score

MODEL_NAME = "text-embedding-3-large"


def get_script_dir():
    """Get the directory containing this script."""
    return Path(__file__).parent.resolve()


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Semantic search with hybrid scoring'
    )
    parser.add_argument(
        'query',
        help='The search query'
    )
    parser.add_argument(
        '--db',
        default=str(get_script_dir() / 'memory.db'),
        help='Path to SQLite database (default: ./memory.db)'
    )
    parser.add_argument(
        '--limit',
        type=int,
        default=10,
        help='Maximum results to return (default: 10)'
    )
    parser.add_argument(
        '--alpha',
        type=float,
        default=1.0,
        help='Similarity weight (default: 1.0)'
    )
    parser.add_argument(
        '--beta',
        type=float,
        default=0.3,
        help='Temporal decay weight (default: 0.3)'
    )
    parser.add_argument(
        '--tau',
        type=float,
        default=7.0,
        help='Decay time constant in days (default: 7.0)'
    )
    parser.add_argument(
        '--json',
        action='store_true',
        dest='json_output',
        help='Output as JSON lines'
    )
    return parser.parse_args()


def unpack_embedding(blob: bytes) -> list:
    """Unpack bytes to float array."""
    n = len(blob) // 4
    return list(struct.unpack(f'{n}f', blob))


def cosine_similarity(a: list, b: list) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def get_query_embedding(query: str) -> list:
    """Get embedding for query text via OpenAI API."""
    try:
        from openai import OpenAI
    except ImportError:
        print("ERROR: openai package not installed.", file=sys.stderr)
        print("Run: pip install openai>=1.0.0", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get('OPENAI_API_KEY')
    if not api_key:
        print("ERROR: OPENAI_API_KEY environment variable not set.", file=sys.stderr)
        sys.exit(1)

    client = OpenAI(api_key=api_key)

    response = client.embeddings.create(
        model=MODEL_NAME,
        input=[query]
    )

    return response.data[0].embedding


def parse_timestamp(ts_str: str) -> datetime:
    """Parse ISO timestamp string to datetime."""
    if not ts_str:
        return datetime.now(timezone.utc)

    # Handle various formats
    ts_str = ts_str.replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(ts_str)
    except ValueError:
        # Fallback: return now if unparseable
        return datetime.now(timezone.utc)


def get_embedded_chunks(conn):
    """Get all chunks with embeddings."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            id, embedding, timestamp,
            anchor_type, anchor_topic, text,
            anchor_choice, anchor_rationale,
            anchor_session, anchor_source,
            scope, chat_id, agent_role, visibility, project_id
        FROM chunks
        WHERE embedding IS NOT NULL
    """)
    return cursor.fetchall()


def format_result_human(rank: int, score: float, row: tuple):
    """Format a single result for human-readable output."""
    (chunk_id, _, timestamp,
     anchor_type, topic, text,
     choice, rationale,
     session, source,
     scope, chat_id, agent_role, visibility, project_id) = row

    type_labels = {
        'd': 'DECISION',
        'q': 'QUESTION',
        'a': 'ACTION',
        'f': 'FACT',
        'n': 'NOTE'
    }
    type_label = type_labels.get(anchor_type, anchor_type or '?')
    topic = topic or '?'
    text = text or ''

    # Header with score
    print(f"\033[1;36m[{score:.2f}]\033[0m \033[1;33m{type_label}: {topic}\033[0m")
    print(f"  {text}")

    if choice:
        print(f"  \033[32mChoice:\033[0m {choice}")

    # Metadata line
    ts_short = timestamp[:10] if timestamp and len(timestamp) >= 10 else '?'
    meta_parts = [ts_short]
    if session:
        meta_parts.append(session)
    if scope and scope != 'shared':
        meta_parts.append(f"scope={scope}")
    if agent_role:
        meta_parts.append(f"role={agent_role}")

    print(f"  \033[90m{' | '.join(meta_parts)}\033[0m")
    print()


def format_result_json(score: float, row: tuple):
    """Format a single result as JSON."""
    (chunk_id, _, timestamp,
     anchor_type, topic, text,
     choice, rationale,
     session, source,
     scope, chat_id, agent_role, visibility, project_id) = row

    result = {
        'score': round(score, 4),
        'id': chunk_id,
        'type': anchor_type,
        'topic': topic,
        'text': text,
        'choice': choice,
        'rationale': rationale,
        'timestamp': timestamp,
        'session': session,
        'source': source,
        'scope': scope,
        'chat_id': chat_id,
        'agent_role': agent_role,
        'visibility': visibility,
        'project_id': project_id
    }
    print(json.dumps(result))


def main():
    """Main entry point."""
    args = parse_args()

    # Check database exists
    if not os.path.exists(args.db):
        print(f"ERROR: Database not found: {args.db}", file=sys.stderr)
        print("Run './mem-db.sh init' to create the database first.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)

    # Get embedded chunks
    chunks = get_embedded_chunks(conn)
    conn.close()

    if not chunks:
        print("ERROR: No chunks with embeddings found.", file=sys.stderr)
        print("Run './mem-db.sh embed' to generate embeddings first.", file=sys.stderr)
        sys.exit(1)

    # Get query embedding
    try:
        query_embedding = get_query_embedding(args.query)
    except Exception as e:
        print(f"ERROR: Failed to embed query: {e}", file=sys.stderr)
        print("Hint: Try keyword search with './mem-db.sh query text=...'", file=sys.stderr)
        sys.exit(1)

    # Score all chunks
    now = datetime.now(timezone.utc)
    scored_results = []

    for row in chunks:
        chunk_id, embedding_blob, timestamp = row[0], row[1], row[2]

        # Unpack embedding and compute similarity
        chunk_embedding = unpack_embedding(embedding_blob)
        similarity = cosine_similarity(query_embedding, chunk_embedding)

        # Compute temporal decay
        ts = parse_timestamp(timestamp)
        decay = temporal_decay_score(ts, now=now, tau_days=args.tau)

        # Hybrid score
        score = args.alpha * similarity + args.beta * decay

        scored_results.append((score, row))

    # Sort by score descending
    scored_results.sort(key=lambda x: x[0], reverse=True)

    # Output results
    for i, (score, row) in enumerate(scored_results[:args.limit]):
        if args.json_output:
            format_result_json(score, row)
        else:
            format_result_human(i + 1, score, row)


if __name__ == '__main__':
    main()
