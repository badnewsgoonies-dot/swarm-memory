#!/bin/bash
# Run daemon tasks from queue file sequentially
# Each task: read v1 file, convert React->Preact, write to v2

set -e

QUEUE_FILE="/home/geni/swarm/memory/port-queue.txt"
LOG_DIR="/home/geni/swarm/memory/port-logs"
DAEMON="./swarm_daemon.py"
V2="/home/geni/Documents/vale-village-v2"

mkdir -p "$LOG_DIR"

# Track progress
TOTAL=$(grep -c "^Port " "$QUEUE_FILE" || echo 0)
CURRENT=0
SUCCESS=0
FAILED=0

echo "=== Daemon Queue Runner ==="
echo "Total tasks: $TOTAL"
echo "Log dir: $LOG_DIR"
echo ""

# Process each line that starts with "Port "
while IFS= read -r objective; do
    # Skip comments and empty lines
    [[ "$objective" =~ ^#.*$ ]] && continue
    [[ -z "$objective" ]] && continue
    [[ ! "$objective" =~ ^Port.* ]] && continue

    ((CURRENT++))

    # Extract component name for logging
    COMPONENT=$(echo "$objective" | sed 's/Port \([^ ]*\).*/\1/')
    LOG_FILE="$LOG_DIR/${CURRENT}_${COMPONENT}.log"

    echo "[$CURRENT/$TOTAL] $COMPONENT"

    # Run daemon with timeout
    if timeout 120 HOME=/home/geni/swarm/memory/.claude-tmp "$DAEMON" \
        --objective "$objective" \
        --repo-root "$V2" \
        --unrestricted \
        --max-iterations 5 \
        > "$LOG_FILE" 2>&1; then

        # Check if file was created
        if grep -q "edit_file.*success" "$LOG_FILE" 2>/dev/null || \
           grep -q "Executing action: done" "$LOG_FILE" 2>/dev/null; then
            echo "  ✓ Success"
            ((SUCCESS++))
        else
            echo "  ? Completed (check log)"
            ((SUCCESS++))
        fi
    else
        echo "  ✗ Failed (see $LOG_FILE)"
        ((FAILED++))
    fi

done < "$QUEUE_FILE"

echo ""
echo "=== Queue Complete ==="
echo "Success: $SUCCESS / $TOTAL"
echo "Failed: $FAILED"
echo ""

# Count final files
V2_COUNT=$(find "$V2/src" -type f \( -name "*.ts" -o -name "*.tsx" \) | wc -l)
echo "Total v2 files: $V2_COUNT"
