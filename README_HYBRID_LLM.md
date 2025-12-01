# Hybrid Local/API LLM Architecture

## Quick Start

### 1. Install Dependencies

```bash
# Install Ollama (for local models)
curl -fsSL https://ollama.com/install.sh | sh

# Pull local models
ollama pull phi3:mini        # Fast tier (3.8GB)
ollama pull llama3:8b        # Fast tier (4.7GB)
ollama pull mixtral:8x7b     # Quality tier (26GB)
ollama pull codestral:22b    # Quality tier for code (12GB)

# Install Python dependencies
pip install pyyaml requests  # For router
pip install openai          # Optional: if using OpenAI
```

### 2. Test Local Models

```bash
# Test Ollama installation
ollama run phi3:mini "What is 2+2?"

# Should respond in < 1 second
```

### 3. Configure API Keys

```bash
# Add to ~/.bashrc
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."  # Optional
```

### 4. Test Router

```bash
# Test routing with quality check
python llm_router.py \
  --prompt "Extract topic from: Implemented async caching for memory queries" \
  --action classify_memory \
  --quality-check

# Expected output:
# === Response ===
# async-caching-memory
#
# === Routing ===
# Tier: local_fast
# Model: phi3-mini
# Cost: $0.000000
# Latency: 87ms
```

### 5. Run Cost Analysis

```bash
# Simulate 30 days of usage
python cost_analysis.py --simulate --days 30

# Output shows savings vs pure API:
# Pure API: $14,220/month
# Hybrid:   $1,993/month
# Savings:  86%
```

### 6. Run Hybrid Daemon (Example)

```bash
# Test hybrid daemon
python swarm_daemon_hybrid_example.py \
  --objective "Classify recent memory entries by topic" \
  --verbose

# Check status with cost breakdown
python swarm_daemon_hybrid_example.py --status
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    DAEMON REQUEST                            │
│               (action + prompt + context)                    │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
              ┌──────────────────┐
              │   LLM ROUTER     │
              │  - Classify task │
              │  - Select tier   │
              │  - Quality check │
              └────┬────┬────┬───┘
                   │    │    │
        ┌──────────┘    │    └──────────┐
        │               │               │
        ▼               ▼               ▼
  ┌─────────┐    ┌──────────┐    ┌──────────┐
  │ TIER 1  │    │ TIER 2   │    │ TIER 3   │
  │ LOCAL   │    │ LOCAL    │    │   API    │
  │  FAST   │    │ QUALITY  │    │ FALLBACK │
  │         │    │          │    │          │
  │ Phi-3   │    │ Mixtral  │    │ Claude   │
  │ Llama8B │    │Codestral │    │  GPT-4   │
  │         │    │          │    │          │
  │ 50-200ms│    │ 200-2s   │    │  1-5s    │
  │   $0    │    │   $0     │    │$2.5-3/M  │
  └─────────┘    └──────────┘    └──────────┘
```

## Files

### Core Components

- **`llm_router.py`** - Main routing logic with quality checks
- **`llm_config.yaml`** - Tiered model configuration
- **`cost_analysis.py`** - Cost simulation and analysis
- **`swarm_daemon_hybrid_example.py`** - Integration example

### Documentation

- **`ARCHITECTURE_HYBRID_LLM.md`** - Detailed architecture design
- **`README_HYBRID_LLM.md`** - This file (quick start guide)

## Configuration

Edit `llm_config.yaml` to customize:

### Enable/Disable Models

```yaml
tiers:
  local_fast:
    models:
      - name: "phi3-mini"
        enabled: true   # Set to false to disable
```

### Adjust Quality Threshold

```yaml
routing:
  quality_threshold: 0.7  # 0.0-1.0, higher = stricter
```

### Set Cost Limits

```yaml
cost_control:
  daily_budget: 10.0      # USD
  alert_threshold: 80     # % of budget
```

### Override Action Routing

```yaml
routing:
  action_tier_overrides:
    spawn_daemon: "api_fallback"  # Always use API
    list_files: "local_fast"      # Always use fast local
```

## Usage Patterns

### Pattern 1: Simple Classification

**Task:** Extract topic from text
**Tier:** LOCAL_FAST (Phi-3 Mini)
**Cost:** $0
**Latency:** 50-100ms

```python
response = router.route(
    prompt="Extract topic from: Fixed auth bug",
    action_type="classify_memory"
)
# Routes to: phi3-mini
# Response: "auth-bug-fix"
```

### Pattern 2: Code Generation

**Task:** Generate Python function
**Tier:** LOCAL_QUALITY (Codestral)
**Cost:** $0
**Latency:** 500-1000ms

```python
response = router.route(
    prompt="Write a function to validate email addresses",
    action_type="edit_file"
)
# Routes to: codestral
# Response: [complete function code]
```

### Pattern 3: Complex Orchestration

**Task:** Spawn sub-daemon with complex objective
**Tier:** API_FALLBACK (Claude Sonnet)
**Cost:** $0.003
**Latency:** 2-3s

```python
response = router.route(
    prompt="ORCHESTRATE: Implement user auth with tests",
    action_type="spawn_daemon"
)
# Routes to: claude-sonnet-4.5
# Response: [detailed orchestration plan]
```

### Pattern 4: Quality Check with Fallback

**Task:** Generate code, ensure quality
**Tier:** LOCAL_QUALITY → API_FALLBACK (on failure)

```python
response, decision, quality, stats = router.route_with_quality_check(
    prompt="Refactor this component for performance",
    action_type="edit_file",
    max_fallback_attempts=2
)

# Try 1: mixtral (quality: 0.65) → FAIL
# Try 2: claude-sonnet (quality: 0.92) → PASS
# Total cost: $0.003
```

## Cost Breakdown

### Realistic Usage Scenario

**Assumptions:**
- 100 daemon iterations/day
- Action distribution:
  - 30% write_memory (LOCAL_QUALITY)
  - 20% read_file (LOCAL_QUALITY)
  - 15% list_files (LOCAL_FAST)
  - 10% edit_file (LOCAL_QUALITY)
  - 10% consolidate (LOCAL_QUALITY)
  - 5% spawn_daemon (API_FALLBACK)
  - 10% other (mixed)

### Monthly Costs

| Architecture | Cost/Month | Savings |
|--------------|------------|---------|
| Pure API (Claude Sonnet) | $135.00 | - |
| Hybrid (Ollama local) | $16.50 | 87.8% |
| Hybrid (Cloud GPU 70B) | $66.00 | 51.1% |

### Break-Even Analysis

**Cloud GPU cost:** ~$50/month (Lambda Labs, RunPod)

- **< 50 iterations/day:** Use Ollama on local machine
- **50-200 iterations/day:** Use Ollama + occasional API
- **200+ iterations/day:** Cloud GPU pays for itself
- **500+ iterations/day:** Deploy vLLM cluster

## Integration with swarm_daemon.py

### Minimal Integration

Replace `call_llm()` in swarm_daemon.py:

```python
from llm_router import LLMRouter

# Initialize once
router = LLMRouter(config_path="llm_config.yaml")

def call_llm(prompt, verbose=False, action_type="unknown"):
    """Call LLM with hybrid routing"""
    response, decision, quality, stats = router.route_with_quality_check(
        prompt,
        action_type=action_type
    )

    if verbose:
        logger.info(f"Routed to: {decision.model.name}")
        logger.info(f"Cost: ${sum(s.cost for s in stats):.6f}")

    return response
```

### Full Integration

See `swarm_daemon_hybrid_example.py` for complete example with:
- Cost tracking in daemon state
- Quality metrics logging
- Tier distribution stats
- Cost summary to memory

## Monitoring

### Real-Time Stats

```python
# Get cost summary
summary = router.get_cost_summary()

print(f"Total cost: ${summary['total_cost']:.4f}")
print(f"Savings: {summary['savings_pct']:.1f}%")
print(f"Tier stats:")
for tier, stats in summary['tier_stats'].items():
    print(f"  {tier}: {stats['calls']} calls")
```

### Export Usage Log

```python
# Export to JSONL
router.export_usage_log("llm_usage.jsonl")

# Analyze with jq
# jq '.[] | select(.tier == "api_fallback")' llm_usage.jsonl
```

### Track in Memory

```bash
# Record LLM usage to memory
./mem-db.sh write t=a topic=llm_cost \
  text="Tier: local_fast, Cost: $0, Quality: 0.85"

# Query recent costs
./mem-db.sh query topic=llm_cost recent=24h
```

## Troubleshooting

### Issue: Ollama not found

```bash
# Check Ollama is running
ollama list

# If not installed:
curl -fsSL https://ollama.com/install.sh | sh

# Start Ollama service
ollama serve
```

### Issue: Model not found

```bash
# Pull missing model
ollama pull phi3:mini

# List available models
ollama list
```

### Issue: Quality checks always fail

```yaml
# Lower quality threshold in llm_config.yaml
routing:
  quality_threshold: 0.6  # Was 0.7
```

### Issue: Too many API fallbacks

```yaml
# Enable more local models
tiers:
  local_quality:
    models:
      - name: "mixtral-8x7b"
        enabled: true  # Was false

# Or disable self-critique (reduces false negatives)
routing:
  enable_self_critique: false
```

### Issue: High latency

```bash
# Check Ollama is using GPU
ollama run phi3:mini --verbose

# If CPU-only, install CUDA/ROCm:
# NVIDIA: Install CUDA 11.8+
# AMD: Install ROCm 5.7+
```

## Performance Tuning

### Optimize for Speed

```yaml
# Use fastest models only
tiers:
  local_fast:
    models:
      - name: "phi3-mini"  # 50ms
        enabled: true
  local_quality:
    models:
      - name: "llama3-8b"  # 200ms (not Mixtral)
        enabled: true
```

### Optimize for Quality

```yaml
# Use highest quality local models
tiers:
  local_quality:
    models:
      - name: "llama3-70b"  # Requires cloud GPU
        enabled: true
      - name: "mixtral-8x7b"
        enabled: true
```

### Optimize for Cost

```yaml
# Use Claude Haiku for API fallback
tiers:
  api_fallback:
    models:
      - name: "claude-haiku"  # $0.8/M vs $3/M
        enabled: true
```

## Advanced Features

### Custom Quality Checks

Extend `check_quality()` in `llm_router.py`:

```python
def check_quality(self, response, action_type, context):
    # Add custom checks
    if action_type == "edit_file":
        # Check syntax
        if not self.validate_code_syntax(response):
            return QualityCheckResult(passed=False, ...)

    # Use parent implementation
    return super().check_quality(response, action_type, context)
```

### Response Caching

Enable caching for repeated prompts:

```yaml
routing:
  enable_caching: true
  cache_ttl: 3600  # 1 hour
```

### Multi-Model Consensus

For critical decisions, use multiple models:

```python
# In router configuration
routing:
  enable_consensus: true
  consensus_models: 3  # Use 3 models, take majority
  consensus_threshold: 0.66  # 2/3 must agree
```

## Next Steps

1. **Test locally:** Run `python llm_router.py --help`
2. **Simulate costs:** Run `python cost_analysis.py --simulate`
3. **Integrate:** Modify swarm_daemon.py (see example)
4. **Monitor:** Track costs in memory database
5. **Optimize:** Adjust tiers based on actual usage

## Resources

- [Ollama Documentation](https://ollama.com/docs)
- [vLLM Documentation](https://docs.vllm.ai/)
- [Claude API](https://docs.anthropic.com/)
- [OpenAI API](https://platform.openai.com/docs)

## Support

For issues or questions:
1. Check `daemon_hybrid.log` for errors
2. Run `python llm_router.py --help`
3. Review `llm_config.yaml` configuration
4. Test individual models: `ollama run phi3:mini "test"`
