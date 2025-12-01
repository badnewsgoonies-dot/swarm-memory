#!/bin/bash
set -e

# ============================================================================
# Ollama Installation and Configuration Script for Linux Mint 22.2
# ============================================================================
#
# This script installs Ollama and configures it for use with the swarm daemon.
# It handles:
# - Installation without sudo (user-local install)
# - Systemd user service setup
# - Model downloads (fast + quality models)
# - Environment configuration
# - Health checks
# - Python integration testing
#
# Usage:
#   chmod +x install-ollama.sh
#   ./install-ollama.sh
#
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.local/bin"
OLLAMA_MODELS_DIR="$HOME/.ollama/models"

# Color output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# ============================================================================
# 1. SYSTEM CHECKS
# ============================================================================

log_info "Starting Ollama installation..."
log_info "System: $(cat /etc/os-release | grep PRETTY_NAME | cut -d'"' -f2)"
log_info "Available disk space: $(df -h /home/geni | tail -1 | awk '{print $4}')"

# Check disk space (need at least 10GB for models)
AVAIL_GB=$(df -BG /home/geni | tail -1 | awk '{print $4}' | sed 's/G//')
if [ "$AVAIL_GB" -lt 10 ]; then
    log_warn "Low disk space: ${AVAIL_GB}GB available. Models may require 5-10GB."
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# ============================================================================
# 2. INSTALL OLLAMA (USER-LOCAL, NO SUDO)
# ============================================================================

if command -v ollama &> /dev/null; then
    log_info "Ollama already installed: $(ollama --version)"
else
    log_info "Installing Ollama to $INSTALL_DIR..."
    mkdir -p "$INSTALL_DIR"

    # Download and install (official install script with OLLAMA_INSTALL_DIR override)
    log_info "Downloading Ollama binary..."
    curl -fsSL https://ollama.com/download/ollama-linux-amd64 -o "$INSTALL_DIR/ollama"
    chmod +x "$INSTALL_DIR/ollama"

    # Ensure it's in PATH
    if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
        log_warn "Adding $INSTALL_DIR to PATH in ~/.bashrc"
        echo '' >> ~/.bashrc
        echo '# Ollama' >> ~/.bashrc
        echo "export PATH=\"$INSTALL_DIR:\$PATH\"" >> ~/.bashrc
        export PATH="$INSTALL_DIR:$PATH"
    fi

    log_info "Ollama installed: $($INSTALL_DIR/ollama --version)"
fi

# ============================================================================
# 3. SYSTEMD USER SERVICE SETUP
# ============================================================================

log_info "Setting up systemd user service..."

# Create systemd user directory
mkdir -p ~/.config/systemd/user

# Create ollama service file
cat > ~/.config/systemd/user/ollama.service <<'EOF'
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
EOF

log_info "Reloading systemd user daemon..."
systemctl --user daemon-reload

log_info "Enabling and starting ollama service..."
systemctl --user enable ollama.service
systemctl --user start ollama.service

# Wait for service to start
log_info "Waiting for Ollama to start..."
sleep 3

# Check service status
if systemctl --user is-active --quiet ollama.service; then
    log_info "Ollama service is running"
else
    log_error "Ollama service failed to start"
    systemctl --user status ollama.service
    exit 1
fi

# ============================================================================
# 4. DOWNLOAD RECOMMENDED MODELS
# ============================================================================

log_info "Downloading recommended models for daemon use..."

# Fast model for classification and quick tasks
log_info "Pulling fast model: llama3.2:3b (smallest, fastest, ~2GB)"
ollama pull llama3.2:3b

# Quality model for reasoning and complex tasks
log_info "Pulling quality model: qwen2.5:14b (best local option, ~9GB)"
ollama pull qwen2.5:14b

log_info "Models installed:"
ollama list

# ============================================================================
# 5. ENVIRONMENT VARIABLES
# ============================================================================

log_info "Configuring environment variables..."

ENV_FILE="$SCRIPT_DIR/.env.ollama"
cat > "$ENV_FILE" <<EOF
# Ollama Configuration for Swarm Daemon
# Source this file: source .env.ollama

# Ollama server (local)
export OLLAMA_HOST=http://127.0.0.1:11434

# Models directory
export OLLAMA_MODELS=$HOME/.ollama/models

# Default models for daemon
export OLLAMA_FAST_MODEL=llama3.2:3b
export OLLAMA_QUALITY_MODEL=qwen2.5:14b

# For daemon integration (if using ollama instead of Claude)
# export LLM_PROVIDER=ollama
# export LLM_MODEL=qwen2.5:14b
EOF

log_info "Environment file created: $ENV_FILE"
log_warn "Add to your shell: source $ENV_FILE"

# ============================================================================
# 6. HEALTH CHECKS
# ============================================================================

log_info "Running health checks..."

# Check service status
log_info "Service status:"
systemctl --user status ollama.service --no-pager | head -10

# Check API endpoint
log_info "Testing API endpoint..."
if curl -s http://127.0.0.1:11434/api/tags | grep -q "models"; then
    log_info "API endpoint is responsive"
else
    log_error "API endpoint not responding"
    exit 1
fi

# Test fast model
log_info "Testing fast model (llama3.2:3b)..."
RESPONSE=$(curl -s http://127.0.0.1:11434/api/generate -d '{
  "model": "llama3.2:3b",
  "prompt": "Say hello in one word",
  "stream": false
}' | jq -r '.response' 2>/dev/null || echo "FAILED")

if [ "$RESPONSE" != "FAILED" ]; then
    log_info "Fast model test passed: $RESPONSE"
else
    log_error "Fast model test failed"
fi

# ============================================================================
# 7. PYTHON INTEGRATION TEST
# ============================================================================

log_info "Creating Python integration test script..."

cat > "$SCRIPT_DIR/test-ollama.py" <<'PYEOF'
#!/usr/bin/env python3
"""
Test script for Ollama Python integration
Tests both direct API calls and ollama package
"""

import json
import requests
import sys
from typing import Dict, Any

OLLAMA_HOST = "http://127.0.0.1:11434"
FAST_MODEL = "llama3.2:3b"
QUALITY_MODEL = "qwen2.5:14b"


def test_api_health() -> bool:
    """Test if Ollama API is responsive"""
    print("[TEST] Checking API health...")
    try:
        resp = requests.get(f"{OLLAMA_HOST}/api/tags", timeout=5)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            print(f"  ✓ API healthy, {len(models)} models available")
            return True
        else:
            print(f"  ✗ API returned status {resp.status_code}")
            return False
    except Exception as e:
        print(f"  ✗ API unreachable: {e}")
        return False


def test_generate(model: str, prompt: str) -> Dict[str, Any]:
    """Test generation with a model"""
    print(f"[TEST] Generating with {model}...")
    try:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_predict": 50
            }
        }
        resp = requests.post(
            f"{OLLAMA_HOST}/api/generate",
            json=payload,
            timeout=30
        )
        if resp.status_code == 200:
            result = resp.json()
            response_text = result.get("response", "").strip()
            print(f"  ✓ Response: {response_text[:100]}...")
            return result
        else:
            print(f"  ✗ Generation failed: {resp.status_code}")
            return {}
    except Exception as e:
        print(f"  ✗ Generation error: {e}")
        return {}


def test_chat(model: str, messages: list) -> Dict[str, Any]:
    """Test chat endpoint"""
    print(f"[TEST] Chat with {model}...")
    try:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False
        }
        resp = requests.post(
            f"{OLLAMA_HOST}/api/chat",
            json=payload,
            timeout=30
        )
        if resp.status_code == 200:
            result = resp.json()
            message = result.get("message", {})
            content = message.get("content", "").strip()
            print(f"  ✓ Chat response: {content[:100]}...")
            return result
        else:
            print(f"  ✗ Chat failed: {resp.status_code}")
            return {}
    except Exception as e:
        print(f"  ✗ Chat error: {e}")
        return {}


def test_daemon_integration():
    """Test daemon-style prompt/response cycle"""
    print("[TEST] Testing daemon integration pattern...")

    # Simulate daemon action decision
    context = """
You are an autonomous agent. Respond with a JSON action.

Available actions:
- {"action": "write_memory", "type": "f", "topic": "test", "text": "..."}
- {"action": "done", "summary": "..."}

Objective: Record that Ollama is working.

Respond with ONE JSON action only.
"""

    result = test_generate(FAST_MODEL, context)
    if result:
        response = result.get("response", "")
        # Try to parse JSON from response
        try:
            # Extract JSON (might be wrapped in markdown)
            if "```json" in response:
                json_str = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                json_str = response.split("```")[1].split("```")[0].strip()
            else:
                json_str = response.strip()

            action = json.loads(json_str)
            print(f"  ✓ Parsed action: {action.get('action', 'unknown')}")
            return True
        except json.JSONDecodeError as e:
            print(f"  ⚠ Response not valid JSON: {e}")
            return False
    return False


def main():
    print("=" * 60)
    print("Ollama Python Integration Tests")
    print("=" * 60)

    tests = [
        ("API Health", test_api_health),
        ("Fast Model Generate", lambda: test_generate(FAST_MODEL, "Explain what you are in one sentence.")),
        ("Quality Model Generate", lambda: test_generate(QUALITY_MODEL, "What is the capital of France? Answer in one word.")),
        ("Chat Endpoint", lambda: test_chat(FAST_MODEL, [
            {"role": "user", "content": "Hello! Say hi back."}
        ])),
        ("Daemon Integration", test_daemon_integration)
    ]

    passed = 0
    failed = 0

    for name, test_func in tests:
        print(f"\n--- {name} ---")
        try:
            result = test_func()
            if result or result is None:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  ✗ Test exception: {e}")
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
PYEOF

chmod +x "$SCRIPT_DIR/test-ollama.py"
log_info "Python test script created: $SCRIPT_DIR/test-ollama.py"

# Run the test
log_info "Running Python integration tests..."
if command -v python3 &> /dev/null; then
    python3 "$SCRIPT_DIR/test-ollama.py"
else
    log_warn "Python3 not found, skipping integration tests"
fi

# ============================================================================
# 8. USAGE INSTRUCTIONS
# ============================================================================

cat <<EOF

========================================================================
Ollama Installation Complete!
========================================================================

Models Installed:
  - Fast:    llama3.2:3b     (~2GB, for quick classification/parsing)
  - Quality: qwen2.5:14b     (~9GB, for reasoning/complex tasks)

Service Management:
  systemctl --user status ollama   # Check status
  systemctl --user restart ollama  # Restart service
  systemctl --user stop ollama     # Stop service
  systemctl --user start ollama    # Start service

Health Checks:
  curl http://127.0.0.1:11434/api/tags           # List models
  ollama list                                    # List models (CLI)
  ollama run llama3.2:3b "Hello"                # Test fast model
  ollama run qwen2.5:14b "Explain AI"           # Test quality model

Python Integration:
  ./test-ollama.py                               # Run integration tests

Environment Setup:
  source $ENV_FILE

Daemon Integration:
  The swarm daemon currently uses Claude CLI by default.
  To use Ollama instead, you would need to modify swarm_daemon.py
  to add an "ollama" provider option that calls the API directly.

Model Recommendations:
  - Use llama3.2:3b for: Quick actions, JSON parsing, classification
  - Use qwen2.5:14b for: Complex reasoning, code analysis, planning

Disk Usage:
  du -sh ~/.ollama                               # Check model storage

Logs:
  journalctl --user -u ollama -f                 # Follow service logs

Next Steps:
  1. Test the models: ollama run llama3.2:3b
  2. Source environment: source .env.ollama
  3. Run integration tests: ./test-ollama.py
  4. Consider daemon integration (requires code changes)

========================================================================
EOF

log_info "Installation script complete!"
