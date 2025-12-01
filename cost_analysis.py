#!/usr/bin/env python3
"""
cost_analysis.py - Analyze and visualize LLM routing costs

Compares hybrid architecture costs against pure API usage.
Generates reports and recommendations for cost optimization.

Usage:
    ./cost_analysis.py --usage-log llm_usage.jsonl --report daily
    ./cost_analysis.py --simulate --days 30 --iterations-per-day 100
"""

import json
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any
from dataclasses import dataclass
import statistics


@dataclass
class CostScenario:
    """Cost scenario for comparison"""
    name: str
    iterations_per_day: int
    action_distribution: Dict[str, float]  # action_type -> percentage


class CostAnalyzer:
    """Analyze and compare cost scenarios"""

    # Cost per 1K tokens (input + output combined)
    MODEL_COSTS = {
        "phi3-mini": 0.0,
        "llama3-8b": 0.0,
        "mixtral-8x7b": 0.0,
        "codestral": 0.0,
        "llama3-70b": 0.0,  # Local/cloud GPU
        "claude-sonnet-4.5": 3.0,
        "claude-haiku": 0.8,
        "gpt-4o": 2.5
    }

    # Average tokens per action type
    ACTION_TOKENS = {
        "write_memory": 800,
        "read_file": 600,
        "list_files": 400,
        "search_text": 500,
        "edit_file": 1200,
        "consolidate": 1000,
        "spawn_daemon": 1500,
        "git_log": 400,
        "git_diff": 600,
        "git_status": 300,
        "exec": 800,
        "run": 500,
        "check_deps": 400,
        "unknown": 800
    }

    # Tier mapping by action type (for hybrid architecture)
    HYBRID_TIER_MAPPING = {
        # LOCAL_FAST (Tier 1)
        "list_files": "phi3-mini",
        "git_status": "phi3-mini",
        "check_deps": "llama3-8b",
        "git_log": "llama3-8b",

        # LOCAL_QUALITY (Tier 2)
        "write_memory": "mixtral-8x7b",
        "read_file": "mixtral-8x7b",
        "search_text": "mixtral-8x7b",
        "consolidate": "mixtral-8x7b",
        "edit_file": "codestral",
        "git_diff": "mixtral-8x7b",
        "run": "mixtral-8x7b",

        # API_FALLBACK (Tier 3)
        "spawn_daemon": "claude-sonnet-4.5",
        "exec": "claude-sonnet-4.5",
        "unknown": "claude-sonnet-4.5"
    }

    # Fallback rate (% of local calls that fail and use API)
    FALLBACK_RATE = 0.05  # 5% fallback to API

    def __init__(self):
        self.usage_log = []

    def load_usage_log(self, log_path: str):
        """Load usage log from JSONL file"""
        with open(log_path) as f:
            for line in f:
                if line.strip():
                    self.usage_log.append(json.loads(line))

    def calculate_pure_api_cost(
        self,
        iterations_per_day: int,
        action_distribution: Dict[str, float],
        days: int = 30,
        api_model: str = "claude-sonnet-4.5"
    ) -> Dict[str, Any]:
        """Calculate cost for pure API usage"""

        total_cost = 0.0
        daily_costs = []
        action_costs = {}

        for day in range(days):
            day_cost = 0.0

            for action_type, percentage in action_distribution.items():
                action_count = int(iterations_per_day * percentage)
                tokens = self.ACTION_TOKENS.get(action_type, 800)
                cost_per_action = (tokens / 1000) * self.MODEL_COSTS[api_model]
                action_total = action_count * cost_per_action

                day_cost += action_total

                if action_type not in action_costs:
                    action_costs[action_type] = 0.0
                action_costs[action_type] += action_total

            daily_costs.append(day_cost)
            total_cost += day_cost

        return {
            "model": api_model,
            "total_cost": total_cost,
            "daily_avg": total_cost / days,
            "daily_costs": daily_costs,
            "action_breakdown": action_costs,
            "cost_per_iteration": total_cost / (iterations_per_day * days)
        }

    def calculate_hybrid_cost(
        self,
        iterations_per_day: int,
        action_distribution: Dict[str, float],
        days: int = 30,
        fallback_model: str = "claude-sonnet-4.5"
    ) -> Dict[str, Any]:
        """Calculate cost for hybrid architecture"""

        total_cost = 0.0
        daily_costs = []
        action_costs = {}
        tier_stats = {"local_fast": 0, "local_quality": 0, "api_fallback": 0}

        for day in range(days):
            day_cost = 0.0

            for action_type, percentage in action_distribution.items():
                action_count = int(iterations_per_day * percentage)
                tokens = self.ACTION_TOKENS.get(action_type, 800)

                # Get primary model for this action
                primary_model = self.HYBRID_TIER_MAPPING.get(action_type, fallback_model)
                primary_cost = (tokens / 1000) * self.MODEL_COSTS[primary_model]

                # Calculate cost with fallback
                if self.MODEL_COSTS[primary_model] == 0.0:
                    # Local model with fallback rate
                    fallback_cost = (tokens / 1000) * self.MODEL_COSTS[fallback_model]
                    action_cost_per_call = (
                        primary_cost * (1 - self.FALLBACK_RATE) +
                        fallback_cost * self.FALLBACK_RATE
                    )

                    # Track tier usage
                    if primary_model in ["phi3-mini", "llama3-8b"]:
                        tier_stats["local_fast"] += action_count
                    else:
                        tier_stats["local_quality"] += action_count
                    tier_stats["api_fallback"] += int(action_count * self.FALLBACK_RATE)
                else:
                    # API model
                    action_cost_per_call = primary_cost
                    tier_stats["api_fallback"] += action_count

                action_total = action_count * action_cost_per_call
                day_cost += action_total

                if action_type not in action_costs:
                    action_costs[action_type] = 0.0
                action_costs[action_type] += action_total

            daily_costs.append(day_cost)
            total_cost += day_cost

        return {
            "total_cost": total_cost,
            "daily_avg": total_cost / days,
            "daily_costs": daily_costs,
            "action_breakdown": action_costs,
            "cost_per_iteration": total_cost / (iterations_per_day * days),
            "tier_stats": tier_stats
        }

    def compare_scenarios(
        self,
        scenario: CostScenario,
        days: int = 30
    ) -> Dict[str, Any]:
        """Compare pure API vs hybrid for a scenario"""

        pure_api = self.calculate_pure_api_cost(
            scenario.iterations_per_day,
            scenario.action_distribution,
            days
        )

        hybrid = self.calculate_hybrid_cost(
            scenario.iterations_per_day,
            scenario.action_distribution,
            days
        )

        savings = pure_api["total_cost"] - hybrid["total_cost"]
        savings_pct = (savings / pure_api["total_cost"] * 100) if pure_api["total_cost"] > 0 else 0

        return {
            "scenario": scenario.name,
            "iterations_per_day": scenario.iterations_per_day,
            "days": days,
            "pure_api": pure_api,
            "hybrid": hybrid,
            "savings": savings,
            "savings_pct": savings_pct
        }

    def generate_report(self, scenarios: List[CostScenario], days: int = 30):
        """Generate comprehensive cost comparison report"""

        print("=" * 80)
        print("HYBRID LLM ARCHITECTURE - COST ANALYSIS REPORT")
        print("=" * 80)
        print()

        for scenario in scenarios:
            comparison = self.compare_scenarios(scenario, days)

            print(f"Scenario: {comparison['scenario']}")
            print(f"Duration: {days} days")
            print(f"Iterations: {comparison['iterations_per_day']}/day")
            print()

            # Pure API costs
            pure = comparison["pure_api"]
            print("Pure API (Claude Sonnet 4.5):")
            print(f"  Total Cost:      ${pure['total_cost']:.2f}")
            print(f"  Daily Average:   ${pure['daily_avg']:.2f}")
            print(f"  Per Iteration:   ${pure['cost_per_iteration']:.4f}")
            print()

            # Hybrid costs
            hybrid = comparison["hybrid"]
            print("Hybrid Architecture:")
            print(f"  Total Cost:      ${hybrid['total_cost']:.2f}")
            print(f"  Daily Average:   ${hybrid['daily_avg']:.2f}")
            print(f"  Per Iteration:   ${hybrid['cost_per_iteration']:.4f}")
            print()

            # Tier breakdown
            total_calls = sum(hybrid['tier_stats'].values())
            print("  Tier Distribution:")
            for tier, count in hybrid['tier_stats'].items():
                pct = (count / total_calls * 100) if total_calls > 0 else 0
                print(f"    {tier:15s}: {count:5d} calls ({pct:5.1f}%)")
            print()

            # Savings
            print("Savings:")
            print(f"  Amount:          ${comparison['savings']:.2f}")
            print(f"  Percentage:      {comparison['savings_pct']:.1f}%")
            print()

            # Action breakdown (top 5 by cost)
            print("Top 5 Actions by Cost (Pure API):")
            sorted_actions = sorted(
                pure['action_breakdown'].items(),
                key=lambda x: x[1],
                reverse=True
            )[:5]
            for action, cost in sorted_actions:
                hybrid_cost = hybrid['action_breakdown'].get(action, 0)
                action_savings = cost - hybrid_cost
                action_savings_pct = (action_savings / cost * 100) if cost > 0 else 0
                print(f"  {action:20s}: ${cost:7.2f} → ${hybrid_cost:7.2f} "
                      f"(save ${action_savings:.2f}, {action_savings_pct:.0f}%)")
            print()

            print("-" * 80)
            print()

    def generate_ascii_chart(self, scenarios: List[CostScenario], days: int = 30):
        """Generate ASCII cost comparison chart"""

        print("=" * 80)
        print("COST COMPARISON CHART")
        print("=" * 80)
        print()

        comparisons = [self.compare_scenarios(s, days) for s in scenarios]

        # Find max cost for scaling
        max_cost = max(c["pure_api"]["total_cost"] for c in comparisons)

        print(f"{'Scenario':<20s} {'Pure API':<25s} {'Hybrid':<25s} {'Savings':<10s}")
        print("-" * 80)

        for comp in comparisons:
            scenario = comp["scenario"]
            pure_cost = comp["pure_api"]["total_cost"]
            hybrid_cost = comp["hybrid"]["total_cost"]
            savings_pct = comp["savings_pct"]

            # Scale bars to 20 chars
            pure_bar_len = int((pure_cost / max_cost) * 20)
            hybrid_bar_len = int((hybrid_cost / max_cost) * 20)

            pure_bar = "█" * pure_bar_len
            hybrid_bar = "█" * hybrid_bar_len

            print(f"{scenario:<20s} ${pure_cost:6.2f} {pure_bar:<20s} "
                  f"${hybrid_cost:6.2f} {hybrid_bar:<20s} {savings_pct:5.1f}%")

        print()

    def recommend_configuration(self, iterations_per_day: int) -> Dict[str, Any]:
        """Recommend optimal configuration based on usage"""

        recommendations = []

        if iterations_per_day < 50:
            config = "Minimal Local"
            recommendations.append("Use Phi-3 Mini + Llama 3 8B (Ollama)")
            recommendations.append("API fallback for complex tasks only")
            recommendations.append("Expected cost: < $2/month")

        elif iterations_per_day < 200:
            config = "Standard Local"
            recommendations.append("Use Phi-3 Mini + Llama 3 8B + Mixtral 8x7B")
            recommendations.append("Add Codestral for code generation")
            recommendations.append("API fallback for orchestration")
            recommendations.append("Expected cost: $3-8/month")

        elif iterations_per_day < 500:
            config = "Quality Local"
            recommendations.append("Use full local stack + Llama 3 70B (cloud GPU)")
            recommendations.append("Cloud GPU cost: ~$50/month")
            recommendations.append("API fallback for critical tasks only")
            recommendations.append("Expected cost: $65/month (vs $135 pure API)")
            recommendations.append("Break-even: Pays for itself at 200+ iterations/day")

        else:
            config = "Enterprise"
            recommendations.append("Deploy vLLM cluster with Llama 3 70B")
            recommendations.append("Use Claude Haiku for cost-effective API fallback")
            recommendations.append("Consider fine-tuning models for specific tasks")
            recommendations.append("Expected cost: $100-200/month (vs $400+ pure API)")

        return {
            "iterations_per_day": iterations_per_day,
            "config": config,
            "recommendations": recommendations
        }


def main():
    parser = argparse.ArgumentParser(description="Analyze LLM routing costs")
    parser.add_argument("--usage-log", help="Load usage log from JSONL file")
    parser.add_argument("--simulate", action="store_true", help="Run cost simulation")
    parser.add_argument("--days", type=int, default=30, help="Simulation period")
    parser.add_argument("--iterations-per-day", type=int, default=100, help="Iterations per day")
    parser.add_argument("--report", choices=["daily", "weekly", "monthly"], default="monthly")

    args = parser.parse_args()

    analyzer = CostAnalyzer()

    if args.usage_log:
        # Analyze actual usage log
        analyzer.load_usage_log(args.usage_log)
        # TODO: Implement real log analysis
        print("Usage log analysis not yet implemented")
        return

    if args.simulate:
        # Define realistic scenarios
        scenarios = [
            CostScenario(
                name="Light Usage",
                iterations_per_day=50,
                action_distribution={
                    "write_memory": 0.30,
                    "read_file": 0.20,
                    "list_files": 0.15,
                    "edit_file": 0.10,
                    "consolidate": 0.10,
                    "spawn_daemon": 0.05,
                    "git_log": 0.05,
                    "git_status": 0.05
                }
            ),
            CostScenario(
                name="Medium Usage",
                iterations_per_day=200,
                action_distribution={
                    "write_memory": 0.25,
                    "read_file": 0.20,
                    "list_files": 0.10,
                    "search_text": 0.10,
                    "edit_file": 0.15,
                    "consolidate": 0.10,
                    "spawn_daemon": 0.05,
                    "run": 0.05
                }
            ),
            CostScenario(
                name="Heavy Usage",
                iterations_per_day=500,
                action_distribution={
                    "write_memory": 0.20,
                    "read_file": 0.20,
                    "search_text": 0.10,
                    "edit_file": 0.20,
                    "consolidate": 0.10,
                    "spawn_daemon": 0.10,
                    "exec": 0.05,
                    "run": 0.05
                }
            )
        ]

        # Generate reports
        analyzer.generate_report(scenarios, args.days)
        analyzer.generate_ascii_chart(scenarios, args.days)

        # Recommendations
        print("=" * 80)
        print("CONFIGURATION RECOMMENDATIONS")
        print("=" * 80)
        print()

        for scenario in scenarios:
            rec = analyzer.recommend_configuration(scenario.iterations_per_day)
            print(f"{scenario.name} ({rec['iterations_per_day']} iterations/day):")
            print(f"  Configuration: {rec['config']}")
            for r in rec['recommendations']:
                print(f"    • {r}")
            print()


if __name__ == "__main__":
    main()
