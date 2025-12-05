#!/bin/bash
# Run this on your RunPod instance to set everything up

echo "=== Installing unsloth + dependencies ==="
pip install "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git" --quiet
pip install --no-deps trl peft accelerate bitsandbytes --quiet
pip install datasets transformers --quiet

echo "=== Verifying GPU ==="
python -c "import torch; print(f'GPU: {torch.cuda.get_device_name(0)}'); print(f'VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB')"

echo "=== Ready to train! ==="
echo "Usage: python train_lora.py --dataset ./data.jsonl --base-model mistral"
echo ""
echo "Base models available:"
echo "  - mistral  (7B, good all-rounder)"
echo "  - llama3   (8B, strong reasoning)"
echo "  - qwen2.5  (7B, good for code)"
echo "  - phi3     (3.8B, fast, less VRAM)"
