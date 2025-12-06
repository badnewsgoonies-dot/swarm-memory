#!/usr/bin/env python3
"""
mcp_memory_server.py - MCP (Model Context Protocol) server for swarm-memory

Exposes the memory database as MCP tools for use with Claude Code and other MCP clients.

Tools provided:
  - memory_query: Query memory entries with filters
  - memory_write: Write new memory entries
  - memory_semantic: Semantic search with embeddings
  - memory_briefing: Generate session briefing
  - memory_status: Get database status and health

Usage:
  python mcp_memory_server.py

MCP Client Configuration (claude_desktop_config.json or settings):
  {
    "mcpServers": {
      "memory": {
        "command": "python",
        "args": ["/path/to/swarm-memory/mcp_memory_server.py"]
      }
    }
  }
"""

import json
import sqlite3
import subprocess
import sys
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

# Server metadata
SERVER_NAME = "swarm-memory"
SERVER_VERSION = "1.0.0"

# Paths
SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR / "memory.db"
MEM_DB_SH = SCRIPT_DIR / "mem-db.sh"


def log_debug(msg: str):
    """Log debug message to stderr (visible in MCP logs)."""
    print(f"[memory-mcp] {msg}", file=sys.stderr)


def run_mem_db(*args: str, timeout: int = 30) -> tuple[str, str, int]:
    """Run mem-db.sh command and return (stdout, stderr, returncode)."""
    try:
        result = subprocess.run(
            [str(MEM_DB_SH)] + list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(SCRIPT_DIR)
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", "Command timed out", 1
    except Exception as e:
        return "", str(e), 1


def get_db() -> sqlite3.Connection:
    """Get database connection."""
    return sqlite3.connect(str(DB_PATH))


def format_relative_time(ts_str: str) -> tuple[str, bool]:
    """Convert ISO timestamp to relative time + freshness flag."""
    if not ts_str:
        return ("?", False)
    ts_str = ts_str.replace('Z', '+00:00')
    try:
        ts = datetime.fromisoformat(ts_str)
    except (ValueError, AttributeError):
        return ("?", False)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = now - ts
    total_seconds = delta.total_seconds()
    if total_seconds < 0:
        return (ts_str[:10], False)
    is_fresh = total_seconds < 3600  # < 1 hour
    if total_seconds < 60:
        return (f"{int(total_seconds)}s ago", is_fresh)
    elif total_seconds < 3600:
        return (f"{int(total_seconds / 60)}m ago", is_fresh)
    elif total_seconds < 86400:
        return (f"{int(total_seconds / 3600)}h ago", is_fresh)
    elif total_seconds < 2592000:
        return (f"{int(total_seconds / 86400)}d ago", is_fresh)
    else:
        return (ts_str[:10], False)


# =============================================================================
# MCP Tool Implementations
# =============================================================================

def tool_memory_query(
    type: Optional[str] = None,
    topic: Optional[str] = None,
    text: Optional[str] = None,
    recent: Optional[str] = None,
    limit: int = 20,
    task_id: Optional[str] = None,
    choice: Optional[str] = None,
    scope: Optional[str] = None,
    **kwargs
) -> dict:
    """
    Query memory entries with filters.

    Args:
        type: Entry type (d=decision, q=question, a=action, f=fact, n=note, c=conversation,
              T=todo, G=goal, M=attempt, R=result, L=lesson, P=phase)
        topic: Filter by topic
        text: Text search (case-insensitive)
        recent: Time window (e.g., "1h", "24h", "7d")
        limit: Max results (default: 20)
        task_id: Filter by linked task ID
        choice: Filter by choice/status
        scope: Filter by scope (shared/chat/agent/team)

    Returns:
        dict with 'results' list and 'count'
    """
    args = ['query', '--json']

    if type:
        args.append(f"t={type}")
    if topic:
        args.append(f"topic={topic}")
    if text:
        args.append(f"text={text}")
    if recent:
        args.append(f"recent={recent}")
    if task_id:
        args.append(f"task_id={task_id}")
    if choice:
        args.append(f"choice={choice}")
    if scope:
        args.append(f"scope={scope}")
    args.append(f"limit={limit}")

    stdout, stderr, code = run_mem_db(*args)

    if code != 0:
        return {"error": stderr or "Query failed", "results": [], "count": 0}

    # Parse JSONL output
    results = []
    for line in stdout.strip().split('\n'):
        if line:
            try:
                row = json.loads(line)
                # Convert to dict with named fields
                if isinstance(row, list) and len(row) >= 6:
                    ts_rel, is_fresh = format_relative_time(row[5] if len(row) > 5 else None)
                    results.append({
                        "type": row[0],
                        "topic": row[1],
                        "text": row[2],
                        "choice": row[3],
                        "rationale": row[4],
                        "timestamp": row[5] if len(row) > 5 else None,
                        "relative_time": ts_rel,
                        "is_fresh": is_fresh,
                        "session": row[6] if len(row) > 6 else None,
                        "source": row[7] if len(row) > 7 else None,
                        "scope": row[8] if len(row) > 8 else None,
                        "task_id": row[16] if len(row) > 16 else None,
                    })
            except json.JSONDecodeError:
                pass

    return {"results": results, "count": len(results)}


def tool_memory_write(
    type: str,
    text: str,
    topic: Optional[str] = None,
    choice: Optional[str] = None,
    rationale: Optional[str] = None,
    task_id: Optional[str] = None,
    importance: Optional[str] = None,
    scope: str = "shared",
    **kwargs
) -> dict:
    """
    Write a new memory entry.

    Args:
        type: Entry type (d=decision, q=question, a=action, f=fact, n=note,
              T=todo, G=goal, M=attempt, R=result, L=lesson, P=phase)
        text: The main content text
        topic: Category/topic tag
        choice: For decisions/todos: the chosen option or status
        rationale: Reasoning behind the choice
        task_id: Link to a parent TODO/GOAL id
        importance: Priority (H/M/L)
        scope: Visibility scope (shared/chat/agent/team)

    Returns:
        dict with entry details including 'id'
    """
    args = ['write', f"t={type}", f"text={text}"]

    if topic:
        args.append(f"topic={topic}")
    if choice:
        args.append(f"choice={choice}")
    if rationale:
        args.append(f"rationale={rationale}")
    if task_id:
        args.append(f"task_id={task_id}")
    if importance:
        args.append(f"importance={importance}")
    if scope:
        args.append(f"scope={scope}")

    stdout, stderr, code = run_mem_db(*args)

    if code != 0:
        return {"error": stderr or "Write failed", "success": False}

    try:
        result = json.loads(stdout)
        result["success"] = True
        return result
    except json.JSONDecodeError:
        return {"success": True, "output": stdout}


def tool_memory_semantic(
    query: str,
    limit: int = 10,
    **kwargs
) -> dict:
    """
    Semantic search using embeddings.

    Args:
        query: Natural language search query
        limit: Max results (default: 10)

    Returns:
        dict with 'results' list containing matches with similarity scores
    """
    stdout, stderr, code = run_mem_db('semantic', query, '--limit', str(limit), '--json')

    if code != 0:
        # Semantic search might not be available if embeddings aren't set up
        return {
            "error": f"Semantic search failed: {stderr}",
            "hint": "Ensure embeddings are generated with ./mem-db.sh embed",
            "results": []
        }

    try:
        results = json.loads(stdout)
        return results
    except json.JSONDecodeError:
        return {"raw": stdout, "results": []}


def tool_memory_briefing(**kwargs) -> dict:
    """
    Generate a session briefing with recent decisions, infrastructure state, and open questions.

    Returns:
        dict with briefing text and structured sections
    """
    try:
        # Try to import and run briefing generator
        sys.path.insert(0, str(SCRIPT_DIR))

        # Check if briefing module exists
        briefing_path = SCRIPT_DIR / "mem-briefing.py"
        if not briefing_path.exists():
            return {
                "error": "Briefing module not found",
                "hint": "mem-briefing.py is required for briefings"
            }

        # Run as subprocess for isolation
        result = subprocess.run(
            [sys.executable, str(briefing_path)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(SCRIPT_DIR)
        )

        if result.returncode != 0:
            return {
                "error": result.stderr or "Briefing generation failed",
                "briefing": None
            }

        return {
            "briefing": result.stdout,
            "success": True
        }
    except Exception as e:
        return {"error": str(e), "briefing": None}


def tool_memory_status(**kwargs) -> dict:
    """
    Get database status and health information.

    Returns:
        dict with database stats, entry counts, embedding coverage, etc.
    """
    if not DB_PATH.exists():
        return {
            "error": "Database not found",
            "hint": "Initialize with ./mem-db.sh init",
            "healthy": False
        }

    try:
        conn = get_db()
        cursor = conn.cursor()

        # Get total count
        cursor.execute("SELECT COUNT(*) FROM chunks")
        total_entries = cursor.fetchone()[0]

        # Get counts by type
        cursor.execute("""
            SELECT anchor_type, COUNT(*)
            FROM chunks
            WHERE anchor_type IS NOT NULL
            GROUP BY anchor_type
            ORDER BY COUNT(*) DESC
        """)
        type_counts = {row[0]: row[1] for row in cursor.fetchall()}

        # Get embedding coverage
        cursor.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL")
        embedded_count = cursor.fetchone()[0]
        embedding_pct = int(100 * embedded_count / total_entries) if total_entries > 0 else 0

        # Get freshness metrics
        cursor.execute("SELECT COUNT(*) FROM chunks WHERE timestamp > datetime('now', '-1 hour')")
        fresh_1h = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM chunks WHERE timestamp > datetime('now', '-24 hours')")
        fresh_24h = cursor.fetchone()[0]

        # Most recent entry
        cursor.execute("SELECT timestamp FROM chunks ORDER BY timestamp DESC LIMIT 1")
        latest = cursor.fetchone()
        latest_ts = latest[0] if latest else None

        conn.close()

        # Format last entry time
        if latest_ts:
            ts_rel, is_fresh = format_relative_time(latest_ts)
            last_entry = ts_rel + (" [FRESH]" if is_fresh else "")
        else:
            last_entry = "none"

        return {
            "healthy": True,
            "database": str(DB_PATH),
            "total_entries": total_entries,
            "type_counts": type_counts,
            "embedding_coverage": f"{embedded_count}/{total_entries} ({embedding_pct}%)",
            "freshness": {
                "last_entry": last_entry,
                "entries_1h": fresh_1h,
                "entries_24h": fresh_24h
            }
        }
    except Exception as e:
        return {
            "error": str(e),
            "healthy": False
        }


# =============================================================================
# MCP Protocol Implementation
# =============================================================================

TOOLS = {
    "memory_query": {
        "name": "memory_query",
        "description": "Query memory entries with filters. Use to search decisions, facts, questions, actions, notes, and task-related entries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "description": "Entry type: d=decision, q=question, a=action, f=fact, n=note, c=conversation, T=todo, G=goal, M=attempt, R=result, L=lesson, P=phase"
                },
                "topic": {
                    "type": "string",
                    "description": "Filter by topic/category"
                },
                "text": {
                    "type": "string",
                    "description": "Text search (case-insensitive substring match)"
                },
                "recent": {
                    "type": "string",
                    "description": "Time window filter (e.g., '1h', '24h', '7d', '1w', '1m')"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (default: 20)",
                    "default": 20
                },
                "task_id": {
                    "type": "string",
                    "description": "Filter by linked task ID"
                },
                "choice": {
                    "type": "string",
                    "description": "Filter by choice/status value"
                },
                "scope": {
                    "type": "string",
                    "description": "Filter by scope (shared/chat/agent/team)"
                }
            }
        },
        "handler": tool_memory_query
    },
    "memory_write": {
        "name": "memory_write",
        "description": "Write a new memory entry. Use to record decisions, facts, questions, todos, lessons learned, etc.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "description": "Entry type: d=decision, q=question, a=action, f=fact, n=note, T=todo, G=goal, M=attempt, R=result, L=lesson, P=phase"
                },
                "text": {
                    "type": "string",
                    "description": "The main content text"
                },
                "topic": {
                    "type": "string",
                    "description": "Category/topic tag"
                },
                "choice": {
                    "type": "string",
                    "description": "For decisions: chosen option. For todos: status (OPEN/IN_PROGRESS/DONE/BLOCKED)"
                },
                "rationale": {
                    "type": "string",
                    "description": "Reasoning behind the choice"
                },
                "task_id": {
                    "type": "string",
                    "description": "Link to parent TODO/GOAL id (for attempts, results, lessons)"
                },
                "importance": {
                    "type": "string",
                    "description": "Priority level: H (high), M (medium), L (low)"
                },
                "scope": {
                    "type": "string",
                    "description": "Visibility scope: shared, chat, agent, team",
                    "default": "shared"
                }
            },
            "required": ["type", "text"]
        },
        "handler": tool_memory_write
    },
    "memory_semantic": {
        "name": "memory_semantic",
        "description": "Semantic search using embeddings. Find relevant entries by meaning, not just keywords.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language search query"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results (default: 10)",
                    "default": 10
                }
            },
            "required": ["query"]
        },
        "handler": tool_memory_semantic
    },
    "memory_briefing": {
        "name": "memory_briefing",
        "description": "Generate a session briefing with recent decisions, infrastructure state, open questions, and recent actions.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        },
        "handler": tool_memory_briefing
    },
    "memory_status": {
        "name": "memory_status",
        "description": "Get database status including entry counts, embedding coverage, and freshness metrics.",
        "inputSchema": {
            "type": "object",
            "properties": {}
        },
        "handler": tool_memory_status
    }
}


def handle_initialize(params: dict) -> dict:
    """Handle initialize request."""
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {}
        },
        "serverInfo": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION
        }
    }


def handle_tools_list(params: dict) -> dict:
    """Handle tools/list request."""
    return {
        "tools": [
            {
                "name": tool["name"],
                "description": tool["description"],
                "inputSchema": tool["inputSchema"]
            }
            for tool in TOOLS.values()
        ]
    }


def handle_tools_call(params: dict) -> dict:
    """Handle tools/call request."""
    tool_name = params.get("name")
    arguments = params.get("arguments", {})

    if tool_name not in TOOLS:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"error": f"Unknown tool: {tool_name}"})
                }
            ],
            "isError": True
        }

    try:
        handler = TOOLS[tool_name]["handler"]
        result = handler(**arguments)

        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, indent=2)
                }
            ]
        }
    except Exception as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"error": str(e)})
                }
            ],
            "isError": True
        }


def process_message(message: dict) -> Optional[dict]:
    """Process a JSON-RPC message and return response."""
    method = message.get("method")
    params = message.get("params", {})
    msg_id = message.get("id")

    # Handle notifications (no id = no response expected)
    if msg_id is None:
        if method == "notifications/initialized":
            log_debug("Client initialized")
        return None

    # Handle requests
    result = None
    error = None

    try:
        if method == "initialize":
            result = handle_initialize(params)
        elif method == "tools/list":
            result = handle_tools_list(params)
        elif method == "tools/call":
            result = handle_tools_call(params)
        else:
            error = {
                "code": -32601,
                "message": f"Method not found: {method}"
            }
    except Exception as e:
        error = {
            "code": -32603,
            "message": str(e)
        }

    response = {"jsonrpc": "2.0", "id": msg_id}
    if error:
        response["error"] = error
    else:
        response["result"] = result

    return response


def main():
    """Main MCP server loop using stdio transport."""
    log_debug(f"Starting {SERVER_NAME} v{SERVER_VERSION}")
    log_debug(f"Database: {DB_PATH}")

    # Read messages from stdin, write to stdout
    while True:
        try:
            # Read line (JSON-RPC message)
            line = sys.stdin.readline()
            if not line:
                break

            line = line.strip()
            if not line:
                continue

            # Parse JSON-RPC message
            try:
                message = json.loads(line)
            except json.JSONDecodeError as e:
                log_debug(f"Invalid JSON: {e}")
                continue

            # Process and respond
            response = process_message(message)
            if response:
                print(json.dumps(response), flush=True)

        except KeyboardInterrupt:
            break
        except Exception as e:
            log_debug(f"Error: {e}")
            continue

    log_debug("Server shutting down")


if __name__ == "__main__":
    main()
