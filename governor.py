#!/usr/bin/env python3
"""
governor.py - Action classification and enforcement for autonomous daemon

Classifies actions as:
- ALLOW: Auto-approve (memory writes: facts, notes, questions, actions)
- ESCALATE: Queue for human review (decisions, config changes, code edits)
- DENY: Block immediately (dangerous operations)

Usage:
    from governor import Governor
    gov = Governor(db_path)
    result = gov.check_action(action_data)
    # result: {'decision': 'ALLOW'|'ESCALATE'|'DENY', 'reason': '...'}

CLI:
    ./governor.py check '{"action": "write_memory", "type": "f", ...}'
    ./governor.py pending          # List pending changes
    ./governor.py approve 123      # Approve pending change
    ./governor.py reject 123       # Reject pending change
    ./governor.py audit            # Show audit log
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()

# Action classification rules
ALLOW_ACTIONS = {
    'write_memory': {
        'types': ['f', 'n', 'q', 'a'],  # facts, notes, questions, actions
        'description': 'Memory writes (non-decisions)'
    },
    'mem_search': {
        'description': 'Read-only memory search'
    },
    'sleep': {
        'description': 'Pause execution'
    },
    'done': {
        'description': 'Mark objective complete'
    }
}

ESCALATE_ACTIONS = {
    'write_memory': {
        'types': ['d'],  # decisions require review
        'description': 'Decision writes require human review'
    },
    'consolidate': {
        'description': 'Memory consolidation modifies existing entries'
    },
    'propose_config_update': {
        'description': 'Configuration changes require review'
    }
}

DENY_ACTIONS = {
    'exec': 'Arbitrary code execution not allowed',
    'delete': 'Direct deletion not allowed',
    'drop': 'Schema changes not allowed',
    'truncate': 'Bulk deletion not allowed'
}


class Governor:
    """Action classification and enforcement"""

    def __init__(self, db_path):
        self.db_path = db_path
        self.actor = 'governor'

    def check_action(self, action_data):
        """
        Check if action is allowed.
        Returns: {'decision': 'ALLOW'|'ESCALATE'|'DENY', 'reason': str, 'pending_id': int|None}
        """
        action = action_data.get('action', '')

        # Check DENY list first
        for deny_action, reason in DENY_ACTIONS.items():
            if deny_action in action.lower():
                self._log_audit(action, action_data, 'DENY', reason)
                return {'decision': 'DENY', 'reason': reason}

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
            WHERE id = ?
        """, (reviewer, now, notes, pending_id))

        # Get the action data for execution
        cursor.execute("SELECT action_type, action_data FROM pending_changes WHERE id = ?", (pending_id,))
        row = cursor.fetchone()
        conn.commit()
        conn.close()

        if row:
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
            WHERE id = ?
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


def main():
    parser = argparse.ArgumentParser(description='Governor - Action enforcement')
    parser.add_argument('--db', default=str(SCRIPT_DIR / 'memory.db'), help='Database path')
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

    args = parser.parse_args()
    gov = Governor(args.db)

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

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
