#!/usr/bin/env bash
#
# session-start.sh - Initialize swarm-memory environment for Claude Code
#
# This hook runs at the start of each Claude Code session (web or CLI).
# It sets up the Python virtual environment and initializes the memory database.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

log() {
    echo "[session-start] $*"
}

# =============================================================================
# ENVIRONMENT DETECTION
# =============================================================================

# Check if running in Claude Code remote environment
IS_REMOTE="${CLAUDE_CODE_REMOTE:-false}"

log "Starting swarm-memory environment setup (remote=$IS_REMOTE)"
log "Project root: $PROJECT_ROOT"

# =============================================================================
# PYTHON VIRTUAL ENVIRONMENT
# =============================================================================

VENV_DIR="$PROJECT_ROOT/.venv"

setup_venv() {
    if [[ ! -d "$VENV_DIR" ]]; then
        log "Creating Python virtual environment..."
        python3 -m venv "$VENV_DIR"
    fi

    log "Activating virtual environment..."
    source "$VENV_DIR/bin/activate"

    # Install/upgrade core dependencies
    if [[ -f "$PROJECT_ROOT/requirements.txt" ]]; then
        log "Installing Python dependencies..."
        pip install --quiet --upgrade pip
        pip install --quiet -r "$PROJECT_ROOT/requirements.txt"
    fi
}

# Only set up venv if sentence-transformers not already available
if ! python3 -c "import sentence_transformers" 2>/dev/null; then
    setup_venv
else
    log "Python dependencies already available"
    # Still activate if venv exists
    if [[ -d "$VENV_DIR" ]]; then
        source "$VENV_DIR/bin/activate"
    fi
fi

# =============================================================================
# MEMORY DATABASE
# =============================================================================

DB_PATH="${SWARM_MEMORY_DB:-$PROJECT_ROOT/memory.db}"

setup_database() {
    if [[ ! -f "$DB_PATH" ]]; then
        log "Initializing memory database..."
        "$PROJECT_ROOT/mem-db.sh" init
    else
        log "Memory database exists at $DB_PATH"
        # Run migrations if needed
        "$PROJECT_ROOT/mem-db.sh" migrate 2>/dev/null || true
    fi
}

setup_database

# =============================================================================
# ENVIRONMENT VARIABLES
# =============================================================================

# Load .env if it exists
if [[ -f "$PROJECT_ROOT/.env" ]]; then
    log "Loading environment from .env"
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi

# =============================================================================
# STATUS CHECK
# =============================================================================

log "Environment setup complete"

# Quick status check (non-blocking)
if [[ -f "$DB_PATH" ]]; then
    CHUNK_COUNT=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM chunks;" 2>/dev/null || echo "0")
    log "Memory database: $CHUNK_COUNT chunks"
fi

# Show Python version
PYTHON_VERSION=$(python3 --version 2>/dev/null || echo "unknown")
log "Python: $PYTHON_VERSION"

exit 0
