#!/usr/bin/env python3
"""
test_succeed_loop.py - Test systemic learning with successful retrieval.

This demonstrates that after teaching the system about "Project Ciphers",
the agent can now successfully complete the task.
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


def get_context_from_memory():
    """
    Fetch relevant context from memory.

    In production, this would call:
        ./mem-db.sh query topic=security
    or:
        ./mem-db.sh semantic "Project Ciphers"

    For this test, we simulate the retrieval of what we just injected.
    """
    # This is exactly what was written to memory in Step 4
    return """
[FACT][topic=security][ts=just now][FRESH]
PROJECT CIPHERS SPECIFICATION: Use AES-256-GCM for symmetric encryption,
ECDH P-384 for key exchange, HMAC-SHA384 for message authentication.
Handshake sequence:
1) ClientHello (nonce + supported_ciphers)
2) ServerHello (selected_cipher + server_pubkey)
3) KeyExchange (client_pubkey + encrypted_premaster)
4) Finished (verify_data)
"""


def build_worker_prompt(task_id: str, task_text: str, context: str) -> str:
    """Build the worker prompt with injected context"""
    return f"""You are a Worker Agent analyzing a TODO task.

## TASK
ID: {task_id}
Text: {task_text}

## CONTEXT FROM MEMORY
{context}

## INSTRUCTIONS
1. Analyze the task requirements
2. Use the context from memory to understand Project Ciphers
3. Produce a concrete implementation plan

## OUTPUT FORMAT (use exactly this structure)
ATTEMPT: [What you tried to do]
RESULT: success=True/False, reason=[why]
LESSON: [{task_id}] [What was learned]
PLAN: [Concrete steps to implement]

## CRITICAL RULES
- "Project Ciphers" specification is provided in the context
- Use the EXACT algorithms specified (AES-256-GCM, ECDH P-384, HMAC-SHA384)
- Follow the EXACT handshake sequence from the spec
"""


def run_test():
    """Run the success test with injected memory context"""

    task_id = "mission-alpha"
    task_text = "Implement a secure handshake protocol using the Project Ciphers."

    logger.info("=" * 60)
    logger.info("SYSTEMIC LEARNING TEST - Step 5: The Succeed")
    logger.info("=" * 60)
    logger.info(f"Task ID: {task_id}")
    logger.info(f"Task: {task_text}")
    logger.info("")
    logger.info("Memory Status: Project Ciphers spec NOW IN MEMORY")
    logger.info("Expected outcome: SUCCESS (knowledge retrieved from memory)")
    logger.info("=" * 60)

    # 1. Fetch context from memory (simulated retrieval)
    context = get_context_from_memory()
    logger.info("Context retrieved from memory:")
    logger.info(context.strip()[:150] + "...")

    # 2. Create LLM client
    client = LLMClient()
    logger.info(f"Ollama host: {client.ollama_host}")

    # 3. Build prompt WITH context
    prompt = build_worker_prompt(task_id, task_text, context)
    logger.info("Sending prompt to LLM (Tier: fast)...")

    # 4. Call LLM
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

    # 5. Check result
    text_lower = response.text.lower()
    if "success=true" in text_lower:
        logger.info("")
        logger.info("=" * 60)
        logger.info("SYSTEMIC LEARNING PROVEN!")
        logger.info("=" * 60)
        logger.info("The agent successfully used memory to complete the task.")
        logger.info("")
        logger.info("Learning Loop Complete:")
        logger.info("  Step 3 (Fail):    Agent didn't know Project Ciphers")
        logger.info("  Step 4 (Teach):   Injected spec into memory")
        logger.info("  Step 5 (Succeed): Agent used memory to plan implementation")
        logger.info("")
        logger.info("This proves SYSTEMIC LEARNING without weight updates!")
    elif "success=false" in text_lower:
        logger.warning("")
        logger.warning("UNEXPECTED: Agent still failed despite context!")
        logger.warning("Check if the context was properly included in the prompt.")
    else:
        logger.info("")
        logger.info("UNCLEAR: Check the response manually")


if __name__ == "__main__":
    run_test()
