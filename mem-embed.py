#!/usr/bin/env python3
"""
mem-embed.py - Generate embeddings for chunks without them.

Usage:
    ./mem-embed.py                    # Embed all unembedded chunks
    ./mem-embed.py --db path/to.db    # Custom database path
    ./mem-embed.py --batch-size 32    # Batch size for efficiency
    ./mem-embed.py --dry-run          # Show what would be embedded
    ./mem-embed.py --force            # Re-embed even if already embedded

Requires:
    OPENAI_API_KEY environment variable
    pip install openai>=1.0.0
"""

import argparse
import os
import sqlite3
import struct
import sys
from pathlib import Path

# Constants
MODEL_NAME = "text-embedding-3-large"
EMBEDDING_DIM = 3072


def get_script_dir():
    """Get the directory containing this script."""
    return Path(__file__).parent.resolve()


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Generate embeddings for memory chunks'
    )
    parser.add_argument(
        '--db',
        default=str(get_script_dir() / 'memory.db'),
        help='Path to SQLite database (default: ./memory.db)'
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=100,
        help='Number of chunks to embed per API call (default: 100)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Show what would be embedded without making API calls'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Re-embed all chunks, even those with existing embeddings'
    )
    return parser.parse_args()


def pack_embedding(vec: list) -> bytes:
    """Pack float array as bytes."""
    return struct.pack(f'{len(vec)}f', *vec)


def unpack_embedding(blob: bytes) -> list:
    """Unpack bytes to float array."""
    n = len(blob) // 4
    return list(struct.unpack(f'{n}f', blob))


def get_embeddings_openai(texts: list) -> list:
    """
    Get embeddings from OpenAI API.

    Returns list of embedding vectors (each 3072 floats for text-embedding-3-large).
    """
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
        input=texts
    )

    return [item.embedding for item in response.data]


def get_chunks_to_embed(conn, force=False):
    """Get chunks that need embeddings."""
    cursor = conn.cursor()

    if force:
        cursor.execute('SELECT id, text FROM chunks WHERE text IS NOT NULL')
    else:
        cursor.execute('SELECT id, text FROM chunks WHERE embedding IS NULL AND text IS NOT NULL')

    return cursor.fetchall()


def update_embedding(conn, chunk_id: int, embedding: list):
    """Store embedding for a chunk with metadata."""
    blob = pack_embedding(embedding)
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE chunks SET embedding = ?, embedding_model = ?, embedding_dim = ? WHERE id = ?',
        (blob, MODEL_NAME, EMBEDDING_DIM, chunk_id)
    )


def get_embedding_stats(conn):
    """Get embedding coverage statistics."""
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM chunks')
    total = cursor.fetchone()[0]

    cursor.execute('SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL')
    embedded = cursor.fetchone()[0]

    return embedded, total


def main():
    """Main entry point."""
    args = parse_args()

    # Check database exists
    if not os.path.exists(args.db):
        print(f"ERROR: Database not found: {args.db}", file=sys.stderr)
        print("Run './mem-db.sh init' to create the database first.", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)

    # Get chunks to embed
    chunks = get_chunks_to_embed(conn, args.force)

    if not chunks:
        embedded, total = get_embedding_stats(conn)
        print(f"All chunks already have embeddings ({embedded}/{total})")
        conn.close()
        return

    print(f"Found {len(chunks)} chunks to embed")

    if args.dry_run:
        print("\nDRY RUN - Would embed:")
        for chunk_id, text in chunks[:10]:
            preview = text[:60] + "..." if len(text) > 60 else text
            print(f"  [{chunk_id}] {preview}")
        if len(chunks) > 10:
            print(f"  ... and {len(chunks) - 10} more")
        conn.close()
        return

    # Process in batches
    total_embedded = 0
    batch_size = args.batch_size

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        batch_ids = [c[0] for c in batch]
        batch_texts = [c[1] for c in batch]

        print(f"Embedding batch {i // batch_size + 1}/{(len(chunks) + batch_size - 1) // batch_size} ({len(batch)} chunks)...")

        try:
            embeddings = get_embeddings_openai(batch_texts)
        except Exception as e:
            print(f"ERROR: API call failed: {e}", file=sys.stderr)
            conn.close()
            sys.exit(1)

        # Store embeddings
        for chunk_id, embedding in zip(batch_ids, embeddings):
            update_embedding(conn, chunk_id, embedding)

        conn.commit()
        total_embedded += len(batch)
        print(f"  Stored {len(batch)} embeddings ({total_embedded}/{len(chunks)} total)")

    conn.close()

    print(f"\nDone! Embedded {total_embedded} chunks.")

    # Show final stats
    conn = sqlite3.connect(args.db)
    embedded, total = get_embedding_stats(conn)
    conn.close()
    print(f"Embedding coverage: {embedded}/{total} ({100*embedded//total if total > 0 else 0}%)")


if __name__ == '__main__':
    main()
