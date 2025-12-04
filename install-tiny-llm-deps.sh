#!/usr/bin/env bash
#
# install-tiny-llm-deps.sh - Install dependencies for tiny_llm.py / train_tiny_llm.py
#
# Usage:
#   chmod +x install-tiny-llm-deps.sh
#   ./install-tiny-llm-deps.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() {
    echo "[tiny-llm] $*"
}

error() {
    echo "[tiny-llm][ERROR] $*" >&2
    exit 1
}

PY_BIN=""
if command -v python3 >/dev/null 2>&1; then
    PY_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PY_BIN="python"
else
    error "Python is required but not found in PATH."
fi

log "Using Python: $PY_BIN ($($PY_BIN --version 2>&1))"

log "Checking for existing torch installation..."
if "$PY_BIN" - <<'PY' >/dev/null 2>&1
import torch  # type: ignore[import-not-found]
PY
then
    log "PyTorch already installed. Nothing to do."
    exit 0
fi

log "PyTorch not found. Installing with pip (user-level, using --break-system-packages)..."

if ! "$PY_BIN" -m pip --version >/dev/null 2>&1; then
    error "pip is not available for $PY_BIN. Install pip first (e.g., python -m ensurepip)."
fi

set -x
"$PY_BIN" -m pip install --user torch --break-system-packages
set +x

log "Verifying torch import..."
if "$PY_BIN" - <<'PY' >/dev/null 2>&1
import torch
print(torch.__version__)
PY
then
    log "PyTorch installation successful."
else
    error "PyTorch import still failing after installation."
fi

log "You can now run: python train_tiny_llm.py"
