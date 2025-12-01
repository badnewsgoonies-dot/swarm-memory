#!/usr/bin/env python3
"""
swarm_daemon_hybrid_example.py - Example integration of hybrid LLM routing

This shows how to integrate llm_router.py into swarm_daemon.py
for intelligent tier-based LLM routing with cost optimization.

Key changes from original swarm_daemon.py:
1. Import and initialize LLMRouter
2. Modify call_llm() to use router
3. Add action type hints to prompts
4. Track costs and quality metrics
5. Export usage stats to memory

Usage (same as original):
    ./swarm_daemon_hybrid_example.py --objective "Your task" --max-iterations 20
    ./swarm_daemon_hybrid_example.py --status  # Shows cost summary
"""

import argparse
import json
import subprocess
import sys
import os
import time
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from pathlib import Path

# Import hybrid routing
from llm_router import LLMRouter

# Import governor for action enforcement
from governor import Governor

# Configuration
SCRIPT_DIR = Path(__file__).parent.resolve()
STATE_FILE = SCRIPT_DIR / "daemon_state_hybrid.json"
KILL_FILE = SCRIPT_DIR / "daemon.kill"
LOG_FILE = SCRIPT_DIR / "daemon_hybrid.log"
MEM_DB = SCRIPT_DIR / "mem-db.sh"
LLM_CONFIG = SCRIPT_DIR / "llm_config.yaml"

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


class HybridDaemonState:
    """Extended daemon state with LLM routing stats"""

    def __init__(self, state_file=None):
        self.state_file = Path(state_file) if state_file else STATE_FILE
        self.objective = ""
        self.iteration = 0
        self.history = []
        self.status = "idle"
        self.started_at = None
        self.last_action = None

        # Hybrid LLM stats
        self.total_cost = 0.0
        self.api_calls = 0
        self.local_calls = 0
        self.fallback_count = 0
        self.avg_quality = 0.0
        self.tier_distribution = {"local_fast": 0, "local_quality": 0, "api_fallback": 0}

    def save(self):
        """Save state to file"""
        data = {
            "objective": self.objective,
            "iteration": self.iteration,
            "history": self.history[-50:],
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_action": self.last_action,
            "total_cost": self.total_cost,
            "api_calls": self.api_calls,
            "local_calls": self.local_calls,
            "fallback_count": self.fallback_count,
            "avg_quality": self.avg_quality,
            "tier_distribution": self.tier_distribution
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
            self.history = data.get("history", [])
            self.status = data.get("status", "idle")
            self.started_at = datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None
            self.last_action = data.get("last_action")

            # Load hybrid stats
            self.total_cost = data.get("total_cost", 0.0)
            self.api_calls = data.get("api_calls", 0)
            self.local_calls = data.get("local_calls", 0)
            self.fallback_count = data.get("fallback_count", 0)
            self.avg_quality = data.get("avg_quality", 0.0)
            self.tier_distribution = data.get("tier_distribution", {
                "local_fast": 0, "local_quality": 0, "api_fallback": 0
            })

            return True
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            return False

    def update_stats(self, routing_decision, quality_result, usage_stats):
        """Update LLM routing statistics"""
        # Track cost
        total_stats_cost = sum(s.cost for s in usage_stats)
        self.total_cost += total_stats_cost

        # Track calls
        for stat in usage_stats:
            if stat.tier.value == "api_fallback":
                self.api_calls += 1
            else:
                self.local_calls += 1

            if stat.fallback_used:
                self.fallback_count += 1

            # Update tier distribution
            self.tier_distribution[stat.tier.value] = self.tier_distribution.get(stat.tier.value, 0) + 1

        # Update quality average
        if quality_result.confidence:
            total_quality = self.avg_quality * (self.iteration - 1)
            self.avg_quality = (total_quality + quality_result.confidence) / self.iteration


def call_llm_hybrid(
    router: LLMRouter,
    prompt: str,
    action_type: str = "unknown",
    context: Optional[Dict[str, Any]] = None,
    verbose: bool = False
) -> tuple:
    """
    Call LLM with hybrid routing.

    Returns: (response, routing_decision, quality_result, usage_stats)
    """

    if verbose:
        logger.info(f"LLM call for action: {action_type}")
        logger.info(f"Prompt length: {len(prompt)} chars")

    try:
        response, decision, quality, stats = router.route_with_quality_check(
            prompt,
            action_type=action_type,
            context=context or {}
        )

        # Log routing decision
        logger.info(f"Routed to: {decision.model.name} ({decision.tier.value})")
        logger.info(f"Quality: {quality.confidence:.2f} (threshold: 0.7)")

        # Log cost
        total_cost = sum(s.cost for s in stats)
        logger.info(f"Cost: ${total_cost:.6f}")

        # Log fallbacks if any
        if len(stats) > 1:
            logger.warning(f"Used {len(stats)} attempts (fallback triggered)")
            for i, stat in enumerate(stats):
                logger.info(f"  Attempt {i+1}: {stat.model_name} - "
                           f"${stat.cost:.6f} - {stat.latency_ms}ms")

        return response, decision, quality, stats

    except Exception as e:
        logger.error(f"LLM call failed: {e}")
        return None, None, None, []


def extract_action_type_from_history(history):
    """Infer next action type from recent history"""
    if not history:
        return "unknown"

    # Look at last action
    last = history[-1]
    last_action = last.get("action", "unknown")

    # Common patterns
    if last_action == "list_files":
        return "read_file"  # Usually read after list
    elif last_action == "read_file":
        return "edit_file"  # Usually edit after read
    elif last_action in ["edit_file", "consolidate"]:
        return "done"  # Might be done after edits

    return "unknown"


def build_prompt_with_action_hint(
    state,
    last_results=None
) -> tuple:
    """
    Build prompt and infer likely action type.

    Returns: (prompt, action_type_hint)
    """

    # Infer action type from history
    action_hint = extract_action_type_from_history(state.history)

    # Build standard prompt (simplified version)
    prompt = f"""OBJECTIVE: {state.objective}
ITERATION: {state.iteration}

RECENT HISTORY:
"""
    for h in state.history[-3:]:
        prompt += f"- {h.get('action')}: {h.get('result', {}).get('output', '')[:100]}...\n"

    if last_results:
        prompt += f"\nLAST RESULTS:\n{json.dumps(last_results, indent=2)}\n"

    prompt += """
AVAILABLE ACTIONS:
write_memory, read_file, edit_file, list_files, search_text, exec, spawn_daemon, done

Output ONE JSON action:
{"action": "...", ...}
"""

    return prompt, action_hint


def run_hybrid_daemon(state: HybridDaemonState, router: LLMRouter, verbose: bool = False):
    """Main daemon loop with hybrid LLM routing"""

    logger.info(f"Starting hybrid daemon with objective: {state.objective}")
    state.status = "running"
    state.started_at = datetime.now()
    state.save()

    last_results = None

    # Simplified loop for demonstration
    max_iterations = 10

    while state.iteration < max_iterations:
        state.iteration += 1

        # Build prompt with action hint
        prompt, action_hint = build_prompt_with_action_hint(state, last_results)

        logger.info(f"Iteration {state.iteration}: calling LLM (action hint: {action_hint})")

        # Call LLM with hybrid routing
        response, decision, quality, stats = call_llm_hybrid(
            router,
            prompt,
            action_type=action_hint,
            context={
                "iteration": state.iteration,
                "history_length": len(state.history)
            },
            verbose=verbose
        )

        if not response:
            logger.error("No response from LLM")
            state.status = "error"
            state.save()
            return

        # Update stats
        state.update_stats(decision, quality, stats)

        # Parse actions (simplified)
        # In real implementation, use parse_actions() from swarm_daemon.py
        try:
            # Try to extract JSON
            import re
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                action_data = json.loads(json_match.group())
                action = action_data.get("action", "unknown")

                logger.info(f"Parsed action: {action}")

                # Record in history
                state.history.append({
                    "action": action,
                    "data": action_data,
                    "result": {"success": True},
                    "tier": decision.tier.value,
                    "cost": sum(s.cost for s in stats),
                    "quality": quality.confidence
                })

                state.last_action = action
                state.save()

                # Check if done
                if action == "done":
                    logger.info(f"Objective completed")
                    state.status = "done"
                    state.save()

                    # Write cost summary to memory
                    cost_summary = f"""Hybrid daemon completed: {state.objective}
Iterations: {state.iteration}
Total cost: ${state.total_cost:.4f}
API calls: {state.api_calls}, Local calls: {state.local_calls}
Fallbacks: {state.fallback_count}
Avg quality: {state.avg_quality:.2f}
Tier distribution: {json.dumps(state.tier_distribution)}"""

                    subprocess.run([
                        str(MEM_DB), "write",
                        "t=a",
                        "topic=daemon-hybrid",
                        f"text={cost_summary}",
                        "choice=completed"
                    ])

                    return

            else:
                logger.warning("No JSON found in response")

        except Exception as e:
            logger.error(f"Failed to parse action: {e}")

        time.sleep(1)

    logger.info("Max iterations reached")
    state.status = "max_iterations"
    state.save()


def show_status(state: HybridDaemonState):
    """Show daemon status with hybrid stats"""
    if not state.load():
        print("No saved state")
        return

    print(f"Status: {state.status}")
    print(f"Objective: {state.objective}")
    print(f"Iteration: {state.iteration}")
    print(f"Last action: {state.last_action}")
    print()
    print("Hybrid LLM Stats:")
    print(f"  Total cost: ${state.total_cost:.4f}")
    print(f"  API calls: {state.api_calls}")
    print(f"  Local calls: {state.local_calls}")
    print(f"  Fallback count: {state.fallback_count}")
    print(f"  Avg quality: {state.avg_quality:.2f}")
    print()
    print("Tier Distribution:")
    total = sum(state.tier_distribution.values())
    for tier, count in state.tier_distribution.items():
        pct = (count / total * 100) if total > 0 else 0
        print(f"  {tier:15s}: {count:3d} ({pct:5.1f}%)")

    # Calculate savings
    # Assume pure API would be $0.003 per iteration (Claude Sonnet)
    api_only_cost = state.iteration * 0.003
    savings = api_only_cost - state.total_cost
    savings_pct = (savings / api_only_cost * 100) if api_only_cost > 0 else 0

    print()
    print(f"Cost Savings:")
    print(f"  Pure API (estimated): ${api_only_cost:.4f}")
    print(f"  Hybrid (actual): ${state.total_cost:.4f}")
    print(f"  Savings: ${savings:.4f} ({savings_pct:.1f}%)")


def main():
    parser = argparse.ArgumentParser(description='Hybrid LLM daemon (example)')
    parser.add_argument('--objective', '-o', help='Objective to accomplish')
    parser.add_argument('--status', action='store_true', help='Show daemon status')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose logging')
    parser.add_argument('--llm-config', default=str(LLM_CONFIG), help='LLM config file')

    args = parser.parse_args()

    state = HybridDaemonState()

    if args.status:
        show_status(state)
        return

    if not args.objective:
        parser.print_help()
        sys.exit(1)

    state.objective = args.objective

    # Initialize LLM router
    logger.info(f"Initializing LLM router with config: {args.llm_config}")
    router = LLMRouter(config_path=args.llm_config)

    try:
        run_hybrid_daemon(state, router, verbose=args.verbose)

        # Print final stats
        print()
        print("=" * 80)
        print("HYBRID DAEMON COMPLETED")
        print("=" * 80)
        show_status(state)

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
