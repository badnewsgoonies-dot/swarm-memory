#!/usr/bin/env python3
"""
governor.py - Action classification and enforcement for autonomous daemon

Classifies actions as:
- ALLOW: Auto-approve safe actions (memory writes f/n/q/a, read-only repo/git, run allowed build commands)
- ESCALATE: Queue for human review (decisions, consolidations, edits, exec/http)
- DENY: Block immediately (dangerous operations OR memory constraint violations)

Memory Enforcement:
- Before allowing edit_file, exec, or http_request actions, the Governor searches
  memory for relevant Decision (d) and Lesson (L) entries that might conflict.
- If a constraint conflict is detected, the action is DENIED with an explanation.

Unrestricted mode (--unrestricted) ALLOWs everything but still logs to audit_log.

Usage:
    from governor import Governor
    gov = Governor(db_path)
    result = gov.check_action(action_data)
    # result: {'decision': 'ALLOW'|'ESCALATE'|'DENY', 'reason': '...', 'constraint_id': int|None}

CLI:
    ./governor.py check '{"action": "write_memory", "type": "f", ...}'
    ./governor.py pending          # List pending changes
    ./governor.py approve 123      # Approve pending change
    ./governor.py reject 123       # Reject pending change
    ./governor.py audit            # Show audit log
    ./governor.py constraints      # Show active memory constraints
"""

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

SCRIPT_DIR = Path(__file__).parent.resolve()
MEM_DB_SCRIPT = SCRIPT_DIR / "mem-db.sh"

# Action classification rules
ALLOW_ACTIONS = {
    'write_memory': {
        'types': ['f', 'n', 'q', 'a'],  # facts, notes, questions, actions
        'description': 'Memory writes (non-decisions)'
    },
    'mem_search': {'description': 'Read-only memory search'},
    'read_file': {'description': 'Repo file read'},
    'list_files': {'description': 'Repo file list'},
    'search_text': {'description': 'Repo text search'},
    'git_log': {'description': 'Git history read'},
    'git_diff': {'description': 'Git diff read'},
    'git_status': {'description': 'Git status read'},
    'run': {'description': 'Run allowed build/test commands'},
    'sleep': {'description': 'Pause execution'},
    'done': {'description': 'Mark objective complete'}
}

ESCALATE_ACTIONS = {
    'write_memory': {
        'types': ['d'],  # decisions require review
        'description': 'Decision writes require human review'
    },
    'consolidate': {'description': 'Memory consolidation modifies existing entries'},
    'propose_config_update': {'description': 'Configuration changes require review'},
    'edit_file': {'description': 'File edits require review'},
    'exec': {'description': 'Arbitrary commands require review'},
    'http_request': {'description': 'HTTP calls require review'}
}

DENY_ACTIONS = {
    'delete': 'Direct deletion not allowed',
    'drop': 'Schema changes not allowed',
    'truncate': 'Bulk deletion not allowed'
}


class Governor:
    """Action classification, memory enforcement, and safety gatekeeper"""

    def __init__(self, db_path, unrestricted=False, enforce_memory=True):
        self.db_path = db_path
        self.actor = 'governor'
        self.unrestricted = unrestricted
        self.enforce_memory = enforce_memory  # Enable memory constraint checking

    def _extract_action_keywords(self, action_data: Dict[str, Any]) -> List[str]:
        """Extract searchable keywords from an action for constraint matching."""
        keywords = []
        action = action_data.get('action', '')

        # Extract from content/text fields
        if action == 'edit_file':
            content = action_data.get('content', '')
            path = action_data.get('path', '')
            # Extract technology mentions from content
            tech_patterns = [
                r'\b(jquery|react|vue|angular|preact|svelte)\b',
                r'\b(python|javascript|typescript|rust|go|java)\b',
                r'\b(postgres|mysql|sqlite|mongodb|redis)\b',
                r'\b(aws|gcp|azure|docker|kubernetes)\b',
            ]
            for pattern in tech_patterns:
                matches = re.findall(pattern, content.lower())
                keywords.extend(matches)
            # Also check the file path
            if path:
                keywords.append(path.split('/')[-1])  # filename

        elif action == 'exec':
            cmd = action_data.get('cmd', '')
            # Extract command name and key arguments
            parts = cmd.split()
            if parts:
                keywords.append(parts[0])  # command name
                # Look for package names, etc.
                for part in parts[1:5]:
                    if not part.startswith('-'):
                        keywords.append(part)

        elif action == 'http_request':
            url = action_data.get('url', '')
            # Extract domain
            import re as re_mod
            domain_match = re_mod.search(r'https?://([^/]+)', url)
            if domain_match:
                keywords.append(domain_match.group(1))

        return [k.lower() for k in keywords if k and len(k) > 2]

    def _search_memory_constraints(self, keywords: List[str]) -> List[Dict[str, Any]]:
        """Search memory for Decision/Lesson entries that might conflict."""
        if not keywords:
            return []

        constraints = []
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Search for decisions and lessons containing any of the keywords
        for keyword in keywords[:10]:  # Limit to first 10 keywords
            try:
                cursor.execute("""
                    SELECT id, anchor_type, anchor_topic, text, anchor_choice, importance
                    FROM chunks
                    WHERE anchor_type IN ('d', 'D', 'L')
                      AND (text LIKE ? OR anchor_topic LIKE ?)
                    ORDER BY timestamp DESC
                    LIMIT 5
                """, (f'%{keyword}%', f'%{keyword}%'))

                for row in cursor.fetchall():
                    constraints.append({
                        'id': row[0],
                        'type': row[1],
                        'topic': row[2],
                        'text': row[3],
                        'choice': row[4],
                        'importance': row[5],
                        'matched_keyword': keyword
                    })
            except sqlite3.Error:
                continue

        conn.close()

        # Deduplicate by ID
        seen_ids = set()
        unique_constraints = []
        for c in constraints:
            if c['id'] not in seen_ids:
                seen_ids.add(c['id'])
                unique_constraints.append(c)

        return unique_constraints

    def _check_constraint_violation(
        self,
        action_data: Dict[str, Any],
        constraints: List[Dict[str, Any]]
    ) -> Optional[Tuple[Dict[str, Any], str]]:
        """
        Check if action violates any memory constraints.

        Returns:
            (constraint, reason) if violation detected, None otherwise
        """
        if not constraints:
            return None

        action = action_data.get('action', '')

        # Negative keywords that indicate a prohibition
        prohibition_patterns = [
            r'\b(ban|banned|prohibit|forbidden|never|avoid|don\'t|do not|must not)\b',
            r'\b(deprecated|removed|replaced|migrated away from)\b',
            r'\b(security risk|vulnerability|unsafe|insecure)\b',
        ]

        for constraint in constraints:
            text = (constraint.get('text') or '').lower()
            choice = (constraint.get('choice') or '').lower()

            # Check if constraint contains prohibition language
            is_prohibition = any(
                re.search(pattern, text, re.IGNORECASE)
                for pattern in prohibition_patterns
            )

            if is_prohibition:
                keyword = constraint.get('matched_keyword', '')
                # Check if the action is trying to use the prohibited thing
                if action == 'edit_file':
                    content = action_data.get('content', '').lower()
                    if keyword in content:
                        return (
                            constraint,
                            f"Memory constraint #{constraint['id']} prohibits '{keyword}': {text[:100]}..."
                        )
                elif action == 'exec':
                    cmd = action_data.get('cmd', '').lower()
                    if keyword in cmd:
                        return (
                            constraint,
                            f"Memory constraint #{constraint['id']} prohibits '{keyword}': {text[:100]}..."
                        )

        return None

    def check_memory_constraints(self, action_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Check if action violates any memory constraints.

        Returns:
            {'violation': bool, 'constraint': dict|None, 'reason': str}
        """
        if not self.enforce_memory:
            return {'violation': False, 'constraint': None, 'reason': 'Memory enforcement disabled'}

        # Only check certain action types
        action = action_data.get('action', '')
        if action not in ('edit_file', 'exec', 'http_request'):
            return {'violation': False, 'constraint': None, 'reason': 'Action type not subject to constraint check'}

        # Extract keywords and search for constraints
        keywords = self._extract_action_keywords(action_data)
        if not keywords:
            return {'violation': False, 'constraint': None, 'reason': 'No searchable keywords found'}

        constraints = self._search_memory_constraints(keywords)
        if not constraints:
            return {'violation': False, 'constraint': None, 'reason': 'No relevant constraints found'}

        # Check for violations
        violation = self._check_constraint_violation(action_data, constraints)
        if violation:
            constraint, reason = violation
            return {'violation': True, 'constraint': constraint, 'reason': reason}

        return {
            'violation': False,
            'constraint': None,
            'reason': f'No violations found (checked {len(constraints)} constraints)',
            'checked_constraints': len(constraints)
        }

    def check_action(self, action_data):
        """
        Check if action is allowed.
        Returns: {'decision': 'ALLOW'|'ESCALATE'|'DENY', 'reason': str, 'pending_id': int|None, 'constraint_id': int|None}
        """
        action = action_data.get('action', '').lower()

        # Check DENY list first (always, even in unrestricted mode)
        for deny_action, reason in DENY_ACTIONS.items():
            if deny_action in action.lower():
                self._log_audit(action, action_data, 'DENY', reason)
                return {'decision': 'DENY', 'reason': reason, 'constraint_id': None}

        # Check memory constraints BEFORE unrestricted bypass
        # Memory constraints are enforced even in unrestricted mode
        # to ensure core architectural decisions are respected
        if self.enforce_memory and action in ('edit_file', 'exec', 'http_request'):
            constraint_result = self.check_memory_constraints(action_data)
            if constraint_result.get('violation'):
                constraint = constraint_result.get('constraint', {})
                reason = f"MEMORY CONSTRAINT VIOLATION: {constraint_result.get('reason', 'Unknown')}"
                self._log_audit(action, action_data, 'DENY', reason)
                return {
                    'decision': 'DENY',
                    'reason': reason,
                    'constraint_id': constraint.get('id'),
                    'constraint_text': constraint.get('text', '')[:200]
                }

        # Unrestricted mode: allow but log (after constraint check)
        if self.unrestricted:
            self._log_audit(action, action_data, 'ALLOW', 'Unrestricted mode')
            return {'decision': 'ALLOW', 'reason': 'Unrestricted mode', 'constraint_id': None}

        # Check ESCALATE conditions
        if action == 'write_memory':
            entry_type = action_data.get('type', 'n')
            if entry_type in ESCALATE_ACTIONS.get('write_memory', {}).get('types', []):
                reason = f"Decision writes (type={entry_type}) require human review"
                pending_id = self._queue_for_review(action, action_data, reason)
                self._log_audit(action, action_data, 'ESCALATE', reason)
                return {'decision': 'ESCALATE', 'reason': reason, 'pending_id': pending_id}

        if action in ESCALATE_ACTIONS and action != 'write_memory':
            reason = ESCALATE_ACTIONS[action].get('description', 'Requires review')
            pending_id = self._queue_for_review(action, action_data, reason)
            self._log_audit(action, action_data, 'ESCALATE', reason)
            return {'decision': 'ESCALATE', 'reason': reason, 'pending_id': pending_id}

        # Check ALLOW conditions
        if action in ALLOW_ACTIONS:
            allow_config = ALLOW_ACTIONS[action]
            if 'types' in allow_config:
                entry_type = action_data.get('type', '')
                if entry_type in allow_config['types']:
                    reason = allow_config.get('description', 'Allowed')
                    self._log_audit(action, action_data, 'ALLOW', reason)
                    return {'decision': 'ALLOW', 'reason': reason}
            else:
                reason = allow_config.get('description', 'Allowed')
                self._log_audit(action, action_data, 'ALLOW', reason)
                return {'decision': 'ALLOW', 'reason': reason}

        # Default: escalate unknown actions
        reason = f"Unknown action '{action}' requires review"
        pending_id = self._queue_for_review(action, action_data, reason)
        self._log_audit(action, action_data, 'ESCALATE', reason)
        return {'decision': 'ESCALATE', 'reason': reason, 'pending_id': pending_id}

    def _queue_for_review(self, action, action_data, reason):
        """Queue action for human review"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            from datetime import UTC
            now = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
        except ImportError:
            now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

        cursor.execute("""
            INSERT INTO pending_changes (action_type, action_data, proposed_by, proposed_at, status)
            VALUES (?, ?, ?, ?, 'pending')
        """, (action, json.dumps(action_data), self.actor, now))
        pending_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return pending_id

    def _log_audit(self, action, action_data, decision, reason):
        """Log decision to audit_log"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            from datetime import UTC
            now = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
        except ImportError:
            now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

        cursor.execute("""
            INSERT INTO audit_log (timestamp, action_type, action_data, decision, reason, actor)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (now, action, json.dumps(action_data), decision, reason, self.actor))
        conn.commit()
        conn.close()

    def get_pending(self):
        """Get all pending changes"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, action_type, action_data, proposed_by, proposed_at
            FROM pending_changes
            WHERE status = 'pending'
            ORDER BY proposed_at ASC
        """)
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                'id': r[0],
                'action_type': r[1],
                'action_data': json.loads(r[2]),
                'proposed_by': r[3],
                'proposed_at': r[4]
            }
            for r in rows
        ]

    def approve(self, pending_id, reviewer='human', notes=''):
        """Approve a pending change"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            from datetime import UTC
            now = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
        except ImportError:
            now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

        cursor.execute("""
            UPDATE pending_changes
            SET status = 'approved', reviewed_by = ?, reviewed_at = ?, review_notes = ?
            WHERE id = ? AND status = 'pending'
        """, (reviewer, now, notes, pending_id))

        # Get the action data for execution
        cursor.execute("SELECT action_type, action_data, status FROM pending_changes WHERE id = ?", (pending_id,))
        row = cursor.fetchone()
        conn.commit()
        conn.close()

        if row and row[2] == 'approved':
            self._log_audit(row[0], json.loads(row[1]), 'APPROVED', f"Approved by {reviewer}: {notes}")
            return {'action_type': row[0], 'action_data': json.loads(row[1])}
        return None

    def reject(self, pending_id, reviewer='human', notes=''):
        """Reject a pending change"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            from datetime import UTC
            now = datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
        except ImportError:
            now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')

        cursor.execute("""
            UPDATE pending_changes
            SET status = 'rejected', reviewed_by = ?, reviewed_at = ?, review_notes = ?
            WHERE id = ? AND status = 'pending'
        """, (reviewer, now, notes, pending_id))
        conn.commit()
        conn.close()
        self._log_audit('reject', {'pending_id': pending_id}, 'REJECTED', f"Rejected by {reviewer}: {notes}")

    def get_audit_log(self, limit=20):
        """Get recent audit log entries"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT timestamp, action_type, decision, reason, actor
            FROM audit_log
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        conn.close()
        return [
            {
                'timestamp': r[0],
                'action_type': r[1],
                'decision': r[2],
                'reason': r[3],
                'actor': r[4]
            }
            for r in rows
        ]

    def get_constraints(self, limit=20) -> List[Dict[str, Any]]:
        """Get active memory constraints (Decision and Lesson entries)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, anchor_type, anchor_topic, text, anchor_choice, importance, timestamp
            FROM chunks
            WHERE anchor_type IN ('d', 'D', 'L')
            ORDER BY
                CASE importance
                    WHEN 'H' THEN 1
                    WHEN 'M' THEN 2
                    WHEN 'L' THEN 3
                    ELSE 4
                END,
                timestamp DESC
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        conn.close()

        type_labels = {
            'd': 'Decision', 'D': 'Decision',
            'L': 'Lesson'
        }

        return [
            {
                'id': r[0],
                'type': type_labels.get(r[1], r[1]),
                'topic': r[2],
                'text': r[3],
                'choice': r[4],
                'importance': r[5] or 'M',
                'timestamp': r[6]
            }
            for r in rows
        ]


def main():
    parser = argparse.ArgumentParser(description='Governor - Action enforcement')
    parser.add_argument('--db', default=str(SCRIPT_DIR / 'memory.db'), help='Database path')
    parser.add_argument('--unrestricted', action='store_true', help='Allow all actions (logged)')
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # check command
    check_parser = subparsers.add_parser('check', help='Check if action is allowed')
    check_parser.add_argument('action_json', help='Action as JSON string')

    # pending command
    subparsers.add_parser('pending', help='List pending changes')

    # approve command
    approve_parser = subparsers.add_parser('approve', help='Approve pending change')
    approve_parser.add_argument('id', type=int, help='Pending change ID')
    approve_parser.add_argument('--notes', default='', help='Review notes')

    # reject command
    reject_parser = subparsers.add_parser('reject', help='Reject pending change')
    reject_parser.add_argument('id', type=int, help='Pending change ID')
    reject_parser.add_argument('--notes', default='', help='Review notes')

    # audit command
    audit_parser = subparsers.add_parser('audit', help='Show audit log')
    audit_parser.add_argument('--limit', type=int, default=20, help='Number of entries')

    # constraints command
    constraints_parser = subparsers.add_parser('constraints', help='Show active memory constraints')
    constraints_parser.add_argument('--limit', type=int, default=20, help='Number of constraints')

    args = parser.parse_args()
    gov = Governor(args.db, unrestricted=args.unrestricted)

    if args.command == 'check':
        action_data = json.loads(args.action_json)
        result = gov.check_action(action_data)
        print(json.dumps(result, indent=2))

    elif args.command == 'pending':
        pending = gov.get_pending()
        if not pending:
            print("No pending changes.")
        else:
            print(f"Pending changes ({len(pending)}):\n")
            for p in pending:
                print(f"  [{p['id']}] {p['action_type']}")
                print(f"      Data: {json.dumps(p['action_data'])[:80]}...")
                print(f"      Proposed: {p['proposed_at']} by {p['proposed_by']}")
                print()

    elif args.command == 'approve':
        result = gov.approve(args.id, notes=args.notes)
        if result:
            print(f"Approved pending change {args.id}")
            print(f"Action: {result['action_type']}")
            print(f"Data: {json.dumps(result['action_data'], indent=2)}")
        else:
            print(f"Pending change {args.id} not found")

    elif args.command == 'reject':
        gov.reject(args.id, notes=args.notes)
        print(f"Rejected pending change {args.id}")

    elif args.command == 'audit':
        entries = gov.get_audit_log(args.limit)
        if not entries:
            print("No audit log entries.")
        else:
            print(f"Audit log (last {len(entries)} entries):\n")
            for e in entries:
                decision_color = {
                    'ALLOW': '\033[32m',      # green
                    'ESCALATE': '\033[33m',   # yellow
                    'DENY': '\033[31m',       # red
                    'APPROVED': '\033[32m',
                    'REJECTED': '\033[31m'
                }.get(e['decision'], '')
                print(f"  {e['timestamp'][:19]} {decision_color}[{e['decision']}]\033[0m {e['action_type']}")
                print(f"      {e['reason']}")
                print()

    elif args.command == 'constraints':
        constraints = gov.get_constraints(args.limit)
        if not constraints:
            print("No memory constraints found (no Decision/Lesson entries).")
        else:
            print(f"Active Memory Constraints ({len(constraints)} entries):\n")
            for c in constraints:
                importance_color = {
                    'H': '\033[31m',  # red for high
                    'M': '\033[33m',  # yellow for medium
                    'L': '\033[32m',  # green for low
                }.get(c['importance'], '')
                type_label = c['type']
                print(f"  [{c['id']}] {importance_color}[{c['importance']}]\033[0m {type_label}: {c['topic']}")
                print(f"      {c['text'][:100]}...")
                if c['choice']:
                    print(f"      Choice: {c['choice']}")
                print()

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
