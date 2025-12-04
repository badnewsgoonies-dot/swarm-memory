#!/usr/bin/env python3
"""
naive_llm.py - Minimal prompt-only text generator.

This is an intentionally naive "LM" that has no prior training data.
For each call, it:

  1. Treats the input prompt as the *only* text it has ever seen.
  2. Builds a simple character-level Markov model over that prompt.
  3. Samples a short continuation using only those statistics.

This approximates a model that "only knows the prompt and nothing else":
all structure comes from the characters and local patterns in the current
prompt, not from any external corpus or pretrained weights.
"""

from __future__ import annotations

import random
from collections import defaultdict
from typing import DefaultDict, List


class NaivePromptLM:
    """Very small character-level Markov model built per prompt."""

    def __init__(self, order: int = 3) -> None:
        self.order = max(1, order)
        self.model: DefaultDict[str, List[str]] = defaultdict(list)

    def fit(self, text: str) -> None:
        """Build n-gram statistics from the given text only."""
        if len(text) <= self.order:
            return

        for i in range(len(text) - self.order):
            key = text[i : i + self.order]
            next_char = text[i + self.order]
            self.model[key].append(next_char)

    def generate(self, max_chars: int = 400) -> str:
        """Sample a continuation using only the learned character transitions."""
        if not self.model:
            return ""

        start_key = random.choice(list(self.model.keys()))
        key = start_key
        out_chars: List[str] = []

        for _ in range(max_chars):
            options = self.model.get(key)
            if not options:
                break
            c = random.choice(options)
            out_chars.append(c)
            key = (key + c)[-self.order :]

        return "".join(out_chars)


def generate_from_prompt(prompt: str, max_chars: int = 400, order: int = 3) -> str:
    """
    Convenience helper: build a NaivePromptLM from the given prompt and
    return a sampled continuation.
    """
    lm = NaivePromptLM(order=order)
    lm.fit(prompt)
    return lm.generate(max_chars=max_chars)

