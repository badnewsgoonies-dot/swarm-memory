#!/usr/bin/env python3
"""
llm_client.py - Hybrid LLM client with tiered routing

Available Models (as of Dec 2025):

OLLAMA (Windows PC @ 10.0.0.122:11434, GTX 1060):
  - llama3.2:3b      - Fast, general purpose
  - gemma3:4b        - Google's efficient model
  - deepseek-coder:6.7b - Code-focused
  - qwen2.5-coder:7b - Code-focused, larger

OPENAI API (via service account key):
  - gpt-4o-mini      - Cheap, fast
  - gpt-4o           - Balanced
  - gpt-4-turbo      - Powerful

CLAUDE CLI (via claude -p):
  - opus      → Opus 4.5 - Most capable for complex work
  - sonnet    → Sonnet 4.5 - Best for everyday tasks
  - haiku     → Haiku 4.5 - Fastest for quick answers

CODEX CLI (via codex exec):
  Models:
    - gpt-5.1-codex-max  - Flagship, deep+fast reasoning
    - gpt-5.1-codex      - Optimized for codex
    - gpt-5.1-codex-mini - Cheaper, faster, less capable
    - gpt-5.1            - Broad world knowledge
  Effort levels (for each model):
    - low    - Fast responses with lighter reasoning
    - medium - Balances speed and reasoning depth (default)
    - high   - Maximizes reasoning depth for complex problems
    - xhigh  - Extra high reasoning depth

Tiers:
  fast   → llama3.2:3b (Ollama) - Classification, routing
  code   → qwen2.5-coder:7b (Ollama) - Code generation
  smart  → gpt-4o-mini (OpenAI) - Cheap cloud reasoning
  claude → Opus 4.5 (Claude CLI) - Complex work
  codex  → gpt-5.1-codex-max high (Codex CLI) - High reasoning
  max    → gpt-5.1-codex-max xhigh (Codex CLI) - Maximum

Usage:
    from llm_client import LLMClient
    client = LLMClient()
    response = client.complete("prompt", tier="code")
"""

import os
import json
import time
import logging
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass, field

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    import urllib.request
    import urllib.error
    HAS_REQUESTS = False

logger = logging.getLogger(__name__)

# Environment configuration
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://10.0.0.122:11434")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
VERBOSE = os.environ.get("LLM_VERBOSE", "0") == "1"

# =============================================================================
# MODEL CONFIGURATION - Edit this to match your actual available models
# =============================================================================

MODELS = {
    # Tier 1: FAST - Quick local inference for classification/routing
    "fast": {
        "provider": "ollama",
        "model": "llama3.2:3b",
        "description": "Fast local model (Ollama)",
        "max_tokens": 500,
        "timeout": 30,
    },

    # Tier 2: CODE - Best local model for code generation
    "code": {
        "provider": "ollama",
        "model": "qwen2.5-coder:7b",
        "description": "Code-focused local model (Ollama)",
        "max_tokens": 2000,
        "timeout": 120,
    },

    # Tier 3: SMART - Cloud API for complex reasoning (cheap)
    "smart": {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "description": "Cloud API - cheap & capable (OpenAI)",
        "max_tokens": 4000,
        "timeout": 120,
    },

    # Tier 4: CLAUDE - Claude CLI for complex work
    "claude": {
        "provider": "claude",
        "model": "opus",  # Opus 4.5 - most capable
        "description": "Claude Opus 4.5 - complex work",
        "max_tokens": 8000,
        "timeout": 300,
    },

    # Tier 5: CODEX - Codex CLI maximum capability
    "codex": {
        "provider": "codex",
        "model": "gpt-5.1-codex-max",
        "effort": "high",  # low, medium, high, xhigh
        "description": "Codex flagship - high reasoning",
        "max_tokens": 8000,
        "timeout": 300,
    },

    # Alias: MAX points to codex with extra high effort
    "max": {
        "provider": "codex",
        "model": "gpt-5.1-codex-max",
        "effort": "xhigh",  # Maximum reasoning depth
        "description": "Maximum capability (Codex xhigh)",
        "max_tokens": 8000,
        "timeout": 600,
    },
}

# All available models by provider
ALTERNATIVE_MODELS = {
    "ollama": ["llama3.2:3b", "gemma3:4b", "deepseek-coder:6.7b", "qwen2.5-coder:7b"],
    "openai": ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo"],
    "claude": ["opus", "sonnet", "haiku"],
    "codex": ["gpt-5.1-codex-max", "gpt-5.1-codex", "gpt-5.1-codex-mini", "gpt-5.1"],
}

# Fallback chain: if one fails, try next
FALLBACK_CHAIN = ["fast", "code", "smart", "max"]

# =============================================================================


@dataclass
class LLMResponse:
    """Response from LLM call"""
    text: str
    model: str
    provider: str
    tier: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    success: bool = True
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "model": self.model,
            "provider": self.provider,
            "tier": self.tier,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "latency_ms": self.latency_ms,
            "success": self.success,
            "error": self.error,
        }


@dataclass
class UsageStats:
    """Track usage across tiers"""
    calls: Dict[str, int] = field(default_factory=lambda: {"fast": 0, "code": 0, "smart": 0, "max": 0})
    tokens: Dict[str, int] = field(default_factory=lambda: {"fast": 0, "code": 0, "smart": 0, "max": 0})
    errors: Dict[str, int] = field(default_factory=lambda: {"fast": 0, "code": 0, "smart": 0, "max": 0})
    fallbacks: int = 0

    def record(self, tier: str, tokens: int = 0, error: bool = False):
        self.calls[tier] = self.calls.get(tier, 0) + 1
        self.tokens[tier] = self.tokens.get(tier, 0) + tokens
        if error:
            self.errors[tier] = self.errors.get(tier, 0) + 1

    def summary(self) -> str:
        lines = ["LLM Usage:"]
        for tier in ["fast", "code", "smart", "max"]:
            if self.calls.get(tier, 0) > 0:
                lines.append(f"  {tier}: {self.calls[tier]} calls, {self.tokens[tier]} tokens, {self.errors.get(tier, 0)} errors")
        if self.fallbacks > 0:
            lines.append(f"  Fallbacks: {self.fallbacks}")
        return "\n".join(lines)


class LLMClient:
    """Hybrid LLM client with tiered routing and fallback"""

    def __init__(self, ollama_host: str = None, openai_key: str = None):
        self.ollama_host = ollama_host or OLLAMA_HOST
        self.openai_key = openai_key or OPENAI_API_KEY
        self.stats = UsageStats()
        self._load_env()

    def _load_env(self):
        """Load API key from .env file if not set"""
        if not self.openai_key:
            env_file = Path(__file__).parent / ".env"
            if env_file.exists():
                for line in env_file.read_text().splitlines():
                    if line.startswith("OPENAI_API_KEY="):
                        self.openai_key = line.split("=", 1)[1].strip()
                        break

    def _http_post(self, url: str, data: dict, headers: dict = None, timeout: int = 60) -> Tuple[dict, int]:
        """Make HTTP POST request"""
        headers = headers or {}
        headers["Content-Type"] = "application/json"
        body = json.dumps(data).encode("utf-8")

        if HAS_REQUESTS:
            try:
                resp = requests.post(url, json=data, headers=headers, timeout=timeout)
                return resp.json() if resp.text else {}, resp.status_code
            except requests.exceptions.Timeout:
                return {"error": "timeout"}, 408
            except requests.exceptions.ConnectionError:
                return {"error": "connection_error"}, 503
            except Exception as e:
                return {"error": str(e)}, 500
        else:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode()), resp.status
            except urllib.error.URLError as e:
                return {"error": str(e)}, 503
            except Exception as e:
                return {"error": str(e)}, 500

    def _call_ollama(self, prompt: str, model: str, max_tokens: int = 1000, timeout: int = 60) -> LLMResponse:
        """Call Ollama API"""
        url = f"{self.ollama_host}/api/generate"
        data = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }

        start = time.time()
        resp, status = self._http_post(url, data, timeout=timeout)
        latency = int((time.time() - start) * 1000)

        if status != 200 or "error" in resp:
            return LLMResponse(
                text="", model=model, provider="ollama", tier="",
                latency_ms=latency, success=False,
                error=resp.get("error", f"HTTP {status}"),
            )

        return LLMResponse(
            text=resp.get("response", ""),
            model=model, provider="ollama", tier="",
            tokens_in=resp.get("prompt_eval_count", 0),
            tokens_out=resp.get("eval_count", 0),
            latency_ms=latency, success=True,
        )

    def _call_openai(self, prompt: str, model: str = "gpt-4o-mini", max_tokens: int = 2000, timeout: int = 120) -> LLMResponse:
        """Call OpenAI API directly"""
        if not self.openai_key:
            return LLMResponse(
                text="", model=model, provider="openai", tier="",
                success=False, error="No OpenAI API key",
            )

        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.openai_key}"}
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }

        start = time.time()
        resp, status = self._http_post(url, data, headers=headers, timeout=timeout)
        latency = int((time.time() - start) * 1000)

        if status != 200 or "error" in resp:
            error_msg = resp.get("error", {})
            if isinstance(error_msg, dict):
                error_msg = error_msg.get("message", f"HTTP {status}")
            return LLMResponse(
                text="", model=model, provider="openai", tier="",
                latency_ms=latency, success=False, error=str(error_msg),
            )

        choices = resp.get("choices", [])
        text = choices[0]["message"]["content"] if choices else ""
        usage = resp.get("usage", {})

        return LLMResponse(
            text=text, model=model, provider="openai", tier="",
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            latency_ms=latency, success=True,
        )

    def _call_claude(self, prompt: str, model: str = "opus", max_tokens: int = 4000, timeout: int = 300) -> LLMResponse:
        """Call Claude CLI (claude -p)

        Models: opus (Opus 4.5), sonnet (Sonnet 4.5), haiku (Haiku 4.5)
        """
        cmd = ["claude", "-p", prompt]
        if model and model != "default":
            cmd = ["claude", "--model", model, "-p", prompt]

        start = time.time()
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            latency = int((time.time() - start) * 1000)
            response = (result.stdout + result.stderr).strip()

            if result.returncode != 0 and not response:
                return LLMResponse(
                    text="", model=model, provider="claude", tier="",
                    latency_ms=latency, success=False,
                    error=f"Claude CLI failed: exit {result.returncode}",
                )

            return LLMResponse(
                text=response, model=model, provider="claude", tier="",
                latency_ms=latency, success=True,
            )
        except subprocess.TimeoutExpired:
            return LLMResponse(
                text="", model=model, provider="claude", tier="",
                latency_ms=int((time.time() - start) * 1000),
                success=False, error="Claude CLI timeout",
            )
        except FileNotFoundError:
            return LLMResponse(
                text="", model=model, provider="claude", tier="",
                success=False, error="Claude CLI not found",
            )
        except Exception as e:
            return LLMResponse(
                text="", model=model, provider="claude", tier="",
                success=False, error=str(e),
            )

    def _call_codex(self, prompt: str, model: str = "gpt-5.1-codex-max", effort: str = "high", max_tokens: int = 4000, timeout: int = 300) -> LLMResponse:
        """Call Codex CLI (codex exec)

        Models: gpt-5.1-codex-max, gpt-5.1-codex, gpt-5.1-codex-mini, gpt-5.1
        Effort: low, medium, high, xhigh (via -c model_reasoning_effort=X)
        """
        # Map effort names to codex config format
        effort_map = {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "extra high": "xhigh"}
        effort_arg = effort_map.get(effort, "high")

        cmd = ["codex", "exec", "-m", model, "-c", f"model_reasoning_effort={effort_arg}", "--full-auto", prompt]

        start = time.time()
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            latency = int((time.time() - start) * 1000)
            response = (result.stdout + result.stderr).strip()

            if result.returncode != 0 and not response:
                return LLMResponse(
                    text="", model=model, provider="codex", tier="",
                    latency_ms=latency, success=False,
                    error=f"Codex CLI failed: exit {result.returncode}",
                )

            return LLMResponse(
                text=response, model=model, provider="codex", tier="",
                latency_ms=latency, success=True,
            )
        except subprocess.TimeoutExpired:
            return LLMResponse(
                text="", model=model, provider="codex", tier="",
                latency_ms=int((time.time() - start) * 1000),
                success=False, error="Codex CLI timeout",
            )
        except FileNotFoundError:
            return LLMResponse(
                text="", model=model, provider="codex", tier="",
                success=False, error="Codex CLI not found",
            )
        except Exception as e:
            return LLMResponse(
                text="", model=model, provider="codex", tier="",
                success=False, error=str(e),
            )

    def _classify_task(self, prompt: str) -> str:
        """Auto-classify prompt to select tier"""
        prompt_lower = prompt.lower()

        # Code keywords → code tier
        code_kw = ["function", "implement", "code", "write", "fix bug", "refactor",
                   "class", "method", "api", "endpoint", "database", "query", "def ", "import "]
        if any(kw in prompt_lower for kw in code_kw):
            return "code"

        # Complex reasoning → smart tier
        smart_kw = ["plan", "design", "architect", "orchestrate", "complex",
                    "analyze", "evaluate", "compare", "trade-off", "strategy"]
        if any(kw in prompt_lower for kw in smart_kw):
            return "smart"

        # Short or classification → fast tier
        if len(prompt) < 200 or "classify" in prompt_lower:
            return "fast"

        return "code"

    def complete(
        self,
        prompt: str,
        tier: str = "auto",
        max_tokens: int = None,
        timeout: int = None,
        fallback: bool = True,
        system_prompt: str = None,
    ) -> LLMResponse:
        """Complete a prompt using specified tier"""

        if tier == "auto":
            tier = self._classify_task(prompt)
            if VERBOSE:
                logger.info(f"Auto-selected tier: {tier}")

        config = MODELS.get(tier)
        if not config:
            return LLMResponse(
                text="", model="", provider="", tier=tier,
                success=False, error=f"Unknown tier: {tier}",
            )

        max_tokens = max_tokens or config["max_tokens"]
        timeout = timeout or config["timeout"]
        full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt

        # Route to provider
        if config["provider"] == "ollama":
            response = self._call_ollama(full_prompt, config["model"], max_tokens, timeout)
        elif config["provider"] == "openai":
            response = self._call_openai(full_prompt, config["model"], max_tokens, timeout)
        elif config["provider"] == "claude":
            response = self._call_claude(full_prompt, config["model"], max_tokens, timeout)
        elif config["provider"] == "codex":
            effort = config.get("effort", "high")
            response = self._call_codex(full_prompt, config["model"], effort, max_tokens, timeout)
        else:
            response = LLMResponse(
                text="", model=config["model"], provider=config["provider"], tier=tier,
                success=False, error=f"Unknown provider: {config['provider']}",
            )

        response.tier = tier
        self.stats.record(tier, response.tokens_in + response.tokens_out, not response.success)

        # Fallback on failure
        if not response.success and fallback:
            tier_idx = FALLBACK_CHAIN.index(tier) if tier in FALLBACK_CHAIN else -1
            if tier_idx >= 0 and tier_idx < len(FALLBACK_CHAIN) - 1:
                next_tier = FALLBACK_CHAIN[tier_idx + 1]
                logger.warning(f"Tier {tier} failed ({response.error}), fallback → {next_tier}")
                self.stats.fallbacks += 1
                return self.complete(prompt, tier=next_tier, max_tokens=max_tokens,
                                    timeout=timeout, fallback=fallback, system_prompt=system_prompt)

        if VERBOSE:
            logger.info(f"[{tier}] {response.model}: {response.text[:200]}...")

        return response

    def health_check(self) -> Dict[str, Any]:
        """Check all providers"""
        results = {}

        # Ollama
        try:
            url = f"{self.ollama_host}/api/tags"
            if HAS_REQUESTS:
                resp = requests.get(url, timeout=5)
                models = [m["name"] for m in resp.json().get("models", [])] if resp.status_code == 200 else []
                results["ollama"] = {"status": "healthy" if models else "no models", "models": models, "host": self.ollama_host}
            else:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    models = [m["name"] for m in json.loads(resp.read().decode()).get("models", [])]
                    results["ollama"] = {"status": "healthy", "models": models, "host": self.ollama_host}
        except Exception as e:
            results["ollama"] = {"status": "error", "error": str(e), "host": self.ollama_host}

        # OpenAI
        if self.openai_key:
            results["openai"] = {"status": "configured", "models": ALTERNATIVE_MODELS["openai"]}
        else:
            results["openai"] = {"status": "no_key"}

        # Claude CLI
        try:
            result = subprocess.run(["claude", "--version"], capture_output=True, timeout=5)
            if result.returncode == 0:
                results["claude"] = {"status": "available", "models": ALTERNATIVE_MODELS["claude"]}
            else:
                results["claude"] = {"status": "error"}
        except:
            results["claude"] = {"status": "not_installed"}

        # Codex CLI
        try:
            result = subprocess.run(["codex", "--version"], capture_output=True, timeout=5)
            if result.returncode == 0:
                results["codex"] = {"status": "available", "models": ALTERNATIVE_MODELS["codex"]}
            else:
                results["codex"] = {"status": "error"}
        except:
            results["codex"] = {"status": "not_installed"}

        return results

    def list_models(self) -> Dict[str, list]:
        """List all available models by provider"""
        health = self.health_check()
        available = {}

        if health.get("ollama", {}).get("status") == "healthy":
            available["ollama"] = health["ollama"]["models"]
        if health.get("openai", {}).get("status") == "configured":
            available["openai"] = ALTERNATIVE_MODELS["openai"]
        if health.get("codex", {}).get("status") == "available":
            available["codex"] = ALTERNATIVE_MODELS["codex"]

        return available

    def get_stats(self) -> UsageStats:
        return self.stats


# Convenience functions
_default_client = None

def get_client() -> LLMClient:
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client

def complete(prompt: str, tier: str = "auto", **kwargs) -> LLMResponse:
    return get_client().complete(prompt, tier=tier, **kwargs)


if __name__ == "__main__":
    client = LLMClient()

    print("=" * 60)
    print("LLM CLIENT - MODEL AVAILABILITY")
    print("=" * 60)

    health = client.health_check()
    for provider, info in health.items():
        status = info.get("status", "unknown")
        print(f"\n{provider.upper()}:")
        print(f"  Status: {status}")
        if "models" in info:
            print(f"  Models: {', '.join(info['models'])}")
        if "host" in info:
            print(f"  Host: {info['host']}")
        if "error" in info:
            print(f"  Error: {info['error']}")

    print("\n" + "=" * 60)
    print("CONFIGURED TIERS")
    print("=" * 60)
    for tier, cfg in MODELS.items():
        print(f"\n{tier.upper()}:")
        print(f"  Provider: {cfg['provider']}")
        print(f"  Model: {cfg['model']}")
        print(f"  Description: {cfg['description']}")

    print("\n" + "=" * 60)
    print("QUICK TEST")
    print("=" * 60)

    resp = client.complete("Say 'hello' in one word", tier="fast")
    print(f"\n[FAST] {resp.model}: {resp.text[:100]}... ({resp.latency_ms}ms)")
