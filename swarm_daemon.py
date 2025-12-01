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
from datetime import datetime, timedelta
from pathlib import Path

# Configuration
SCRIPT_DIR = Path(__file__).parent.resolve()
STATE_FILE = SCRIPT_DIR / "daemon_state.json"
KILL_FILE = SCRIPT_DIR / "daemon.kill"
LOG_FILE = SCRIPT_DIR / "daemon.log"
MEM_DB = SCRIPT_DIR / "mem-db.sh"

# Rate limiting
MAX_ITERATIONS_PER_HOUR = 100
ITERATION_WINDOW = timedelta(hours=1)

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

    def save(self):
        """Save state to file"""
        data = {
            "objective": self.objective,
            "iteration": self.iteration,
            "iteration_times": [t.isoformat() for t in self.iteration_times],
            "history": self.history[-50:],  # Keep last 50 actions
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_action": self.last_action
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


def call_llm(prompt, verbose=False):
    """Call Claude CLI with prompt"""
    if verbose:
        logger.info(f"LLM prompt ({len(prompt)} chars):\n{prompt[:500]}...")
    else:
        logger.debug(f"LLM prompt: {prompt[:200]}...")
    try:
        result = subprocess.run(
            ['claude', '-p', prompt],
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


def execute_action(action_data):
    """Execute a single action from JSON"""
    action = action_data.get("action", "unknown")
    logger.info(f"Executing action: {action}")

    if action == "write_memory":
        # Write to memory
        t = action_data.get("type", "n")
        topic = action_data.get("topic", "daemon")
        text = action_data.get("text", "")
        choice = action_data.get("choice", "")

        args = [f"t={t}", f"topic={topic}", f"text={text}"]
        if choice:
            args.append(f"choice={choice}")

        output, success = call_mem_db("write", *args)
        return {"success": success, "output": output[:500]}

    elif action == "mem_search":
        # Search memory
        query = action_data.get("query", "")
        limit = action_data.get("limit", 10)

        # Parse query into args
        args = query.split() + [f"limit={limit}"]
        output, success = call_mem_db("render", *args)
        return {"success": success, "output": output[:2000]}

    elif action == "consolidate":
        # Consolidate memory
        target = action_data.get("id", "recent")
        if target == "recent":
            output, success = call_mem_db("consolidate", "--recent")
        elif target == "all":
            output, success = call_mem_db("consolidate", "--all")
        else:
            output, success = call_mem_db("consolidate", "--id", str(target))
        return {"success": success, "output": output[:1000]}

    elif action == "sleep":
        # Pause execution
        seconds = min(action_data.get("seconds", 5), 300)  # Max 5 minutes
        logger.info(f"Sleeping for {seconds} seconds")
        time.sleep(seconds)
        return {"success": True, "output": f"Slept {seconds}s"}

    elif action == "done":
        # Mark objective complete
        summary = action_data.get("summary", "Objective completed")
        logger.info(f"Objective done: {summary}")
        return {"success": True, "done": True, "summary": summary}

    else:
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


def build_prompt(state, last_results=None):
    """Build prompt for LLM with context"""

    # Get recent memory context
    context_output, _ = call_mem_db("render", "limit=10")

    prompt = f"""You are an autonomous daemon working on an objective.

OBJECTIVE: {state.objective}

ITERATION: {state.iteration}

RECENT MEMORY:
{context_output}

AVAILABLE ACTIONS (respond with JSON):
- {{"action": "write_memory", "type": "f|d|q|a|n", "topic": "...", "text": "..."}}
- {{"action": "mem_search", "query": "t=d topic=...", "limit": 5}}
- {{"action": "consolidate", "id": "recent|all|<number>"}}
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


def run_daemon(state, verbose=False):
    """Main daemon loop"""
    logger.info(f"Starting daemon with objective: {state.objective}")
    state.status = "running"
    state.started_at = datetime.now()
    state.save()

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
        prompt = build_prompt(state, last_results)
        logger.info(f"Iteration {state.iteration}: calling LLM")

        response = call_llm(prompt, verbose=verbose)
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

        # Execute first action
        action_data = actions[0]
        logger.info(f"Executing: {json.dumps(action_data)}")

        result = execute_action(action_data)
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
    else:
        # New objective
        objective = args.objective
        if args.objective_file:
            objective = Path(args.objective_file).read_text().strip()

        if not objective:
            parser.print_help()
            sys.exit(1)

        state.objective = objective

    # Set max iterations
    global MAX_ITERATIONS_PER_HOUR
    MAX_ITERATIONS_PER_HOUR = args.max_iterations

    try:
        run_daemon(state, verbose=args.verbose)
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
