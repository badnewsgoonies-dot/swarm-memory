# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Memory System Overview

This is a shared memory database for maintaining context across chat sessions. The system stores decisions, facts, questions, actions, and notes in a SQLite database with vector embeddings for semantic search.

**CRITICAL: Query memory FIRST before exploring code or spawning agents.**
The answer is likely already stored. Only explore code if memory doesn't have it.

**Do NOT:**
- Read `anchors.jsonl` directly (doesn't scale, use the database)
- Launch Explore agents for questions about this project's own setup
- Read source files to answer questions about past decisions

## Quick Reference

### Query memory
```bash
./mem-db.sh query t=d limit=5          # Recent decisions
./mem-db.sh query topic=<topic>        # By topic
./mem-db.sh query text=<keyword>       # Keyword search
./mem-db.sh semantic "search query"    # Semantic search with embeddings
```

### Record memories
```bash
./mem-db.sh write t=d topic=X text="Decision made" choice="chosen option"
./mem-db.sh write t=f topic=X text="Fact learned"
./mem-db.sh write t=q topic=X text="Open question"
```

### Types
| Type | Letter | Use for |
|------|--------|---------|
| Decision | `d` | Choices made, with rationale |
| Question | `q` | Open questions needing answers |
| Fact | `f` | Learned information |
| Action | `a` | Things done |
| Note | `n` | General notes |
| Conversation | `c` | Chat messages (auto-captured) |

### Other commands
```bash
./mem-db.sh status                     # Database stats and embedding coverage
./mem-db.sh embed                      # Generate embeddings for new entries
./mem-db.sh render t=d limit=10        # Compact glyph format for LLM context
```

## Temporal Awareness

The memory system includes temporal awareness features to help you understand recency:

### Relative Time Display

Timestamps are displayed as relative time instead of absolute dates:
- `5s ago` - 5 seconds ago
- `10m ago` - 10 minutes ago
- `2h ago` - 2 hours ago
- `3d ago` - 3 days ago
- `2025-11-15` - Older than 30 days (shows date)

### Freshness Marker

Entries less than 1 hour old are marked with `[FRESH]`:
- In `query` output: bright green `[FRESH]` tag
- In `render` output: `[FRESH]` tag in glyph header
- Example: `[D][topic=hooks][ts=5m ago][FRESH] Installed pre-commit hook`

### Recent Filter

Use `recent=` to filter entries by time window:

```bash
./mem-db.sh query recent=1h            # Last hour
./mem-db.sh query recent=24h           # Last 24 hours
./mem-db.sh query recent=7d            # Last 7 days
./mem-db.sh query recent=1w            # Last week
./mem-db.sh query recent=1m            # Last month (30 days)
```

Supported units:
- `h` - hours
- `d` - days
- `w` - weeks (7 days each)
- `m` - months (30 days each)

### When to Check Recency

Always check recency when:
- Evaluating hook status or setup changes
- Reviewing recent decisions that might affect current work
- Looking for the most up-to-date context
- Verifying if remembered information is still current
- Debugging recent changes or issues

Example workflow:
```bash
# Check if hooks were recently set up
./mem-db.sh query topic=hooks recent=24h

# Get fresh decisions from today
./mem-db.sh query t=d recent=1d

# Review recent actions in the last hour
./mem-db.sh query t=a recent=1h
```

## Conversation Capture

User prompts and assistant responses are automatically captured to memory via hooks:

### What's Captured
- **User messages**: Prompts with ≥20 chars and ≥3 words (trivial messages filtered)
- **Assistant messages**: Responses with ≥100 chars and ≥15 words (summaries preferred)

### Query Conversations
```bash
./mem-db.sh query t=c                     # All conversation entries
./mem-db.sh query t=c choice=user         # User messages only
./mem-db.sh query t=c choice=assistant    # Assistant responses only
./mem-db.sh query t=c recent=1h           # Recent conversation
./mem-db.sh query t=c chat_id=<session>   # Specific session
```

### Memory Type
- Type: `c` (conversation)
- Choice field: `user` or `assistant`
- Scope: `chat` (session-specific)

## Architecture

### Core Components

- **mem-db.sh** - Main CLI for all memory operations (query, write, embed, semantic search)
- **memory.db** - SQLite database with chunks table and vector embeddings
- **anchors.jsonl** - JSONL backup (append-only, synced from database)

### Daemon System

- **swarm_daemon.py** - Autonomous agent that executes objectives via JSON action protocol
- **governor.py** - Rate limiting and safety enforcement for daemon actions
- **daemon.log** - Full transcript of daemon prompts and responses

Run a daemon:
```bash
./swarm_daemon.py --objective "Your task here" --repo-root /path/to/repo --max-iterations 20
```

Add `--unrestricted` for file editing and shell commands (use with caution).

## Orchestration

The daemon supports autonomous implement→audit→fix cycles via the orchestrator pattern. This enables a parent daemon to spawn sub-daemons and coordinate them through memory glyphs.

### Workflow State Machine

```
IMPLEMENT → AUDIT → [PASS: DONE] / [FAIL: FIX → AUDIT]
```

### Spawn with Wait (Blocking Mode)

Use the `wait` parameter to block until a sub-daemon completes:

```json
{"action": "spawn_daemon", "objective": "Implement feature X", "wait": true, "timeout": 300, "max_iterations": 20}
```

**Parameters:**
- `objective` (required): Task for the sub-daemon
- `repo` (optional): Repository path (defaults to parent's repo_root)
- `max_iterations` (optional): Max iterations for sub-daemon (default: 10)
- `wait` (optional): Block until completion (default: false)
- `timeout` (optional): Max seconds to wait (default: 300)

**Returns (when wait=true):**
- `sub_status`: done/error/killed/interrupted
- `sub_result`: Last action from sub-daemon history
- `sub_history`: Last 3 actions for context

### Orchestration Glyphs

Track workflow state via memory glyphs with standardized topics and choices:

- `topic=orch_{id}` - All glyphs for an orchestration (id is MD5 hash of objective)
- `choice=implement_done` - Implementation phase complete
- `choice=audit:pass` - Audit passed, ready for done
- `choice=audit:fail` - Audit failed, needs fix
- `choice=fix_done` - Fix complete, ready for re-audit
- `choice=escalate` - Stuck, needs human intervention

**Example:**
```bash
# Write phase completion glyph
./mem-db.sh write t=a topic=orch_abc123 text="IMPLEMENT complete" choice="implement_done"

# Query orchestration status
./mem-db.sh query topic=orch_abc123 t=a recent=1h limit=20
```

### Query Orchestration Status

Use the `orch_status` action to check current orchestration state:

```json
{"action": "orch_status", "orch_id": "abc123"}
```

**Returns:**
- `phases`: List of phase choices in order
- `latest`: Most recent phase
- `entry_count`: Number of orchestration entries
- `entries`: First 5 entries for context

### Run Orchestrated Workflow

Prefix your objective with "ORCHESTRATE:" to enable orchestration mode:

```bash
./swarm_daemon.py --objective "ORCHESTRATE: Implement and test feature X" --max-iterations 50 --unrestricted
```

**How it works:**
1. Daemon detects "ORCHESTRATE:" prefix
2. Extracts actual objective and generates orchestration ID
3. Queries memory for current phase (implement/audit/fix/done)
4. Injects phase-specific instructions into prompt
5. LLM decides appropriate actions based on current phase
6. Phase transitions are recorded as memory glyphs

**Anti-Loop Protection:**
- Max 5 rounds per orchestration
- Error signature tracking (escalates on repeated errors)
- Each sub-daemon has iteration limits
- Timeout enforcement on blocking spawns

### Orchestration Example

```bash
# Start orchestrated workflow
./swarm_daemon.py --objective "ORCHESTRATE: Add user authentication to API" --unrestricted --max-iterations 50

# Monitor progress
./mem-db.sh query topic=orch_$(echo -n "Add user authentication to API" | md5sum | cut -c1-8) t=a recent=1h

# Check current phase
./swarm_daemon.py --status
```

**Typical flow:**
1. **IMPLEMENT phase**: Spawns sub-daemon to implement the feature
2. **AUDIT phase**: Spawns sub-daemon to check for bugs, run tests
3. **FIX phase** (if audit fails): Spawns sub-daemon to fix issues
4. **AUDIT phase** (retry): Re-audits after fix
5. **DONE**: Completes after audit passes or max rounds reached

### Embeddings

Local embeddings using sentence-transformers (`all-MiniLM-L6-v2`, 384 dimensions). Requires Python venv:
```bash
. .venv/bin/activate
./mem-db.sh embed              # Embed unprocessed entries
./mem-db.sh semantic "query"   # Hybrid keyword + semantic search
```

## Related Projects

This memory system supports:
- `/home/geni/Documents/vale-village` - Original React game
- `/home/geni/Documents/vale-village-v2` - Preact port (in progress)

### v1 to v2 Migration Notes

When porting React to Preact:
- Imports: `from 'react'` → `from 'preact/hooks'`
- Use `strict: true` in tsconfig.json (affects Zod type inference)
- Keep zustand at v4.5.x (v5 removes GetState/SetState)
- Add `vite-env.d.ts` for ImportMeta.env types
