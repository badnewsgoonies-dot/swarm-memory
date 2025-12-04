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
import re
from typing import List, Tuple, Optional, Dict, Any
from datetime import datetime, timedelta
from pathlib import Path

# Import governor for action enforcement
from governor import Governor


# =============================================================================
# ERROR SIGNATURE EXTRACTION
# =============================================================================

def extract_error_signature(audit_log: str) -> str:
    """
    Extract a canonical error signature from audit log output.

    Returns a signature string in format: <category>:<details>

    Categories:
    - ts:TS<code>:<first line of message> - TypeScript compiler errors
    - jest:<test name> - Jest/Vitest test failures
    - runtime:<ErrorType>:<message> - Runtime errors (TypeError, etc.)
    - human:<metric> - Human-provided metrics like plan_quality=4
    - unknown:<first line truncated to 80 chars> - Fallback
    """
    if not audit_log or not audit_log.strip():
        return "unknown:empty_audit_log"

    log = audit_log.strip()

    # 1. TypeScript errors: TS(\d+): <message>
    # Match patterns like "error TS2304: Cannot find name 'foo'"
    ts_match = re.search(r'(?:error\s+)?(TS\d+):\s*(.+?)(?:\n|$)', log, re.IGNORECASE)
    if ts_match:
        ts_code = ts_match.group(1).upper()
        msg = ts_match.group(2).strip()[:60]  # First 60 chars of message
        return f"ts:{ts_code}:{msg}"

    # 2. Jest/Vitest test failures: ● <test name> or FAIL <test name>
    # Match "● TestSuite > test name" or "FAIL src/foo.test.ts"
    jest_match = re.search(r'(?:●|FAIL)\s+(.+?)(?:\n|$)', log)
    if jest_match:
        test_name = jest_match.group(1).strip()[:80]
        return f"jest:{test_name}"

    # Also check for "✕" (vitest failure marker)
    vitest_match = re.search(r'[✕✗]\s+(.+?)(?:\n|$)', log)
    if vitest_match:
        test_name = vitest_match.group(1).strip()[:80]
        return f"jest:{test_name}"

    # 3. Runtime errors: TypeError, ReferenceError, etc.
    # Match "TypeError: Cannot read property 'x' of undefined"
    runtime_match = re.search(
        r'(TypeError|ReferenceError|SyntaxError|RangeError|Error|Exception):\s*(.+?)(?:\n|$)',
        log, re.IGNORECASE
    )
    if runtime_match:
        error_type = runtime_match.group(1)
        msg = runtime_match.group(2).strip()[:60]
        return f"runtime:{error_type}:{msg}"

    # 4. Human metrics: plan_quality=N;exec_success=false etc.
    # Match patterns like "plan_quality=4" or "exec_success=false"
    human_match = re.search(r'((?:plan_quality|exec_success|audit_score)[=:][^;\s]+(?:;[^;\s]+)*)', log, re.IGNORECASE)
    if human_match:
        metric = human_match.group(1).strip()[:80]
        return f"human:{metric}"

    # 5. ESLint/linting errors
    lint_match = re.search(r'(\d+:\d+)\s+(error|warning)\s+(.+?)\s+(\S+)$', log, re.MULTILINE)
    if lint_match:
        rule = lint_match.group(4)
        msg = lint_match.group(3).strip()[:40]
        return f"lint:{rule}:{msg}"

    # 6. Build/compilation errors (generic)
    build_match = re.search(r'(?:error|failed|failure)[:\s]+(.+?)(?:\n|$)', log, re.IGNORECASE)
    if build_match:
        msg = build_match.group(1).strip()[:60]
        return f"build:{msg}"

    # 7. Fallback: first non-empty line truncated to 80 chars
    for line in log.split('\n'):
        line = line.strip()
        if line and not line.startswith('#'):
            return f"unknown:{line[:80]}"

    return "unknown:no_parseable_content"


# =============================================================================
# PHASE GLYPH HELPERS
# =============================================================================

def write_phase_glyph(
    task_id: str,
    topic: str,
    from_phase: str,
    to_phase: str,
    round_num: int,
    error_sig: str,
    text: str,
    mem_db_path: Path
) -> Tuple[str, bool]:
    """
    Write a PHASE glyph to memory tracking orchestration phase transition.

    Args:
        task_id: The TODO/GOAL id being orchestrated
        topic: Topic for the glyph (usually matches the task's topic)
        from_phase: Previous phase (IMPLEMENT, AUDIT, FIX)
        to_phase: New phase (AUDIT, FIX, DONE, BLOCKED)
        round_num: Current orchestration round
        error_sig: Error signature from audit (or "none")
        text: Human-readable description of the transition
        mem_db_path: Path to mem-db.sh script

    Returns:
        Tuple of (output, success)
    """
    choice = f"{from_phase}->{to_phase}"
    links_data = {
        "from": from_phase,
        "to": to_phase,
        "round": round_num,
        "error": error_sig
    }
    links_json = json.dumps(links_data)

    try:
        result = subprocess.run(
            [
                str(mem_db_path), "write",
                "t=P",
                f"topic={topic}",
                f"task={task_id}",
                f"choice={choice}",
                f"text={text}",
                f"links={links_json}"
            ],
            capture_output=True,
            text=True,
            timeout=60
        )
        return result.stdout.strip(), result.returncode == 0
    except Exception as e:
        return str(e), False


def query_previous_error_signature(task_id: str, mem_db_path: Path) -> Optional[str]:
    """
    Query the most recent PHASE glyph for a task where error is not "none".

    Returns the error signature string, or None if not found.
    """
    try:
        result = subprocess.run(
            [
                str(mem_db_path), "query",
                "t=P",
                f"task_id={task_id}",
                "recent=1h",
                "limit=10",
                "--json"
            ],
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0 or not result.stdout.strip():
            return None

        # Parse JSONL output - each line is an array
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            try:
                # Query returns array: [type, topic, text, choice, rationale, ts, session, source, ...]
                # We need to look at the links field which contains the error
                row = json.loads(line)
                # The links field is at index 15 in the query output
                # But actually we need to check the JSON structure
                # Let's try parsing as a dict if it's returned differently
                if isinstance(row, dict):
                    links_str = row.get('links', '')
                elif isinstance(row, list) and len(row) > 15:
                    links_str = row[15]  # links field position
                else:
                    continue

                if links_str:
                    links_data = json.loads(links_str) if isinstance(links_str, str) else links_str
                    error = links_data.get('error', 'none')
                    if error and error != 'none':
                        return error
            except (json.JSONDecodeError, IndexError, TypeError):
                continue

        return None
    except Exception as e:
        logging.getLogger(__name__).warning(f"Failed to query previous error: {e}")
        return None


def write_blocked_result(
    task_id: str,
    topic: str,
    blocked_reason: str,
    error_sig: str,
    round_num: int,
    mem_db_path: Path
) -> None:
    """
    Write RESULT (failure) and LESSON glyphs when task is blocked.
    """
    logger = logging.getLogger(__name__)

    # Write RESULT glyph with failure and orchestration metrics
    try:
        # Include rich orchestration metrics for analysis
        metric = f"orch_rounds={round_num};orch_phase=blocked;orch_blocked_reason={blocked_reason};error_sig={error_sig}"
        subprocess.run(
            [
                str(mem_db_path), "write",
                "t=R",
                f"topic={topic}",
                f"task={task_id}",
                "choice=failure",
                f"text=Task blocked after {round_num} rounds: {blocked_reason}",
                f"metric={metric}"
            ],
            capture_output=True,
            timeout=60
        )
        logger.info(f"Wrote RESULT glyph: orch_rounds={round_num}, blocked_reason={blocked_reason}")
    except Exception as e:
        logger.error(f"Failed to write RESULT glyph: {e}")

    # Write LESSON glyph
    try:
        if blocked_reason == "repeated_error_signature":
            lesson_text = (
                f"Task {task_id} stuck on same error ({error_sig}) for 2+ rounds. "
                "Likely needs human intervention or different approach. "
                "Consider: 1) Manual debugging, 2) Simplifying the task, 3) Checking dependencies."
            )
        else:  # max_rounds
            lesson_text = (
                f"Task {task_id} hit max rounds ({round_num}) with different errors each time. "
                "May need task breakdown or architectural review. "
                "Pattern suggests systemic issues rather than simple bugs."
            )

        subprocess.run(
            [
                str(mem_db_path), "write",
                "t=L",
                f"topic={topic}",
                f"task={task_id}",
                f"text={lesson_text}"
            ],
            capture_output=True,
            timeout=60
        )
        logger.info(f"Wrote LESSON glyph for blocked task")
    except Exception as e:
        logger.error(f"Failed to write LESSON glyph: {e}")


def write_success_result(
    task_id: str,
    topic: str,
    round_num: int,
    mem_db_path: Path
) -> None:
    """
    Write RESULT (success) glyph when orchestration completes successfully.
    """
    logger = logging.getLogger(__name__)

    try:
        # Include orchestration metrics for analysis
        metric = f"orch_rounds={round_num};orch_phase=done;orch_blocked_reason=none"
        subprocess.run(
            [
                str(mem_db_path), "write",
                "t=R",
                f"topic={topic}",
                f"task={task_id}",
                "choice=success",
                f"text=Orchestration completed successfully in {round_num} round(s)",
                f"metric={metric}"
            ],
            capture_output=True,
            timeout=60
        )
        logger.info(f"Wrote RESULT glyph: orch_rounds={round_num}, success")
    except Exception as e:
        logger.error(f"Failed to write success RESULT glyph: {e}")


# =============================================================================
# ORCHESTRATION STATE MACHINE
# =============================================================================

class OrchestrationState:
    """
    Manages orchestration state for IMPLEMENT -> AUDIT -> FIX loops.

    Tracks:
    - Current phase (implement, audit, fix, done, blocked)
    - Round count (increments on each FIX)
    - Error signatures for anti-loop detection
    - Task metadata
    """

    MAX_ROUNDS = 5

    def __init__(
        self,
        task_id: str,
        topic: str,
        objective: str,
        mem_db_path: Path
    ):
        self.task_id = task_id
        self.topic = topic
        self.objective = objective
        self.mem_db_path = mem_db_path
        self.orch_id = hashlib.md5(objective.encode()).hexdigest()[:8]

        # State
        self.current_phase = "implement"
        self.current_round = 1
        self.last_error_sig: Optional[str] = None
        self.last_audit_log: Optional[str] = None
        self.is_blocked = False
        self.blocked_reason: Optional[str] = None

        self.logger = logging.getLogger(__name__)

    def load_from_memory(self) -> None:
        """Load current orchestration state from PHASE glyphs in memory."""
        try:
            result = subprocess.run(
                [
                    str(self.mem_db_path), "query",
                    "t=P",
                    f"task_id={self.task_id}",
                    "recent=2h",
                    "limit=20",
                    "--json"
                ],
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode != 0 or not result.stdout.strip():
                self.logger.info(f"No existing PHASE glyphs for task {self.task_id}")
                return

            # Parse entries to determine current phase and round
            entries = []
            for line in result.stdout.strip().split('\n'):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    entries.append(row)
                except json.JSONDecodeError:
                    continue

            if not entries:
                return

            # Process entries (newest first) to determine state
            for row in entries:
                try:
                    if isinstance(row, list) and len(row) > 15:
                        choice = row[3]  # anchor_choice
                        links_str = row[15]  # links field
                    elif isinstance(row, dict):
                        choice = row.get('anchor_choice', '')
                        links_str = row.get('links', '')
                    else:
                        continue

                    # Parse links JSON
                    if links_str:
                        links = json.loads(links_str) if isinstance(links_str, str) else links_str
                        to_phase = links.get('to', '').lower()
                        round_num = links.get('round', 1)
                        error = links.get('error', 'none')

                        if to_phase == 'blocked':
                            self.is_blocked = True
                            self.blocked_reason = "previous_block"
                            self.logger.info(f"Task {self.task_id} is already BLOCKED")
                            return

                        if to_phase == 'done':
                            self.current_phase = 'done'
                            self.logger.info(f"Task {self.task_id} is already DONE")
                            return

                        # Use most recent state
                        self.current_phase = to_phase if to_phase else 'implement'
                        self.current_round = round_num
                        if error and error != 'none':
                            self.last_error_sig = error
                        break

                except (json.JSONDecodeError, IndexError, TypeError, KeyError) as e:
                    self.logger.debug(f"Failed to parse PHASE entry: {e}")
                    continue

            self.logger.info(
                f"Loaded orchestration state: phase={self.current_phase}, "
                f"round={self.current_round}, last_error={self.last_error_sig}"
            )

        except Exception as e:
            self.logger.warning(f"Failed to load orchestration state: {e}")

    def transition_implement_to_audit(self) -> bool:
        """Transition from IMPLEMENT to AUDIT phase."""
        if self.is_blocked:
            return False

        output, success = write_phase_glyph(
            task_id=self.task_id,
            topic=self.topic,
            from_phase="IMPLEMENT",
            to_phase="AUDIT",
            round_num=self.current_round,
            error_sig="none",
            text="Implementation complete; ready for audit.",
            mem_db_path=self.mem_db_path
        )

        if success:
            self.current_phase = "audit"
            self.logger.info(f"Phase transition: IMPLEMENT -> AUDIT (round {self.current_round})")
        return success

    def transition_audit_pass(self) -> bool:
        """Transition from AUDIT to DONE (audit passed)."""
        if self.is_blocked:
            return False

        output, success = write_phase_glyph(
            task_id=self.task_id,
            topic=self.topic,
            from_phase="AUDIT",
            to_phase="DONE",
            round_num=self.current_round,
            error_sig="none",
            text="All tests passed; task complete.",
            mem_db_path=self.mem_db_path
        )

        if success:
            self.current_phase = "done"
            self.logger.info(f"Phase transition: AUDIT -> DONE (round {self.current_round})")
            # Write success RESULT with orchestration metrics
            write_success_result(
                task_id=self.task_id,
                topic=self.topic,
                round_num=self.current_round,
                mem_db_path=self.mem_db_path
            )
        return success

    def transition_audit_fail(self, audit_log: str) -> Tuple[bool, str]:
        """
        Process audit failure and decide whether to FIX or BLOCK.

        Returns:
            Tuple of (can_continue, reason)
            - (True, "fix") - Can proceed to FIX phase
            - (False, "repeated_error_signature") - Same error twice, BLOCKED
            - (False, "max_rounds") - Hit max rounds, BLOCKED
        """
        if self.is_blocked:
            return False, "already_blocked"

        # Extract error signature from audit log
        new_error_sig = extract_error_signature(audit_log)
        self.last_audit_log = audit_log
        self.logger.info(f"Extracted error signature: {new_error_sig}")

        # Check for repeated error signature (anti-loop)
        prev_error_sig = query_previous_error_signature(self.task_id, self.mem_db_path)
        self.logger.debug(f"Previous error signature: {prev_error_sig}")

        if prev_error_sig and new_error_sig == prev_error_sig:
            # Same error twice - BLOCK
            self.logger.warning(
                f"Repeated error signature detected: {new_error_sig}. "
                f"Blocking task {self.task_id}."
            )
            self._block_task("repeated_error_signature", new_error_sig)
            return False, "repeated_error_signature"

        # Check max rounds
        if self.current_round >= self.MAX_ROUNDS:
            self.logger.warning(
                f"Max rounds ({self.MAX_ROUNDS}) reached for task {self.task_id}. "
                f"Blocking task."
            )
            self._block_task("max_rounds", new_error_sig)
            return False, "max_rounds"

        # Can proceed to FIX
        text_snippet = new_error_sig[:50]
        output, success = write_phase_glyph(
            task_id=self.task_id,
            topic=self.topic,
            from_phase="AUDIT",
            to_phase="FIX",
            round_num=self.current_round,
            error_sig=new_error_sig,
            text=f"Audit failed ({text_snippet}); preparing fix.",
            mem_db_path=self.mem_db_path
        )

        if success:
            self.current_phase = "fix"
            self.last_error_sig = new_error_sig
            self.logger.info(
                f"Phase transition: AUDIT -> FIX (round {self.current_round}, "
                f"error={new_error_sig[:40]}...)"
            )
        return True, "fix"

    def transition_fix_to_audit(self) -> bool:
        """Transition from FIX to AUDIT phase (increment round)."""
        if self.is_blocked:
            return False

        self.current_round += 1

        output, success = write_phase_glyph(
            task_id=self.task_id,
            topic=self.topic,
            from_phase="FIX",
            to_phase="AUDIT",
            round_num=self.current_round,
            error_sig="none",
            text=f"Fix applied; re-auditing (round {self.current_round}).",
            mem_db_path=self.mem_db_path
        )

        if success:
            self.current_phase = "audit"
            self.logger.info(f"Phase transition: FIX -> AUDIT (round {self.current_round})")
        return success

    def _block_task(self, reason: str, error_sig: str) -> None:
        """Mark task as BLOCKED with appropriate glyphs."""
        self.is_blocked = True
        self.blocked_reason = reason

        # Write PHASE glyph for BLOCKED transition
        write_phase_glyph(
            task_id=self.task_id,
            topic=self.topic,
            from_phase="AUDIT",
            to_phase="BLOCKED",
            round_num=self.current_round,
            error_sig=error_sig,
            text=f"Task blocked: {reason} ({error_sig[:40]})",
            mem_db_path=self.mem_db_path
        )

        # Write RESULT and LESSON glyphs
        write_blocked_result(
            task_id=self.task_id,
            topic=self.topic,
            blocked_reason=reason,
            error_sig=error_sig,
            round_num=self.current_round,
            mem_db_path=self.mem_db_path
        )

        self.current_phase = "blocked"

    def get_phase_instructions(self) -> str:
        """Get LLM instructions for current phase."""
        if self.is_blocked:
            return f"""
TASK IS BLOCKED
===============
Reason: {self.blocked_reason}
Last Error: {self.last_error_sig or 'unknown'}
Round: {self.current_round}

This task cannot proceed automatically. Return a done action explaining the blockage.
"""

        if self.current_phase == "done":
            return """
TASK IS COMPLETE
================
Return a done action with a summary of what was accomplished.
"""

        if self.current_phase == "implement":
            return f"""
PHASE: IMPLEMENT (Round {self.current_round})
===================
1. Spawn implementation sub-daemon with wait=true
2. After it completes, write memory glyph with topic=orch_{self.orch_id} choice=implement_done
3. Then proceed to AUDIT phase
"""

        if self.current_phase == "audit":
            return f"""
PHASE: AUDIT (Round {self.current_round})
================
1. Spawn audit sub-daemon with wait=true (run tests, type-check, lint)
2. Collect the output for error analysis
3. If audit passes: transition to DONE
4. If audit fails: the error will be analyzed for anti-loop detection
5. Write memory glyph with topic=orch_{self.orch_id} choice=audit:pass or audit:fail
"""

        if self.current_phase == "fix":
            return f"""
PHASE: FIX (Round {self.current_round})
==============
Last Error: {self.last_error_sig or 'unknown'}

1. Spawn fix sub-daemon with wait=true targeting the specific error
2. After fix completes, write memory glyph with topic=orch_{self.orch_id} choice=fix_done
3. Then proceed to re-AUDIT

WARNING: If the same error appears again, task will be BLOCKED.
"""

        return "Unknown phase - check orchestration state."

    def to_context_string(self) -> str:
        """Generate context string for LLM prompt."""
        return f"""
ORCHESTRATION MODE ACTIVE
=========================
Task ID: {self.task_id}
Orchestration ID: orch_{self.orch_id}
Topic: {self.topic}
Objective: {self.objective}
Current Phase: {self.current_phase.upper()}
Current Round: {self.current_round}/{self.MAX_ROUNDS}
Last Error Signature: {self.last_error_sig or 'none'}
Status: {'BLOCKED - ' + (self.blocked_reason or '') if self.is_blocked else 'active'}

{self.get_phase_instructions()}
"""


# =============================================================================
# ORCHESTRATOR SYSTEM PROMPT
# =============================================================================

ORCHESTRATOR_SYSTEM_PROMPT = """
You are the ORCHESTRATOR for a codebase.

You do NOT directly edit code; instead you design and coordinate daemon runs
in three phases:

  IMPLEMENT -> AUDIT -> FIX -> AUDIT -> ... -> DONE or BLOCKED

You will see:
- A high-level objective with an ORCHESTRATE: prefix
- PHASE glyphs that describe previous rounds, for example:
  [PHASE][task=clockcrypts-demo-001][from=IMPLEMENT][to=AUDIT][round=1][error=build:missing-dependency]
- ATTEMPT / RESULT / LESSON glyphs from prior daemons
- Logs and error output from previous actions (builds, tests, etc.)

CRITICAL CONTEXT FOR GAME BUILDING
----------------------------------
In this repository there is a game design manual:

  docs/game_manual_demo.md

This manual defines:
- The intended game loop and player experience
- The minimum scope for a shippable demo
- A "Definition of Demo Complete" section that acts as the acceptance criteria

Your job is to treat that manual as the SINGLE SOURCE OF TRUTH for:
- What features should exist
- What counts as done
- Which features are "nice to have" vs required for the demo

GENERAL RULES
-------------
1. You NEVER directly edit files. You only propose JSON actions like:
   {"action":"read_file", ...}, {"action":"apply_patch", ...}, {"action":"exec", ...},
   {"action":"spawn_daemon", ...}, {"action":"orch_transition", ...}.
2. You must keep a clear distinction between phases:
   - IMPLEMENT: Create or modify code/assets to move towards the goal.
   - AUDIT: Run builds/tests and inspect results; no major new features.
   - FIX: Minimal, targeted changes to resolve specific errors found in AUDIT.
3. You must respect anti-loop rules:
   - The system tracks error signatures (e.g., ts:TS2304:Cannot find name 'foo').
   - If you see the same error signature more than once in a round, do NOT keep retrying the same fix.
   - If you cannot make progress after the configured max rounds, you should transition to BLOCKED.

PHASE-BY-PHASE BEHAVIOR
-----------------------

PHASE = IMPLEMENT
-----------------
Main goals:
- Ensure the project structure exists (Godot 4 + C# or other engine/framework).
- Implement the core features described in the manual that are required for the demo:
  - Title screen -> Start Run -> Game flow
  - Player controller and basic combat
  - Enemy behaviors
  - Rooms and progression
  - Boss and Run Summary screen

In IMPLEMENT phase, you should:

1. ENSURE MANUAL IS LOADED
   - If you haven't seen docs/game_manual_demo.md content in this orchestration round,
     your FIRST action MUST be to read it:

     {"action":"read_file","path":"docs/game_manual_demo.md"}

   - Summarize and keep in mind especially:
     - Core Game Loop
     - Player section
     - Enemy section
     - Rooms & Layout
     - Definition of Demo Complete

2. CHECK PROJECT SKELETON
   - If the project is missing or broken (no project.godot, etc.), orchestrate its creation.
   - Use spawn_daemon or exec to run:
     - Engine-specific commands if needed (for C# build / project).
   - Ensure a clear folder structure exists for scenes and scripts.

3. IMPLEMENT FEATURES IN SMALL, SAFE STEPS
   - Prefer sequences like:
     - read_file (existing code)
     - edit_file (incremental change)
     - exec ("build command")
   - Use spawn_daemon with wait=true for multi-step editing work, but avoid spawning too many levels deep.

4. KEEP MANUAL AS CHECKLIST
   - When adding features, explicitly align them with sections of docs/game_manual_demo.md.
   - Example: "Implement Player controller per section 3.2: HP, movement, shooting".

5. PREPARE FOR AUDIT
   - Once the major demo features appear to be implemented, ensure there is at least ONE clear way
     to run the demo (e.g., by running the project or a test build).
   - Then use an orch_transition to move to AUDIT:

     {"action":"orch_transition","task_id":"<task_id>","transition":"implement_done","audit_log":"...what was implemented..."}

PHASE = AUDIT
-------------
Main goals:
- Verify the build runs without errors.
- Check that the demo requirements are satisfied according to the manual's "Definition of Demo Complete".

In AUDIT phase, you should:

1. RUN BUILDS/TESTS
   - Use actions like:
     {"action":"exec","cmd":"<build or run command>","cwd":"<project_root>"}
   - Capture build output and summarize failures.

2. CHECK AGAINST MANUAL
   - Cross-check the observed behavior/logs against the "Definition of Demo Complete".
   - If possible, instrument or log enough to know:
     - Can the game start from Title -> Start Run?
     - Does the player spawn and move?
     - Are rooms, enemies, boss, and summary reachable?

3. DECIDE AUDIT RESULT
   - If the demo clearly satisfies the manual's Demo Complete criteria:
     - Use orch_transition with "audit_pass":

       {"action":"orch_transition","task_id":"<task_id>","transition":"audit_pass","audit_log":"...why this meets Demo Complete..."}

   - If there are missing features or errors:
     - Use orch_transition with "audit_fail" and an audit_log that clearly lists:
       - Build/runtime errors (with error signatures if possible).
       - Missing features relative to the manual.

PHASE = FIX
-----------
Main goals:
- Address the specific shortcomings found in the last AUDIT.
- Avoid guessing; fix concretely.

In FIX phase, you should:

1. REVIEW LAST AUDIT_LOG
   - Identify the main problems:
     - Build errors
     - Crashes
     - Missing core features from Demo Complete list

2. PLAN MINIMAL FIXES
   - Prefer targeted patches:
     - Single file changes
     - Small adjustments
   - Do NOT redesign the entire architecture in FIX. That belongs in IMPLEMENT.

3. APPLY FIXES VIA ACTIONS
   - Use read_file / edit_file / exec / spawn_daemon as needed.
   - After implementing likely fixes, transition back to AUDIT:

     {"action":"orch_transition","task_id":"<task_id>","transition":"fix_done","audit_log":"...what was fixed and why..."}

ANTI-LOOP & BLOCKING
--------------------
- Each AUDIT failure should be associated with an error signature (build errors, missing feature label, etc.).
- If you see the same error signature repeatedly and cannot resolve it in the current round:
  - Move towards BLOCKED state by using:
    {"action":"orch_transition","task_id":"<task_id>","transition":"blocked","audit_log":"...why this is blocked, which Demo Complete criteria are unmet, and what human input is required..."}

- When BLOCKED, ensure the audit_log clearly states:
  - Which manual sections are still not satisfied.
  - Exact errors and what additional information or permissions a human needs to provide.

OUTPUT FORMAT
-------------
For EVERY step, you must output exactly ONE top-level JSON object describing the next action, e.g.:

{"action":"read_file","path":"docs/game_manual_demo.md"}

or:

{"action":"exec","cmd":"pnpm build","cwd":"/path/to/project"}

or:

{"action":"orch_transition","task_id":"clockcrypts-demo-001","transition":"audit_pass","audit_log":"...details..."}

Do NOT include commentary outside the JSON except where specifically allowed by the daemon.
"""

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
        self.rate_limit_backoff_count = 0  # Track consecutive rate limit hits for exponential backoff

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
            "llm_tier": self.llm_tier,
            "rate_limit_backoff_count": self.rate_limit_backoff_count
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
            self.rate_limit_backoff_count = data.get("rate_limit_backoff_count", 0)
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
        # Reset backoff on successful iteration
        self.rate_limit_backoff_count = 0


def check_kill_switch():
    """Check if kill switch is active"""
    return KILL_FILE.exists()


def clear_kill_switch():
    """Clear the kill switch"""
    if KILL_FILE.exists():
        KILL_FILE.unlink()


def call_llm(prompt, verbose=False, log_full_response=False, provider="claude", model=None, tier="auto"):
    """Call LLM with prompt (Claude CLI, Codex, or Hybrid local/API)

    Providers:
        - claude: Claude CLI (default)
        - codex: OpenAI Codex CLI
        - hybrid: Tiered Ollama + OpenAI (fast/code/smart/auto)
        - ollama: Direct Ollama call
        - openai: Direct OpenAI API call

    Args:
        log_full_response: If True, logs complete LLM response including reasoning text
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
                if log_full_response:
                    logger.info(f"LLM FULL response ({response.tier}, {response.latency_ms}ms):\n{response.text}")
                elif verbose:
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
        if log_full_response:
            logger.info(f"LLM FULL response ({len(response)} chars):\n{response}")
        elif verbose:
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


def execute_action(action_data, repo_root: Path, unrestricted: bool, state=None):
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
            "--state-file", str(sub_state_file),
        ]
        # Inherit LLM provider from parent state if available
        if state is not None:
            cmd.extend(["--llm", state.llm_provider])
            if state.llm_model:
                cmd.extend(["--llm-model", state.llm_model])
            if state.llm_tier and state.llm_tier != "auto":
                cmd.extend(["--tier", state.llm_tier])
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

    if action == "orch_transition":
        # Handle orchestration phase transitions with anti-loop detection
        task_id = action_data.get("task_id", "")
        topic = action_data.get("topic", "orchestration")
        objective = action_data.get("objective", "")
        transition = action_data.get("transition", "")  # e.g., "implement_done", "audit_pass", "audit_fail", "fix_done"
        audit_log = action_data.get("audit_log", "")  # Required for audit_fail transition

        if not task_id or not transition:
            return {"success": False, "error": "task_id and transition required"}

        # Create orchestration state
        orch = OrchestrationState(
            task_id=task_id,
            topic=topic,
            objective=objective or f"Orchestrated task {task_id}",
            mem_db_path=MEM_DB
        )
        orch.load_from_memory()

        # Handle transitions
        if transition == "implement_done":
            success = orch.transition_implement_to_audit()
            return {
                "success": success,
                "phase": orch.current_phase,
                "round": orch.current_round,
                "output": f"Transitioned to AUDIT phase (round {orch.current_round})"
            }

        elif transition == "audit_pass":
            success = orch.transition_audit_pass()
            return {
                "success": success,
                "phase": orch.current_phase,
                "round": orch.current_round,
                "output": "Audit passed, task complete!",
                "done": True
            }

        elif transition == "audit_fail":
            if not audit_log:
                return {"success": False, "error": "audit_log required for audit_fail transition"}

            can_continue, reason = orch.transition_audit_fail(audit_log)

            if not can_continue:
                # Task is blocked
                return {
                    "success": True,
                    "phase": "blocked",
                    "round": orch.current_round,
                    "blocked": True,
                    "blocked_reason": reason,
                    "error_signature": orch.last_error_sig,
                    "output": f"Task blocked: {reason}. Error: {orch.last_error_sig}"
                }
            else:
                # Proceed to FIX
                return {
                    "success": True,
                    "phase": orch.current_phase,
                    "round": orch.current_round,
                    "error_signature": orch.last_error_sig,
                    "output": f"Transitioned to FIX phase. Error: {orch.last_error_sig}"
                }

        elif transition == "fix_done":
            success = orch.transition_fix_to_audit()
            return {
                "success": success,
                "phase": orch.current_phase,
                "round": orch.current_round,
                "output": f"Fix complete, re-auditing (round {orch.current_round})"
            }

        else:
            return {"success": False, "error": f"Unknown transition: {transition}"}

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

    # EDGE CASE: Unknown action auto-recovery - map common unknown actions
    # Map mkdir -> exec, touch -> exec, etc.
    UNKNOWN_ACTION_MAPPINGS = {
        "mkdir": lambda data: {
            "action": "exec",
            "cmd": f"mkdir -p {shlex.quote(data.get('path', data.get('dir', '')))}",
            "cwd": data.get("cwd", str(repo_root))
        },
        "touch": lambda data: {
            "action": "exec",
            "cmd": f"touch {shlex.quote(data.get('path', ''))}",
            "cwd": data.get("cwd", str(repo_root))
        },
        "rm": lambda data: {
            "action": "exec",
            "cmd": f"rm -f {shlex.quote(data.get('path', ''))}",
            "cwd": data.get("cwd", str(repo_root))
        },
        "mv": lambda data: {
            "action": "exec",
            "cmd": f"mv {shlex.quote(data.get('src', ''))} {shlex.quote(data.get('dst', ''))}",
            "cwd": data.get("cwd", str(repo_root))
        },
    }

    if action in UNKNOWN_ACTION_MAPPINGS:
        logger.info(f"Auto-mapping unknown action '{action}' to exec")
        try:
            mapped_action = UNKNOWN_ACTION_MAPPINGS[action](action_data)
            return execute_action(mapped_action, repo_root, unrestricted, state=state)
        except Exception as e:
            logger.error(f"Failed to map action '{action}': {e}")
            return {"success": False, "error": f"Failed to map action '{action}': {e}"}

    logger.warning(f"Unknown action: {action}")
    return {"success": False, "error": f"Unknown action: {action}", "unknown_action": True}


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
    """Build prompt for LLM with context, including HUD injection"""

    context_output, _ = call_mem_db("render", "limit=10")
    repo_context = collect_repo_context(repo_root)

    mode = "UNRESTRICTED" if unrestricted else "REVIEWED (safe defaults)"
    llm = state.llm_provider.upper()

    # =============================================================================
    # HUD INJECTION - Heads-Up Display for unified Time, Tasks, Memory
    # =============================================================================
    hud_output = ""
    try:
        hud_script = SCRIPT_DIR / "hooks" / "print-hud.sh"
        if hud_script.exists():
            result = subprocess.run(
                [str(hud_script)],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0 and result.stdout.strip():
                hud_output = result.stdout.strip()
                logger.debug(f"HUD injected: {len(hud_output)} chars")
    except Exception as e:
        logger.warning(f"Failed to generate HUD: {e}")
        # Continue without HUD if it fails

    # Check for orchestration mode
    orch_context = ""
    orch_state = None
    if state.objective.upper().startswith("ORCHESTRATE:"):
        # Extract actual objective and optional task_id
        # Format: "ORCHESTRATE: [task_id:XXX] [topic:YYY] objective text"
        actual_objective = state.objective[12:].strip()

        # Validate non-empty objective
        if not actual_objective:
            logger.warning("Empty orchestration objective, treating as normal mode")
        else:
            # Parse optional task_id and topic from objective
            task_id = None
            topic = "orchestration"

            # Check for task_id: prefix
            import re as re_module
            task_match = re_module.search(r'\[task_id:([^\]]+)\]', actual_objective)
            if task_match:
                task_id = task_match.group(1).strip()
                actual_objective = re_module.sub(r'\[task_id:[^\]]+\]\s*', '', actual_objective)

            topic_match = re_module.search(r'\[topic:([^\]]+)\]', actual_objective)
            if topic_match:
                topic = topic_match.group(1).strip()
                actual_objective = re_module.sub(r'\[topic:[^\]]+\]\s*', '', actual_objective)

            # Default task_id from objective hash if not provided
            if not task_id:
                task_id = f"orch-{hashlib.md5(actual_objective.encode()).hexdigest()[:8]}"

            # Create orchestration state and load from memory
            orch_state = OrchestrationState(
                task_id=task_id,
                topic=topic,
                objective=actual_objective,
                mem_db_path=MEM_DB
            )
            orch_state.load_from_memory()

            # Generate context from orchestration state
            orch_context = orch_state.to_context_string()

            # Add recent PHASE glyph history
            phase_result, _ = call_mem_db("query", "t=P", f"task_id={task_id}", "recent=2h", "limit=5")
            if phase_result:
                orch_context += f"\nRecent PHASE History:\n{phase_result[:1500]}\n"

            # Log orchestration state for debugging
            logger.info(
                f"Orchestration: task={task_id}, phase={orch_state.current_phase}, "
                f"round={orch_state.current_round}, blocked={orch_state.is_blocked}"
            )

    # Add orchestrator system prompt if in orchestration mode
    orch_system_prompt = ""
    if orch_state is not None:
        orch_system_prompt = ORCHESTRATOR_SYSTEM_PROMPT + "\n" + "=" * 60 + "\n\n"

    # Build the prompt with HUD at the very top
    # HUD provides unified view of Time, Tasks, and Memory (Source of Truth)
    hud_section = ""
    if hud_output:
        hud_section = f"""{hud_output}

================================================================================
SYSTEM INSTRUCTION: The OFFICIAL PROJECT HUD above is your Source of Truth.
You must prioritize these Tasks and Constraints above all else.
================================================================================

"""

    prompt = f"""{hud_section}{orch_system_prompt}OBJECTIVE: {state.objective}
REPO: {repo_root} | MODE: {mode} | ITERATION: {state.iteration}

{orch_context}
MEMORY:
{context_output}

REPO:
{repo_context}

ACTIONS:
write_memory: {{"action":"write_memory","type":"f|d|q|a|n|P|I","topic":"...","text":"..."}}
read_file: {{"action":"read_file","path":"file.ts","max_bytes":4000}}
edit_file: {{"action":"edit_file","path":"file.ts","content":"...","reason":"why"}}
list_files: {{"action":"list_files","path":"src"}}
search_text: {{"action":"search_text","query":"TODO","path":"src"}}
exec: {{"action":"exec","cmd":"pnpm install","cwd":"/path"}}
spawn_daemon: {{"action":"spawn_daemon","objective":"Sub-task","repo":"/path","max_iterations":10,"wait":false,"timeout":300}}
orch_status: {{"action":"orch_status","orch_id":"abc123"}}
orch_transition: {{"action":"orch_transition","task_id":"XXX","transition":"implement_done|audit_pass|audit_fail|fix_done","audit_log":"..."}}
git_status/git_log/git_diff: {{"action":"git_status"}}
done: {{"action":"done","summary":"What was accomplished"}}

"""

    if last_results:
        prompt += f"LAST ACTION RESULTS:\n{json.dumps(last_results, indent=2)}\n\n"

        # EDGE CASE: Unknown action auto-recovery - inject hint about valid actions
        if last_results.get("unknown_action"):
            prompt += """
**IMPORTANT: Unknown action error!**
The action you tried is not recognized. Here are the VALID actions you can use:

- write_memory, mem_search, consolidate
- read_file, edit_file, list_files, search_text
- exec (for shell commands like mkdir, rm, mv, etc.)
- spawn_daemon, orch_status, orch_transition
- git_status, git_log, git_diff, check_deps
- run, http_request, sleep, done

**For filesystem operations like mkdir, use exec:**
Example: {"action":"exec","cmd":"mkdir -p path/to/dir"}

Choose a VALID action from the list above.

"""

    if state.history:
        recent_history = state.history[-5:]
        prompt += "RECENT ACTIONS:\n"
        for h in recent_history:
            prompt += f"- {h.get('action', '?')}: {h.get('result', {}).get('output', '')[:100]}...\n"
        prompt += "\n"

    # Detect action loops (action+path signature)
    loop_detected = False
    loop_action = None
    if len(state.history) >= 3:
        # Build action signatures including path/query for better detection
        def action_sig(h):
            a = h.get('action', '?')
            p = h.get('path', h.get('query', h.get('cmd', '')))
            if p:
                return f"{a}:{p[:50]}"
            return a

        last_5_sigs = [action_sig(h) for h in state.history[-5:]]
        last_3 = [h.get('action') for h in state.history[-3:]]

        # Check for exact signature repeats (action + same path)
        if len(last_5_sigs) >= 3:
            sig_counts = {}
            for sig in last_5_sigs:
                sig_counts[sig] = sig_counts.get(sig, 0) + 1
            max_repeat = max(sig_counts.values()) if sig_counts else 0
            if max_repeat >= 3:
                loop_detected = True
                loop_action = max(sig_counts, key=sig_counts.get)

        # Also check action-only repeats
        if len(set(last_3)) == 1:
            loop_detected = True
            loop_action = last_3[0]

        if loop_detected:
            prompt += f"\n**LOOP DETECTED: You repeated '{loop_action}' multiple times.**\n"
            prompt += "**MANDATORY: You MUST use a DIFFERENT action now. Options:**\n"
            prompt += "- If stuck on list_files: use edit_file to CREATE the file directly\n"
            prompt += "- If stuck on read_file: use edit_file to WRITE changes\n"
            prompt += "- If stuck on exec: use edit_file instead\n"
            prompt += "- If truly stuck: use {\"action\":\"done\", \"summary\":\"Blocked: <reason>\"}\n\n"

    prompt += """RULES:
- Output ONLY ONE JSON action. No explanation, no markdown, just JSON.
- You have FULL ACCESS to all paths. NEVER ask for permissions.
- After list_files: use read_file on interesting files.
- After read_file: use edit_file to write new code.
- Progress: list -> read -> edit -> done. Don't loop on same action.
- UNRESTRICTED = full filesystem access granted.

{"action":"...", ...}"""

    return prompt


def run_daemon(state, repo_root: Path, unrestricted: bool, verbose=False, log_full_response=False, use_governor=True):
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

        # Check rate limit with exponential backoff
        if not state.check_rate_limit():
            # Exponential backoff: 60s, 120s, 240s, max 300s
            state.rate_limit_backoff_count += 1
            wait_time = min(60 * (2 ** (state.rate_limit_backoff_count - 1)), 300)
            logger.warning(
                f"Rate limit reached (attempt {state.rate_limit_backoff_count}), "
                f"waiting {wait_time}s (exponential backoff)"
            )
            state.save()
            time.sleep(wait_time)
            continue

        # Build prompt and call LLM
        prompt = build_prompt(state, repo_root, unrestricted, last_results)
        logger.info(f"Iteration {state.iteration}: calling LLM")

        response = call_llm(
            prompt,
            verbose=verbose,
            log_full_response=log_full_response,
            provider=state.llm_provider,
            model=state.llm_model,
            tier=state.llm_tier
        )
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

            # EDGE CASE: Check for consecutive parse errors (OAuth expired, bad model, etc.)
            if len(state.history) >= 3:
                last_3_actions = [h.get('action') for h in state.history[-3:]]
                if all(a == 'parse_error' for a in last_3_actions):
                    logger.error("3 consecutive parse errors. Likely auth/model issue. Terminating.")
                    state.status = "error"
                    state.history.append({
                        "action": "auto_error",
                        "reason": "Consecutive parse errors - check API auth/model",
                        "result": {"success": False}
                    })
                    state.save()
                    return

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

        result = execute_action(action_data, repo_root, unrestricted, state=state)
        last_results = result

        # Record in history (including path for loop detection)
        state.record_iteration()
        history_entry = {
            "action": action_data.get("action"),
            "path": action_data.get("path", action_data.get("query", action_data.get("cmd", ""))),
            "data": action_data,
            "result": result
        }
        state.history.append(history_entry)
        state.last_action = action_data.get("action")
        state.save()

        # EDGE CASE: Hard loop breaker - if same action+path 5+ times, force terminate
        if len(state.history) >= 5:
            def action_sig(h):
                a = h.get('action', '?')
                p = h.get('path', '')
                return f"{a}:{p[:50]}" if p else a

            last_5 = [action_sig(h) for h in state.history[-5:]]
            sig_counts = {}
            for sig in last_5:
                sig_counts[sig] = sig_counts.get(sig, 0) + 1
            max_repeat = max(sig_counts.values()) if sig_counts else 0

            if max_repeat >= 5:
                loop_sig = max(sig_counts, key=sig_counts.get)
                logger.error(f"HARD LOOP DETECTED: '{loop_sig}' repeated 5 times. Force terminating.")
                state.status = "blocked"
                state.history.append({
                    "action": "auto_blocked",
                    "reason": f"Loop detected: {loop_sig}",
                    "result": {"success": False, "error": "Forced termination due to action loop"}
                })
                state.save()
                return

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
    parser.add_argument('--log-full-response', action='store_true', help='Log complete LLM responses including reasoning text before JSON')
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
        run_daemon(
            state,
            repo_root,
            state.unrestricted,
            verbose=args.verbose,
            log_full_response=args.log_full_response,
            use_governor=not args.no_governor
        )
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
