#!/usr/bin/env python3
"""
swarm_daemon.py - Autonomous memory daemon with JSON action protocol

Runs a continuous loop executing objectives autonomously.
LLM decides actions, daemon executes them. No approval gates.

Usage:
    ./swarm_daemon.py --objective "Organize memory by consolidating duplicates"
    ./swarm_daemon.py --objective-file objectives.txt
    ./swarm_daemon.py --resume  # Resume from daemon_state.json
    ./swarm_daemon.py --max-iterations 10  # Limit iterations
    ./swarm_daemon.py --repo-root /home/user/Documents/vale-village  # Operate inside repo
    ./swarm_daemon.py --unrestricted  # Allow full action set (see safety notes)
    ./swarm_daemon.py --llm codex --llm-model gpt-5.1-codex-max  # Use Codex instead of Claude

Actions (JSON protocol):
    {"action": "write_memory", "type": "f", "topic": "x", "text": "..."}
    {"action": "mem_search", "query": "topic=x", "limit": 5}
    {"action": "consolidate", "id": 123}  # or "recent" or "all"
    {"action": "sleep", "seconds": 5}
    {"action": "done", "summary": "Completed objective"}

Safety:
    - Max 100 iterations/hour (configurable)
    - Kill switch: touch daemon.kill to stop
    - All prompts/responses logged to daemon.log
"""

import argparse
import json
import subprocess
import sys
import os
import time
import logging
import shlex
from typing import List, Tuple
from datetime import datetime, timedelta
from pathlib import Path

# Import governor for action enforcement
from governor import Governor

# Configuration
SCRIPT_DIR = Path(__file__).parent.resolve()
STATE_FILE = SCRIPT_DIR / "daemon_state.json"
KILL_FILE = SCRIPT_DIR / "daemon.kill"
LOG_FILE = SCRIPT_DIR / "daemon.log"
MEM_DB = SCRIPT_DIR / "mem-db.sh"
DEFAULT_REPO_ROOT = Path(os.environ.get("REPO_ROOT", Path.home() / "Documents" / "vale-village"))

# Rate limiting
MAX_ITERATIONS_PER_HOUR = 100
ITERATION_WINDOW = timedelta(hours=1)
SAFE_COMMAND_PREFIXES: Tuple[str, ...] = (
    "npm", "yarn", "pnpm", "bun", "go", "cargo", "python", "pip", "pytest", "make", "node"
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class DaemonState:
    """Persistent state for daemon across restarts"""

    def __init__(self):
        self.objective = ""
        self.iteration = 0
        self.iteration_times = []  # timestamps for rate limiting
        self.history = []  # action history
        self.status = "idle"  # idle, running, done, error
        self.started_at = None
        self.last_action = None
        self.repo_root = str(DEFAULT_REPO_ROOT)
        self.unrestricted = False
        self.llm_provider = "claude"
        self.llm_model = None

    def save(self):
        """Save state to file"""
        data = {
            "objective": self.objective,
            "iteration": self.iteration,
            "iteration_times": [t.isoformat() for t in self.iteration_times],
            "history": self.history[-50:],  # Keep last 50 actions
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_action": self.last_action,
            "repo_root": self.repo_root,
            "unrestricted": self.unrestricted,
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model
        }
        STATE_FILE.write_text(json.dumps(data, indent=2))

    def load(self):
        """Load state from file"""
        if not STATE_FILE.exists():
            return False
        try:
            data = json.loads(STATE_FILE.read_text())
            self.objective = data.get("objective", "")
            self.iteration = data.get("iteration", 0)
            self.iteration_times = [
                datetime.fromisoformat(t) for t in data.get("iteration_times", [])
            ]
            self.history = data.get("history", [])
            self.status = data.get("status", "idle")
            self.started_at = datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None
            self.last_action = data.get("last_action")
            self.repo_root = data.get("repo_root", str(DEFAULT_REPO_ROOT))
            self.unrestricted = bool(data.get("unrestricted", False))
            self.llm_provider = data.get("llm_provider", "claude")
            self.llm_model = data.get("llm_model")
            return True
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            return False

    def check_rate_limit(self):
        """Check if we're within rate limits"""
        now = datetime.now()
        cutoff = now - ITERATION_WINDOW
        # Remove old timestamps
        self.iteration_times = [t for t in self.iteration_times if t > cutoff]
        return len(self.iteration_times) < MAX_ITERATIONS_PER_HOUR

    def record_iteration(self):
        """Record an iteration for rate limiting"""
        self.iteration_times.append(datetime.now())
        self.iteration += 1


def check_kill_switch():
    """Check if kill switch is active"""
    return KILL_FILE.exists()


def clear_kill_switch():
    """Clear the kill switch"""
    if KILL_FILE.exists():
        KILL_FILE.unlink()


def call_llm(prompt, verbose=False, provider="claude", model=None):
    """Call LLM CLI with prompt (Claude or Codex)"""
    if verbose:
        logger.info(f"LLM prompt ({len(prompt)} chars):\n{prompt[:500]}...")
    else:
        logger.debug(f"LLM prompt: {prompt[:200]}...")

    if provider == "codex":
        codex_model = model or os.environ.get("CODEX_MODEL", "gpt-5.1-codex-latest")
        cmd = ['codex', 'exec', '-m', codex_model, '--full-auto', prompt]
    else:
        claude_model = model or os.environ.get("CLAUDE_MODEL")
        cmd = ['claude']
        if claude_model:
            cmd.extend(['-m', claude_model])
        cmd.extend(['-p', prompt])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120
        )
        response = result.stdout.strip()
        if verbose:
            logger.info(f"LLM response ({len(response)} chars):\n{response[:500]}...")
        else:
            logger.debug(f"LLM response: {response[:200]}...")
        return response
    except subprocess.TimeoutExpired:
        logger.error("LLM call timed out")
        return None
    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return None


def call_mem_db(cmd, *args):
    """Call mem-db.sh with command and args"""
    try:
        result = subprocess.run(
            [str(MEM_DB), cmd] + list(args),
            capture_output=True,
            text=True,
            timeout=60
        )
        return result.stdout.strip(), result.returncode == 0
    except Exception as e:
        logger.error(f"mem-db.sh {cmd} failed: {e}")
        return str(e), False


def resolve_repo_root(path_str: str) -> Path:
    """Resolve and validate repository root"""
    root = Path(path_str).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Repo root not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Repo root is not a directory: {root}")
    return root


def is_within_repo(path: Path, repo_root: Path) -> bool:
    """Check if path is within repo root"""
    try:
        path.resolve().relative_to(repo_root.resolve())
        return True
    except ValueError:
        return False


def run_command(
    cmd: List[str],
    cwd: Path,
    unrestricted: bool,
    allowed_prefixes: Tuple[str, ...] = ()
) -> Tuple[str, bool]:
    """
    Run a command with optional allowlist enforcement.
    Returns (output, success).
    """
    if not cmd:
        return "Empty command", False

    if not unrestricted and allowed_prefixes:
        if not any(cmd[0].startswith(prefix) for prefix in allowed_prefixes):
            return f"Command '{cmd[0]}' not allowed in reviewed mode", False

    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=300
        )
        output = result.stdout + result.stderr
        return output.strip(), result.returncode == 0
    except subprocess.TimeoutExpired:
        return "Command timed out", False
    except FileNotFoundError:
        return f"Command not found: {cmd[0]}", False
    except Exception as e:
        return str(e), False


def collect_repo_context(repo_root: Path) -> str:
    """Collect lightweight repo context: branch, status, recent log"""
    parts = []

    # Branch
    branch_cmd = ["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"]
    branch_out, ok = run_command(branch_cmd, repo_root, True)
    if ok:
        parts.append(f"Branch: {branch_out.splitlines()[0]}")

    # Status
    status_cmd = ["git", "-C", str(repo_root), "status", "--short"]
    status_out, _ = run_command(status_cmd, repo_root, True)
    if status_out:
        parts.append("Status:\n" + "\n".join(status_out.splitlines()[:50]))

    # Recent log
    log_cmd = ["git", "-C", str(repo_root), "log", "-n", "5", "--oneline"]
    log_out, _ = run_command(log_cmd, repo_root, True)
    if log_out:
        parts.append("Recent commits:\n" + log_out)

    return "\n".join(parts)


def execute_action(action_data, repo_root: Path, unrestricted: bool):
    """Execute a single action from JSON with repo awareness"""
    action = action_data.get("action", "unknown")
    logger.info(f"Executing action: {action}")

    # Memory writes/read/search -------------------------------------------------
    if action == "write_memory":
        t = action_data.get("type", "n")
        topic = action_data.get("topic", "daemon")
        text = action_data.get("text", "")
        choice = action_data.get("choice", "")
        args = [f"t={t}", f"topic={topic}", f"text={text}"]
        if choice:
            args.append(f"choice={choice}")
        output, success = call_mem_db("write", *args)
        return {"success": success, "output": output[:500]}

    if action == "mem_search":
        query = action_data.get("query", "")
        limit = action_data.get("limit", 10)
        args = query.split() + [f"limit={limit}"]
        output, success = call_mem_db("render", *args)
        return {"success": success, "output": output[:2000]}

    if action == "consolidate":
        target = action_data.get("id", "recent")
        if target == "recent":
            output, success = call_mem_db("consolidate", "--recent")
        elif target == "all":
            output, success = call_mem_db("consolidate", "--all")
        else:
            output, success = call_mem_db("consolidate", "--id", str(target))
        return {"success": success, "output": output[:1000]}

    # Repo reads ---------------------------------------------------------------
    if action == "read_file":
        path_str = action_data.get("path", "")
        max_bytes = int(action_data.get("max_bytes", 5000))
        raw_path = Path(path_str).expanduser()
        target = raw_path if raw_path.is_absolute() else (repo_root / raw_path)
        target = target.resolve()
        if not unrestricted and not is_within_repo(target, repo_root):
            return {"success": False, "error": "Path outside repo root"}
        try:
            data = target.read_bytes()[:max_bytes]
            try:
                text = data.decode("utf-8", errors="replace")
            except Exception:
                text = str(data)
            return {"success": True, "output": text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    if action == "list_files":
        rel = action_data.get("path", "")
        target_dir = (repo_root / rel).resolve() if rel else repo_root
        if not unrestricted and not is_within_repo(target_dir, repo_root):
            return {"success": False, "error": "Path outside repo root"}
        files = []
        for root, _, filenames in os.walk(target_dir):
            for name in filenames:
                full = Path(root) / name
                try:
                    rel_path = full.relative_to(repo_root)
                    files.append(str(rel_path))
                except Exception:
                    continue
            if len(files) >= 200:
                break
        return {"success": True, "files": files[:200]}

    if action == "search_text":
        query = action_data.get("query", "")
        rel = action_data.get("path", "")
        target_dir = (repo_root / rel).resolve() if rel else repo_root
        if not unrestricted and not is_within_repo(target_dir, repo_root):
            return {"success": False, "error": "Path outside repo root"}
        cmd = ["rg", "--no-heading", "--line-number", "--max-count", "20", query, str(target_dir)]
        output, success = run_command(cmd, repo_root, unrestricted, ())
        return {"success": success, "output": output[:4000]}

    if action == "git_log":
        limit = str(action_data.get("limit", 10))
        cmd = ["git", "-C", str(repo_root), "log", "-n", limit, "--oneline"]
        output, success = run_command(cmd, repo_root, True)
        return {"success": success, "output": output[:4000]}

    if action == "git_diff":
        path = action_data.get("path")
        cmd = ["git", "-C", str(repo_root), "diff", "--stat"]
        if path:
            cmd.append(path)
        output, success = run_command(cmd, repo_root, True)
        return {"success": success, "output": output[:4000]}

    if action == "git_status":
        cmd = ["git", "-C", str(repo_root), "status", "--short"]
        output, success = run_command(cmd, repo_root, True)
        return {"success": success, "output": output[:4000]}

    # Command execution --------------------------------------------------------
    if action == "run":
        cmd_str = action_data.get("cmd", "")
        if not cmd_str:
            return {"success": False, "error": "Missing cmd"}
        cmd = shlex.split(cmd_str)
        output, success = run_command(cmd, repo_root, unrestricted, SAFE_COMMAND_PREFIXES)
        return {"success": success, "output": output[:4000]}

    if action == "exec":
        if not unrestricted:
            return {"success": False, "error": "exec not allowed in reviewed mode"}
        cmd_str = action_data.get("cmd", "")
        if not cmd_str:
            return {"success": False, "error": "Missing cmd"}
        # Support cwd parameter for working directory
        cwd_str = action_data.get("cwd", "")
        if cwd_str:
            exec_cwd = Path(cwd_str).expanduser().resolve()
            if not exec_cwd.exists():
                return {"success": False, "error": f"cwd does not exist: {exec_cwd}"}
        else:
            exec_cwd = repo_root
        # Use shell=True to support shell built-ins (cd, &&, pipes, etc.)
        try:
            result = subprocess.run(
                cmd_str,
                shell=True,
                cwd=str(exec_cwd),
                capture_output=True,
                text=True,
                timeout=300
            )
            output = result.stdout + result.stderr
            return {"success": result.returncode == 0, "output": output.strip()[:4000]}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Command timed out"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    if action == "edit_file":
        path_str = action_data.get("path", "")
        content = action_data.get("content", "")
        mode = action_data.get("mode", "replace")
        target = (repo_root / path_str).resolve()
        if not unrestricted and not is_within_repo(target, repo_root):
            return {"success": False, "error": "Path outside repo root"}
        try:
            if mode == "append":
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("a", encoding="utf-8") as f:
                    f.write(content)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            return {"success": True, "output": f"Wrote {target}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    if action == "http_request":
        if not unrestricted:
            return {"success": False, "error": "http_request not allowed in reviewed mode"}
        import urllib.request
        method = action_data.get("method", "GET").upper()
        url = action_data.get("url", "")
        body = action_data.get("body")
        headers = action_data.get("headers", {})
        req = urllib.request.Request(url, method=method)
        for k, v in headers.items():
            req.add_header(k, v)
        data = body.encode("utf-8") if isinstance(body, str) else body
        try:
            with urllib.request.urlopen(req, data=data, timeout=30) as resp:
                text = resp.read(4000).decode("utf-8", errors="replace")
                return {"success": True, "status": resp.status, "output": text}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # Control ------------------------------------------------------------------
    if action == "sleep":
        seconds = min(action_data.get("seconds", 5), 300)  # Max 5 minutes
        logger.info(f"Sleeping for {seconds} seconds")
        time.sleep(seconds)
        return {"success": True, "output": f"Slept {seconds}s"}

    if action == "done":
        summary = action_data.get("summary", "Objective completed")
        logger.info(f"Objective done: {summary}")
        return {"success": True, "done": True, "summary": summary}

    logger.warning(f"Unknown action: {action}")
    return {"success": False, "error": f"Unknown action: {action}"}


def parse_actions(response):
    """Parse JSON actions from LLM response"""
    actions = []

    # Try to find JSON in the response
    # Look for {...} patterns
    import re
    json_pattern = r'\{[^{}]*\}'
    matches = re.findall(json_pattern, response, re.DOTALL)

    for match in matches:
        try:
            action = json.loads(match)
            if "action" in action:
                actions.append(action)
        except json.JSONDecodeError:
            continue

    # If no valid JSON found, try to parse entire response
    if not actions:
        try:
            action = json.loads(response)
            if "action" in action:
                actions.append(action)
        except json.JSONDecodeError:
            pass

    return actions


def build_prompt(state, repo_root: Path, unrestricted: bool, last_results=None):
    """Build prompt for LLM with context"""

    context_output, _ = call_mem_db("render", "limit=10")
    repo_context = collect_repo_context(repo_root)

    mode = "UNRESTRICTED" if unrestricted else "REVIEWED (safe defaults)"
    llm = state.llm_provider.upper()

    prompt = f"""You are an autonomous daemon working on an objective inside a repo.

MODE: {mode}
REPO: {repo_root}
LLM: {llm}
OBJECTIVE: {state.objective}
ITERATION: {state.iteration}

RECENT MEMORY:
{context_output}

REPO CONTEXT:
{repo_context}

AVAILABLE ACTIONS (one JSON object):
- {{"action": "write_memory", "type": "f|d|q|a|n", "topic": "...", "text": "..."}}
- {{"action": "mem_search", "query": "t=d topic=...", "limit": 5}}
- {{"action": "consolidate", "id": "recent|all|<number>"}}
- {{"action": "read_file", "path": "src/index.ts", "max_bytes": 4000}}
- {{"action": "list_files", "path": "src"}}
- {{"action": "search_text", "query": "TODO", "path": "src"}}
- {{"action": "git_log", "limit": 10}}
- {{"action": "git_diff", "path": "src"}}
- {{"action": "git_status"}}
- {{"action": "run", "cmd": "npm test"}}  # safe commands only unless unrestricted
- {{"action": "edit_file", "path": "file", "content": "...", "mode": "replace|append"}}  # unrestricted only
- {{"action": "exec", "cmd": "cd src && ls -la", "cwd": "/path/to/dir"}}  # unrestricted, shell=True
- {{"action": "http_request", "method": "GET", "url": "https://..."}}
- {{"action": "sleep", "seconds": 5}}
- {{"action": "done", "summary": "What was accomplished"}}

"""

    if last_results:
        prompt += f"LAST ACTION RESULTS:\n{json.dumps(last_results, indent=2)}\n\n"

    if state.history:
        recent_history = state.history[-5:]
        prompt += "RECENT ACTIONS:\n"
        for h in recent_history:
            prompt += f"- {h.get('action', '?')}: {h.get('result', {}).get('output', '')[:100]}...\n"
        prompt += "\n"

    prompt += """Decide your next action. Output ONLY valid JSON for one action.
If the objective is complete, use the "done" action.

Your action:"""

    return prompt


def run_daemon(state, repo_root: Path, unrestricted: bool, verbose=False, use_governor=True):
    """Main daemon loop"""
    logger.info(f"Starting daemon with objective: {state.objective}")
    logger.info(f"Repo root: {repo_root} | Mode: {'UNRESTRICTED' if unrestricted else 'REVIEWED'}")
    state.status = "running"
    state.started_at = datetime.now()
    state.save()

    # Initialize governor for action enforcement
    governor = Governor(str(SCRIPT_DIR / "memory.db"), unrestricted=unrestricted) if use_governor else None
    if governor:
        logger.info("Governor enabled - actions will be pre-checked")

    last_results = None

    while True:
        # Check kill switch
        if check_kill_switch():
            logger.warning("Kill switch activated, stopping daemon")
            state.status = "killed"
            state.save()
            clear_kill_switch()
            return

        # Check rate limit
        if not state.check_rate_limit():
            wait_time = 60
            logger.warning(f"Rate limit reached, waiting {wait_time}s")
            time.sleep(wait_time)
            continue

        # Build prompt and call LLM
        prompt = build_prompt(state, repo_root, unrestricted, last_results)
        logger.info(f"Iteration {state.iteration}: calling LLM")

        response = call_llm(prompt, verbose=verbose, provider=state.llm_provider, model=state.llm_model)
        if not response:
            logger.error("No response from LLM")
            state.status = "error"
            state.save()
            return

        # Parse actions from response
        actions = parse_actions(response)
        if not actions:
            logger.warning(f"No valid actions parsed from: {response[:200]}")
            # Record failed parse
            state.record_iteration()
            state.history.append({
                "action": "parse_error",
                "response": response[:500],
                "result": {"success": False}
            })
            state.save()
            continue

        # Execute first action (with governor pre-check)
        action_data = actions[0]
        logger.info(f"Proposed action: {json.dumps(action_data)}")

        # Governor pre-flight check
        if governor:
            gov_result = governor.check_action(action_data)
            logger.info(f"Governor decision: {gov_result['decision']} - {gov_result['reason']}")

            if gov_result['decision'] == 'DENY':
                result = {'success': False, 'error': f"Blocked by governor: {gov_result['reason']}"}
                last_results = result
                state.record_iteration()
                state.history.append({
                    'action': action_data.get('action'),
                    'data': action_data,
                    'result': result,
                    'governor': 'DENY'
                })
                state.save()
                continue

            elif gov_result['decision'] == 'ESCALATE':
                result = {
                    'success': False,
                    'escalated': True,
                    'pending_id': gov_result.get('pending_id'),
                    'reason': gov_result['reason']
                }
                logger.info(f"Action escalated to pending queue (id={gov_result.get('pending_id')})")
                last_results = result
                state.record_iteration()
                state.history.append({
                    'action': action_data.get('action'),
                    'data': action_data,
                    'result': result,
                    'governor': 'ESCALATE'
                })
                state.save()
                continue

            # ALLOW - proceed with execution
            logger.info(f"Governor approved, executing action")

        result = execute_action(action_data, repo_root, unrestricted)
        last_results = result

        # Record in history
        state.record_iteration()
        state.history.append({
            "action": action_data.get("action"),
            "data": action_data,
            "result": result
        })
        state.last_action = action_data.get("action")
        state.save()

        # Check if done
        if result.get("done"):
            logger.info(f"Daemon completed: {result.get('summary')}")
            state.status = "done"
            state.save()

            # Write completion to memory
            call_mem_db("write",
                "t=a",
                "topic=daemon",
                f"text=Daemon completed objective: {state.objective}. Summary: {result.get('summary')}",
                "choice=done"
            )
            return

        # Small delay between iterations
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description='Autonomous memory daemon')
    parser.add_argument('--objective', '-o', help='Objective to accomplish')
    parser.add_argument('--objective-file', help='File containing objective')
    parser.add_argument('--resume', action='store_true', help='Resume from saved state')
    parser.add_argument('--max-iterations', type=int, default=100, help='Max iterations')
    parser.add_argument('--clear-kill', action='store_true', help='Clear kill switch')
    parser.add_argument('--status', action='store_true', help='Show daemon status')
    parser.add_argument('--verbose', '-v', action='store_true', help='Log full prompts/responses at INFO level')
    parser.add_argument('--no-governor', action='store_true', help='Disable governor pre-flight checks')
    parser.add_argument('--repo-root', default=str(DEFAULT_REPO_ROOT), help='Repository root for actions')
    parser.add_argument('--unrestricted', action='store_true', help='Allow full action set (exec/edit/http); logs all actions')
    parser.add_argument('--llm', choices=['claude', 'codex'], default='claude', help='LLM provider (default: claude)')
    parser.add_argument('--llm-model', help='Override model for the chosen provider')
    args = parser.parse_args()

    if args.clear_kill:
        clear_kill_switch()
        print("Kill switch cleared")
        return

    state = DaemonState()

    if args.status:
        if state.load():
            print(f"Status: {state.status}")
            print(f"Objective: {state.objective}")
            print(f"Iteration: {state.iteration}")
            print(f"Last action: {state.last_action}")
            print(f"Repo root: {state.repo_root}")
            print(f"Unrestricted: {state.unrestricted}")
            print(f"LLM: {state.llm_provider} ({state.llm_model or 'default'})")
        else:
            print("No saved state")
        return

    if args.resume:
        if not state.load():
            print("No state to resume from", file=sys.stderr)
            sys.exit(1)
        if state.status == "done":
            print("Objective already completed", file=sys.stderr)
            sys.exit(0)
        # Allow overriding repo/unrestricted on resume
        state.repo_root = args.repo_root or state.repo_root
        state.unrestricted = bool(args.unrestricted or state.unrestricted)
        state.llm_provider = args.llm or state.llm_provider
        state.llm_model = args.llm_model or state.llm_model
    else:
        # New objective
        objective = args.objective
        if args.objective_file:
            objective = Path(args.objective_file).read_text().strip()

        if not objective:
            parser.print_help()
            sys.exit(1)

        state.objective = objective
        state.repo_root = args.repo_root
        state.unrestricted = args.unrestricted
        state.llm_provider = args.llm
        state.llm_model = args.llm_model

    # Set max iterations
    global MAX_ITERATIONS_PER_HOUR
    MAX_ITERATIONS_PER_HOUR = args.max_iterations

    # Validate repo root
    try:
        repo_root = resolve_repo_root(state.repo_root)
        state.repo_root = str(repo_root)
    except Exception as e:
        print(f"Invalid repo root: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        run_daemon(state, repo_root, state.unrestricted, verbose=args.verbose, use_governor=not args.no_governor)
    except KeyboardInterrupt:
        logger.info("Daemon interrupted by user")
        state.status = "interrupted"
        state.save()
    except Exception as e:
        logger.exception(f"Daemon error: {e}")
        state.status = "error"
        state.save()
        raise


if __name__ == '__main__':
    main()
