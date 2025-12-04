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
    # Type-specific tau values for Working Memory (fast decay types)
    tau_idea: float = 0.1  # Ideas (I) decay very fast unless reinforced
    tau_conversation: float = 1.0  # Conversations decay faster than default
    # Type weights: Decision/Edit > Note > Conversation/Chat
    type_weights: dict = field(
        default_factory=lambda: {
            'd': 1.2,  # Decision - high weight
            'e': 1.1,  # Edit - elevated weight
            'f': 1.0,  # Fact - normal weight
            'a': 1.0,  # Action - normal weight
            'q': 0.9,  # Question - slightly lower
            'n': 0.8,  # Note - lower weight
            'c': 0.6,  # Conversation/Chat - lowest weight
            'I': 0.9,  # Idea - high initially but decays fast
            # Task types
            'T': 1.3,  # TODO - very high
            'G': 1.3,  # GOAL - very high
            'M': 1.0,  # ATTEMPT - normal
            'R': 1.1,  # RESULT - elevated
            'L': 1.2,  # LESSON - high
            'P': 1.0,  # PHASE - normal
        }
    )
    # Topic locking boost for entries matching active task
    active_task_boost: float = 0.5
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


def importance_score(label: Optional[str]) -> tuple[float, float, bool]:
    """
    Map importance label to score, recency tau multiplier, and immortal flag.

    Returns:
        (importance_value, tau_multiplier, is_immortal)
        
    Immortal Memories: When importance is H/High/Critical, the memory is
    "immortal" - its recency score should be 1.0 regardless of age.
    """
    if not label:
        return 0.3, 1.0, False
    label = label.lower()
    if label in ("h", "high", "critical"):
        # Immortal: these memories never decay
        return 1.0, 1.6, True
    if label in ("m", "med", "medium"):
        return 0.5, 1.25, False
    if label in ("l", "low"):
        return 0.1, 1.0, False
    return 0.3, 1.0, False


def get_type_tau(anchor_type: Optional[str], weights: WeightConfig) -> float:
    """
    Get the tau (decay rate) for a specific entry type.
    
    Ideas (I) decay very fast (Working Memory), while other types
    use the default tau_days.
    """
    if anchor_type == 'I':
        return weights.tau_idea
    if anchor_type == 'c':
        return weights.tau_conversation
    return weights.tau_days


def get_type_weight(anchor_type: Optional[str], weights: WeightConfig) -> float:
    """
    Get the type-based weight multiplier for an entry.
    
    Decision/Edit > Note > Conversation/Chat
    """
    if not anchor_type:
        return 1.0
    return weights.type_weights.get(anchor_type, 1.0)


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
    active_task_id: Optional[str] = None,
) -> dict:
    """
    Compute priority score and component breakdown for a memory entry.

    Args:
        entry: The memory entry to score
        todos: List of open TODO entries for alignment scoring
        weights: Weight configuration
        now: Current time (defaults to now)
        active_task_id: If set, entries linked to this task get a massive boost (Topic Locking)

    Returns:
        {
            "score": float,
            "components": {"recency": R, "importance": I, "todo": T, "urgency": E, "type_weight": W, "task_boost": B},
            "matched_todo": todo_entry or None,
            "age_days": float,
            "is_immortal": bool
        }
    """
    now = now or datetime.now(timezone.utc)
    ts = parse_timestamp(entry.timestamp)
    age_days = (now - ts).total_seconds() / 86_400.0
    
    # Get importance score and check for immortality
    imp_value, tau_multiplier, is_immortal = importance_score(entry.importance)
    
    # Get type-specific tau for decay calculation
    base_tau = get_type_tau(entry.anchor_type, weights)
    effective_tau = base_tau * tau_multiplier
    
    # Calculate recency - immortal memories always have recency=1.0
    if is_immortal:
        recency = 1.0
    else:
        recency = temporal_decay_score(ts, now=now, tau_days=effective_tau)
    
    # Get type weight multiplier
    type_weight = get_type_weight(entry.anchor_type, weights)
    
    # Calculate TODO alignment
    todo_alignment, matched = best_todo_alignment(entry, todos, weights)
    
    # Calculate urgency
    urgency = urgency_score(entry, matched, now, weights)
    
    # Topic Locking: boost for active task
    task_boost = 0.0
    if active_task_id:
        # Check if entry is linked to the active task
        # Use word boundary matching to avoid false positives (e.g., 'task1' matching 'task123')
        raw_links = entry.links or ""
        
        # Check for exact match with word/delimiter boundaries in raw links
        # Handles comma-separated, space-separated, and JSON array formats
        import re
        # Pattern matches the task ID surrounded by common delimiters or start/end of string
        link_pattern = r'(?:^|[,\s\[\]"\'{};:|])' + re.escape(active_task_id) + r'(?:$|[,\s\[\]"\'{};:|])'
        if re.search(link_pattern, raw_links):
            task_boost = weights.active_task_boost
        
        # Note: We do NOT use parse_links here because it tokenizes by word boundaries,
        # which would cause false positives (e.g., 'task' matching 'task-123' after tokenization).
        
        # Check topic for exact match (case-insensitive)
        if not task_boost and entry.topic:
            # Exact topic match only, no substring matching
            if entry.topic.lower() == active_task_id.lower():
                task_boost = weights.active_task_boost * 0.5  # Partial boost for exact topic match

    # Calculate final score with type weight
    base_score = (
        weights.w_recency * recency
        + weights.w_importance * imp_value
        + weights.w_todo_link * todo_alignment
        + weights.w_urgency * urgency
        + task_boost
    )
    
    # Apply type weight as a multiplier
    score = base_score * type_weight

    return {
        "score": float(score),
        "components": {
            "recency": float(recency),
            "importance": float(imp_value),
            "todo": float(todo_alignment),
            "urgency": float(urgency),
            "type_weight": float(type_weight),
            "task_boost": float(task_boost),
        },
        "matched_todo": matched,
        "age_days": age_days,
        "is_immortal": is_immortal,
    }

