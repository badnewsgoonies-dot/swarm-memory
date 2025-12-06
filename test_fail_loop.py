#!/usr/bin/env python3
"""
test_fail_loop.py - Test systemic learning with intentional failure

This script directly tests the mission-alpha task which should FAIL
because "Project Ciphers" is not defined in memory.
"""

import os
import logging

# Set OLLAMA_HOST before importing llm_client
os.environ["OLLAMA_HOST"] = "http://localhost:11434"

from llm_client import LLMClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


def build_worker_prompt(task_id: str, task_text: str, context: str = "") -> str:
    """Build the worker prompt for analyzing a task"""
    return f"""You are a Worker Agent analyzing a TODO task.

## TASK
ID: {task_id}
Text: {task_text}

## CONTEXT
{context if context else "(No additional context provided)"}

## INSTRUCTIONS
1. Analyze the task requirements
2. Check if you have SPECIFIC knowledge of "Project Ciphers"
3. If you know the EXACT specification, produce a plan
4. If you don't know the EXACT specification, report failure

## OUTPUT FORMAT (use exactly this structure)
ATTEMPT: [What you tried to do]
RESULT: success=True/False, reason=[why]
LESSON: [{task_id}] [What was learned]

## CRITICAL RULES
- "Project Ciphers" is a SPECIFIC proprietary system, NOT a general concept
- You MUST know the EXACT cipher suite (e.g., specific algorithm names, key sizes)
- Generic cryptography knowledge is NOT sufficient
- If you cannot name the EXACT ciphers in Project Ciphers, you MUST report:
  RESULT: success=False, reason=Project Ciphers specification unknown
- DO NOT assume or guess what Project Ciphers might be
- DO NOT use generic cryptography concepts as a substitute
"""


def run_test():
    """Run the fail test on mission-alpha"""

    # The intentionally impossible task
    task_id = "mission-alpha"
    task_text = "Implement a secure handshake protocol using the Project Ciphers."

    logger.info("=" * 60)
    logger.info("SYSTEMIC LEARNING TEST - Step 3: The Fail")
    logger.info("=" * 60)
    logger.info(f"Task ID: {task_id}")
    logger.info(f"Task: {task_text}")
    logger.info("")
    logger.info("Expected outcome: FAILURE (Project Ciphers not in memory)")
    logger.info("=" * 60)

    # Create client
    client = LLMClient()
    logger.info(f"Ollama host: {client.ollama_host}")

    # Build prompt with NO context about Project Ciphers
    prompt = build_worker_prompt(task_id, task_text, context="")
    logger.info("Sending prompt to LLM (Tier: fast)...")

    # Call LLM
    response = client.complete(
        prompt,
        tier="fast",
        timeout=120
    )

    if not response.success:
        logger.error(f"LLM Failed: {response.error}")
        return

    logger.info(f"LLM Response ({response.latency_ms}ms):")
    logger.info("-" * 40)
    print(response.text)
    logger.info("-" * 40)

    # Check if it correctly failed
    text_lower = response.text.lower()
    if "success=false" in text_lower or "failure" in text_lower or "missing" in text_lower:
        logger.info("")
        logger.info("PASS: Agent correctly reported failure!")
        logger.info("The agent recognized it doesn't know 'Project Ciphers'")
    elif "success=true" in text_lower:
        logger.warning("")
        logger.warning("UNEXPECTED: Agent claimed success!")
        logger.warning("The agent may have hallucinated Project Ciphers info")
    else:
        logger.info("")
        logger.info("UNCLEAR: Check the response manually")


if __name__ == "__main__":
    run_test()
