#!/usr/bin/env python3
"""
llm_router.py - Tiered LLM routing for hybrid local/API architecture

Routes daemon actions to appropriate LLM tier based on:
- Task complexity (classification, generation, orchestration)
- Quality requirements (can fail vs. must succeed)
- Cost constraints (prefer local, fallback to API)

Tiers:
  1. LOCAL_FAST (Phi-3, Llama 3 8B) - Simple classification/extraction
  2. LOCAL_QUALITY (Llama 3 70B, Mixtral) - Code generation, reasoning
  3. API_FALLBACK (Claude, GPT-4) - Complex orchestration, quality failures

Usage:
    from llm_router import LLMRouter

    router = LLMRouter(config_path="llm_config.yaml")
    response = router.route(prompt, action_type="code_generation", context={})

    # With quality check
    response = router.route_with_quality_check(
        prompt,
        action_type="code_generation",
        quality_threshold=0.8
    )
"""

import json
import logging
import time
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import yaml
import re
import hashlib


logger = logging.getLogger(__name__)


class Tier(Enum):
    """LLM tier classification"""
    LOCAL_FAST = "local_fast"
    LOCAL_QUALITY = "local_quality"
    API_FALLBACK = "api_fallback"


class ActionComplexity(Enum):
    """Task complexity classification"""
    SIMPLE = "simple"          # Yes/no, classification, extraction
    MODERATE = "moderate"      # Code gen, multi-step reasoning, summarization
    COMPLEX = "complex"        # Orchestration, critical decisions, error recovery


@dataclass
class ModelConfig:
    """Configuration for a specific model"""
    name: str
    tier: Tier
    provider: str  # ollama, claude, openai, vllm
    model_id: str
    max_tokens: int = 4096
    temperature: float = 0.7
    timeout: int = 60
    cost_per_1k_tokens: float = 0.0
    context_window: int = 8192
    enabled: bool = True
    endpoint: Optional[str] = None  # For local models


@dataclass
class RoutingDecision:
    """Result of routing decision"""
    tier: Tier
    model: ModelConfig
    reason: str
    estimated_cost: float = 0.0
    fallback_chain: List[ModelConfig] = field(default_factory=list)


@dataclass
class QualityCheckResult:
    """Result of quality assessment"""
    passed: bool
    confidence: float  # 0.0-1.0
    issues: List[str]
    reasoning: str


@dataclass
class UsageStats:
    """Track usage and costs"""
    tier: Tier
    model_name: str
    prompt_tokens: int
    completion_tokens: int
    cost: float
    latency_ms: int
    success: bool
    quality_score: Optional[float] = None
    fallback_used: bool = False


class LLMRouter:
    """
    Intelligent LLM routing with tiered fallback and quality checks.

    Decision flow:
    1. Classify task complexity and requirements
    2. Select tier based on complexity + quality needs
    3. Try selected tier with quality check
    4. Fallback to higher tier if quality fails
    5. Track costs and success rates
    """

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = Path(config_path) if config_path else Path(__file__).parent / "llm_config.yaml"
        self.config = self._load_config()
        self.models = self._initialize_models()
        self.usage_log: List[UsageStats] = []

    def _load_config(self) -> Dict:
        """Load configuration from YAML"""
        if self.config_path.exists():
            with open(self.config_path) as f:
                return yaml.safe_load(f)
        else:
            # Default configuration
            return self._default_config()

    def _default_config(self) -> Dict:
        """Default configuration if no file exists"""
        return {
            "tiers": {
                "local_fast": {
                    "models": [
                        {
                            "name": "phi3-mini",
                            "provider": "ollama",
                            "model_id": "phi3:mini",
                            "max_tokens": 2048,
                            "cost_per_1k_tokens": 0.0,
                            "enabled": True
                        },
                        {
                            "name": "llama3-8b",
                            "provider": "ollama",
                            "model_id": "llama3:8b",
                            "max_tokens": 4096,
                            "cost_per_1k_tokens": 0.0,
                            "enabled": True
                        }
                    ]
                },
                "local_quality": {
                    "models": [
                        {
                            "name": "llama3-70b",
                            "provider": "vllm",
                            "model_id": "meta-llama/Meta-Llama-3-70B-Instruct",
                            "endpoint": "http://localhost:8000/v1/completions",
                            "max_tokens": 8192,
                            "cost_per_1k_tokens": 0.0,
                            "enabled": False
                        },
                        {
                            "name": "mixtral-8x7b",
                            "provider": "ollama",
                            "model_id": "mixtral:8x7b",
                            "max_tokens": 8192,
                            "cost_per_1k_tokens": 0.0,
                            "enabled": True
                        }
                    ]
                },
                "api_fallback": {
                    "models": [
                        {
                            "name": "claude-sonnet-4.5",
                            "provider": "claude",
                            "model_id": "claude-sonnet-4-5-20250929",
                            "max_tokens": 8192,
                            "cost_per_1k_tokens": 3.0,  # $3 per 1M input tokens
                            "enabled": True
                        },
                        {
                            "name": "gpt-4o",
                            "provider": "openai",
                            "model_id": "gpt-4o",
                            "max_tokens": 4096,
                            "cost_per_1k_tokens": 2.5,
                            "enabled": True
                        }
                    ]
                }
            },
            "routing": {
                "prefer_local": True,
                "quality_threshold": 0.7,
                "max_fallback_attempts": 2,
                "enable_caching": True
            }
        }

    def _initialize_models(self) -> Dict[Tier, List[ModelConfig]]:
        """Initialize model configurations from config"""
        models = {}

        for tier_name, tier_config in self.config.get("tiers", {}).items():
            tier = Tier(tier_name)
            models[tier] = []

            for model_data in tier_config.get("models", []):
                if not model_data.get("enabled", True):
                    continue

                model = ModelConfig(
                    name=model_data["name"],
                    tier=tier,
                    provider=model_data["provider"],
                    model_id=model_data["model_id"],
                    max_tokens=model_data.get("max_tokens", 4096),
                    temperature=model_data.get("temperature", 0.7),
                    timeout=model_data.get("timeout", 60),
                    cost_per_1k_tokens=model_data.get("cost_per_1k_tokens", 0.0),
                    context_window=model_data.get("context_window", 8192),
                    enabled=model_data.get("enabled", True),
                    endpoint=model_data.get("endpoint")
                )
                models[tier].append(model)

        return models

    def classify_task(self, action_type: str, context: Dict[str, Any]) -> ActionComplexity:
        """
        Classify task complexity based on action type and context.

        Simple tasks:
        - Memory classification (type, topic extraction)
        - Yes/no decisions
        - Simple text extraction
        - Basic validation

        Moderate tasks:
        - Code generation
        - File editing
        - Multi-step reasoning
        - Summarization

        Complex tasks:
        - Orchestration (spawn_daemon with complex objectives)
        - Error recovery and debugging
        - Critical decisions affecting system state
        - Multi-file refactoring
        """

        # Simple classification tasks
        simple_actions = {
            "classify_memory", "extract_topic", "validate_format",
            "yes_no_decision", "tag_classification"
        }

        # Moderate generation tasks
        moderate_actions = {
            "write_memory", "read_file", "search_text", "list_files",
            "git_log", "git_diff", "git_status", "run", "check_deps",
            "edit_file", "summarize", "consolidate"
        }

        # Complex orchestration tasks
        complex_actions = {
            "spawn_daemon", "orch_status", "exec", "http_request"
        }

        if action_type in simple_actions:
            return ActionComplexity.SIMPLE
        elif action_type in complex_actions:
            return ActionComplexity.COMPLEX
        elif action_type in moderate_actions:
            # Check context for additional complexity signals
            if context.get("requires_reasoning"):
                return ActionComplexity.MODERATE
            if context.get("multi_file"):
                return ActionComplexity.COMPLEX
            return ActionComplexity.MODERATE

        # Default to moderate for unknown actions
        return ActionComplexity.MODERATE

    def select_tier(
        self,
        complexity: ActionComplexity,
        quality_critical: bool = False,
        prefer_local: bool = True
    ) -> Tier:
        """
        Select appropriate tier based on complexity and requirements.

        Routing strategy:
        - SIMPLE + local preference → LOCAL_FAST
        - MODERATE + local preference → LOCAL_QUALITY
        - COMPLEX or quality_critical → API_FALLBACK (unless prefer_local=False and local quality exists)
        """

        if quality_critical:
            # Critical tasks always use API fallback
            return Tier.API_FALLBACK

        if complexity == ActionComplexity.SIMPLE:
            return Tier.LOCAL_FAST if prefer_local else Tier.LOCAL_QUALITY

        elif complexity == ActionComplexity.MODERATE:
            if prefer_local and self.models.get(Tier.LOCAL_QUALITY):
                return Tier.LOCAL_QUALITY
            return Tier.API_FALLBACK

        else:  # COMPLEX
            return Tier.API_FALLBACK

    def route(
        self,
        prompt: str,
        action_type: str = "unknown",
        context: Optional[Dict[str, Any]] = None,
        quality_critical: bool = False
    ) -> RoutingDecision:
        """
        Route request to appropriate LLM tier.

        Returns RoutingDecision with selected model and fallback chain.
        """
        context = context or {}

        # Classify task
        complexity = self.classify_task(action_type, context)

        # Select tier
        prefer_local = self.config.get("routing", {}).get("prefer_local", True)
        tier = self.select_tier(complexity, quality_critical, prefer_local)

        # Get available models for tier
        available_models = self.models.get(tier, [])
        if not available_models:
            # Fallback to API if no local models
            tier = Tier.API_FALLBACK
            available_models = self.models.get(tier, [])

        if not available_models:
            raise RuntimeError(f"No models available for tier {tier}")

        # Select first available model
        model = available_models[0]

        # Build fallback chain
        fallback_chain = []
        if tier == Tier.LOCAL_FAST:
            fallback_chain = self.models.get(Tier.LOCAL_QUALITY, [])[:1] + self.models.get(Tier.API_FALLBACK, [])[:1]
        elif tier == Tier.LOCAL_QUALITY:
            fallback_chain = self.models.get(Tier.API_FALLBACK, [])[:1]

        # Estimate cost
        estimated_tokens = len(prompt.split()) * 1.3  # Rough estimate
        estimated_cost = (estimated_tokens / 1000) * model.cost_per_1k_tokens

        reason = f"Complexity={complexity.value}, Tier={tier.value}, Critical={quality_critical}"

        return RoutingDecision(
            tier=tier,
            model=model,
            reason=reason,
            estimated_cost=estimated_cost,
            fallback_chain=fallback_chain
        )

    def call_model(self, model: ModelConfig, prompt: str, **kwargs) -> Tuple[str, UsageStats]:
        """
        Call specific model and return response with usage stats.
        """
        start_time = time.time()

        try:
            if model.provider == "ollama":
                response = self._call_ollama(model, prompt, **kwargs)
            elif model.provider == "vllm":
                response = self._call_vllm(model, prompt, **kwargs)
            elif model.provider == "claude":
                response = self._call_claude(model, prompt, **kwargs)
            elif model.provider == "openai":
                response = self._call_openai(model, prompt, **kwargs)
            else:
                raise ValueError(f"Unknown provider: {model.provider}")

            latency_ms = int((time.time() - start_time) * 1000)

            # Estimate tokens (rough approximation)
            prompt_tokens = len(prompt.split()) * 1.3
            completion_tokens = len(response.split()) * 1.3
            cost = ((prompt_tokens + completion_tokens) / 1000) * model.cost_per_1k_tokens

            stats = UsageStats(
                tier=model.tier,
                model_name=model.name,
                prompt_tokens=int(prompt_tokens),
                completion_tokens=int(completion_tokens),
                cost=cost,
                latency_ms=latency_ms,
                success=True
            )

            return response, stats

        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            logger.error(f"Model call failed: {e}")

            stats = UsageStats(
                tier=model.tier,
                model_name=model.name,
                prompt_tokens=0,
                completion_tokens=0,
                cost=0.0,
                latency_ms=latency_ms,
                success=False
            )

            return f"ERROR: {str(e)}", stats

    def _call_ollama(self, model: ModelConfig, prompt: str, **kwargs) -> str:
        """Call Ollama local model"""
        cmd = [
            "ollama", "run", model.model_id,
            "--temperature", str(model.temperature),
        ]

        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=model.timeout
        )

        if result.returncode != 0:
            raise RuntimeError(f"Ollama call failed: {result.stderr}")

        return result.stdout.strip()

    def _call_vllm(self, model: ModelConfig, prompt: str, **kwargs) -> str:
        """Call vLLM endpoint"""
        import requests

        response = requests.post(
            model.endpoint,
            json={
                "model": model.model_id,
                "prompt": prompt,
                "max_tokens": model.max_tokens,
                "temperature": model.temperature
            },
            timeout=model.timeout
        )

        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["text"]

    def _call_claude(self, model: ModelConfig, prompt: str, **kwargs) -> str:
        """Call Claude API via CLI"""
        cmd = ["claude", "--model", model.model_id, "-p", prompt]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=model.timeout
        )

        if result.returncode != 0:
            raise RuntimeError(f"Claude call failed: {result.stderr}")

        return (result.stdout + result.stderr).strip()

    def _call_openai(self, model: ModelConfig, prompt: str, **kwargs) -> str:
        """Call OpenAI API"""
        try:
            from openai import OpenAI
            client = OpenAI()

            response = client.chat.completions.create(
                model=model.model_id,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=model.max_tokens,
                temperature=model.temperature
            )

            return response.choices[0].message.content

        except ImportError:
            raise RuntimeError("openai package not installed. Run: pip install openai")

    def check_quality(self, response: str, action_type: str, context: Dict[str, Any]) -> QualityCheckResult:
        """
        Assess response quality using heuristics and self-critique.

        Quality checks:
        1. Format validation (JSON for actions, code for edits)
        2. Completeness (has required fields)
        3. Coherence (no contradictions, hallucinations)
        4. Self-critique (use fast local model to validate)
        """

        issues = []
        confidence = 1.0

        # 1. Format validation
        if action_type in ["write_memory", "edit_file", "spawn_daemon"]:
            try:
                # Try to extract JSON
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    json.loads(json_match.group())
                else:
                    issues.append("No valid JSON found")
                    confidence -= 0.3
            except json.JSONDecodeError:
                issues.append("Invalid JSON format")
                confidence -= 0.3

        # 2. Completeness check
        if len(response.strip()) < 20:
            issues.append("Response too short")
            confidence -= 0.2

        # 3. Hallucination detection (simple heuristics)
        hallucination_markers = [
            "I don't have access",
            "I cannot",
            "As an AI",
            "I apologize",
            "I'm not sure"
        ]
        if any(marker.lower() in response.lower() for marker in hallucination_markers):
            issues.append("Possible hallucination or refusal")
            confidence -= 0.4

        # 4. Self-critique (optional, uses fast model)
        if confidence < 0.7 and self.models.get(Tier.LOCAL_FAST):
            critique_prompt = f"""Evaluate this response for quality issues:

Response: {response[:500]}
Task: {action_type}

Is this a valid, useful response? Answer YES or NO with brief reason."""

            try:
                fast_model = self.models[Tier.LOCAL_FAST][0]
                critique, _ = self.call_model(fast_model, critique_prompt)
                if "NO" in critique.upper():
                    issues.append(f"Self-critique failed: {critique}")
                    confidence -= 0.2
            except Exception as e:
                logger.warning(f"Self-critique failed: {e}")

        passed = confidence >= self.config.get("routing", {}).get("quality_threshold", 0.7)
        reasoning = f"Confidence: {confidence:.2f}, Issues: {len(issues)}"

        return QualityCheckResult(
            passed=passed,
            confidence=confidence,
            issues=issues,
            reasoning=reasoning
        )

    def route_with_quality_check(
        self,
        prompt: str,
        action_type: str = "unknown",
        context: Optional[Dict[str, Any]] = None,
        max_fallback_attempts: int = 2
    ) -> Tuple[str, RoutingDecision, QualityCheckResult, List[UsageStats]]:
        """
        Route with automatic fallback on quality failure.

        Returns:
            (response, routing_decision, quality_result, all_usage_stats)
        """
        context = context or {}
        all_stats = []

        # Initial routing
        decision = self.route(prompt, action_type, context)

        attempts = 0
        current_model = decision.model
        fallback_chain = [decision.model] + decision.fallback_chain

        while attempts < max_fallback_attempts and attempts < len(fallback_chain):
            logger.info(f"Attempt {attempts + 1}: Using {current_model.name} ({current_model.tier.value})")

            # Call model
            response, stats = self.call_model(current_model, prompt)
            all_stats.append(stats)

            if not stats.success:
                logger.warning(f"Model call failed: {current_model.name}")
                attempts += 1
                if attempts < len(fallback_chain):
                    current_model = fallback_chain[attempts]
                    stats.fallback_used = True
                continue

            # Quality check
            quality = self.check_quality(response, action_type, context)
            stats.quality_score = quality.confidence

            if quality.passed:
                logger.info(f"Quality check passed: {quality.confidence:.2f}")
                self.usage_log.extend(all_stats)
                return response, decision, quality, all_stats

            logger.warning(f"Quality check failed: {quality.reasoning}")
            logger.warning(f"Issues: {quality.issues}")

            # Try next tier
            attempts += 1
            if attempts < len(fallback_chain):
                current_model = fallback_chain[attempts]
                stats.fallback_used = True

        # All attempts exhausted, return best attempt
        logger.error("All fallback attempts exhausted")
        self.usage_log.extend(all_stats)
        return response, decision, quality, all_stats

    def get_cost_summary(self) -> Dict[str, Any]:
        """Get cost summary and savings analysis"""
        total_cost = sum(s.cost for s in self.usage_log)

        # Calculate hypothetical API-only cost
        api_only_cost = 0.0
        for stat in self.usage_log:
            if stat.tier != Tier.API_FALLBACK:
                # Estimate what it would have cost with Claude
                api_model = next(
                    (m for m in self.models.get(Tier.API_FALLBACK, []) if m.name.startswith("claude")),
                    None
                )
                if api_model:
                    tokens = stat.prompt_tokens + stat.completion_tokens
                    api_only_cost += (tokens / 1000) * api_model.cost_per_1k_tokens
            else:
                api_only_cost += stat.cost

        savings = api_only_cost - total_cost
        savings_pct = (savings / api_only_cost * 100) if api_only_cost > 0 else 0

        # Success rates by tier
        tier_stats = {}
        for tier in Tier:
            tier_calls = [s for s in self.usage_log if s.tier == tier]
            if tier_calls:
                tier_stats[tier.value] = {
                    "calls": len(tier_calls),
                    "success_rate": sum(1 for s in tier_calls if s.success) / len(tier_calls),
                    "avg_quality": sum(s.quality_score or 0 for s in tier_calls) / len(tier_calls),
                    "total_cost": sum(s.cost for s in tier_calls)
                }

        return {
            "total_cost": total_cost,
            "api_only_cost": api_only_cost,
            "savings": savings,
            "savings_pct": savings_pct,
            "total_calls": len(self.usage_log),
            "tier_stats": tier_stats
        }

    def export_usage_log(self, output_path: str):
        """Export usage log to JSON for analysis"""
        data = []
        for stat in self.usage_log:
            data.append({
                "tier": stat.tier.value,
                "model_name": stat.model_name,
                "prompt_tokens": stat.prompt_tokens,
                "completion_tokens": stat.completion_tokens,
                "cost": stat.cost,
                "latency_ms": stat.latency_ms,
                "success": stat.success,
                "quality_score": stat.quality_score,
                "fallback_used": stat.fallback_used
            })

        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)


def main():
    """CLI for testing router"""
    import argparse

    parser = argparse.ArgumentParser(description="LLM Router CLI")
    parser.add_argument("--config", default="llm_config.yaml", help="Config file")
    parser.add_argument("--prompt", required=True, help="Prompt to route")
    parser.add_argument("--action", default="unknown", help="Action type")
    parser.add_argument("--quality-check", action="store_true", help="Enable quality check with fallback")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    router = LLMRouter(config_path=args.config)

    if args.quality_check:
        response, decision, quality, stats = router.route_with_quality_check(
            args.prompt,
            action_type=args.action
        )
        print(f"\n=== Response ===\n{response}\n")
        print(f"\n=== Routing ===")
        print(f"Tier: {decision.tier.value}")
        print(f"Model: {decision.model.name}")
        print(f"Reason: {decision.reason}")
        print(f"\n=== Quality ===")
        print(f"Passed: {quality.passed}")
        print(f"Confidence: {quality.confidence:.2f}")
        print(f"Issues: {quality.issues}")
        print(f"\n=== Stats ===")
        for i, stat in enumerate(stats):
            print(f"Attempt {i+1}: {stat.model_name} - ${stat.cost:.6f} - {stat.latency_ms}ms")
    else:
        decision = router.route(args.prompt, action_type=args.action)
        response, stats = router.call_model(decision.model, args.prompt)

        print(f"\n=== Response ===\n{response}\n")
        print(f"\n=== Routing ===")
        print(f"Tier: {decision.tier.value}")
        print(f"Model: {decision.model.name}")
        print(f"Cost: ${stats.cost:.6f}")
        print(f"Latency: {stats.latency_ms}ms")

    # Print cost summary
    summary = router.get_cost_summary()
    print(f"\n=== Cost Summary ===")
    print(f"Total Cost: ${summary['total_cost']:.6f}")
    print(f"API-Only Cost: ${summary['api_only_cost']:.6f}")
    print(f"Savings: ${summary['savings']:.6f} ({summary['savings_pct']:.1f}%)")


if __name__ == "__main__":
    main()
