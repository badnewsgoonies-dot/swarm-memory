#!/usr/bin/env python3
"""
LoRA Fine-tuning Script for RunPod/Cloud GPUs
Uses unsloth for 2x faster training with 70% less VRAM

Usage:
    python train_lora.py --dataset ./data.jsonl --base-model mistral
    python train_lora.py --dataset ./data.jsonl --base-model llama3 --epochs 3
"""

import argparse
import json
import os

def main():
    parser = argparse.ArgumentParser(description="Fine-tune LLM with LoRA")
    parser.add_argument("--dataset", required=True, help="Path to JSONL training data")
    parser.add_argument("--base-model", default="mistral", choices=["mistral", "llama3", "qwen2.5", "phi3"])
    parser.add_argument("--output", default="./output", help="Output directory for model")
    parser.add_argument("--epochs", type=int, default=1, help="Training epochs")
    parser.add_argument("--batch-size", type=int, default=2, help="Batch size")
    parser.add_argument("--learning-rate", type=float, default=2e-4, help="Learning rate")
    parser.add_argument("--lora-rank", type=int, default=16, help="LoRA rank (8-64)")
    parser.add_argument("--max-seq-length", type=int, default=2048, help="Max sequence length")
    args = parser.parse_args()

    # Model mappings
    MODEL_MAP = {
        "mistral": "unsloth/mistral-7b-v0.3-bnb-4bit",
        "llama3": "unsloth/llama-3-8b-bnb-4bit",
        "qwen2.5": "unsloth/Qwen2.5-7B-bnb-4bit",
        "phi3": "unsloth/Phi-3.5-mini-instruct-bnb-4bit",
    }

    print(f"Loading base model: {args.base_model}")

    # Import unsloth (must be first import)
    from unsloth import FastLanguageModel
    from datasets import Dataset
    from trl import SFTTrainer
    from transformers import TrainingArguments
    import torch

    # Load model with 4-bit quantization
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=MODEL_MAP[args.base_model],
        max_seq_length=args.max_seq_length,
        dtype=None,  # Auto-detect
        load_in_4bit=True,
    )

    # Add LoRA adapters
    model = FastLanguageModel.get_peft_model(
        model,
        r=args.lora_rank,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                       "gate_proj", "up_proj", "down_proj"],
        lora_alpha=args.lora_rank,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=42,
    )

    # Load dataset
    print(f"Loading dataset: {args.dataset}")
    with open(args.dataset, "r") as f:
        data = [json.loads(line) for line in f]

    # Format for training
    def format_prompt(example):
        if "messages" in example:
            # Chat format
            text = ""
            for msg in example["messages"]:
                role = msg["role"]
                content = msg["content"]
                if role == "system":
                    text += f"<|system|>\n{content}</s>\n"
                elif role == "user":
                    text += f"<|user|>\n{content}</s>\n"
                elif role == "assistant":
                    text += f"<|assistant|>\n{content}</s>\n"
            return {"text": text}
        elif "instruction" in example:
            # Alpaca format
            text = f"### Instruction:\n{example['instruction']}\n\n"
            if example.get("input"):
                text += f"### Input:\n{example['input']}\n\n"
            text += f"### Response:\n{example['output']}"
            return {"text": text}
        else:
            return {"text": example.get("text", str(example))}

    dataset = Dataset.from_list(data).map(format_prompt)
    print(f"Dataset size: {len(dataset)} examples")

    # Training config
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=args.max_seq_length,
        dataset_num_proc=2,
        packing=False,
        args=TrainingArguments(
            per_device_train_batch_size=args.batch_size,
            gradient_accumulation_steps=4,
            warmup_steps=5,
            num_train_epochs=args.epochs,
            learning_rate=args.learning_rate,
            fp16=not torch.cuda.is_bf16_supported(),
            bf16=torch.cuda.is_bf16_supported(),
            logging_steps=1,
            optim="adamw_8bit",
            weight_decay=0.01,
            lr_scheduler_type="linear",
            seed=42,
            output_dir=args.output,
            report_to="none",
        ),
    )

    # Train
    print("Starting training...")
    trainer_stats = trainer.train()
    print(f"Training complete! Loss: {trainer_stats.training_loss:.4f}")

    # Save LoRA adapter
    print(f"Saving model to {args.output}")
    model.save_pretrained(args.output)
    tokenizer.save_pretrained(args.output)

    # Export to GGUF for Ollama (optional)
    print("Exporting to GGUF format...")
    try:
        model.save_pretrained_gguf(
            f"{args.output}/gguf",
            tokenizer,
            quantization_method="q4_k_m"
        )
        print(f"GGUF saved to {args.output}/gguf")
        print("\nTo use in Ollama:")
        print(f"  ollama create mymodel -f {args.output}/gguf/Modelfile")
    except Exception as e:
        print(f"GGUF export failed (optional): {e}")

    print("\nDone! Your fine-tuned model is ready.")

if __name__ == "__main__":
    main()
