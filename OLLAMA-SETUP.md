# Ollama Installation and Configuration Guide

Complete guide for installing and configuring Ollama on Linux Mint 22.2 for use with the swarm daemon memory system.

## System Information

- **OS**: Linux Mint 22.2 (based on Ubuntu 24.04)
- **Package Manager**: apt
- **Sudo Access**: Not required (user-local installation)
- **Available Disk**: ~306GB (sufficient for models)
- **Installation Location**: `~/.local/bin/ollama`
- **Models Directory**: `~/.ollama/models`

## Quick Start (One Command)

```bash
./install-ollama.sh
```

This single script handles everything: installation, service setup, model downloads, health checks, and testing.

## Step-by-Step Manual Installation

If you prefer to understand each step or need to troubleshoot:

### 1. Install Ollama Binary

```bash
# Create local bin directory
mkdir -p ~/.local/bin

# Download Ollama
curl -fsSL https://ollama.com/download/ollama-linux-amd64 -o ~/.local/bin/ollama

# Make executable
chmod +x ~/.local/bin/ollama

# Add to PATH (add to ~/.bashrc for persistence)
export PATH="$HOME/.local/bin:$PATH"

# Verify installation
ollama --version
```

### 2. Set Up Systemd User Service

Create service file at `~/.config/systemd/user/ollama.service`:

```ini
[Unit]
Description=Ollama Service
After=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/ollama serve
Restart=always
RestartSec=3
Environment="OLLAMA_HOST=127.0.0.1:11434"
Environment="OLLAMA_MODELS=%h/.ollama/models"

[Install]
WantedBy=default.target
```

Enable and start the service:

```bash
# Reload systemd
systemctl --user daemon-reload

# Enable service to start on login
systemctl --user enable ollama.service

# Start service now
systemctl --user start ollama.service

# Check status
systemctl --user status ollama.service
```

### 3. Download Recommended Models

#### Fast Model (for quick tasks, classification, JSON parsing)

```bash
ollama pull llama3.2:3b
```

- **Size**: ~2GB
- **Speed**: Very fast
- **Use for**: Quick actions, lightweight classification, JSON generation
- **Context**: 128k tokens

#### Quality Model (for reasoning, complex analysis)

```bash
ollama pull qwen2.5:14b
```

- **Size**: ~9GB
- **Speed**: Moderate (still fast on modern hardware)
- **Use for**: Complex reasoning, code analysis, planning, detailed responses
- **Context**: 128k tokens
- **Why this model**: Best local option for quality/speed tradeoff

### 4. Configure Environment Variables

Create `.env.ollama` file:

```bash
cat > .env.ollama <<'EOF'
# Ollama Configuration
export OLLAMA_HOST=http://127.0.0.1:11434
export OLLAMA_MODELS=$HOME/.ollama/models
export OLLAMA_FAST_MODEL=llama3.2:3b
export OLLAMA_QUALITY_MODEL=qwen2.5:14b
EOF

# Source it
source .env.ollama

# Add to ~/.bashrc for persistence
echo "source $(pwd)/.env.ollama" >> ~/.bashrc
```

### 5. Health Checks

```bash
# Check service status
systemctl --user status ollama

# Test API endpoint
curl http://127.0.0.1:11434/api/tags

# List installed models
ollama list

# Test fast model
ollama run llama3.2:3b "Say hello in one word"

# Test quality model
ollama run qwen2.5:14b "What is 2+2? Answer in one word."
```

### 6. Run Python Integration Tests

```bash
# Run the automated test suite
./test-ollama.py
```

This tests:
- API connectivity
- Model generation (both fast and quality)
- Chat endpoint
- Daemon integration pattern (JSON action parsing)

## Model Recommendations

### For Swarm Daemon Use Cases

| Task Type | Recommended Model | Rationale |
|-----------|------------------|-----------|
| Action classification | `llama3.2:3b` | Fast, reliable for structured output |
| JSON parsing | `llama3.2:3b` | Quick response, good at formats |
| Code analysis | `qwen2.5:14b` | Better reasoning, code understanding |
| Planning/strategy | `qwen2.5:14b` | Superior reasoning capabilities |
| Quick queries | `llama3.2:3b` | Minimal latency |
| Complex reasoning | `qwen2.5:14b` | Worth the extra compute time |

### Alternative Models

If you need different tradeoffs:

```bash
# Ultra-fast, smaller (1GB)
ollama pull llama3.2:1b

# Medium quality/speed (4GB)
ollama pull llama3.2:7b

# Larger, more capable (22GB) - if you have disk space
ollama pull qwen2.5:32b

# Code-specialized (4GB)
ollama pull deepseek-coder:6.7b
```

## Integration with Swarm Daemon

### Current State

The swarm daemon currently supports:
- `claude` provider (default) - uses Claude CLI
- `codex` provider - uses Codex API

### Adding Ollama Support

To integrate Ollama, you would modify `swarm_daemon.py` to add an "ollama" provider:

```python
# In call_llm() function
if provider == "ollama":
    import requests
    model = model or os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")
    response = requests.post(
        "http://127.0.0.1:11434/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False
        }
    )
    return response.json()["response"]
```

Then use it:

```bash
./swarm_daemon.py --llm ollama --llm-model qwen2.5:14b --objective "Your task"
```

### Python API Usage

Direct API calls (no dependencies):

```python
import requests

def call_ollama(prompt, model="qwen2.5:14b"):
    response = requests.post(
        "http://127.0.0.1:11434/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_predict": 1000
            }
        },
        timeout=60
    )
    return response.json()["response"]
```

## Service Management

```bash
# Start service
systemctl --user start ollama

# Stop service
systemctl --user stop ollama

# Restart service
systemctl --user restart ollama

# Check status
systemctl --user status ollama

# View logs
journalctl --user -u ollama -f

# Disable service (prevent auto-start)
systemctl --user disable ollama

# Re-enable service
systemctl --user enable ollama
```

## Resource Usage

### Disk Space

```bash
# Check model storage
du -sh ~/.ollama

# List model sizes
ollama list

# Remove unused models
ollama rm model-name
```

Expected sizes:
- `llama3.2:3b`: ~2GB
- `qwen2.5:14b`: ~9GB
- Total: ~11GB

### Memory Usage

- **llama3.2:3b**: ~4GB RAM during inference
- **qwen2.5:14b**: ~12GB RAM during inference

### GPU Acceleration

Ollama automatically uses GPU if available (NVIDIA CUDA, AMD ROCm, or Apple Metal).

Check GPU usage:
```bash
# NVIDIA
nvidia-smi

# AMD
radeontop
```

## Troubleshooting

### Service Won't Start

```bash
# Check logs
journalctl --user -u ollama -n 50

# Verify binary
~/.local/bin/ollama --version

# Try manual start
~/.local/bin/ollama serve
```

### API Not Responding

```bash
# Check if service is running
systemctl --user is-active ollama

# Check port
ss -tlnp | grep 11434

# Test connection
curl -v http://127.0.0.1:11434/api/tags
```

### Model Download Fails

```bash
# Check disk space
df -h ~

# Try manual pull with verbose output
ollama pull llama3.2:3b --verbose

# Check network
curl -I https://ollama.com
```

### Slow Performance

1. Check if using GPU:
   ```bash
   journalctl --user -u ollama | grep -i gpu
   ```

2. Reduce model size:
   ```bash
   ollama pull llama3.2:1b  # Smaller, faster
   ```

3. Adjust generation parameters:
   ```bash
   # Lower num_predict for faster responses
   curl http://127.0.0.1:11434/api/generate -d '{
     "model": "llama3.2:3b",
     "prompt": "Hello",
     "stream": false,
     "options": {"num_predict": 50}
   }'
   ```

## Performance Benchmarks

Approximate inference times on modern hardware (CPU only):

| Model | Short Response (50 tokens) | Long Response (500 tokens) |
|-------|---------------------------|---------------------------|
| llama3.2:3b | 2-3 seconds | 15-20 seconds |
| qwen2.5:14b | 5-8 seconds | 40-60 seconds |

With GPU acceleration, these times are significantly reduced (2-5x faster).

## Security Considerations

1. **Local Only**: Ollama binds to 127.0.0.1 by default (not accessible from network)
2. **No Authentication**: Local API has no auth (fine for single-user systems)
3. **User Service**: Runs as your user (no elevated privileges needed)
4. **Model Safety**: Models downloaded from official Ollama registry

To expose over network (use with caution):
```bash
# Edit service file to bind to 0.0.0.0
Environment="OLLAMA_HOST=0.0.0.0:11434"

# Then restart
systemctl --user restart ollama
```

## Useful Commands Reference

```bash
# Model Management
ollama list                          # List installed models
ollama pull model-name               # Download a model
ollama rm model-name                 # Remove a model
ollama show model-name               # Show model details

# Running Models
ollama run model-name                # Interactive chat
ollama run model-name "prompt"       # Single prompt

# API Usage
curl http://127.0.0.1:11434/api/tags              # List models
curl http://127.0.0.1:11434/api/generate -d '{    # Generate
  "model": "llama3.2:3b",
  "prompt": "Hello",
  "stream": false
}'

# Service Management
systemctl --user status ollama       # Status
systemctl --user restart ollama      # Restart
journalctl --user -u ollama -f       # Logs

# Disk Management
du -sh ~/.ollama                     # Check storage
ollama list                          # See model sizes
```

## Next Steps

1. **Test the installation**: Run `./test-ollama.py`
2. **Try the models**: `ollama run llama3.2:3b` and `ollama run qwen2.5:14b`
3. **Monitor performance**: Check response times for your use case
4. **Consider daemon integration**: Modify `swarm_daemon.py` to add Ollama provider
5. **Optimize**: Adjust models based on speed/quality needs

## Additional Resources

- [Ollama Documentation](https://github.com/ollama/ollama)
- [Model Library](https://ollama.com/library)
- [API Reference](https://github.com/ollama/ollama/blob/main/docs/api.md)
- [Model Cards](https://ollama.com/library) - Detailed info on each model
