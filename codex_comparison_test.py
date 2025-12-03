#!/usr/bin/env python3
"""
Codex Variant Comparison Test

Compares Codex CLI variants on identical coding task:
- codex-mini-high: gpt-5.1-codex-mini with high effort
- codex-5.1-high: gpt-5.1 with high effort
- codex-max-high: gpt-5.1-codex-max with high effort
- codex-max-low: gpt-5.1-codex-max with low effort

Measures: time, output length, code structure quality
"""

import json
import time
import subprocess
import sys
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

# The coding task - same for all variants
CODING_TASK = """
Write a Python function called `parse_duration` that converts human-readable duration strings into total seconds.

Requirements:
1. Accept strings like "2h30m", "1d12h", "45m30s", "1w2d"
2. Support units: w(weeks), d(days), h(hours), m(minutes), s(seconds)
3. Handle edge cases: empty string (return 0), invalid format (raise ValueError)
4. Return an integer (total seconds)
5. Include type hints
6. Include docstring with examples

Example usage:
  parse_duration("2h30m") -> 9000
  parse_duration("1d") -> 86400
  parse_duration("1w2d") -> 777600

Just output the Python code, no explanations.
"""

# Variants to test
VARIANTS = [
    {"tier": "codex-mini-high", "model": "gpt-5.1-codex-mini", "effort": "high"},
    {"tier": "codex-5.1-high", "model": "gpt-5.1", "effort": "high"},
    {"tier": "codex-max-high", "model": "gpt-5.1-codex-max", "effort": "high"},
    {"tier": "codex-max-low", "model": "gpt-5.1-codex-max", "effort": "low"},
]


@dataclass
class TestResult:
    tier: str
    model: str
    effort: str
    time_sec: float
    output_len: int
    has_function: bool
    has_docstring: bool
    has_type_hints: bool
    has_error_handling: bool
    exit_code: int
    error: Optional[str] = None
    output: str = ""


def run_codex(model: str, effort: str, prompt: str, timeout: int = 300) -> tuple[str, float, int]:
    """Run codex CLI and return (output, time_sec, exit_code)"""
    cmd = ["codex", "exec", "-m", model, "-c", f"model_reasoning_effort={effort}", "--full-auto", prompt]

    start = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        elapsed = time.time() - start
        output = (result.stdout + result.stderr).strip()

        # Strip version banner if present
        if "OpenAI Codex" in output:
            lines = output.split("\n")
            # Find first non-banner line
            for i, line in enumerate(lines):
                if not line.startswith("OpenAI Codex") and not line.startswith("─"):
                    output = "\n".join(lines[i:])
                    break

        return output, elapsed, result.returncode
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {timeout}s", time.time() - start, -1
    except FileNotFoundError:
        return "Codex CLI not found", 0, -1
    except Exception as e:
        return f"ERROR: {e}", time.time() - start, -1


def analyze_output(output: str) -> dict:
    """Analyze code output for quality markers"""
    return {
        "has_function": "def parse_duration" in output,
        "has_docstring": '"""' in output or "'''" in output,
        "has_type_hints": "->" in output and ":" in output,
        "has_error_handling": "raise ValueError" in output or "raise" in output.lower(),
    }


def run_test(variant: dict) -> TestResult:
    """Run test for a single variant"""
    print(f"\n{'='*60}")
    print(f"Testing: {variant['tier']}")
    print(f"  Model: {variant['model']}, Effort: {variant['effort']}")
    print(f"{'='*60}")

    output, time_sec, exit_code = run_codex(variant["model"], variant["effort"], CODING_TASK)

    analysis = analyze_output(output)

    result = TestResult(
        tier=variant["tier"],
        model=variant["model"],
        effort=variant["effort"],
        time_sec=round(time_sec, 1),
        output_len=len(output),
        has_function=analysis["has_function"],
        has_docstring=analysis["has_docstring"],
        has_type_hints=analysis["has_type_hints"],
        has_error_handling=analysis["has_error_handling"],
        exit_code=exit_code,
        error=output if exit_code != 0 and len(output) < 200 else None,
        output=output[:2000] if exit_code == 0 else "",
    )

    print(f"  Time: {result.time_sec}s")
    print(f"  Output length: {result.output_len} chars")
    print(f"  Has function: {result.has_function}")
    print(f"  Has docstring: {result.has_docstring}")
    print(f"  Has type hints: {result.has_type_hints}")
    print(f"  Has error handling: {result.has_error_handling}")

    if result.error:
        print(f"  ERROR: {result.error[:100]}")

    return result


def main():
    print("\n" + "="*70)
    print("CODEX VARIANT COMPARISON TEST")
    print("Task: Implement parse_duration() function")
    print("="*70)

    results = []

    for variant in VARIANTS:
        result = run_test(variant)
        results.append(result)

    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print(f"\n{'Tier':<18} {'Time':>8} {'Len':>6} {'Func':>5} {'Doc':>5} {'Type':>5} {'Err':>5}")
    print("-" * 60)

    for r in results:
        func = "✓" if r.has_function else "✗"
        doc = "✓" if r.has_docstring else "✗"
        typ = "✓" if r.has_type_hints else "✗"
        err = "✓" if r.has_error_handling else "✗"
        print(f"{r.tier:<18} {r.time_sec:>7.1f}s {r.output_len:>6} {func:>5} {doc:>5} {typ:>5} {err:>5}")

    # Find winner
    print("\n" + "-" * 60)

    # Quality score = sum of booleans
    def quality_score(r):
        return sum([r.has_function, r.has_docstring, r.has_type_hints, r.has_error_handling])

    best_quality = max(results, key=quality_score)
    fastest = min(results, key=lambda r: r.time_sec if r.exit_code == 0 else 9999)

    print(f"\nBest quality: {best_quality.tier} (score: {quality_score(best_quality)}/4)")
    print(f"Fastest: {fastest.tier} ({fastest.time_sec}s)")

    # Save results
    output_file = Path("/tmp/codex_comparison_results.json")
    with open(output_file, "w") as f:
        json.dump([asdict(r) for r in results], f, indent=2)
    print(f"\nResults saved to: {output_file}")

    # Save individual outputs
    for r in results:
        if r.output:
            out_file = Path(f"/tmp/codex_output_{r.tier.replace('-', '_')}.py")
            with open(out_file, "w") as f:
                f.write(f"# Generated by {r.tier} ({r.model}, effort={r.effort})\n")
                f.write(f"# Time: {r.time_sec}s\n\n")
                f.write(r.output)
            print(f"Output saved: {out_file}")

    return results


if __name__ == "__main__":
    results = main()
