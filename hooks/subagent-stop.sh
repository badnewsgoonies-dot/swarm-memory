#!/bin/bash
# Hook: Log subagent stop events with transcript
# Triggered on SubagentStop

timestamp=$(date '+%H:%M:%S')
echo "[$timestamp] STOP agent=$CLAUDE_AGENT_ID" >> ./subagent.log

# Append transcript if available
if [ -n "$CLAUDE_AGENT_TRANSCRIPT_PATH" ] && [ -f "$CLAUDE_AGENT_TRANSCRIPT_PATH" ]; then
    cat "$CLAUDE_AGENT_TRANSCRIPT_PATH" >> ./subagent.log
fi
echo '---' >> ./subagent.log
