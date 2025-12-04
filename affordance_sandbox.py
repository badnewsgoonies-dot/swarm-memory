#!/usr/bin/env python3
"""
affordance_sandbox.py - Run text-only affordance reasoning scenarios against an LLM.

This script loads structured "world descriptions" (basketball-like, fishing-like,
and synthetic) from affordance_scenarios.json, sends them to a configured LLM via
llm_client, and optionally logs the responses into the memory database as RESULT
glyphs.

Usage:
    ./affordance_sandbox.py --scenario ball_ring_game --tier fast --log
    ./affordance_sandbox.py --all --tier fast --log

By default it uses the "fast" tier (small local model) to approximate a
text-only agent with limited world knowledge.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from llm_client import LLMClient


SCRIPT_DIR = Path(__file__).parent
SCENARIOS_FILE = SCRIPT_DIR / "affordance_scenarios.json"

INVESTIGATION_PROTOCOL = (
    "You are an AFFORDANCE INVESTIGATOR.\n"
    "You do not know any sports, tools, or games by name.\n"
    "You must infer possible uses only from shapes, sizes, weights, textures, "
    "materials, how an agent can interact, and the surrounding environment.\n\n"
    "For each world, follow this procedure:\n"
    "1) Isolated object analysis: describe shape, relative size to a human, "
    "likely weight, texture/material, and any special features like openings, "
    "recesses, joints, or tethers.\n"
    "2) Agent–object relation: list how a human-like agent could hold, carry, "
    "push, pull, rotate, drop, or throw each object. Propose simple experiments "
    "such as dropping or sliding the object to see how it moves.\n"
    "3) Environment scan: list static structures and their geometry (frames, "
    "openings, surfaces, volumes, heights, distances, constraints) and which "
    "objects could fit, pass through, rest in, or be retained by them.\n"
    "4) Interaction hypotheses: combine object properties and environment "
    "structures to propose at least three candidate activities, described only "
    "in terms of motions and geometry, not real-world names.\n"
    "5) Experiment and selection: for each candidate, describe a short test "
    "using only allowed actions, then choose the single best activity that uses "
    "the most of the special geometry and has a clear success/failure signal.\n"
)

MEANING_PROTOCOL = (
    "You are not just solving a task; you are exploring.\n"
    "Treat each scenario as an opportunity to notice subtle differences between\n"
    "actions and outcomes. When you consider activities, pay attention to small\n"
    "variations: different angles, timings, positions, and how they change what\n"
    "the environment affords. Your 'goal' is to understand and savor these\n"
    "differences, not just name a single solution.\n\n"
    "For each world, after selecting the best activity, briefly reflect on:\n"
    "- How small changes in motion would alter success or failure.\n"
    "- What you \"learn\" about the world from those variations.\n"
    "- How this world is similar to or different from other worlds you have seen.\n"
)


def _extract_tier_from_text(text: str) -> Optional[str]:
    """
    Extract the tier string from a stored RESULT text block.
    Looks for a line starting with '- Tier:'.
    """
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- Tier:"):
            return line.split(":", 1)[1].strip()
    return None


def load_affordance_memories(limit: int = 5, tier_filter: Optional[str] = None) -> str:
    """
    Load recent affordance-related entries from the memory database and format
    them as a compact lessons/episodes section suitable for prompt injection.
    """
    db_path = os.environ.get("MEMORY_DB", str(SCRIPT_DIR / "memory.db"))
    db = Path(db_path)
    if not db.exists():
        return ""

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        max_rows = limit * 10 if tier_filter else limit
        cursor.execute(
            """
            SELECT anchor_type, text, timestamp
            FROM chunks
            WHERE anchor_topic = 'affordance-reasoning'
              AND text IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (max_rows,),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    filtered: List[tuple] = []
    for anchor_type, text, ts in rows:
        if tier_filter:
            tier = _extract_tier_from_text(text or "")
            if tier != tier_filter:
                continue
        filtered.append((anchor_type, text, ts))
        if len(filtered) >= limit:
            break

    if not filtered:
        return ""

    lines: List[str] = ["PAST AFFORDANCE EPISODES:"]
    for anchor_type, text, ts in filtered:
        ts_short = ts[:10] if ts and len(ts) >= 10 else "?"
        snippet = (text or "").replace("\n", " ").strip()
        if len(snippet) > 240:
            snippet = snippet[:237] + "..."
        lines.append(f"- [{anchor_type or '?'}][{ts_short}] {snippet}")

    return "\n".join(lines)


def load_scenarios(path: Path) -> List[Dict[str, Any]]:
    """
    Load affordance scenarios from a JSON file.

    The file should contain a list of objects with at least:
      - id
      - label
      - description
      - world
      - intended_affordance
    """
    if not path.exists():
        raise FileNotFoundError(f"Scenarios file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"Expected list of scenarios in {path}, got {type(data)}")

    required_keys = {"id", "label", "description", "world", "intended_affordance"}
    for scenario in data:
        missing = required_keys - scenario.keys()
        if missing:
            raise ValueError(f"Scenario missing keys {missing}: {scenario}")

    return data


def build_prompt(
    world_description: str,
    include_protocol: bool = False,
    include_meaning: bool = False,
) -> str:
    """
    Build the affordance reasoning prompt for a single scenario.
    """
    template = (
        "You are an AFFORDANCE REASONER.\n"
        "You only see a description of objects and an environment.\n"
        "You must infer plausible uses or activities from structure alone.\n"
        "Do NOT name or reference any real-world sports, tools, or brand names.\n"
        "Focus only on shapes, positions, and what actions the environment invites.\n\n"
        "WORLD DESCRIPTION:\n"
        "{world}\n\n"
        "TASK:\n"
        "1. List 3 plausible activities a human could perform here.\n"
        "2. For each, explain why the geometry and layout make it natural.\n"
        "3. Pick the single activity that best matches the environment's intended use "
        "and justify your choice.\n"
    )
    parts: List[str] = []
    if include_meaning:
        parts.append(MEANING_PROTOCOL)
    if include_protocol:
        parts.append(INVESTIGATION_PROTOCOL)
    parts.append(template.format(world=world_description))
    return "\n\n".join(parts)


def call_llm(client: LLMClient, prompt: str, tier: str) -> str:
    """
    Call the LLM with the given prompt and tier, returning the text response.
    """
    response = client.complete(prompt, tier=tier)
    if not response.success:
        raise RuntimeError(f"LLM call failed for tier '{tier}': {response.error}")
    return response.text.strip()


def call_tiny_llm(prompt: str, ckpt_path: Path, max_new_tokens: int = 256) -> str:
    """
    Run a local tiny GPT checkpoint for inference (supports char or word tokenizer).
    """
    try:
        import torch
        import re
    except ImportError as exc:
        raise RuntimeError("Tiny LLM inference requires torch. Install torch first.") from exc

    from tiny_llm import TinyGPT, TinyGPTConfig

    if not ckpt_path.exists():
        raise RuntimeError(f"Tiny LLM checkpoint not found: {ckpt_path}")

    ckpt = torch.load(ckpt_path, map_location="cpu")
    cfg = TinyGPTConfig(**ckpt["config"])
    model = TinyGPT(cfg)
    model.load_state_dict(ckpt["model_state"])
    model.eval()

    stoi = ckpt["stoi"]
    itos = ckpt["itos"]
    tokenizer = ckpt.get("tokenizer", "char")

    def encode(text: str) -> torch.Tensor:
        if tokenizer == "word":
            toks = re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE)
            ids = [stoi.get(tok, 0) for tok in toks]
        else:
            ids = [stoi.get(ch, 0) for ch in text]
        return torch.tensor(ids, dtype=torch.long).unsqueeze(0)

    def decode(ids: torch.Tensor) -> str:
        toks = [itos.get(int(i), "<unk>") for i in ids]
        if tokenizer == "word":
            return " ".join(toks)
        return "".join(toks)

    idx = encode(prompt)
    if idx.size(1) > cfg.block_size:
        idx = idx[:, -cfg.block_size :]

    with torch.no_grad():
        out = model.generate(idx, max_new_tokens=max_new_tokens)

    generated = out[0, idx.size(1) :].tolist()
    return decode(torch.tensor(generated))


def log_result_to_memory(
    scenario: Dict[str, Any],
    tier: str,
    response_text: str,
    dry_run: bool = False,
) -> None:
    """
    Log a RESULT glyph into the memory database using mem-db.sh.
    """
    summary = (
        f"Affordance scenario result\n"
        f"- Scenario: {scenario['id']} ({scenario['label']})\n"
        f"- Tier: {tier}\n\n"
        f"World description:\n{scenario['world']}\n\n"
        f"Model response:\n{response_text}\n\n"
        f"Intended affordance (for human evaluation):\n"
        f"{scenario['intended_affordance']}\n"
    )

    if dry_run:
        print("[DRY-RUN] Would log RESULT glyph to memory.")
        print(summary)
        return

    cmd = [
        str(SCRIPT_DIR / "mem-db.sh"),
        "write",
        "t=R",
        "topic=affordance-reasoning",
        "choice=affordance-experiment",
        f"text={summary}",
        "scope=shared",
        "visibility=public",
        "role=researcher",
        "source=affordance_sandbox",
    ]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Failed to log RESULT glyph via mem-db.sh: {exc}") from exc


def run_scenario(
    client: LLMClient,
    scenario: Dict[str, Any],
    tier: str,
    log_to_memory: bool,
    dry_run_log: bool = False,
    include_protocol: bool = False,
    include_meaning: bool = False,
    use_memory: bool = False,
    memory_limit: int = 5,
    tiny_ckpt: Optional[Path] = None,
    tiny_max_new: int = 256,
) -> None:
    """
    Run a single scenario, print the response, and optionally log to memory.
    """
    print("=" * 80)
    print(f"Scenario: {scenario['id']} - {scenario['label']}")
    print("-" * 80)
    print(scenario["description"])
    print("\nWORLD DESCRIPTION:\n")
    print(scenario["world"])
    print("\n--- LLM RESPONSE ---\n")

    base_prompt = build_prompt(
        scenario["world"],
        include_protocol=include_protocol,
        include_meaning=include_meaning,
    )
    # For naive tier, personal memory = only naive episodes; otherwise use shared episodes.
    tier_filter = tier if (use_memory and tier == "naive") else None
    memory_block = (
        load_affordance_memories(limit=memory_limit, tier_filter=tier_filter)
        if use_memory
        else ""
    )
    if memory_block:
        prompt = f"{memory_block}\n\n{base_prompt}"
    else:
        prompt = base_prompt

    if tier == "tiny":
        if not tiny_ckpt:
            raise RuntimeError("Tiny tier requested but no checkpoint provided.")
        response_text = call_tiny_llm(prompt, tiny_ckpt, max_new_tokens=tiny_max_new)
    else:
        response_text = call_llm(client, prompt, tier=tier)
    print(response_text)
    print("\n--- INTENDED AFFORDANCE (for human evaluation) ---\n")
    print(scenario["intended_affordance"])
    print("=" * 80)

    if log_to_memory:
        log_result_to_memory(scenario, tier, response_text, dry_run=dry_run_log)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """
    Parse command-line arguments.
    """
    parser = argparse.ArgumentParser(
        description="Run affordance reasoning scenarios against an LLM and optionally log results to memory."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--scenario",
        help="ID of a single scenario to run (see affordance_scenarios.json).",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Run all scenarios in affordance_scenarios.json.",
    )
    parser.add_argument(
        "--tier",
        default="fast",
        help="LLM tier to use (fast/code/smart/claude/codex/max/naive/tiny).",
    )
    parser.add_argument(
        "--log",
        action="store_true",
        help="Log results into memory via mem-db.sh as RESULT glyphs.",
    )
    parser.add_argument(
        "--log-dry-run",
        action="store_true",
        help="Print what would be logged to memory without writing.",
    )
    parser.add_argument(
        "--protocol",
        action="store_true",
        help="Prepend an explicit affordance investigation protocol to the prompt.",
    )
    parser.add_argument(
        "--goal",
        action="store_true",
        help="Prepend a 'meaning-of-life' exploration protocol emphasizing subtle differences and learning.",
    )
    parser.add_argument(
        "--with-memory",
        action="store_true",
        help="Include recent affordance-reasoning episodes from memory.db in the prompt.",
    )
    parser.add_argument(
        "--memory-limit",
        type=int,
        default=5,
        help="How many recent affordance-reasoning entries to include when using --with-memory or tier=affordance-memory.",
    )
    parser.add_argument(
        "--tiny-checkpoint",
        default=str(SCRIPT_DIR / "tiny_llm_ckpt" / "model.pt"),
        help="Path to tiny LLM checkpoint (used when --tier tiny).",
    )
    parser.add_argument(
        "--tiny-max-new",
        type=int,
        default=256,
        help="Max new tokens to generate for tiny tier.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """
    Entry point for CLI.
    """
    args = parse_args(argv)

    try:
        scenarios = load_scenarios(SCENARIOS_FILE)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    scenarios_by_id = {s["id"]: s for s in scenarios}

    selected: List[Dict[str, Any]]
    if args.all:
        selected = scenarios
    else:
        if args.scenario not in scenarios_by_id:
            print(
                f"ERROR: Scenario '{args.scenario}' not found. "
                f"Available IDs: {', '.join(scenarios_by_id.keys())}",
                file=sys.stderr,
            )
            return 1
        selected = [scenarios_by_id[args.scenario]]

    client = LLMClient()

    # Interpret special tier alias: "affordance-memory" means
    # "use Claude with memory injection enabled".
    effective_tier = args.tier
    use_memory = args.with_memory
    if args.tier == "affordance-memory":
        effective_tier = "claude"
        use_memory = True

    for scenario in selected:
        try:
            run_scenario(
                client=client,
                scenario=scenario,
                tier=effective_tier,
                log_to_memory=args.log,
                dry_run_log=args.log_dry_run,
                include_protocol=args.protocol,
                include_meaning=args.goal,
                use_memory=use_memory,
                memory_limit=args.memory_limit,
                tiny_ckpt=Path(args.tiny_checkpoint),
                tiny_max_new=args.tiny_max_new,
            )
        except RuntimeError as exc:
            print(f"ERROR running scenario '{scenario['id']}': {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
