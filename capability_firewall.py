#!/usr/bin/env python3
"""
capability_firewall.py - Tiered capability firewall for autonomous agents.

Implements permission tiers, sandbox enforcement, hard ceilings, and approval
gates. The head agent can see all context but cannot trigger dangerous tools.
Workers operate inside directory jails with bounded recursion depth.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

# Tier ordering for comparison
TIER_ORDER = {
    "safe": 0,
    "moderate": 1,
    "dangerous": 2
}


@dataclass
class ToolPolicy:
    """Policy describing how a tool may be used."""

    tier: str
    requires_approval: bool = False
    sandboxed: bool = True
    allow_domains: Optional[List[str]] = None
    max_bytes: int = 8192
    timeout: int = 60
    description: str = ""


# Tool catalog with tiered permissions
TOOL_POLICIES: Dict[str, ToolPolicy] = {
    # Safe
    "plan": ToolPolicy(tier="safe", sandboxed=False, description="LLM-only planning"),
    "list_files": ToolPolicy(tier="safe", description="Enumerate files inside jail"),
    "read_file": ToolPolicy(tier="safe", description="Read file contents"),
    "search_text": ToolPolicy(tier="safe", description="Ripgrep inside jail"),
    "mem_search": ToolPolicy(tier="safe", sandboxed=False, description="Memory search"),
    "git_status": ToolPolicy(tier="safe", description="Read git status"),
    "git_log": ToolPolicy(tier="safe", description="Read git log"),
    "git_diff": ToolPolicy(tier="safe", description="Read git diff"),
    # Moderate
    "write_file": ToolPolicy(tier="moderate", description="Create or replace files"),
    "edit_file": ToolPolicy(tier="moderate", description="Append or edit files"),
    "run_tests": ToolPolicy(tier="moderate", description="Run tests/builds with allowlist"),
    "spawn_worker": ToolPolicy(tier="moderate", description="Spawn bounded sub-agent"),
    # Dangerous
    "exec": ToolPolicy(tier="dangerous", requires_approval=True, description="Arbitrary command execution"),
    "http_request": ToolPolicy(tier="dangerous", requires_approval=True, sandboxed=False, description="Outbound HTTP call"),
    "delete_path": ToolPolicy(tier="dangerous", requires_approval=True, description="Delete files or directories"),
    "git_push": ToolPolicy(tier="dangerous", requires_approval=True, description="Push changes upstream"),
    "spawn_unbounded": ToolPolicy(tier="dangerous", requires_approval=True, description="Unbounded recursion spawn"),
}


@dataclass
class ExecutionBudget:
    """Tracks hard ceilings for steps, runtime, and recursion depth."""

    max_steps: int = 50
    max_seconds: int = 900
    max_recursion: int = 2
    start_time: float = field(default_factory=time.time)
    steps: int = 0

    def consume_step(self, recursion_depth: int) -> Tuple[bool, str]:
        """Increment step counter and check ceilings."""
        self.steps += 1
        if self.steps > self.max_steps:
            return False, "Step budget exceeded"
        elapsed = time.time() - self.start_time
        if elapsed > self.max_seconds:
            return False, "Time budget exceeded"
        if recursion_depth > self.max_recursion:
            return False, "Recursion ceiling exceeded"
        return True, "Within budget"

    def status(self) -> Dict[str, int]:
        elapsed = int(time.time() - self.start_time)
        remaining_time = max(self.max_seconds - elapsed, 0)
        remaining_steps = max(self.max_steps - self.steps, 0)
        return {
            "steps_used": self.steps,
            "steps_remaining": remaining_steps,
            "seconds_elapsed": elapsed,
            "seconds_remaining": remaining_time,
            "max_recursion": self.max_recursion
        }


class WorkspaceSandbox:
    """Directory jail plus network allowlist."""

    def __init__(self, root: Path, allowed_domains: Optional[List[str]] = None, read_only: bool = False):
        self.root = Path(root).resolve()
        self.allowed_domains = allowed_domains or []
        self.read_only = read_only
        self.root.mkdir(parents=True, exist_ok=True)

    def _is_within(self, path: Path) -> bool:
        try:
            path.relative_to(self.root)
            return True
        except ValueError:
            return False

    def resolve_path(self, candidate: str) -> Path:
        """Resolve a path relative to the jail and ensure no escapes."""
        raw = Path(candidate)
        path = raw if raw.is_absolute() else (self.root / raw)
        path = path.resolve()
        if not self._is_within(path):
            raise PermissionError(f"Path escapes sandbox: {candidate}")
        if self.read_only and not path.exists():
            raise PermissionError("Sandbox is read-only")
        return path

    def assert_domain_allowed(self, url: str, override: Optional[List[str]] = None):
        domains = override if override is not None else self.allowed_domains
        if not domains:
            raise PermissionError("Network access disabled for this sandbox")
        host = urlparse(url).netloc
        if not host:
            raise PermissionError("Malformed URL")
        if not any(host.endswith(domain) for domain in domains):
            raise PermissionError(f"Domain not allowed: {host}")


class ApprovalQueue:
    """Queues escalations and logs audit decisions using SQLite."""

    def __init__(self, db_path: Path, actor: str):
        self.db_path = Path(db_path)
        self.actor = actor
        self._ensure_tables()

    def _ensure_tables(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pending_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type TEXT,
                action_data TEXT,
                proposed_by TEXT,
                proposed_at TEXT,
                status TEXT,
                reviewed_by TEXT,
                reviewed_at TEXT,
                review_notes TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                action_type TEXT,
                action_data TEXT,
                decision TEXT,
                reason TEXT,
                actor TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _utc_now(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def queue(self, action_type: str, action_data: Dict, reason: str) -> int:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO pending_changes (action_type, action_data, proposed_by, proposed_at, status)
            VALUES (?, ?, ?, ?, 'pending')
            """,
            (action_type, json.dumps(action_data), self.actor, self._utc_now())
        )
        pending_id = cur.lastrowid
        conn.commit()
        conn.close()
        self.log_audit(action_type, action_data, "ESCALATE", reason, pending_id=pending_id)
        return pending_id

    def log_audit(self, action_type: str, action_data: Dict, decision: str, reason: str, pending_id: Optional[int] = None):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        payload = dict(action_data)
        if pending_id is not None:
            payload["pending_id"] = pending_id
        cur.execute(
            """
            INSERT INTO audit_log (timestamp, action_type, action_data, decision, reason, actor)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (self._utc_now(), action_type, json.dumps(payload), decision, reason, self.actor)
        )
        conn.commit()
        conn.close()

    def list_pending(self) -> List[Dict]:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, action_type, action_data, proposed_by, proposed_at, status
            FROM pending_changes
            WHERE status = 'pending'
            ORDER BY proposed_at ASC
            """
        )
        rows = cur.fetchall()
        conn.close()
        return [
            {
                "id": row[0],
                "action_type": row[1],
                "action_data": json.loads(row[2]),
                "proposed_by": row[3],
                "proposed_at": row[4],
                "status": row[5]
            }
            for row in rows
        ]

    def approve(self, pending_id: int, reviewer: str, notes: str = "") -> Optional[Dict]:
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE pending_changes
            SET status = 'approved', reviewed_by = ?, reviewed_at = ?, review_notes = ?
            WHERE id = ? AND status = 'pending'
            """,
            (reviewer, self._utc_now(), notes, pending_id)
        )
        cur.execute("SELECT action_type, action_data, status FROM pending_changes WHERE id = ?", (pending_id,))
        row = cur.fetchone()
        conn.commit()
        conn.close()
        if row and row[2] == "approved":
            action_data = json.loads(row[1])
            self.log_audit(row[0], action_data, "APPROVED", f"Approved by {reviewer}: {notes}", pending_id=pending_id)
            return {"action_type": row[0], "action_data": action_data}
        return None

    def reject(self, pending_id: int, reviewer: str, notes: str = ""):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE pending_changes
            SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, review_notes = ?
            WHERE id = ? AND status = 'pending'
            """,
            (reviewer, self._utc_now(), notes, pending_id)
        )
        conn.commit()
        conn.close()
        self.log_audit("reject", {"pending_id": pending_id}, "REJECTED", f"Rejected by {reviewer}: {notes}", pending_id=pending_id)


class CapabilityFirewall:
    """Capability firewall enforcing tiers, sandbox, ceilings, and approvals."""

    def __init__(
        self,
        db_path: str,
        sandbox_root: str,
        tier: str = "safe",
        actor: str = "agent",
        actor_role: str = "worker",
        allowed_domains: Optional[List[str]] = None,
        budget: Optional[ExecutionBudget] = None
    ):
        if tier not in TIER_ORDER:
            raise ValueError(f"Unknown tier: {tier}")
        self.tier = tier
        self.actor = actor
        self.actor_role = actor_role
        self.sandbox = WorkspaceSandbox(Path(sandbox_root), allowed_domains=allowed_domains)
        self.budget = budget or ExecutionBudget()
        self.approvals = ApprovalQueue(Path(db_path), actor=actor)
        self.tools = ToolWrappers(self)

    def _tier_allows(self, tool_policy: ToolPolicy) -> bool:
        return TIER_ORDER[self.tier] >= TIER_ORDER[tool_policy.tier]

    def _audit(self, action_name: str, action_data: Dict, decision: str, reason: str, pending_id: Optional[int] = None):
        self.approvals.log_audit(action_name, action_data, decision, reason, pending_id=pending_id)

    def _escalate(self, action_name: str, action_data: Dict, reason: str) -> Dict:
        pending_id = self.approvals.queue(action_name, action_data, reason)
        return {
            "decision": "escalate",
            "reason": reason,
            "pending_id": pending_id,
            "sanitized": action_data,
            "budget": self.budget.status()
        }

    def guard_action(self, action: Dict, actor_role: Optional[str] = None, recursion_depth: int = 0) -> Dict:
        """Validate an action and return allow/escalate/deny with sanitized payload."""
        role = actor_role or self.actor_role

        ok, budget_reason = self.budget.consume_step(recursion_depth)
        if not ok:
            self._audit(action.get("action", "unknown"), action, "DENY", budget_reason)
            return {
                "decision": "deny",
                "reason": budget_reason,
                "budget": self.budget.status()
            }

        action_name = action.get("action") or action.get("tool")
        if not action_name:
            return {
                "decision": "deny",
                "reason": "Missing action name",
                "budget": self.budget.status()
            }

        policy = TOOL_POLICIES.get(action_name)
        if not policy:
            return self._escalate(action_name, action, "Unknown tool - requires review")

        if role == "head" and policy.tier != "safe":
            return self._escalate(action_name, action, "Head role restricted to safe tools")

        if not self._tier_allows(policy):
            return self._escalate(action_name, action, f"{policy.tier} tool requires higher tier than {self.tier}")

        if recursion_depth > self.budget.max_recursion:
            self._audit(action_name, action, "DENY", "Recursion depth exceeded")
            return {
                "decision": "deny",
                "reason": "Recursion depth exceeded",
                "budget": self.budget.status()
            }

        sanitized = dict(action)

        # Path enforcement
        for path_field in ("path", "cwd", "dest", "target", "source"):
            if path_field in action:
                try:
                    sanitized[path_field] = str(self.sandbox.resolve_path(str(action[path_field])))
                except PermissionError as e:
                    self._audit(action_name, action, "DENY", str(e))
                    return {
                        "decision": "deny",
                        "reason": str(e),
                        "budget": self.budget.status()
                    }

        # Network enforcement
        if action_name in ("http_request",) and "url" in action:
            try:
                self.sandbox.assert_domain_allowed(str(action["url"]), policy.allow_domains)
            except PermissionError as e:
                self._audit(action_name, action, "DENY", str(e))
                return {
                    "decision": "deny",
                    "reason": str(e),
                    "budget": self.budget.status()
                }

        # Decide approval vs allow
        if policy.requires_approval or policy.tier == "dangerous":
            return self._escalate(action_name, sanitized, f"{action_name} requires approval")

        self._audit(action_name, sanitized, "ALLOW", f"Allowed in tier {self.tier}")
        return {
            "decision": "allow",
            "reason": f"Allowed in tier {self.tier}",
            "sanitized": sanitized,
            "budget": self.budget.status()
        }


class ToolWrappers:
    """Executes tools with firewall checks."""

    def __init__(self, firewall: CapabilityFirewall):
        self.firewall = firewall

    def list_files(self, rel: str = ".") -> Dict:
        decision = self.firewall.guard_action({"action": "list_files", "path": rel})
        if decision["decision"] != "allow":
            return decision
        root = Path(decision["sanitized"].get("path", self.firewall.sandbox.root))
        files = []
        for dirpath, _, filenames in os.walk(root):
            for name in filenames:
                full = Path(dirpath) / name
                try:
                    rel_path = full.relative_to(self.firewall.sandbox.root)
                    files.append(str(rel_path))
                except ValueError:
                    continue
                if len(files) >= 200:
                    break
            if len(files) >= 200:
                break
        return {"decision": "allow", "files": files, "budget": decision["budget"]}

    def read_file(self, path: str, max_bytes: Optional[int] = None) -> Dict:
        max_bytes = max_bytes or TOOL_POLICIES["read_file"].max_bytes
        decision = self.firewall.guard_action({"action": "read_file", "path": path, "max_bytes": max_bytes})
        if decision["decision"] != "allow":
            return decision
        target = Path(decision["sanitized"]["path"])
        data = target.read_bytes()[:max_bytes]
        text = data.decode("utf-8", errors="replace")
        return {"decision": "allow", "content": text, "budget": decision["budget"]}

    def write_file(self, path: str, content: str, mode: str = "replace") -> Dict:
        decision = self.firewall.guard_action({"action": "write_file", "path": path, "mode": mode})
        if decision["decision"] != "allow":
            return decision
        target = Path(decision["sanitized"]["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append":
            with target.open("a", encoding="utf-8") as handle:
                handle.write(content)
        else:
            target.write_text(content, encoding="utf-8")
        return {"decision": "allow", "wrote": str(target), "budget": decision["budget"]}

    def edit_file(self, path: str, content: str) -> Dict:
        return self.write_file(path, content, mode="replace")

    def run_tests(self, cmd: List[str], cwd: Optional[str] = None, timeout: int = 120) -> Dict:
        action = {"action": "run_tests", "cmd": cmd, "cwd": cwd or "."}
        decision = self.firewall.guard_action(action)
        if decision["decision"] != "allow":
            return decision
        workdir = Path(decision["sanitized"].get("cwd", self.firewall.sandbox.root))
        try:
            result = subprocess.run(
                cmd,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=timeout
            )
            output = result.stdout + result.stderr
            return {
                "decision": "allow",
                "returncode": result.returncode,
                "output": output[:4000],
                "budget": decision["budget"]
            }
        except subprocess.TimeoutExpired:
            return {"decision": "deny", "reason": "Test command timed out", "budget": decision["budget"]}

    def exec(self, cmd: str, cwd: Optional[str] = None, timeout: int = 60) -> Dict:
        action = {"action": "exec", "cmd": cmd, "cwd": cwd or "."}
        decision = self.firewall.guard_action(action)
        if decision["decision"] != "allow":
            return decision
        workdir = Path(decision["sanitized"].get("cwd", self.firewall.sandbox.root))
        try:
            result = subprocess.run(
                cmd,
                cwd=str(workdir),
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            output = result.stdout + result.stderr
            return {
                "decision": "allow",
                "returncode": result.returncode,
                "output": output[:4000],
                "budget": decision["budget"]
            }
        except subprocess.TimeoutExpired:
            return {"decision": "deny", "reason": "Exec command timed out", "budget": decision["budget"]}

    def http_request(self, url: str, method: str = "GET", body: Optional[str] = None, headers: Optional[Dict] = None, timeout: int = 10) -> Dict:
        action = {"action": "http_request", "url": url, "method": method, "headers": headers or {}}
        decision = self.firewall.guard_action(action)
        if decision["decision"] != "allow":
            return decision
        import urllib.request
        req = urllib.request.Request(url, method=method.upper())
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        data = body.encode("utf-8") if isinstance(body, str) else body
        try:
            with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
                text = resp.read(4000).decode("utf-8", errors="replace")
                return {
                    "decision": "allow",
                    "status": resp.status,
                    "output": text,
                    "budget": decision["budget"]
                }
        except Exception as exc:
            return {"decision": "deny", "reason": str(exc), "budget": decision["budget"]}

    def delete_path(self, path: str) -> Dict:
        decision = self.firewall.guard_action({"action": "delete_path", "path": path})
        if decision["decision"] != "allow":
            return decision
        target = Path(decision["sanitized"]["path"])
        if target.is_dir():
            for root, dirs, files in os.walk(target, topdown=False):
                for fname in files:
                    Path(root, fname).unlink(missing_ok=True)
                for dname in dirs:
                    Path(root, dname).rmdir()
            target.rmdir()
        else:
            target.unlink(missing_ok=True)
        return {"decision": "allow", "deleted": str(target), "budget": decision["budget"]}

    def spawn_worker(self, objective: str, depth: int) -> Dict:
        action = {"action": "spawn_worker", "objective": objective, "depth": depth}
        decision = self.firewall.guard_action(action, recursion_depth=depth)
        return decision


def parse_args():
    parser = argparse.ArgumentParser(description="Capability firewall CLI")
    parser.add_argument("--db", default="memory.db", help="Path to SQLite db for approvals/audit")
    parser.add_argument("--sandbox", default="/tmp/agent-sandbox", help="Sandbox root directory")
    parser.add_argument("--tier", choices=["safe", "moderate", "dangerous"], default="safe", help="Permission tier")
    parser.add_argument("--actor", default="agent", help="Actor id")
    parser.add_argument("--role", choices=["head", "worker"], default="worker", help="Actor role")
    parser.add_argument("--allowed-domain", action="append", dest="allowed_domains", help="Whitelisted domain (repeatable)")
    sub = parser.add_subparsers(dest="command", required=True)

    check_parser = sub.add_parser("check", help="Check an action JSON against the firewall")
    check_parser.add_argument("action_json", help='Action JSON, e.g. \'{"action":"read_file","path":"README.md"}\'')
    check_parser.add_argument("--depth", type=int, default=0, help="Recursion depth for the action")

    sub.add_parser("pending", help="List pending approvals")

    approve_parser = sub.add_parser("approve", help="Approve a pending change")
    approve_parser.add_argument("id", type=int, help="Pending id")
    approve_parser.add_argument("--reviewer", default="human", help="Reviewer name")
    approve_parser.add_argument("--notes", default="", help="Review notes")

    reject_parser = sub.add_parser("reject", help="Reject a pending change")
    reject_parser.add_argument("id", type=int, help="Pending id")
    reject_parser.add_argument("--reviewer", default="human", help="Reviewer name")
    reject_parser.add_argument("--notes", default="", help="Review notes")

    return parser.parse_args()


def main():
    args = parse_args()
    firewall = CapabilityFirewall(
        db_path=args.db,
        sandbox_root=args.sandbox,
        tier=args.tier,
        actor=args.actor,
        actor_role=args.role,
        allowed_domains=args.allowed_domains
    )

    if args.command == "check":
        action = json.loads(args.action_json)
        decision = firewall.guard_action(action, recursion_depth=args.depth)
        print(json.dumps(decision, indent=2))

    elif args.command == "pending":
        pending = firewall.approvals.list_pending()
        if not pending:
            print("No pending changes.")
        else:
            for item in pending:
                print(f"[{item['id']}] {item['action_type']} {item['proposed_at']} by {item['proposed_by']}")
                print(json.dumps(item["action_data"], indent=2)[:200])
                print()

    elif args.command == "approve":
        result = firewall.approvals.approve(args.id, reviewer=args.reviewer, notes=args.notes)
        if result:
            print(f"Approved {args.id}: {result['action_type']}")
            print(json.dumps(result["action_data"], indent=2))
        else:
            print(f"Pending change {args.id} not found or already processed.")

    elif args.command == "reject":
        firewall.approvals.reject(args.id, reviewer=args.reviewer, notes=args.notes)
        print(f"Rejected {args.id}")


if __name__ == "__main__":
    main()
