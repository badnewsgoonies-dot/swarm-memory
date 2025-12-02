#!/usr/bin/env bash
#
# session-start.sh - Generate and inject session briefing at chat start
#
# Hook event: PreToolUse (first tool) or can be manually triggered
# Outputs briefing to stdout for context injection
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIEFING_SCRIPT="${SCRIPT_DIR}/../mem-briefing.py"
BRIEFING_CACHE="${SCRIPT_DIR}/../.briefing-cache.md"
LOG_FILE="${SCRIPT_DIR}/../hooks.log"

log() { echo "[$(date -Iseconds)] [session-start] $*" >> "$LOG_FILE"; }

# Check if briefing was generated recently (within 5 minutes)
if [[ -f "$BRIEFING_CACHE" ]]; then
    CACHE_AGE=$(( $(date +%s) - $(stat -c %Y "$BRIEFING_CACHE" 2>/dev/null || echo 0) ))
    if [[ $CACHE_AGE -lt 300 ]]; then
        log "Using cached briefing (${CACHE_AGE}s old)"
        cat "$BRIEFING_CACHE"
        exit 0
    fi
fi

# Generate fresh briefing
if [[ -x "$BRIEFING_SCRIPT" ]]; then
    log "Generating fresh briefing..."
    python3 "$BRIEFING_SCRIPT" > "$BRIEFING_CACHE" 2>/dev/null
    if [[ -s "$BRIEFING_CACHE" ]]; then
        log "Briefing generated successfully"
        cat "$BRIEFING_CACHE"
    else
        log "Briefing generation produced empty output"
    fi
else
    log "ERROR: Briefing script not found or not executable: $BRIEFING_SCRIPT"
fi

exit 0
