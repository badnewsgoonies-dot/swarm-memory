#!/usr/bin/env python3
"""
ingest_gh_issue.py - Ingest a GitHub issue as a bounty TODO

Usage:
    python ingest_gh_issue.py https://github.com/owner/repo/issues/123
    python ingest_gh_issue.py owner/repo 123
    python ingest_gh_issue.py --list owner/repo  # List bounty issues

Requires: gh CLI (GitHub CLI) to be installed and authenticated.
"""

import argparse
import json
import os
import re
import subprocess
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DB_PATH = Path(os.environ.get("MEMORY_DB", SCRIPT_DIR / "memory.db"))


def run_gh(args: list) -> dict:
    """Run a gh CLI command and return JSON output"""
    cmd = ["gh"] + args
    result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if result.returncode != 0:
        raise Exception(f"gh command failed: {result.stderr}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def parse_issue_url(url: str) -> tuple:
    """Parse GitHub issue URL into (owner/repo, issue_number)"""
    # Handle full URL: https://github.com/owner/repo/issues/123
    match = re.match(r'https?://github\.com/([^/]+/[^/]+)/issues/(\d+)', url)
    if match:
        return match.group(1), int(match.group(2))

    # Handle short form: owner/repo 123
    match = re.match(r'^([^/]+/[^/]+)$', url)
    if match:
        return match.group(1), None

    raise ValueError(f"Invalid GitHub issue URL: {url}")


def get_issue_details(repo: str, issue_num: int) -> dict:
    """Fetch issue details from GitHub"""
    return run_gh([
        "issue", "view", str(issue_num),
        "--repo", repo,
        "--json", "number,title,body,labels,state,author,createdAt,url"
    ])


def list_bounty_issues(repo: str, label: str = "bounty") -> list:
    """List open issues with bounty label"""
    issues = run_gh([
        "issue", "list",
        "--repo", repo,
        "--label", label,
        "--state", "open",
        "--json", "number,title,labels"
    ])
    return issues


def extract_bounty_amount(labels: list) -> str:
    """Extract bounty amount from labels like '$60', '$100'"""
    for label in labels:
        name = label.get('name', '')
        if name.startswith('$'):
            return name
    return "unknown"


def create_todo(task_id: str, topic: str, text: str, importance: str = "M",
                source: str = "gh-ingest", links: dict = None):
    """Create a TODO in the memory database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    links_json = json.dumps(links) if links else json.dumps({"id": task_id})

    # Check if TODO already exists
    cursor.execute("""
        SELECT id FROM chunks
        WHERE anchor_type='T' AND task_id=?
    """, (task_id,))

    if cursor.fetchone():
        print(f"TODO {task_id} already exists, updating...")
        cursor.execute("""
            UPDATE chunks SET text=?, anchor_choice='OPEN', timestamp=?
            WHERE anchor_type='T' AND task_id=?
        """, (text, ts, task_id))
    else:
        cursor.execute("""
            INSERT INTO chunks (
                bucket, timestamp, text, anchor_type, anchor_topic,
                anchor_choice, anchor_source, task_id, links, importance
            ) VALUES (
                'anchor', ?, ?, 'T', ?, 'OPEN', ?, ?, ?, ?
            )
        """, (ts, text, topic, source, task_id, links_json, importance))

    conn.commit()
    conn.close()
    print(f"Created TODO: {task_id}")


def ingest_issue(repo: str, issue_num: int):
    """Ingest a GitHub issue as a TODO"""
    print(f"Fetching issue {repo}#{issue_num}...")
    issue = get_issue_details(repo, issue_num)

    # Extract info
    title = issue.get('title', 'Unknown')
    body = issue.get('body', '')[:500]  # Truncate body
    labels = issue.get('labels', [])
    url = issue.get('url', f'https://github.com/{repo}/issues/{issue_num}')
    author = issue.get('author', {}).get('login', 'unknown')

    # Determine bounty amount and importance
    bounty = extract_bounty_amount(labels)
    if bounty != "unknown":
        importance = "H" if int(bounty.replace('$', '')) >= 100 else "M"
    else:
        importance = "M"

    # Build task ID
    repo_short = repo.split('/')[-1]
    task_id = f"{repo_short}-{issue_num}"

    # Build topic from labels
    label_names = [l.get('name', '') for l in labels]
    topic = f"bounty-{repo_short}"

    # Build description
    text = f"[{bounty}] {title}\n\nRepo: {repo}\nURL: {url}\nAuthor: @{author}\nLabels: {', '.join(label_names)}\n\n{body}"

    # Create links for tracking
    links = {
        "id": task_id,
        "repo": repo,
        "issue": issue_num,
        "url": url,
        "bounty": bounty
    }

    create_todo(task_id, topic, text, importance, links=links)

    print(f"\n{'='*50}")
    print(f"Ingested: {task_id}")
    print(f"  Title: {title}")
    print(f"  Bounty: {bounty}")
    print(f"  Importance: {importance}")
    print(f"  URL: {url}")
    print(f"{'='*50}")
    print(f"\nRun bounty hunter with:")
    print(f"  python agent_loop.py --mode bounty-hunter --max-todos 1")


def main():
    parser = argparse.ArgumentParser(description="Ingest GitHub issues as bounty TODOs")
    parser.add_argument("url_or_repo", nargs="?", help="GitHub issue URL or owner/repo")
    parser.add_argument("issue_num", nargs="?", type=int, help="Issue number (if not in URL)")
    parser.add_argument("--list", "-l", action="store_true", help="List bounty issues")
    parser.add_argument("--label", default="bounty", help="Label to filter by (default: bounty)")

    args = parser.parse_args()

    if not args.url_or_repo:
        parser.print_help()
        return

    if args.list:
        # List mode
        repo = args.url_or_repo
        print(f"Listing bounty issues in {repo}...")
        issues = list_bounty_issues(repo, args.label)

        if not issues:
            print(f"No open issues with label '{args.label}'")
            # Try "good first issue" as fallback
            issues = list_bounty_issues(repo, "good first issue")
            if issues:
                print(f"\nFound {len(issues)} 'good first issue' instead:")

        for issue in issues:
            labels = [l.get('name', '') for l in issue.get('labels', [])]
            bounty = extract_bounty_amount(issue.get('labels', []))
            print(f"  #{issue['number']:5} [{bounty:>6}] {issue['title'][:60]}")
        return

    # Ingest mode
    try:
        repo, issue_num_from_url = parse_issue_url(args.url_or_repo)
        issue_num = args.issue_num or issue_num_from_url

        if not issue_num:
            raise ValueError("Issue number required. Use --list to see issues.")

        ingest_issue(repo, issue_num)

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
