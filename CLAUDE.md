# Memory System

This folder has a shared memory database. Use it to maintain context across chats.

**IMPORTANT: Use the CLI commands below. Do NOT read anchors.jsonl directly.**
The JSONL file won't fit in context as memory grows. The CLI filters and limits results.

## Query before answering
```bash
./mem-db.sh query t=d limit=5          # Recent decisions
./mem-db.sh query topic=<topic>        # By topic
./mem-db.sh query text=<keyword>       # Keyword search
```

## Record important things
```bash
./mem-db.sh write t=d topic=X text="Decision made" choice="chosen option"
./mem-db.sh write t=f topic=X text="Fact learned"
./mem-db.sh write t=q topic=X text="Open question"
```

## Types
- `d` = decision
- `q` = question
- `f` = fact
- `a` = action
- `n` = note

## Check status
```bash
./mem-db.sh status
```
