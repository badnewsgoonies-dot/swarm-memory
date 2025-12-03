#!/usr/bin/env python3
"""Task-centric priority scoring utilities.

Implements:
    priority = w_recency*R + w_importance*I + w_todo_link*T + w_urgency*E
where:
    - R: recency decay exp(-days_since / tau)
    - I: importance (h=1.0, m=0.5, l=0.1) with slower decay for key moments
    - T: alignment to open TODOs (semantic/topic/project/link bonuses)
    - E: urgency from deadlines and risk/failure markers
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional, Sequence, Tuple

from temporal_decay import temporal_decay_score


@dataclass
class WeightConfig:
    """Weights and tuning knobs for priority scoring."""

    w_recency: float = 0.35
    w_importance: float = 0.25
    w_todo_link: float = 0.25
    w_urgency: float = 0.15
    tau_days: float = 5.0
    topic_bonus: float = 0.15
    project_bonus: float = 0.1
    link_bonus: float = 0.08
    risk_keywords: Tuple[str, ...] = field(
        default_factory=lambda: (
            "blocker",
            "blocked",
            "fail",
            "failing",
            "bug",
            "regression",
            "risk",
            "urgent",
            "deadline",
            "slip",
            "late",
            "broken",
        )
    )


@dataclass
class Entry:
    """Normalized memory entry used for scoring."""

    id: int
    text: str
    timestamp: Optional[str]
    topic: Optional[str]
    importance: Optional[str]
    due: Optional[str]
    links: Optional[str]
    anchor_type: Optional[str]
    anchor_choice: Optional[str]
    project_id: Optional[str]
    scope: Optional[str]
    chat_id: Optional[str]
    embedding_model: Optional[str] = None
    embedding_dim: Optional[int] = None
    embedding: Optional[bytes] = None
    vector: Optional[list[float]] = None


def parse_timestamp(ts: Optional[str]) -> datetime:
    """Parse an ISO timestamp to an aware datetime."""
    if not ts:
        return datetime.now(timezone.utc)
    ts = ts.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def decode_vector(entry: Entry) -> Optional[list[float]]:
    """Decode embedding blob to list of floats."""
    if entry.vector is not None:
        return entry.vector
    if not entry.embedding or not entry.embedding_dim:
        return None
    dim = entry.embedding_dim
    expected_bytes = 4 * dim
    blob = entry.embedding
    if len(blob) % 4 != 0:
        return None
    actual_dim = len(blob) // 4
    if actual_dim != dim:
        dim = actual_dim
    try:
        entry.vector = list(struct.unpack(f"{dim}f", blob))
    except struct.error:
        return None
    return entry.vector


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Compute cosine similarity between two vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def tokenize(text: str) -> set[str]:
    """Lightweight tokenization for lexical similarity."""
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in text or "")
    return {tok for tok in cleaned.split() if tok}


def lexical_similarity(a: str, b: str) -> float:
    """Jaccard similarity between token sets for fallback matching."""
    if not a or not b:
        return 0.0
    ta = tokenize(a)
    tb = tokenize(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def importance_score(label: Optional[str]) -> tuple[float, float]:
    """
    Map importance label to score and recency tau multiplier.

    Returns:
        (importance_value, tau_multiplier)
    """
    if not label:
        return 0.3, 1.0
    label = label.lower()
    if label in ("h", "high", "critical"):
        return 1.0, 1.6
    if label in ("m", "med", "medium"):
        return 0.5, 1.25
    if label in ("l", "low"):
        return 0.1, 1.0
    return 0.3, 1.0


def parse_links(raw: Optional[str]) -> set[str]:
    """Parse links field (JSON, CSV, or space-delimited) into tokens."""
    if not raw:
        return set()
    raw = raw.strip()
    if not raw:
        return set()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, str):
            candidates = [parsed]
        elif isinstance(parsed, list):
            candidates = [str(item) for item in parsed]
        elif isinstance(parsed, dict):
            candidates = [str(v) for v in parsed.values()]
        else:
            candidates = [str(parsed)]
    except (json.JSONDecodeError, TypeError):
        candidates = [part.strip() for part in raw.replace(";", ",").split(",")]
    tokens = set()
    for item in candidates:
        tokens.update(tokenize(item))
    return tokens


def best_todo_alignment(
    entry: Entry,
    todos: Sequence[Entry],
    weights: WeightConfig,
) -> tuple[float, Optional[Entry]]:
    """
    Compute todo alignment T and return (score, matched_todo).
    """
    if not todos:
        return 0.0, None

    entry_vec = decode_vector(entry)
    entry_links = parse_links(entry.links)
    best_score = 0.0
    best_todo: Optional[Entry] = None

    for todo in todos:
        sim = 0.0
        todo_vec = decode_vector(todo)

        if (
            entry_vec is not None
            and todo_vec is not None
            and entry.embedding_model
            and todo.embedding_model
            and entry.embedding_model == todo.embedding_model
        ):
            sim = cosine_similarity(entry_vec, todo_vec)
        else:
            sim = lexical_similarity(entry.text, todo.text)

        if entry.topic and todo.topic and entry.topic == todo.topic:
            sim += weights.topic_bonus

        if entry.project_id and todo.project_id and entry.project_id == todo.project_id:
            sim += weights.project_bonus

        if entry_links:
            link_overlap = entry_links & parse_links(todo.links)
            if link_overlap:
                sim += weights.link_bonus

        if sim > best_score:
            best_score = sim
            best_todo = todo

    return min(best_score, 1.0), best_todo


def due_urgency(due_str: Optional[str], now: datetime) -> float:
    """Urgency from due date proximity."""
    if not due_str:
        return 0.0
    due_str = due_str.replace("Z", "+00:00")
    try:
        due_dt = datetime.fromisoformat(due_str)
    except ValueError:
        return 0.0
    if due_dt.tzinfo is None:
        due_dt = due_dt.replace(tzinfo=timezone.utc)

    days_until = (due_dt - now).total_seconds() / 86_400.0
    if days_until <= 0:
        return 1.0
    # Linear decay over a two-week window
    return max(0.0, 1.0 - (days_until / 14.0))


def risk_score(text: Optional[str], keywords: Iterable[str]) -> float:
    """Score presence of risk/stress markers."""
    if not text:
        return 0.0
    tokens = tokenize(text)
    hits = sum(1 for kw in keywords if kw in tokens)
    if hits == 0:
        return 0.0
    return min(0.6, 0.2 + 0.2 * hits)


def urgency_score(entry: Entry, matched_todo: Optional[Entry], now: datetime, weights: WeightConfig) -> float:
    """Combine due dates and risk markers."""
    due_component = max(
        due_urgency(entry.due, now),
        due_urgency(matched_todo.due, now) if matched_todo else 0.0,
    )
    risk_component = max(
        risk_score(entry.text, weights.risk_keywords),
        risk_score(matched_todo.text, weights.risk_keywords) if matched_todo else 0.0,
    )

    status = (entry.anchor_choice or "").lower()
    todo_status = (matched_todo.anchor_choice or "").lower() if matched_todo else ""
    status_flags = {"blocked", "failing", "stuck"}
    if status in status_flags or todo_status in status_flags:
        risk_component = max(risk_component, 0.5)

    return max(due_component, risk_component)


def priority_score(
    entry: Entry,
    todos: Sequence[Entry],
    weights: WeightConfig,
    now: Optional[datetime] = None,
) -> dict:
    """
    Compute priority score and component breakdown for a memory entry.

    Returns:
        {
            "score": float,
            "components": {"recency": R, "importance": I, "todo": T, "urgency": E},
            "matched_todo": todo_entry or None,
            "age_days": float
        }
    """
    now = now or datetime.now(timezone.utc)
    ts = parse_timestamp(entry.timestamp)
    age_days = (now - ts).total_seconds() / 86_400.0
    imp_value, tau_multiplier = importance_score(entry.importance)
    recency = temporal_decay_score(ts, now=now, tau_days=weights.tau_days * tau_multiplier)
    todo_alignment, matched = best_todo_alignment(entry, todos, weights)
    urgency = urgency_score(entry, matched, now, weights)

    score = (
        weights.w_recency * recency
        + weights.w_importance * imp_value
        + weights.w_todo_link * todo_alignment
        + weights.w_urgency * urgency
    )

    return {
        "score": float(score),
        "components": {
            "recency": float(recency),
            "importance": float(imp_value),
            "todo": float(todo_alignment),
            "urgency": float(urgency),
        },
        "matched_todo": matched,
        "age_days": age_days,
    }

