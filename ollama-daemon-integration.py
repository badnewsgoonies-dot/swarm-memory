#!/usr/bin/env python3
"""
ollama-daemon-integration.py - Example Ollama integration for swarm_daemon.py

This file shows how to add Ollama support to the swarm daemon.
It can be used as a reference for modifying swarm_daemon.py.

Usage:
    1. Add the call_ollama() function to swarm_daemon.py
    2. Modify the call_llm() function to support provider="ollama"
    3. Run with: ./swarm_daemon.py --llm ollama --llm-model qwen2.5:14b
"""

import requests
import json
import os
from typing import Optional, Dict, Any


# ============================================================================
# Ollama API Client
# ============================================================================

class OllamaClient:
    """Simple Ollama API client for daemon integration"""

    def __init__(
        self,
        host: str = "http://127.0.0.1:11434",
        default_model: str = "qwen2.5:14b"
    ):
        self.host = host.rstrip('/')
        self.default_model = default_model

    def generate(
        self,
        prompt: str,
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        timeout: int = 120
    ) -> str:
        """
        Generate text from prompt.

        Args:
            prompt: Input prompt
            model: Model name (defaults to instance default)
            temperature: Randomness (0.0-1.0)
            max_tokens: Max tokens to generate
            timeout: Request timeout in seconds

        Returns:
            Generated text string

        Raises:
            requests.RequestException: On API errors
        """
        model = model or self.default_model

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens
            }
        }

        response = requests.post(
            f"{self.host}/api/generate",
            json=payload,
            timeout=timeout
        )
        response.raise_for_status()

        result = response.json()
        return result.get("response", "").strip()

    def chat(
        self,
        messages: list,
        model: Optional[str] = None,
        temperature: float = 0.7,
        timeout: int = 120
    ) -> str:
        """
        Chat with conversation history.

        Args:
            messages: List of {"role": "user/assistant", "content": "..."}
            model: Model name
            temperature: Randomness
            timeout: Request timeout

        Returns:
            Assistant's response text
        """
        model = model or self.default_model

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature
            }
        }

        response = requests.post(
            f"{self.host}/api/chat",
            json=payload,
            timeout=timeout
        )
        response.raise_for_status()

        result = response.json()
        message = result.get("message", {})
        return message.get("content", "").strip()

    def list_models(self) -> list:
        """List available models"""
        response = requests.get(f"{self.host}/api/tags", timeout=10)
        response.raise_for_status()
        return response.json().get("models", [])

    def is_healthy(self) -> bool:
        """Check if Ollama API is responsive"""
        try:
            response = requests.get(f"{self.host}/api/tags", timeout=5)
            return response.status_code == 200
        except:
            return False


# ============================================================================
# Integration with swarm_daemon.py
# ============================================================================

def call_ollama(
    prompt: str,
    model: Optional[str] = None,
    verbose: bool = False
) -> str:
    """
    Call Ollama API (to be integrated into swarm_daemon.py's call_llm()).

    This function should be added to swarm_daemon.py and called from
    the call_llm() function when provider == "ollama".

    Args:
        prompt: Prompt text
        model: Model name (falls back to env var)
        verbose: Enable verbose logging

    Returns:
        Generated response text
    """
    # Get configuration from environment
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    default_model = os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")
    model = model or default_model

    if verbose:
        print(f"[Ollama] Using model: {model}")
        print(f"[Ollama] Prompt length: {len(prompt)} chars")

    # Create client and generate
    client = OllamaClient(host=host, default_model=model)
    response = client.generate(prompt, model=model)

    if verbose:
        print(f"[Ollama] Response length: {len(response)} chars")

    return response


# ============================================================================
# Modified call_llm() for swarm_daemon.py
# ============================================================================

def call_llm_with_ollama_support(
    prompt: str,
    verbose: bool = False,
    provider: str = "claude",
    model: Optional[str] = None
) -> str:
    """
    Modified version of call_llm() from swarm_daemon.py with Ollama support.

    Add this logic to the existing call_llm() function in swarm_daemon.py.

    Args:
        prompt: Prompt text
        verbose: Enable verbose logging
        provider: LLM provider (claude/codex/ollama)
        model: Model name (provider-specific)

    Returns:
        Generated response text
    """
    import subprocess
    import logging

    logger = logging.getLogger(__name__)

    if verbose:
        logger.info(f"LLM prompt ({len(prompt)} chars):\n{prompt[:500]}...")
    else:
        logger.debug(f"LLM prompt: {prompt[:200]}...")

    # Ollama provider
    if provider == "ollama":
        return call_ollama(prompt, model=model, verbose=verbose)

    # Codex provider
    elif provider == "codex":
        codex_model = model or os.environ.get("CODEX_MODEL", "gpt-5.1-codex-latest")
        cmd = ['codex', 'exec', '-m', codex_model, '--full-auto', prompt]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return result.stdout.strip()

    # Claude provider (default)
    else:
        claude_model = model or os.environ.get("CLAUDE_MODEL")
        cmd = ['claude']
        if claude_model:
            cmd.extend(['--model', claude_model])
        cmd.extend(['-p', prompt])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return result.stdout.strip()


# ============================================================================
# Example Usage
# ============================================================================

def example_daemon_task():
    """Example of using Ollama for a daemon-style task"""

    # Initialize client
    client = OllamaClient(
        host=os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434"),
        default_model="qwen2.5:14b"
    )

    # Check health
    if not client.is_healthy():
        print("ERROR: Ollama API not responding")
        return

    # Define daemon-style prompt
    prompt = """
You are an autonomous agent managing a memory database.

Available actions:
- {"action": "write_memory", "type": "f", "topic": "test", "text": "..."}
- {"action": "mem_search", "query": "topic=test", "limit": 5}
- {"action": "done", "summary": "..."}

Current objective: Test Ollama integration with daemon.

Respond with ONE JSON action to test the system.
"""

    # Generate response
    print("Sending prompt to Ollama...")
    response = client.generate(prompt, model="qwen2.5:14b", temperature=0.3)

    print(f"\nResponse:\n{response}\n")

    # Try to parse JSON action
    try:
        # Extract JSON (handle markdown wrapping)
        json_text = response
        if "```json" in response:
            json_text = response.split("```json")[1].split("```")[0].strip()
        elif "```" in response:
            json_text = response.split("```")[1].split("```")[0].strip()

        action = json.loads(json_text)
        print(f"Parsed action: {action.get('action', 'unknown')}")
        print(f"Full action: {json.dumps(action, indent=2)}")

    except json.JSONDecodeError as e:
        print(f"Failed to parse JSON: {e}")
        print("This is expected if the model doesn't return valid JSON")


# ============================================================================
# Command-Line Testing
# ============================================================================

if __name__ == "__main__":
    import sys

    # Check if Ollama is available
    client = OllamaClient()
    if not client.is_healthy():
        print("ERROR: Ollama API is not responding at http://127.0.0.1:11434")
        print("Make sure Ollama service is running:")
        print("  systemctl --user status ollama")
        sys.exit(1)

    # List models
    print("Available models:")
    models = client.list_models()
    for model in models:
        print(f"  - {model['name']} ({model.get('size', 'unknown')})")

    print("\n" + "=" * 70)
    print("Testing daemon integration pattern...")
    print("=" * 70 + "\n")

    # Run example
    example_daemon_task()

    print("\n" + "=" * 70)
    print("Integration test complete!")
    print("=" * 70)
    print("\nTo use with swarm_daemon.py:")
    print("  1. Add call_ollama() function to swarm_daemon.py")
    print("  2. Modify call_llm() to handle provider='ollama'")
    print("  3. Run: ./swarm_daemon.py --llm ollama --llm-model qwen2.5:14b")
