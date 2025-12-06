#!/bin/bash
# Hook: Log subagent start events
# Triggered on SubagentStart

timestamp=$(date '+%H:%M:%S')
echo "[$timestamp] START agent=$CLAUDE_AGENT_ID" >> ./subagent.log
