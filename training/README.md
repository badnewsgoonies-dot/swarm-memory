# LLM Training on RunPod

Train your own fine-tuned LLM for $20-50 using LoRA + unsloth.

## Quick Start

### 1. Create RunPod Account
1. Go to https://runpod.io
2. Create account, add $25 credit
3. Go to "Pods" → "Deploy"

### 2. Select GPU Pod
**Recommended for 7B models:**
| GPU | VRAM | Cost | Training Time |
|-----|------|------|---------------|
| RTX A4000 | 16GB | ~$0.20/hr | ~2-4 hrs |
| RTX A5000 | 24GB | ~$0.30/hr | ~1-2 hrs |
| RTX A6000 | 48GB | ~$0.50/hr | ~30 min |

**Template:** Select `runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04`

### 3. Start Pod and Connect
1. Click "Connect" → "Start Web Terminal" or use SSH
2. Upload your files or clone from git

### 4. Install Dependencies
```bash
# Install unsloth (fast LoRA training)
pip install "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git"
pip install --no-deps trl peft accelerate bitsandbytes
pip install datasets transformers
```

### 5. Upload Your Data
Create a `data.jsonl` file with your training examples:

```jsonl
{"messages": [{"role": "user", "content": "Your question"}, {"role": "assistant", "content": "Ideal response"}]}
{"messages": [{"role": "user", "content": "Another question"}, {"role": "assistant", "content": "Another response"}]}
```

**Minimum:** 50-100 examples
**Recommended:** 500-1000 examples
**Quality > Quantity** - clean, consistent data matters most

### 6. Run Training
```bash
# Upload train_lora.py and data.jsonl first
python train_lora.py --dataset ./data.jsonl --base-model mistral --epochs 1

# Options:
#   --base-model: mistral, llama3, qwen2.5, phi3
#   --epochs: 1-3 (more = better fit, risk of overfit)
#   --lora-rank: 8-64 (higher = more capacity)
```

### 7. Download Your Model
After training completes:
```bash
# Zip the output
zip -r my_model.zip output/

# Download via RunPod file browser or:
# Use runpodctl or scp to download
```

### 8. Use in Ollama (Local)
```bash
# If GGUF was exported:
ollama create mymodel -f output/gguf/Modelfile
ollama run mymodel

# Or create Modelfile manually:
cat > Modelfile << 'EOF'
FROM mistral:7b
ADAPTER ./output
EOF
ollama create mymodel -f Modelfile
```

## Cost Estimate

| Dataset Size | GPU | Time | Cost |
|--------------|-----|------|------|
| 100 examples | A4000 | ~30 min | ~$0.10 |
| 500 examples | A4000 | ~1.5 hr | ~$0.30 |
| 1000 examples | A4000 | ~3 hr | ~$0.60 |
| 5000 examples | A5000 | ~4 hr | ~$1.20 |

**Tip:** Start with 100 examples to test, then scale up.

## Dataset Formats Supported

### Chat Format (recommended)
```json
{"messages": [
  {"role": "system", "content": "You are..."},
  {"role": "user", "content": "Question"},
  {"role": "assistant", "content": "Answer"}
]}
```

### Alpaca Format
```json
{"instruction": "What to do", "input": "Optional context", "output": "Expected response"}
```

### Raw Text
```json
{"text": "Full training text here"}
```

## Tips for Good Results

1. **Clean data** - Remove duplicates, fix typos
2. **Consistent format** - Same style across all examples
3. **Diverse examples** - Cover different scenarios
4. **Quality responses** - The model learns to mimic your outputs
5. **Test first** - Train on 50 examples, evaluate, then scale

## Troubleshooting

**Out of Memory:**
- Reduce `--batch-size` to 1
- Reduce `--max-seq-length` to 1024
- Use smaller base model (phi3)

**Poor Results:**
- Need more/better training data
- Try 2-3 epochs instead of 1
- Increase `--lora-rank` to 32 or 64

**Model Won't Load in Ollama:**
- Re-export GGUF with different quantization
- Check Modelfile syntax
