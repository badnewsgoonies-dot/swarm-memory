# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Session Startup - DO THIS FIRST

**Every new session, run the briefing to get context:**
```bash
python3 ./mem-briefing.py
```

This generates a summary of:
- Recent decisions (24h)
- Infrastructure state (Ollama, APIs, etc.)
- Open questions
- Recent actions

If user says "resume" or "what were we doing", query recent memory:
```bash
./mem-db.sh query recent=2h limit=10
```

## Memory System Overview

This is a shared memory database for maintaining context across chat sessions. The system stores decisions, facts, questions, actions, and notes in a SQLite database with vector embeddings for semantic search.

**CRITICAL: Query memory FIRST before exploring code or spawning agents.**
The answer is likely already stored. Only explore code if memory doesn't have it.

**Do NOT:**
- Read `anchors.jsonl` directly (doesn't scale, use the database)
- Launch Explore agents for questions about this project's own setup
- Read source files to answer questions about past decisions

## Cross-Machine Access (API Server)

Memory can be accessed from other machines via HTTP API:

**Start server (on Linux box):**
```bash
python3 ./mem-server.py --port 8765 --host 0.0.0.0
```

**Access from Windows or other machines:**
```bash
# Health check
curl http://10.0.0.X:8765/health

# Get briefing
curl http://10.0.0.X:8765/briefing

# Query memories
curl "http://10.0.0.X:8765/query?t=d&limit=5"

# Write memory
curl -X POST http://10.0.0.X:8765/write \
  -H "Content-Type: application/json" \
  -d '{"type": "f", "topic": "test", "text": "Testing from Windows"}'

# LLM proxy (call LLMs through Linux without local API key)
curl -X POST http://10.0.0.X:8765/llm \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Say hello", "tier": "fast"}'
# Tiers: fast, code, smart, claude, codex, max
```

## Quick Reference

### Query memory
```bash
./mem-db.sh query t=d limit=5          # Recent decisions
./mem-db.sh query topic=<topic>        # By topic
./mem-db.sh query text=<keyword>       # Keyword search
./mem-db.sh query recent=1h            # Last hour
./mem-db.sh semantic "search query"    # Semantic search with embeddings
```

### Record memories
```bash
./mem-db.sh write t=d topic=X text="Decision made" choice="chosen option"
./mem-db.sh write t=f topic=X text="Fact learned"
./mem-db.sh write t=q topic=X text="Open question"
```

### Types

**Core types (lowercase):**
| Type | Letter | Use for |
|------|--------|---------|
| Decision | `d` | Choices made, with rationale |
| Question | `q` | Open questions needing answers |
| Fact | `f` | Learned information |
| Action | `a` | Things done |
| Note | `n` | General notes |
| Conversation | `c` | Chat messages (auto-captured) |

**Task-centric types (uppercase):**
| Type | Letter | Use for |
|------|--------|---------|
| Todo | `T` | Tasks to be done |
| Goal | `G` | High-level objectives |
| Attempt | `M` | Work attempts on a task |
| Result | `R` | Outcomes of attempts |
| Lesson | `L` | Learnings from attempts |
| Phase | `P` | Orchestrator phase transitions |

**PHASE glyph details:**
- `anchor_choice` = transition label (e.g., `IMPLEMENT->AUDIT`, `AUDIT->FIX`)
- `task_id` = the TODO/GOAL id (e.g., `vv2-001`, `port-003`)
- `links` = JSON: `{"from": "<phase>", "to": "<phase>", "round": <int>, "error": "<error_signature or 'none'>"}`
- Use `task=<id>` as a convenient alias for `task_id=<id>`

Example:
```bash
./mem-db.sh write t=P topic=port-ui task=vv2-001 \
  choice="IMPLEMENT->AUDIT" \
  text="First implementation ready for audit" \
  links='{"from": "IMPLEMENT", "to": "AUDIT", "round": 1, "error": "none"}'
```

### TODO Statuses

TODOs (`anchor_type='T'`) use `anchor_choice` as a status field:

| Status | Meaning |
|--------|---------|
| `OPEN` | Task is available for work |
| `IN_PROGRESS` | Task is being actively worked on |
| `DONE` | Task completed successfully |
| `BLOCKED` | Task cannot proceed; needs human intervention |

**Commands:**
```bash
./mem-todo.sh list                      # Lists all TODOs, BLOCKED shown first
./mem-todo.sh list --status OPEN        # Only OPEN tasks
./mem-todo.sh update <id> --status BLOCKED
./mem-todo.sh block <id>                # Shortcut for --status BLOCKED
./mem-todo.sh done <id>                 # Shortcut for --status DONE
```

**BLOCKED convention:**
When marking a task BLOCKED, agents should also:
1. Write a RESULT glyph with `choice=failure` and `metric=blocked_reason=<reason>`
2. Write a LESSON glyph explaining why the task is stuck
3. Optionally append `(BLOCKED: <reason>)` to the TODO text itself

Example:
```bash
# Mark task blocked
./mem-todo.sh block vv-001

# Log the reason
./mem-db.sh write t=R topic=port-ui task=vv-001 choice=failure \
  text="Task blocked: repeated TypeScript error" \
  metric="blocked_reason=repeated_error_signature:ts:TS2304"

# Log lesson learned
./mem-db.sh write t=L topic=port-ui task=vv-001 \
  text="Task vv-001 stuck on TS2304 error for 2 rounds. Needs manual debugging."
```

**Worker behavior:**
- Workers (`agent_loop.py`) only pick up `OPEN` tasks
- Workers will NOT revert a BLOCKED task to OPEN
- A human or manager must explicitly reopen blocked tasks

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

## Game Building Workflow (Clockwork Crypts Demo)

The orchestrator supports game building with a structured evaluation pipeline:

```
Manual → Orchestrator → Build → Eval → Human Playtest → Manager → New TODOs
```

### Game Design Manual

The game manual (`docs/game_manual_demo.md`) is the single source of truth:
- Core game loop and player experience
- Minimum scope for shippable demo
- "Definition of Demo Complete" acceptance criteria

### Real-Time Monitoring

Watch agents work in real-time:
```bash
./watch-agents.sh all           # tmux 4-pane dashboard
./watch-agents.sh phases        # Watch PHASE transitions
./watch-agents.sh task <id>     # Watch specific task history
./watch-agents.sh daemon [log]  # Tail daemon log
```

### Orchestration

Start the game-building orchestrator:
```bash
# Add TODO
./mem-todo.sh add \
  --id clockcrypts-demo-001 \
  --topic clockcrypts-demo \
  --text "ORCHESTRATE: Build Clockwork Crypts demo per docs/game_manual_demo.md" \
  --importance H

# Run daemon
python3 swarm_daemon.py \
  --objective "ORCHESTRATE: [task_id:clockcrypts-demo-001] [topic:clockcrypts-demo] Build the Clockwork Crypts Godot 4 project based on docs/game_manual_demo.md." \
  --repo-root /path/to/clockcrypts-godot \
  --unrestricted \
  --max-iterations 80 \
  --verbose 2>&1 | tee /tmp/clockcrypts-demo.log
```

### Structural Evaluation

After orchestration, run the eval script:
```bash
./eval-clockcrypts-demo.sh clockcrypts-demo-001 /path/to/clockcrypts-godot

# Checks:
# - Game manual exists
# - project.godot exists
# - Entry scene exists
# - Player/Enemy scripts exist
# - Godot headless build passes
# Logs RESULT + LESSON to memory
```

### Human Playtest

Interactive checklist for gameplay feel:
```bash
./human-playtest.sh clockcrypts-demo-001

# Checklist:
# 1. Core Flow (title, rooms, boss, summary)
# 2. Player (movement, attack, health)
# 3. Enemies (spawn, variety, behaviors)
# 4. Rooms (layouts, progression)
# 5. Boss (encounter, phases)
# 6. Stability (no crashes, no soft-locks)
# Logs RESULT + LESSON to memory
```

### View All Evaluations

```bash
./mem-log.sh history clockcrypts-demo-001   # Full task history
./mem-db.sh query t=R task_id=clockcrypts-demo-001   # All RESULT glyphs
./mem-db.sh query t=L task_id=clockcrypts-demo-001   # All LESSON glyphs
```

### Evaluation Sources

- `demo-eval`: Structural + build checks (automated)
- `human-playtest`: Gameplay feel checks (interactive)
- `orchestrator`: Phase transitions (automated)
