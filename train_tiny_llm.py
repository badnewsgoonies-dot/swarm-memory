#!/usr/bin/env python3
"""
train_tiny_llm.py - Train a tiny GPT-style LM on text from memory.db.

This is a *minimal* training script intended as a starting point:
  - Reads text from chunks in memory.db (by default all affordance-reasoning RESULT glyphs).
  - Builds a character-level vocabulary.
  - Trains TinyGPT to predict next characters.

It is not optimized for performance; it is meant to be simple and hackable.

Usage (once torch is installed):
    python train_tiny_llm.py --epochs 5
    python train_tiny_llm.py --topic affordance-reasoning --epochs 10
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Optional, Dict

try:
    import torch
    from torch.utils.data import Dataset, DataLoader
except ImportError as exc:
    raise SystemExit(
        "ERROR: train_tiny_llm.py requires PyTorch. Install with:\n"
        "    pip install torch\n"
    ) from exc

from tiny_llm import TinyGPT, TinyGPTConfig


SCRIPT_DIR = Path(__file__).parent
DEFAULT_DB = os.environ.get("MEMORY_DB", str(SCRIPT_DIR / "memory.db"))


def load_text_from_db(
    db_path: str,
    topic: str = "affordance-reasoning",
    max_rows: int = 1000,
) -> List[str]:
    """Load text fields from memory.db for a given topic (or all topics if topic='all')."""
    db = Path(db_path)
    if not db.exists():
        raise SystemExit(f"Database not found at {db}")

    conn = sqlite3.connect(str(db))
    cursor = conn.cursor()

    try:
        if topic.lower() == "all":
            cursor.execute(
                """
                SELECT text
                FROM chunks
                WHERE text IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (max_rows,),
            )
        else:
            cursor.execute(
                """
                SELECT text
                FROM chunks
                WHERE anchor_topic = ?
                  AND text IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (topic, max_rows),
            )
        rows = cursor.fetchall()
    finally:
        conn.close()

    texts = [(t or "").strip() for (t,) in rows if t]
    if not texts:
        raise SystemExit(f"No text rows found for topic='{topic}' in {db}")
    return texts


def build_vocab(texts: List[str]) -> Tuple[dict, dict]:
    """Deprecated: kept for backwards compatibility; use TokenEncoder instead."""
    all_text = "\n\n".join(texts)
    chars = sorted(set(all_text))
    stoi = {ch: i for i, ch in enumerate(chars)}
    itos = {i: ch for ch, i in stoi.items()}
    return stoi, itos


class TokenEncoder:
    """
    Simple token encoder supporting 'char' (default) and 'word' tokenization.
    Always reserves token 0 for <unk>.
    """

    def __init__(self, stoi: Dict[str, int], itos: Dict[int, str], tokenizer: str):
        self.stoi = stoi
        self.itos = itos
        self.tokenizer = tokenizer
        self.unk_id = 0

    @staticmethod
    def from_texts(
        texts: List[str],
        tokenizer: str = "char",
        min_freq: int = 1,
        vocab_limit: Optional[int] = None,
    ) -> "TokenEncoder":
        if tokenizer == "char":
            all_text = "\n\n".join(texts)
            vocab = sorted(set(all_text))
            tokens = ["<unk>"] + vocab
        elif tokenizer == "word":
            counter: Dict[str, int] = {}
            for t in texts:
                for tok in re.findall(r"\w+|[^\w\s]", t, flags=re.UNICODE):
                    counter[tok] = counter.get(tok, 0) + 1
            # filter by min_freq
            items = [(tok, freq) for tok, freq in counter.items() if freq >= min_freq]
            # sort by frequency desc then token
            items.sort(key=lambda x: (-x[1], x[0]))
            if vocab_limit:
                items = items[: max(0, vocab_limit - 1)]  # reserve <unk>
            tokens = ["<unk>"] + [tok for tok, _ in items]
        else:
            raise ValueError(f"Unsupported tokenizer: {tokenizer}")

        stoi = {tok: idx for idx, tok in enumerate(tokens)}
        itos = {idx: tok for tok, idx in stoi.items()}
        return TokenEncoder(stoi=stoi, itos=itos, tokenizer=tokenizer)

    def encode(self, text: str) -> List[int]:
        if self.tokenizer == "char":
            return [self.stoi.get(ch, self.unk_id) for ch in text]
        # word tokenizer
        tokens = re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE)
        return [self.stoi.get(tok, self.unk_id) for tok in tokens]

    def decode(self, ids: List[int]) -> str:
        toks = [self.itos.get(i, "<unk>") for i in ids]
        if self.tokenizer == "char":
            return "".join(toks)
        return " ".join(toks)


class CharDataset(Dataset):
    def __init__(self, tokens: List[int], block_size: int):
        self.tokens = tokens
        self.block_size = block_size

    def __len__(self) -> int:
        return max(0, len(self.tokens) - self.block_size)

    def __getitem__(self, idx: int):
        window = self.tokens[idx : idx + self.block_size + 1]
        x = torch.tensor(window[:-1], dtype=torch.long)
        y = torch.tensor(window[1:], dtype=torch.long)
        return x, y


@dataclass
class TrainConfig:
    db: str
    topic: str
    max_rows: int
    epochs: int
    batch_size: int
    lr: float
    block_size: int
    n_layers: int
    n_heads: int
    d_model: int
    d_ff: int
    device: str
    max_steps: int | None
    log_topic: Optional[str]
    log_task: Optional[str]
    tokenizer: str
    min_freq: int
    vocab_limit: Optional[int]


def train(cfg: TrainConfig) -> None:
    texts = load_text_from_db(cfg.db, topic=cfg.topic, max_rows=cfg.max_rows)
    encoder = TokenEncoder.from_texts(
        texts,
        tokenizer=cfg.tokenizer,
        min_freq=cfg.min_freq,
        vocab_limit=cfg.vocab_limit,
    )
    # Flatten all texts into a single token stream
    tokens: List[int] = []
    sep = "\n\n" if encoder.tokenizer == "char" else " <sep> "
    for t in texts:
        tokens.extend(encoder.encode(t))
        tokens.extend(encoder.encode(sep))

    dataset = CharDataset(tokens, block_size=cfg.block_size)
    if len(dataset) == 0:
        raise SystemExit("Dataset is empty after windowing; increase text size or decrease block_size.")

    loader = DataLoader(dataset, batch_size=cfg.batch_size, shuffle=True)

    model_cfg = TinyGPTConfig(
        vocab_size=len(encoder.stoi),
        block_size=cfg.block_size,
        n_layers=cfg.n_layers,
        n_heads=cfg.n_heads,
        d_model=cfg.d_model,
        d_ff=cfg.d_ff,
    )
    device = torch.device(cfg.device)
    model = TinyGPT(model_cfg).to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=cfg.lr)

    print(f"Training TinyGPT on topic='{cfg.topic}' from {cfg.db}")
    print(f"Tokenizer={cfg.tokenizer}, vocab size={model_cfg.vocab_size}, block_size={cfg.block_size}, device={device}")

    last_loss = None
    for epoch in range(1, cfg.epochs + 1):
        model.train()
        total_loss = 0.0
        batches = 0
        for step, (x, y) in enumerate(loader, start=1):
            x = x.to(device)
            y = y.to(device)
            optimizer.zero_grad(set_to_none=True)
            _, loss = model(x, y)
            assert loss is not None
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += loss.item()
            batches += 1

            if step % 50 == 0:
                running = total_loss / max(1, batches)
                print(f"  step {step}: running_loss={running:.4f}")

            if cfg.max_steps and step >= cfg.max_steps:
                print(f"  Reached max_steps={cfg.max_steps}, stopping epoch early.")
                break

        avg_loss = total_loss / max(1, batches)
        last_loss = avg_loss
        print(f"Epoch {epoch}/{cfg.epochs} - loss={avg_loss:.4f}")

    # Save final model and vocab to disk
    out_dir = SCRIPT_DIR / "tiny_llm_ckpt"
    out_dir.mkdir(parents=True, exist_ok=True)
    ckpt_path = out_dir / "model.pt"
    vocab_path = out_dir / "vocab.json"

    torch.save(
        {
            "model_state": model.state_dict(),
            "config": model_cfg.__dict__,
            "stoi": encoder.stoi,
            "itos": encoder.itos,
            "tokenizer": encoder.tokenizer,
            "min_freq": cfg.min_freq,
            "vocab_limit": cfg.vocab_limit,
        },
        ckpt_path,
    )
    import json

    with vocab_path.open("w", encoding="utf-8") as f:
        json.dump({"stoi": encoder.stoi, "itos": encoder.itos}, f, ensure_ascii=False, indent=2)

    print(f"Saved model to {ckpt_path}")
    print(f"Saved vocab to {vocab_path}")

    if cfg.log_topic:
        metric = f"train_loss={last_loss:.4f}" if last_loss is not None else None
        summary = (
            f"Trained TinyGPT topic={cfg.topic} epochs={cfg.epochs} "
            f"vocab={model_cfg.vocab_size} d_model={model_cfg.d_model} "
            f"layers={model_cfg.n_layers} heads={model_cfg.n_heads} block={model_cfg.block_size}"
        )
        cmd = [
            str(SCRIPT_DIR / "mem-db.sh"),
            "write",
            "t=R",
            f"topic={cfg.log_topic}",
            "choice=success",
            f"text={summary}",
        ]
        if cfg.log_task:
            cmd.append(f"task={cfg.log_task}")
        if metric:
            cmd.append(f"metric={metric}")
        try:
            subprocess.run(cmd, check=True, cwd=SCRIPT_DIR)
            print(f"Logged training result to memory (topic={cfg.log_topic})")
        except subprocess.CalledProcessError as exc:
            print(f"WARNING: failed to log training result: {exc}")


def parse_args() -> TrainConfig:
    parser = argparse.ArgumentParser(description="Train a tiny GPT-style LM on memory.db text.")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"Path to memory.db (default: {DEFAULT_DB})")
    parser.add_argument("--topic", default="affordance-reasoning", help="Anchor topic to train on")
    parser.add_argument("--max-rows", type=int, default=1000, help="Max rows to load from memory.db")
    parser.add_argument("--epochs", type=int, default=5, help="Number of epochs")
    parser.add_argument("--batch-size", type=int, default=32, help="Batch size")
    parser.add_argument("--lr", type=float, default=3e-4, help="Learning rate")
    parser.add_argument("--block-size", type=int, default=128, help="Sequence length")
    parser.add_argument("--n-layers", type=int, default=4, help="Number of transformer layers")
    parser.add_argument("--n-heads", type=int, default=4, help="Number of attention heads")
    parser.add_argument("--d-model", type=int, default=128, help="Model dimension")
    parser.add_argument("--d-ff", type=int, default=512, help="Feed-forward dimension")
    parser.add_argument("--device", default="cpu", help="Device to use (cpu or cuda)")
    parser.add_argument("--max-steps", type=int, default=None, help="Max steps per epoch (for quick smoke tests)")
    parser.add_argument("--log-topic", default=None, help="If set, write a RESULT glyph to memory after training")
    parser.add_argument("--log-task", default=None, help="Optional task id to attach to the RESULT glyph")
    parser.add_argument("--tokenizer", choices=["char", "word"], default="char", help="Tokenizer to use (char or word)")
    parser.add_argument("--min-freq", type=int, default=1, help="Minimum token frequency (word tokenizer)")
    parser.add_argument("--vocab-limit", type=int, default=None, help="Maximum vocabulary size (word tokenizer, includes <unk>)")
    args = parser.parse_args()

    return TrainConfig(
        db=args.db,
        topic=args.topic,
        max_rows=args.max_rows,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        block_size=args.block_size,
        n_layers=args.n_layers,
        n_heads=args.n_heads,
        d_model=args.d_model,
        d_ff=args.d_ff,
        device=args.device,
        max_steps=args.max_steps,
        log_topic=args.log_topic,
        log_task=args.log_task,
        tokenizer=args.tokenizer,
        min_freq=args.min_freq,
        vocab_limit=args.vocab_limit,
    )


if __name__ == "__main__":
    cfg = parse_args()
    train(cfg)
