# Capability Firewall

Capability firewall for autonomous agents that can spawn workers, write files, and use the network. The firewall pairs tiered permissions with sandboxing, hard ceilings, and approval gates; the head agent can observe everything but cannot trigger dangerous tools.

## Roles and tiers
- Head: omniscient context, forced to `safe` tools only; may request approvals for escalations but cannot execute them directly.
- Worker: lives in a sandboxed directory jail and inherits the tier assigned at launch (`safe` → read/search/plan, `moderate` → code edits/tests, `dangerous` → exec/network with approval).
- Auditor/Human: reviews escalations and unlocks queued operations.

Tier schema (see `capability_firewall.py`):
- `safe`: list_files, read_file, search_text, mem_search, plan, status checks.
- `moderate`: `safe` + edit_file, write_file, run_tests, format, spawn_worker (bounded).
- `dangerous`: `moderate` + exec, http_request, delete_path, git_push, unbounded_spawns. Always passes through approval.

## Enforcement components
- Permission schema: each tool has `tier`, `requires_approval`, `sandboxed` (path guard), `allow_domains` (network whitelist), `max_bytes` (reads), and `timeout`.
- Sandboxes: per-worker jail rooted at `sandbox_root`. Paths are resolved and denied if they escape (no `..` or symlink hops). Network calls must target a whitelisted domain.
- Hard ceilings: `ExecutionBudget` enforces `max_steps`, `max_seconds`, and `max_recursion` (spawn depth). Once exceeded, actions are denied.
- Approval gates: `ApprovalQueue` writes pending requests to `pending_changes` and `audit_log` (same tables the governor uses). Dangerous tools or tier jumps create a pending record instead of executing.
- Omniscient/not omnipotent: the head runs in read-only tier and can view all jails; workers see only their jail; escalations carry the requesting agent id and spawn depth.

## Daemon flow (head + workers)
1) Head proposes an action; firewall checks tier/budget/sandbox and either ALLOW, ESCALATE (queue), or DENY. Unknown tools escalate by default.
2) If ALLOW: the tool wrapper executes inside the jail with timeouts and size caps.
3) If ESCALATE: write to `pending_changes` with reason; human approves via `python governor.py approve <id>` or the queue helper in `capability_firewall.py`.
4) Workers inherit `sandbox_root`, `tier`, `max_recursion`, and `parent_id`; spawn requests increment depth and are denied if the ceiling is hit.
5) Audit trail: every decision writes to `audit_log` with actor, tier, and sandbox id.

## Using the reference implementation
- Module: `capability_firewall.py` exposes `CapabilityFirewall`, `ToolWrappers`, and `ExecutionBudget`.
- Drop-in guard for the daemon: wrap each LLM action with `firewall.guard_action(...)` before `execute_action`. The returned decision includes `allow/escalate/deny`, the sanitized payload, and any queued `pending_id`.
- Example (head mode):
    ```python
    fw = CapabilityFirewall(db_path="memory.db", sandbox_root="/tmp/jails/head", tier="safe", actor="head")
    decision = fw.guard_action({"action": "read_file", "path": "README.md"}, actor_role="head")
    ```
- Example (worker mode with approval):
    ```python
    fw = CapabilityFirewall(db_path="memory.db", sandbox_root="/tmp/jails/w1", tier="moderate", actor="worker:w1")
    result = fw.tools.http_request("https://api.example.com/data", headers={}, timeout=5)
    if result["decision"] == "escalate":
        print("Queued for review", result["pending_id"])
    ```

## Operational checks
- Budgets: enforce via `firewall.budget.consume_step()` each action; deny once any ceiling is exceeded.
- Sandbox correctness: ensure `sandbox_root` is created per worker; disallow symlinks that leave the jail.
- Approval loop: `python capability_firewall.py pending|approve|reject` mirrors the governor CLI and keeps audit parity.
- Network: set `allowed_domains` to a minimal list; omit to block outbound entirely.
