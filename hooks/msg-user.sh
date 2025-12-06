#!/bin/bash
# Hook: Capture user prompts to memory
# Triggered on UserPromptSubmit

# Read the hook input from stdin
input=$(cat)

# Extract the user's message
message=$(echo "$input" | jq -r '.prompt // empty' 2>/dev/null)

# Skip if empty or too short
if [ -z "$message" ] || [ ${#message} -lt 20 ]; then
    exit 0
fi

# Count words
word_count=$(echo "$message" | wc -w)
if [ "$word_count" -lt 3 ]; then
    exit 0
fi

# Escape for storage
escaped_msg=$(echo "$message" | head -c 500)

# Write to memory (assumes hook runs from repo root)
./mem-db.sh write t=c topic=chat choice=user text="$escaped_msg" 2>/dev/null

exit 0
