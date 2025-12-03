# Repository Guidelines

## Project Structure & Module Organization
- Core memory assets: `anchors.jsonl` (append-only glyph log) and `memory.db` (SQLite). Do not hand-edit; use the CLIs.
- Shell entrypoints: `mem-db.sh` (init/migrate/sync/query/render/embed/semantic/consolidate/prune), `mem-search.sh` (filters or DB delegation), and `head-with-memory.sh` (inject glyphs into prompts).
- Python helpers: `mem-sync.py` (anchors → DB), `mem-embed.py` (vector generation), `mem-semantic.py` (semantic search), `mem-consolidate.py` (LLM-based consolidation), plus `temporal_decay.py` (scoring) and `swarm_daemon.py` (daemon).
- See `CLAUDE.md` for the memory-first workflow.

## Build, Test, and Development Commands
- Bootstrap/maintain DB: `./mem-db.sh init`, `./mem-db.sh migrate`, `./mem-db.sh sync` (or `--dry-run`), `./mem-db.sh status`.
- Query/render: `./mem-db.sh query t=d limit=5 --json` or `./mem-db.sh render topic=memory limit=5`.
- Embeddings/search: `./mem-db.sh embed --backend local` (no API key) or `--backend api`; `./mem-semantic.py "retrieval plan" --limit 5 --json`.
- Prompt helpers: `./head-with-memory.sh --dry-run "Summarize memory health"` shows what would be injected.
- Cleanup/maintenance: `./mem-db.sh consolidate --recent` and `./mem-db.sh prune 30` to manage superseded/old chunks.

## TODO Status Conventions

TODOs use `anchor_choice` for status tracking:
- **OPEN**: Ready to be picked up by a worker
- **IN_PROGRESS**: Currently being worked on
- **DONE**: Completed successfully
- **BLOCKED**: Cannot proceed automatically; needs human help

### When to mark BLOCKED
- Same error signature appears twice in audit cycles (anti-loop triggered)
- Maximum orchestration rounds (5) reached without success
- Missing dependencies or information that can't be resolved automatically
- Worker explicitly determines task is stuck

### BLOCKED logging convention
When setting status to BLOCKED, agents MUST also log a reason:

1. **RESULT glyph** with failure metric:
   ```bash
   ./mem-db.sh write t=R topic=<topic> task=<id> choice=failure \
     text="Task blocked: <reason>" \
     metric="blocked_reason=<category>:<signature>"
   ```

   Categories: `repeated_error_signature`, `max_rounds`, `missing_info`, `human_escalation`

2. **LESSON glyph** explaining the blockage:
   ```bash
   ./mem-db.sh write t=L topic=<topic> task=<id> \
     text="Task <id> blocked because <explanation>. Consider: <suggestions>"
   ```

3. **PHASE glyph** if using orchestration:
   ```bash
   ./mem-db.sh write t=P topic=<topic> task=<id> \
     choice="AUDIT->BLOCKED" \
     links='{"from":"AUDIT","to":"BLOCKED","round":N,"error":"<sig>"}'
   ```

### Worker behavior
- `find_open_todo()` only picks up `anchor_choice='OPEN'` tasks
- Workers set status to BLOCKED on failure, never revert BLOCKED to OPEN
- Only humans or manager agents with explicit intent may reopen blocked tasks

## Coding Style & Naming Conventions
- Python: PEP 8, 4-space indents, argparse-based CLIs with docstrings; prefer pure functions and explicit error messages. Keep embeddings model metadata (`embedding_model`, `embedding_dim`) consistent with `MODELS` in `mem-embed.py`.
- Bash: `set -euo pipefail`, lowercase functions, quote variables, and accept filters as `key=value` to match `mem-search.sh`/`mem-db.sh`.
- Glyph fields: use `t` in `d/q/a/f/n`, `topic=<slug>`, `choice=<status/decision>`, `scope`/`chat_id`/`role`/`visibility` when relevant.

## Testing Guidelines
- No formal test suite; smoke: `./mem-db.sh status`, `./mem-db.sh query limit=3`, `./mem-semantic.py "test" --limit 3 --json`.
- Run `./mem-db.sh sync --dry-run` after ingestion changes, and `./mem-db.sh embed --dry-run` after embedding tweaks to confirm selection.
- Validate consolidation with `./mem-db.sh consolidate --recent --dry-run` before enabling writes.

## Commit & Pull Request Guidelines
- Commit messages in history are short, imperative, and specific (e.g., `Add hierarchical retrieval + daemon audit fixes (Phase 3)`). Match that tone; group related changes per commit.
- PRs should include intent/scope, key commands run (status/sync/search checks), and any schema or CLI flag changes. Link issues when applicable; add screenshots only if CLI output is non-obvious.
- Avoid committing generated state (`memory.db` embeddings) unless required; prefer reproducible steps.

## Security & Configuration Tips
- Local embedding is default; API paths need `OPENAI_API_KEY`. Keep keys out of commits and shell history.
- For multi-chat isolation, set `scope`, `chat_id`, `role`, `visibility`, and `project` consistently when writing via `mem-db.sh write`.
- Treat anchors as append-only; if you must correct content, emit a superseding glyph rather than editing existing lines.
