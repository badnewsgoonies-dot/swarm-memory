#!/usr/bin/env bash
#
# print-hud.sh - Project Heads-Up Display
#
# Generates a unified HUD showing:
#   - Top 5 OPEN tasks (type=T, choice=OPEN)
#   - Active Mandates (importance=Critical or type=Mandate)
#
# Usage:
#   ./hooks/print-hud.sh [--json]
#
# Output format (default):
#   ASCII box showing task list and mandates
#
# Options:
#   --json    Output in JSON format for programmatic use
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEM_DB="${SCRIPT_DIR}/../mem-db.sh"

JSON_OUTPUT=0
for arg in "$@"; do
    [[ "$arg" == "--json" ]] && JSON_OUTPUT=1
done

# Check mem-db.sh exists
if [[ ! -x "$MEM_DB" ]]; then
    echo "ERROR: mem-db.sh not found at $MEM_DB" >&2
    exit 1
fi

# Query top 5 OPEN tasks
# Type T = Todo, choice = OPEN
get_open_tasks() {
    "$MEM_DB" query t=T choice=OPEN limit=5 --json 2>/dev/null || echo ""
}

# Query active mandates
# Critical importance entries are considered "mandates" - they represent
# important decisions or rules that should always be visible
get_mandates() {
    # Get Critical importance entries only (these are the true mandates)
    "$MEM_DB" query importance=Critical limit=5 --json 2>/dev/null || echo ""
}

# Parse task JSON and extract fields
# Format: [type, topic, text, choice, rationale, timestamp, session, source, scope, chat_id, agent_role, visibility, project_id, importance, due, links, task_id, metric]
parse_task_json() {
    local line="$1"
    python3 -c "
import json
import sys
try:
    data = json.loads('''$line''')
    # Array format from query --json
    if isinstance(data, list) and len(data) >= 4:
        topic = data[1] or 'general'
        text = (data[2] or '')[:60].replace('\n', ' ')
        choice = data[3] or ''
        importance = data[13] if len(data) > 13 else ''
        due = data[14] if len(data) > 14 else ''
        task_id = data[16] if len(data) > 16 else ''
        # Format: [task_id/topic] text (priority)
        priority = 'HIGH' if importance in ['H', 'Critical'] else 'NORMAL'
        id_label = task_id if task_id else topic
        print(f'{id_label}|{text}|{priority}|{due}')
except:
    pass
" 2>/dev/null
}

# Format output as ASCII HUD
print_ascii_hud() {
    local tasks="$1"
    local mandates="$2"
    
    echo "╔════════════════════════════════════════════════════════════════════╗"
    echo "║                    PROJECT HUD - SOURCE OF TRUTH                    ║"
    echo "╠════════════════════════════════════════════════════════════════════╣"
    
    # Tasks section
    echo "║ ▶ OPEN TASKS                                                        ║"
    echo "║ ─────────────────────────────────────────────────────────────────── ║"
    
    local task_count=0
    if [[ -n "$tasks" ]]; then
        while IFS= read -r line; do
            [[ -z "$line" ]] && continue
            parsed=$(parse_task_json "$line")
            [[ -z "$parsed" ]] && continue
            
            IFS='|' read -r id text priority due <<< "$parsed"
            task_count=$((task_count + 1))
            
            # Format task line with priority indicator
            local priority_mark="  "
            [[ "$priority" == "HIGH" ]] && priority_mark="★ "
            
            # Truncate and pad for alignment
            id="${id:0:12}"
            text="${text:0:40}"
            printf "║ %s%-12s │ %-40s │ %s ║\n" "$priority_mark" "$id" "$text" "${priority:0:6}"
        done <<< "$tasks"
    fi
    
    if [[ $task_count -eq 0 ]]; then
        echo "║   (No open tasks)                                                   ║"
    fi
    
    echo "║                                                                      ║"
    echo "║ ▶ ACTIVE MANDATES                                                    ║"
    echo "║ ─────────────────────────────────────────────────────────────────── ║"
    
    # Mandates section
    local mandate_count=0
    if [[ -n "$mandates" ]]; then
        while IFS= read -r line; do
            [[ -z "$line" ]] && continue
            parsed=$(parse_task_json "$line")
            [[ -z "$parsed" ]] && continue
            
            IFS='|' read -r id text priority due <<< "$parsed"
            mandate_count=$((mandate_count + 1))
            
            # Truncate text for mandates
            text="${text:0:55}"
            printf "║ ⚡ %-60s ║\n" "$text"
        done <<< "$mandates"
    fi
    
    if [[ $mandate_count -eq 0 ]]; then
        echo "║   (No active mandates)                                              ║"
    fi
    
    echo "╠════════════════════════════════════════════════════════════════════╣"
    echo "║ Update tasks via: write_memory with type=T, choice=DONE            ║"
    echo "╚════════════════════════════════════════════════════════════════════╝"
}

# Format output as JSON
print_json_hud() {
    local tasks="$1"
    local mandates="$2"
    
    python3 - "$tasks" "$mandates" <<'PYEOF'
import json
import sys

tasks_raw = sys.argv[1]
mandates_raw = sys.argv[2]

def parse_entries(raw):
    entries = []
    for line in raw.strip().split('\n'):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            if isinstance(data, list) and len(data) >= 4:
                entries.append({
                    "type": data[0],
                    "topic": data[1],
                    "text": data[2],
                    "choice": data[3],
                    "importance": data[13] if len(data) > 13 else None,
                    "due": data[14] if len(data) > 14 else None,
                    "task_id": data[16] if len(data) > 16 else None
                })
        except:
            continue
    return entries

hud = {
    "source": "PROJECT_HUD",
    "open_tasks": parse_entries(tasks_raw),
    "mandates": parse_entries(mandates_raw)
}

print(json.dumps(hud, indent=2))
PYEOF
}

main() {
    local tasks
    local mandates
    
    tasks=$(get_open_tasks)
    mandates=$(get_mandates)
    
    if [[ $JSON_OUTPUT -eq 1 ]]; then
        print_json_hud "$tasks" "$mandates"
    else
        print_ascii_hud "$tasks" "$mandates"
    fi
}

main "$@"
