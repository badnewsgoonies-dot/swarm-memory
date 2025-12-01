## Multi‑Chat Shared Memory: Conceptually & Practically Solid

This note explains why a **shared external memory OS** for multiple chats/agents is not only *conceptually* solid, but can also be *practically* solid in the current `swarm/memory` stack.

It is written so you can hand it to another assistant (e.g., Claude) as a concise design brief.

---

## 1. Conceptual Model

The core idea:

- **Head chat** – the conversational interface (one per chat window).
- **Memory OS** – external store + retrieval (here: `anchors.jsonl` ↔ `memory.db`).
- **Sub‑agents** – workers that read/write memory, summarize, refactor, etc.

Because memory lives outside the prompt:

- Any number of chats (`Chat A`, `Chat B`, `Chat C`, …) can attach to the **same** memory state.
- They can all:
  - Read the same facts, decisions, and tasks.
  - Build on each other’s actions.
  - Update a shared set of glyphs.

This is the same principle used by systems like Letta, Zep, Mem0, etc., but the current repo already has a clean split:

- A **pure glyph layer** (`anchors.jsonl` → `memory.db` `chunks` table).
- A **retrieval layer** (`mem-db.sh query`, `mem-search.sh`).
- A **no raw logs to head** policy (head only sees distilled glyphs).

From a **theory/architecture** standpoint, this is solid: nothing about LLMs prevents many chats from sharing one external memory OS.

---

## 2. Current Practical Stack (This Repo)

Today, the memory pipeline already looks like a small Memory OS:

- `anchors.jsonl` – append‑only glyph log (compact JSON arrays).
- `mem-sync.py` – incremental sync from `anchors.jsonl` into SQLite.
- `memory.db` – SQLite store:
  - `chunks` table: type (`t`), topic, text, choice, extra, timestamp, session, source, etc.
  - `sync_state` table: last synced line, timestamps.
- `mem-db.sh` – CLI for:
  - `status` – counts, sync progress.
  - `sync` – incremental log → DB.
  - `query` – `t=`, `topic=`, `text=`, `session=`, `source=`, `limit=`, `--json`.
- `mem-search.sh` – existing search helper:
  - Legacy mode: jq over `anchors.jsonl`.
  - New mode: `--db` delegating to `mem-db.sh query`.

So *practically*, you already have:

- A shared, external memory store.
- A stable CLI/API surface that any chat/agent can call.
- Proven basic behaviors (sync, query, dual jq/DB modes).

What’s missing for **multi‑chat** use is mainly: **scoping**, **roles**, and **a few extra columns and conventions**.

---

## 3. Schema Extensions for Multi‑Chat Memory

To move from “conceptually solid” to “practically solid” multi‑chat memory, extend the `chunks` schema with explicit scope and identity fields.

### 3.1. Additional Columns

On top of the existing fields (`t`, `topic`, `text`, `choice`, `extra`, `timestamp`, `session`, `source`, …), add:

- `scope` – logical visibility of the glyph:
  - `'shared'` – global project/world knowledge.
  - `'chat'` – tied to a specific chat thread.
  - `'agent'` – tied to a particular agent role.
  - `'team'` – shared within a subset of chats/roles.
- `chat_id` – stable identifier for the chat/window (nullable for global).
- `agent_role` – role that produced/owns the glyph, e.g.:
  - `'architect' | 'coder' | 'reviewer' | 'pm' | 'assistant' | 'user' | …'`.
- `visibility` – semantic visibility:
  - `'public'` – safe for any role/chat to see.
  - `'private'` – private to the owner (e.g., chat scratchpad).
  - `'internal'` – internal system notes / reasoning.
- (optional) `project_id` – link glyphs to a project/space.

### 3.2. Examples

- **Global fact** shared by all chats:
  - `scope='shared', chat_id=NULL, visibility='public'`
- **Per‑chat working thought**:
  - `scope='chat', chat_id='chat_A', visibility='internal'`
- **Architect‑only design sketch**:
  - `scope='agent', agent_role='architect', visibility='private'`

The physical DB stays single; the **logical memory views** change per chat/role.

---

## 4. Retrieval Patterns for Multiple Chats

With those columns in place, retrieval becomes “shared but scoped.”

### 4.1. Per‑Chat, Per‑Role Context

For a given chat with:

- `chat_id = CHAT_X`
- `agent_role = 'coder'`
- optional `project_id = 'project_alpha'`

You can define a context query like:

- Include:
  - `scope='shared' AND visibility='public'`
  - `OR scope='chat' AND chat_id=CHAT_X`
  - `OR scope='agent' AND agent_role='coder'`
  - (optionally) filter by `project_id='project_alpha'`

In CLI terms, `mem-db.sh query` could grow flags like:

- `scope=shared|chat|agent|team`
- `chat_id=...`
- `role=architect|coder|reviewer|pm`
- `project=...`

### 4.2. PM‑Safe Views

For a “Project Manager” view:

- Only fetch glyphs with:
  - `visibility='public'`
  - `scope IN ('shared', 'team')`
  - relevant `project_id`.

That lets you avoid leaking internal scratch notes while still giving a complete picture of status, decisions, and risks.

---

## 5. Writes, Consistency, and Conflicts

To keep multi‑chat memory **practically robust**, use standard DB patterns.

### 5.1. Atomic Writes

- Each logical memory operation (e.g., “save 3 glyphs from one reasoning step”) should be a **single transaction**.
- `mem-sync.py` already batches syncs; multi‑chat writes can follow the same pattern.

### 5.2. Versioning & History

- Keep `created_at` (already present as `timestamp`) and optionally `updated_at` or `rev`.
- Prefer **append‑only** operations:
  - Instead of overwriting, insert new glyphs that supersede old ones.
  - E.g., represent task state transitions as separate glyphs (`[TODO]`, `[TODO_UPDATE]`) rather than in‑place edits.

### 5.3. Conflict Representation

- “Conflicts” are usually conceptual (agents disagree), not technical.
- Represent disagreement as **multiple glyphs** with:
  - Different `source`, `agent_role`, or `session`.
- When you must logically replace something:
  - Use a clear pattern:
    - old: `[TODO][status=open]`
    - new: `[TODO_UPDATE][status=done, ref=old_id]`

SQLite will handle concurrent reads + serialized writes; you mainly need **clear conventions**, not heavy distributed systems machinery.

---

## 6. Public vs Private Thought Buffers

Your earlier intuition about “recent thoughts / whatnots” is correct: you want both **global shared state** and **private working buffers**.

### 6.1. Shared Long‑Term Memory

- Stable content (facts, decisions, tasks, plans) that should survive across chats:
  - `scope='shared'`, `visibility='public'`, optionally `project_id=...`.
- These are what other chats/agents should see when they attach to the same project/memory OS.

### 6.2. Per‑Chat Working Memory

- Chat‑local scratch space:
  - `scope='chat'`, `chat_id=...`, `visibility='internal'` or `'private'`.
- Optionally **promote** items from per‑chat scratch to shared:
  - When a tentative idea becomes a committed decision, emit a new shared glyph.

This mirrors:

- Registers (per‑thread) → per‑chat scratch.
- RAM (shared) → per‑agent/per‑team state.
- Disk (persistent) → long‑term shared glyphs.

---

## 7. Is This Only Conceptual, or Also Practical?

Answering the original question directly:

- **Conceptually solid**:
  - Yes. An external memory OS that multiple chats attach to is a sound architecture.
  - Sharing one memory across chats allows cross‑session identity, project continuity, and multi‑agent collaboration.

- **Practically solid** (with minimal work):
  - Yes, if you add:
    - Explicit `scope`, `chat_id`, `agent_role`, `visibility` (and optionally `project_id`) columns.
    - Role‑ and scope‑aware retrieval filters in `mem-db.sh` / `mem-search.sh`.
    - Atomic write semantics and an append‑first mindset for updates.

The current repo already provides:

- A real external store (`memory.db`).
- An incremental sync layer (`mem-sync.py`).
- A query interface (`mem-db.sh`, `mem-search.sh --db`).

So this is not "good only on paper." It is a **small, concrete extension** away from a fully practical multi‑chat, multi‑agent memory OS that multiple chats (and tools like Claude, etc.) can safely share.

---

## 8. Implemented Role System (Phase 5)

The role system is now implemented in `head-with-memory.sh`.

### 8.1. Available Roles

| Role | Focus | Default Write Type |
|------|-------|-------------------|
| `architect` | System design, architecture decisions | `d` (decisions) |
| `coder` | Implementation, debugging, code quality | `f`/`n` (facts/notes) |
| `reviewer` | Code review, QA, testing | `f`/`q` (facts/questions) |
| `pm` | Requirements, coordination, tracking | `a`/`q` (actions/questions) |

### 8.2. Usage

```bash
# Architect mode with chat isolation
./head-with-memory.sh --role architect --chat-id session123 "Design the auth system"

# Coder mode
./head-with-memory.sh --role coder "Implement the login endpoint"

# Write with role
./mem-db.sh write t=f topic=auth text="JWT chosen over sessions" role=architect visibility=public
./mem-db.sh write t=n topic=scratch text="WIP notes" role=coder visibility=internal chat_id=session123
```

### 8.3. Scoping Rules

Memory injection follows strict isolation rules:

1. **Shared + Public** (universal): `scope=shared visibility=public`
   - All roles and chats see these entries
   - Use for project-wide decisions, facts, requirements

2. **Chat-scoped** (session isolation): `scope=chat chat_id=X`
   - Only visible within the same chat session
   - Use for working notes, WIP, scratch

3. **Role + Public** (role sharing): `role=X visibility=public`
   - All sessions with the same role see these
   - Use for role-specific patterns, templates

4. **Role + Internal + Chat** (private to session): `role=X visibility=internal chat_id=Y`
   - Only visible to same role AND same chat session
   - Use for role-specific working notes within a session

### 8.4. What Each Role Sees

```
┌─────────────────────────────────────────────────────────────┐
│                    Shared + Public                          │
│         (decisions, facts, requirements)                    │
│              Visible to ALL roles/chats                     │
└─────────────────────────────────────────────────────────────┘
         │
         ├── Chat A (chat_id=A)
         │   └── Chat-scoped entries for A only
         │
         ├── Chat B (chat_id=B)
         │   └── Chat-scoped entries for B only
         │
         └── Role: architect
             ├── Public architect entries (all architect sessions)
             └── Internal architect + chat_id=X (only this session)
```

### 8.5. Preventing Data Leaks

The `build_memory_context()` function enforces:

- **No cross-chat leakage**: Chat-scoped entries require matching `chat_id`
- **No cross-role private access**: Role internal entries require both matching role AND chat_id
- **Public is explicit**: Only `visibility=public` entries are shared across sessions
- **Custom filters are user responsibility**: `--filters` bypasses scoping (use carefully)

