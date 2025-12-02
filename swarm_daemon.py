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
import hashlib
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

    def __init__(self, state_file=None):
        self.state_file = Path(state_file) if state_file else STATE_FILE
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
        self.llm_tier = "auto"

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
            "llm_model": self.llm_model,
            "llm_tier": self.llm_tier
        }
        self.state_file.write_text(json.dumps(data, indent=2))

    def load(self):
        """Load state from file"""
        if not self.state_file.exists():
            return False
        try:
            data = json.loads(self.state_file.read_text())
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
            self.llm_tier = data.get("llm_tier", "auto")
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


def call_llm(prompt, verbose=False, provider="claude", model=None, tier="auto"):
    """Call LLM with prompt (Claude CLI, Codex, or Hybrid local/API)

    Providers:
        - claude: Claude CLI (default)
        - codex: OpenAI Codex CLI
        - hybrid: Tiered Ollama + OpenAI (fast/code/smart/auto)
        - ollama: Direct Ollama call
        - openai: Direct OpenAI API call
    """
    if verbose:
        logger.info(f"LLM prompt ({len(prompt)} chars, provider={provider}, tier={tier}):\n{prompt[:500]}...")
    else:
        logger.debug(f"LLM prompt: {prompt[:200]}...")

    # Hybrid provider using llm_client
    if provider in ("hybrid", "ollama", "openai"):
        try:
            from llm_client import LLMClient
            client = LLMClient()

            # Map provider to tier
            if provider == "ollama":
                use_tier = model or "fast"  # Use model as tier hint for ollama
            elif provider == "openai":
                use_tier = "smart"
            else:
                use_tier = tier

            response = client.complete(prompt, tier=use_tier)

            if response.success:
                if verbose:
                    logger.info(f"LLM response ({response.tier}, {response.latency_ms}ms):\n{response.text[:500]}...")
                return response.text
            else:
                logger.error(f"Hybrid LLM failed: {response.error}")
                return None
        except ImportError:
            logger.error("llm_client module not found, falling back to claude")
            provider = "claude"
        except Exception as e:
            logger.error(f"Hybrid LLM call failed: {e}")
            return None

    # Codex CLI
    if provider == "codex":
        codex_model = model or os.environ.get("CODEX_MODEL", "gpt-5.1-codex-latest")
        cmd = ['codex', 'exec', '-m', codex_model, '--full-auto', prompt]
    # Claude CLI (default)
    else:
        claude_model = model or os.environ.get("CLAUDE_MODEL")
        cmd = ['claude']
        if claude_model:
            cmd.extend(['--model', claude_model])
        cmd.extend(['-p', prompt])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120
        )
        # Claude CLI may output to stderr in non-TTY mode
        response = (result.stdout + result.stderr).strip()
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

    if action == "spawn_daemon":
        # Spawn a sub-daemon with a specific objective (fire and forget or blocking)
        sub_objective = action_data.get("objective", "")
        sub_repo = action_data.get("repo", str(repo_root))
        max_iter = action_data.get("max_iterations", 10)
        wait = action_data.get("wait", False)  # NEW: blocking mode
        timeout = action_data.get("timeout", 300)  # NEW: max wait seconds

        if not sub_objective:
            return {"success": False, "error": "objective required"}

        # Generate unique state file to avoid collision with parent daemon
        import uuid
        sub_state_id = uuid.uuid4().hex[:8]
        sub_state_file = Path(sub_repo) / f"daemon_state_{sub_state_id}.json"

        # subprocess already imported at module level (line 32)
        script_path = Path(__file__).resolve()
        cmd = [
            "python3", str(script_path),
            "--objective", sub_objective,
            "--repo-root", sub_repo,
            "--unrestricted",
            "--max-iterations", str(max_iter),
            "--state-file", str(sub_state_file)
        ]
        # Run in background
        env = os.environ.copy()
        env["HOME"] = str(Path.home() / "swarm/memory/.claude-tmp")
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        call_mem_db("write", "t=a", "topic=daemon", f"text=Spawned sub-daemon PID {proc.pid}: {sub_objective[:100]}")

        if wait:
            # Blocking mode: poll for completion using sub-daemon's unique state file
            state_file = sub_state_file
            start_time = time.time()
            logger.info(f"Waiting for sub-daemon PID {proc.pid} (timeout: {timeout}s)")

            while time.time() - start_time < timeout:
                if state_file.exists():
                    try:
                        with open(state_file) as f:
                            sub_state = json.load(f)
                        if sub_state.get("status") in ["done", "error", "stopped", "killed", "interrupted"]:
                            # Sub-daemon completed
                            sub_result = None
                            if sub_state.get("history"):
                                sub_result = sub_state["history"][-1]
                            return {
                                "success": True,
                                "output": f"Sub-daemon completed: {sub_state.get('status')}",
                                "pid": proc.pid,
                                "sub_result": sub_result,
                                "sub_status": sub_state.get("status"),
                                "sub_history": sub_state.get("history", [])[-3:]  # Last 3 actions
                            }
                    except (json.JSONDecodeError, IOError):
                        pass

                # Check if process is still running
                poll_result = proc.poll()
                if poll_result is not None:
                    # Process exited, check state file one last time
                    if state_file.exists():
                        try:
                            with open(state_file) as f:
                                sub_state = json.load(f)
                            return {
                                "success": True,
                                "output": f"Sub-daemon exited with code {poll_result}",
                                "pid": proc.pid,
                                "sub_status": sub_state.get("status"),
                                "sub_history": sub_state.get("history", [])[-3:]
                            }
                        except (json.JSONDecodeError, IOError, OSError):
                            pass
                    return {"success": False, "error": f"Sub-daemon exited unexpectedly (code {poll_result})", "pid": proc.pid}

                time.sleep(5)  # Poll every 5 seconds

            # Timeout reached
            logger.warning(f"Sub-daemon PID {proc.pid} timeout after {timeout}s")
            return {"success": False, "error": f"Sub-daemon timeout after {timeout}s", "pid": proc.pid, "timeout": True}

        # Non-blocking (existing behavior)
        return {"success": True, "output": f"Spawned sub-daemon PID {proc.pid}", "pid": proc.pid}

    if action == "orch_status":
        # Query orchestration status from memory glyphs
        orch_id = action_data.get("orch_id", "")
        if not orch_id:
            return {"success": False, "error": "orch_id required"}

        # Query memory for orchestration glyphs
        result, ok = call_mem_db("query", f"topic=orch_{orch_id}", "t=a", "recent=1h", "limit=20", "--json")

        if not ok or not result:
            return {"success": True, "orch_id": orch_id, "phases": [], "latest": "unknown", "entry_count": 0}

        # Parse to find latest phase
        try:
            entries = [json.loads(line) for line in result.strip().split('\n') if line.strip()]
            phases = [e.get("anchor_choice", "") for e in entries if e.get("anchor_choice")]
            latest_phase = phases[0] if phases else "unknown"
            return {
                "success": True,
                "orch_id": orch_id,
                "phases": phases,
                "latest": latest_phase,
                "entry_count": len(entries),
                "entries": entries[:5]  # Include first 5 entries for context
            }
        except Exception as e:
            logger.warning(f"Failed to parse orch_status entries: {e}")
            return {"success": True, "orch_id": orch_id, "phases": [], "latest": "unknown", "entry_count": 0}

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

    # Dependency management ----------------------------------------------------
    if action == "check_deps":
        # Check if node_modules exists and lockfile is in sync
        pkg_manager = action_data.get("manager", "pnpm")  # pnpm, npm, yarn
        node_modules = repo_root / "node_modules"

        if not node_modules.exists():
            return {"success": True, "status": "missing", "output": "node_modules not found, run install"}

        # Check lockfile freshness
        lockfiles = {
            "pnpm": "pnpm-lock.yaml",
            "npm": "package-lock.json",
            "yarn": "yarn.lock"
        }
        lockfile = repo_root / lockfiles.get(pkg_manager, "pnpm-lock.yaml")
        pkg_json = repo_root / "package.json"

        if not lockfile.exists():
            return {"success": True, "status": "no-lockfile", "output": f"No {lockfile.name} found"}

        # Compare timestamps - if package.json is newer than lockfile, deps might be stale
        if pkg_json.exists() and pkg_json.stat().st_mtime > lockfile.stat().st_mtime:
            return {"success": True, "status": "stale", "output": "package.json newer than lockfile, consider reinstall"}

        # Quick validation with frozen lockfile
        if pkg_manager == "pnpm":
            cmd = "pnpm install --frozen-lockfile --dry-run 2>&1 || echo 'needs-install'"
        elif pkg_manager == "npm":
            cmd = "npm ci --dry-run 2>&1 || echo 'needs-install'"
        else:
            cmd = "yarn install --frozen-lockfile --check-files 2>&1 || echo 'needs-install'"

        try:
            result = subprocess.run(cmd, shell=True, cwd=str(repo_root), capture_output=True, text=True, timeout=30)
            if "needs-install" in result.stdout or result.returncode != 0:
                return {"success": True, "status": "outdated", "output": "Dependencies need refresh"}
            return {"success": True, "status": "ok", "output": "Dependencies up to date"}
        except Exception as e:
            return {"success": True, "status": "unknown", "output": f"Could not verify: {e}"}

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
        reason = action_data.get("reason", "")  # Optional: why the edit was made
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
            # Auto-record edit to memory
            edit_summary = f"Edited {path_str} ({mode})"
            if reason:
                edit_summary += f": {reason}"
            call_mem_db("write", "t=a", "topic=daemon-edit", f"text={edit_summary[:200]}")
            return {"success": True, "output": f"Wrote {target}", "recorded": True}
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
    """Parse JSON actions from LLM response - handles nested JSON and markdown blocks"""
    actions = []
    import re

    # 1. First try extracting from markdown code blocks
    code_blocks = re.findall(r'```(?:json)?\s*(\{.+?\})\s*```', response, re.DOTALL)
    for block in code_blocks:
        try:
            action = json.loads(block)
            if "action" in action:
                actions.append(action)
                return actions  # Return first valid action
        except json.JSONDecodeError:
            continue

    # 2. Find JSON by matching balanced braces
    def find_json_objects(text):
        objects = []
        i = 0
        while i < len(text):
            if text[i] == '{':
                depth = 1
                start = i
                i += 1
                in_string = False
                escape_next = False
                while i < len(text) and depth > 0:
                    c = text[i]
                    if escape_next:
                        escape_next = False
                    elif c == '\\':
                        escape_next = True
                    elif c == '"' and not escape_next:
                        in_string = not in_string
                    elif not in_string:
                        if c == '{':
                            depth += 1
                        elif c == '}':
                            depth -= 1
                    i += 1
                if depth == 0:
                    objects.append(text[start:i])
            else:
                i += 1
        return objects

    for obj_str in find_json_objects(response):
        try:
            action = json.loads(obj_str)
            if "action" in action:
                actions.append(action)
                return actions  # Return first valid action
        except json.JSONDecodeError:
            continue

    return actions


def build_prompt(state, repo_root: Path, unrestricted: bool, last_results=None):
    """Build prompt for LLM with context"""

    context_output, _ = call_mem_db("render", "limit=10")
    repo_context = collect_repo_context(repo_root)

    mode = "UNRESTRICTED" if unrestricted else "REVIEWED (safe defaults)"
    llm = state.llm_provider.upper()

    # Check for orchestration mode
    orch_context = ""
    if state.objective.upper().startswith("ORCHESTRATE:"):
        # Extract actual objective
        actual_objective = state.objective[12:].strip()

        # Validate non-empty objective
        if not actual_objective:
            logger.warning("Empty orchestration objective, treating as normal mode")
        else:
            orch_id = hashlib.md5(actual_objective.encode()).hexdigest()[:8]

            # Query current orchestration state
            orch_result, _ = call_mem_db("query", f"topic=orch_{orch_id}", "t=a", "recent=1h", "limit=10", "--json")

            # Determine current phase from memory
            # current_round counts number of audit failures (fix attempts)
            current_phase = "implement"  # default
            current_round = 1
            try:
                entries = [json.loads(l) for l in orch_result.strip().split('\n') if l.strip()]
                for e in entries:
                    choice = e.get("anchor_choice", "")
                    if "audit:pass" in choice:
                        current_phase = "done"
                        break
                    elif "audit:fail" in choice:
                        current_phase = "fix"
                        current_round += 1
                    elif "fix_done" in choice:
                        current_phase = "audit"
                    elif "implement_done" in choice:
                        current_phase = "audit"
            except (json.JSONDecodeError, ValueError, KeyError):
                pass

            # Inject orchestration context (only if valid objective)
            orch_context = f"""
ORCHESTRATION MODE ACTIVE
========================
Orchestration ID: orch_{orch_id}
Actual Objective: {actual_objective}
Current Phase: {current_phase}
Current Round: {current_round}
Max Rounds: 5

Recent Orchestration History:
{orch_result[:2000] if orch_result else '(none)'}

PHASE INSTRUCTIONS:
- If phase is 'implement': Spawn implementation sub-daemon with wait=true
- If phase is 'audit': Spawn audit sub-daemon with wait=true
- If phase is 'fix': Spawn fix sub-daemon based on audit findings
- If phase is 'done': Return done action with summary
- If round > 5: Escalate to human

Write a glyph after each phase transition using write_memory with topic=orch_{orch_id}

"""

    prompt = f"""OBJECTIVE: {state.objective}
REPO: {repo_root} | MODE: {mode} | ITERATION: {state.iteration}

{orch_context}
MEMORY:
{context_output}

REPO:
{repo_context}

ACTIONS:
write_memory: {{"action":"write_memory","type":"f|d|q|a|n","topic":"...","text":"..."}}
read_file: {{"action":"read_file","path":"file.ts","max_bytes":4000}}
edit_file: {{"action":"edit_file","path":"file.ts","content":"...","reason":"why"}}
list_files: {{"action":"list_files","path":"src"}}
search_text: {{"action":"search_text","query":"TODO","path":"src"}}
exec: {{"action":"exec","cmd":"pnpm install","cwd":"/path"}}
spawn_daemon: {{"action":"spawn_daemon","objective":"Sub-task","repo":"/path","max_iterations":10,"wait":false,"timeout":300}}
orch_status: {{"action":"orch_status","orch_id":"abc123"}}
git_status/git_log/git_diff: {{"action":"git_status"}}
done: {{"action":"done","summary":"What was accomplished"}}

"""

    if last_results:
        prompt += f"LAST ACTION RESULTS:\n{json.dumps(last_results, indent=2)}\n\n"

    if state.history:
        recent_history = state.history[-5:]
        prompt += "RECENT ACTIONS:\n"
        for h in recent_history:
            prompt += f"- {h.get('action', '?')}: {h.get('result', {}).get('output', '')[:100]}...\n"
        prompt += "\n"

    # Detect action loops
    if len(state.history) >= 3:
        last_3 = [h.get('action') for h in state.history[-3:]]
        if len(set(last_3)) == 1:
            prompt += f"\n**WARNING: You repeated '{last_3[0]}' 3 times. CHANGE ACTION. If listing files, READ one. If reading, EDIT or WRITE.**\n\n"

    prompt += """RULES:
- Output ONLY ONE JSON action. No explanation, no markdown, just JSON.
- You have FULL ACCESS to all paths. NEVER ask for permissions.
- After list_files: use read_file on interesting files.
- After read_file: use edit_file to write new code.
- Progress: list -> read -> edit -> done. Don't loop on same action.
- UNRESTRICTED = full filesystem access granted.

{"action":"...", ...}"""

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

        response = call_llm(prompt, verbose=verbose, provider=state.llm_provider, model=state.llm_model, tier=state.llm_tier)
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
    parser.add_argument('--llm', choices=['claude', 'codex', 'hybrid', 'ollama', 'openai'], default='claude',
                       help='LLM provider: claude (CLI), codex, hybrid (Ollama+OpenAI), ollama, openai')
    parser.add_argument('--llm-model', help='Override model for the chosen provider')
    parser.add_argument('--tier', choices=['auto', 'fast', 'code', 'smart'], default='auto',
                       help='LLM tier for hybrid mode: fast (llama3.2:3b), code (deepseek-coder), smart (gpt-4o-mini)')
    parser.add_argument('--state-file', help='Custom state file path (for sub-daemons)')
    args = parser.parse_args()

    if args.clear_kill:
        clear_kill_switch()
        print("Kill switch cleared")
        return

    state = DaemonState(state_file=args.state_file)

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
        state.llm_tier = args.tier or state.llm_tier
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
        state.llm_tier = args.tier

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
