#!/usr/bin/env python3
"""
affordance_eval.py - Basic evaluation for affordance sandbox experiments.

This script reads RESULT glyphs produced by affordance_sandbox.py
    (topic=affordance-reasoning, type=R)
from memory.db and computes simple, reproducible metrics per
  - scenario id
  - model tier / condition

It does not try to be perfect; instead it provides:
  - a crude "match" heuristic for whether the model captured the intended
    affordance, based on scenario-specific keywords in the *model response*
    section only, and
  - a geometry coverage score based on whether key entity symbols appear
    in the model response (e.g., S1/F1/C1 for the aperture scenario).

Usage examples:
    python affordance_eval.py
    python affordance_eval.py --scenario ball_ring_game
    python affordance_eval.py --tier claude --json
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


SCRIPT_DIR = Path(__file__).parent
DEFAULT_DB = os.environ.get("MEMORY_DB", str(SCRIPT_DIR / "memory.db"))


SCENARIO_CONFIG = {
    "ball_ring_game": {
        "entities": ["S1", "F1", "C1"],
        "match_patterns": [
            r"pass(?:es)? through (?:the )?opening",
            r"through F1",
            r"through .*aperture",
            r"elevated aperture",
            r"propel(?:s|led|ling)? .* through",
            r"launch(?:es|ed|ing)? .* through",
            r"project(?:s|ed|ing)? .* through",
            r"trajectory .* through",
            r"into C1",
            r"captur(?:e|ed|es) in C1",
            r"retained in (?:the )?capture region",
            r"retention (?:zone|basin|recess)",
        ],
    },
    "rod_line_water": {
        "entities": ["L1", "T1", "H1", "R"],
        "match_patterns": [
            r"interlock[s]? with .*unit",
            r"mechanical(?:ly)? engage[s]? .*unit",
            r"extract[s]? .*unit from R",
            r"transfer[s]? .*unit from inside R to outside R",
            r"pull[s]? .*unit out of R",
        ],
    },
    "rails_and_cube": {
        "entities": ["B1", "G1", "G2", "G3"],
        "match_patterns": [
            r"travels? along (?:the )?guides",
            r"moves? along the three guides",
            r"from (?:the )?platform end to (?:the )?opposite end",
            r"without losing contact with (?:the )?guides",
        ],
    },
    "jump_rope": {
        "entities": ["R1", "H1", "H2", "P"],
        "match_patterns": [
            r"rotate[s]? .*loop",
            r"loop R1 .*rotate[s]?",
            r"sweep[s]? under (?:the )?feet",
            r"pass(?:es)? beneath .*feet",
            r"step[s]? clear of R1",
            r"without R1 striking (?:the )?body",
        ],
    },
}


@dataclass
class Episode:
    scenario_id: str
    tier: str
    text: str  # full text field from DB
    model_response: str  # extracted "Model response" section


@dataclass
class Metrics:
    n: int = 0
    match_count: int = 0
    coverage_sum: int = 0
    coverage_max: int = 0

    def add(self, match: bool, coverage: int, coverage_max: int) -> None:
        self.n += 1
        self.coverage_sum += coverage
        self.coverage_max = coverage_max
        if match:
            self.match_count += 1

    def as_dict(self) -> Dict[str, float]:
        return {
            "episodes": self.n,
            "match_rate": (self.match_count / self.n) if self.n else 0.0,
            "avg_coverage": (self.coverage_sum / self.n) if self.n else 0.0,
            "coverage_max": float(self.coverage_max),
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate affordance sandbox runs stored in memory.db"
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=f"Path to SQLite database (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--scenario",
        help="Filter to a specific scenario id (e.g., ball_ring_game)",
    )
    parser.add_argument(
        "--tier",
        help="Filter to a specific tier/condition (e.g., claude, naive, affordance-memory)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON (per scenario/tier)",
    )
    return parser.parse_args()


def extract_scenario_and_tier(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Parse scenario id and tier from the structured summary text produced
    by affordance_sandbox.log_result_to_memory.
    """
    scenario_id = None
    tier = None

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- Scenario:"):
            # Example: "- Scenario: ball_ring_game (Convex unit and elevated aperture)"
            # Take the first token after the colon up to the next space or "("
            rest = line.split(":", 1)[1].strip()
            token = rest.split()[0]
            scenario_id = token.strip()
        elif line.startswith("- Tier:"):
            tier = line.split(":", 1)[1].strip()

    return scenario_id, tier


def extract_model_response(text: str) -> str:
    """
    Extract only the "Model response" section from the stored summary
    text between "Model response:" and "Intended affordance".
    """
    lower = text.lower()
    start_key = "model response:"
    end_key = "intended affordance"

    start_idx = lower.find(start_key)
    if start_idx == -1:
        return text
    start_idx += len(start_key)

    end_idx = lower.find(end_key, start_idx)
    if end_idx == -1:
        body = text[start_idx:]
    else:
        body = text[start_idx:end_idx]

    return body.strip()


def load_episodes(db_path: str, scenario_filter: Optional[str], tier_filter: Optional[str]) -> List[Episode]:
    db = Path(db_path)
    if not db.exists():
        print(f"ERROR: DB not found at {db}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db))
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            SELECT text
            FROM chunks
            WHERE anchor_type = 'R'
              AND anchor_topic = 'affordance-reasoning'
              AND text IS NOT NULL
            ORDER BY timestamp DESC
            """
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    episodes: List[Episode] = []
    for (text,) in rows:
        scenario_id, tier = extract_scenario_and_tier(text or "")
        if not scenario_id or not tier:
            continue
        if scenario_filter and scenario_id != scenario_filter:
            continue
        if tier_filter and tier != tier_filter:
            continue
        model_resp = extract_model_response(text or "")
        episodes.append(Episode(scenario_id=scenario_id, tier=tier, text=text, model_response=model_resp))

    return episodes


def compute_episode_metrics(ep: Episode) -> Tuple[bool, int, int]:
    """
    Compute (match, coverage, coverage_max) for a single episode using
    SCENARIO_CONFIG. If no config is available, returns zeros.
    """
    cfg = SCENARIO_CONFIG.get(ep.scenario_id)
    if not cfg:
        return False, 0, 0

    body = ep.model_response
    lower = body.lower()

    # Coverage: how many of the key entity symbols appear in the model response?
    entities = cfg.get("entities", [])
    coverage = sum(1 for e in entities if e in body)
    coverage_max = len(entities)

    # Match heuristic: does any of the scenario-specific patterns appear?
    match_patterns = cfg.get("match_patterns", [])
    matched = any(re.search(pat, lower) for pat in match_patterns)

    # Relaxed action heuristic for naive tier: count any action-ish verb as a "hit"
    # so we can see whether the naive model is at least suggesting interactions.
    if not matched and ep.tier == "naive":
        action_patterns = [
            r"bounce",
            r"throw",
            r"launch",
            r"propel",
            r"project",
            r"lift",
            r"pick",
            r"grab",
            r"push",
            r"pull",
            r"swing",
            r"rotate",
            r"sweep",
            r"lock",
            r"interlock",
            r"attach",
            r"capture",
        ]
        matched = any(re.search(pat, lower) for pat in action_patterns)

    return matched, coverage, coverage_max


def main() -> int:
    args = parse_args()

    episodes = load_episodes(args.db, args.scenario, args.tier)
    if not episodes:
        print("No affordance-reasoning RESULT episodes found matching filters.", file=sys.stderr)
        return 1

    # Aggregate metrics per (scenario_id, tier)
    table: Dict[Tuple[str, str], Metrics] = defaultdict(Metrics)
    for ep in episodes:
        match, coverage, coverage_max = compute_episode_metrics(ep)
        key = (ep.scenario_id, ep.tier)
        table[key].add(match, coverage, coverage_max)

    if args.json:
        import json

        out = {}
        for (scenario_id, tier), m in sorted(table.items()):
            out.setdefault(scenario_id, {})[tier] = m.as_dict()
        print(json.dumps(out, indent=2))
        return 0

    # Human-readable summary
    print("Affordance Evaluation Summary")
    print("=============================")
    for (scenario_id, tier), m in sorted(table.items()):
        stats = m.as_dict()
        print(f"\nScenario: {scenario_id} | Tier: {tier}")
        print(f"  Episodes     : {stats['episodes']:.0f}")
        print(f"  Match rate   : {stats['match_rate']:.2f}")
        print(f"  Avg coverage : {stats['avg_coverage']:.2f} / {stats['coverage_max']:.0f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
