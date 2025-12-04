#!/usr/bin/env python3
"""
tiny_llm.py - Minimal GPT-style language model in pure PyTorch.

This is intentionally small and explicit so it can be:
  - inspected,
  - wired to the memory DB as a trainable component later,
  - and used as a "from scratch" baseline distinct from big pretrained LLMs.

It is a *character-level* causal model:
  - Input: sequences of integer token ids (chars)
  - Output: logits over next-token distribution
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
except ImportError as exc:  # pragma: no cover - only hit when torch missing
    raise SystemExit(
        "ERROR: tiny_llm.py requires PyTorch. Install with:\n"
        "    pip install torch\n"
    ) from exc


@dataclass
class TinyGPTConfig:
    vocab_size: int
    block_size: int = 128
    n_layers: int = 4
    n_heads: int = 4
    d_model: int = 128
    d_ff: int = 512
    dropout: float = 0.1


class CausalSelfAttention(nn.Module):
    def __init__(self, cfg: TinyGPTConfig) -> None:
        super().__init__()
        assert cfg.d_model % cfg.n_heads == 0
        self.n_heads = cfg.n_heads
        self.head_dim = cfg.d_model // cfg.n_heads
        self.key = nn.Linear(cfg.d_model, cfg.d_model, bias=False)
        self.query = nn.Linear(cfg.d_model, cfg.d_model, bias=False)
        self.value = nn.Linear(cfg.d_model, cfg.d_model, bias=False)
        self.proj = nn.Linear(cfg.d_model, cfg.d_model, bias=False)
        self.dropout = nn.Dropout(cfg.dropout)

        # Causal mask so position i cannot attend to j > i
        mask = torch.tril(torch.ones(cfg.block_size, cfg.block_size)).view(
            1, 1, cfg.block_size, cfg.block_size
        )
        self.register_buffer("mask", mask)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B, T, C = x.size()
        k = self.key(x).view(B, T, self.n_heads, self.head_dim).transpose(1, 2)  # B, H, T, D
        q = self.query(x).view(B, T, self.n_heads, self.head_dim).transpose(1, 2)
        v = self.value(x).view(B, T, self.n_heads, self.head_dim).transpose(1, 2)

        # Scaled dot-product attention
        att = (q @ k.transpose(-2, -1)) / math.sqrt(self.head_dim)  # B, H, T, T
        att = att.masked_fill(self.mask[:, :, :T, :T] == 0, float("-inf"))
        att = F.softmax(att, dim=-1)
        att = self.dropout(att)
        y = att @ v  # B, H, T, D

        y = y.transpose(1, 2).contiguous().view(B, T, C)
        y = self.proj(y)
        y = self.dropout(y)
        return y


class Block(nn.Module):
    def __init__(self, cfg: TinyGPTConfig) -> None:
        super().__init__()
        self.ln1 = nn.LayerNorm(cfg.d_model)
        self.ln2 = nn.LayerNorm(cfg.d_model)
        self.attn = CausalSelfAttention(cfg)
        self.ff = nn.Sequential(
            nn.Linear(cfg.d_model, cfg.d_ff),
            nn.GELU(),
            nn.Linear(cfg.d_ff, cfg.d_model),
            nn.Dropout(cfg.dropout),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.attn(self.ln1(x))
        x = x + self.ff(self.ln2(x))
        return x


class TinyGPT(nn.Module):
    """
    Minimal GPT-style language model.
    """

    def __init__(self, cfg: TinyGPTConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self.tok_emb = nn.Embedding(cfg.vocab_size, cfg.d_model)
        self.pos_emb = nn.Embedding(cfg.block_size, cfg.d_model)
        self.drop = nn.Dropout(cfg.dropout)
        self.blocks = nn.ModuleList([Block(cfg) for _ in range(cfg.n_layers)])
        self.ln_f = nn.LayerNorm(cfg.d_model)
        self.head = nn.Linear(cfg.d_model, cfg.vocab_size, bias=False)

        self.apply(self._init_weights)

    def _init_weights(self, module: nn.Module) -> None:
        if isinstance(module, (nn.Linear, nn.Embedding)):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if isinstance(module, nn.Linear) and module.bias is not None:
                nn.init.zeros_(module.bias)

    def forward(self, idx: torch.Tensor, targets: torch.Tensor | None = None) -> Tuple[torch.Tensor, torch.Tensor | None]:
        """
        idx: (B, T) integer token ids
        targets: (B, T) or None

        Returns:
            logits: (B, T, vocab_size)
            loss: scalar or None
        """
        B, T = idx.size()
        if T > self.cfg.block_size:
            raise ValueError(f"Sequence length {T} exceeds block_size {self.cfg.block_size}")

        pos = torch.arange(0, T, device=idx.device, dtype=torch.long).unsqueeze(0)  # 1, T
        x = self.tok_emb(idx) + self.pos_emb(pos)
        x = self.drop(x)
        for blk in self.blocks:
            x = blk(x)
        x = self.ln_f(x)
        logits = self.head(x)

        loss: torch.Tensor | None
        if targets is not None:
            loss = F.cross_entropy(
                logits.view(-1, logits.size(-1)),
                targets.view(-1),
            )
        else:
            loss = None
        return logits, loss

    @torch.no_grad()
    def generate(self, idx: torch.Tensor, max_new_tokens: int) -> torch.Tensor:
        """
        Autoregressively sample new tokens, starting from idx.
        """
        for _ in range(max_new_tokens):
            idx_cond = idx[:, -self.cfg.block_size :]
            logits, _ = self(idx_cond)
            logits = logits[:, -1, :]  # last position
            probs = F.softmax(logits, dim=-1)
            next_token = torch.multinomial(probs, num_samples=1)
            idx = torch.cat([idx, next_token], dim=1)
        return idx

