#!/bin/bash
# Hook: Record file edits to memory
# Triggered on PostToolUse for Write|Edit

# Read the hook input from stdin
input=$(cat)

# Extract file path from the tool result
file_path=$(echo "$input" | jq -r '.tool_input.file_path // .tool_input.path // empty' 2>/dev/null)

# Skip if no file path
if [ -z "$file_path" ]; then
    exit 0
fi

# Get just the filename
filename=$(basename "$file_path")

# Write to memory (assumes hook runs from repo root)
./mem-db.sh write t=a topic=edits text="Edited: $filename" 2>/dev/null

exit 0
