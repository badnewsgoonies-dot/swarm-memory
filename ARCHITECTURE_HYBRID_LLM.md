# Hybrid Local/API LLM Architecture Design

## Overview

This document describes a tiered LLM architecture for the swarm daemon system that intelligently routes tasks between local models and API services to optimize for cost, latency, and quality.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         DAEMON REQUEST                           │
│                    (action + prompt + context)                   │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                      LLM ROUTER                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ 1. Task Classification (simple/moderate/complex)         │   │
│  │ 2. Tier Selection (fast/quality/api)                     │   │
│  │ 3. Model Selection (first available in tier)             │   │
│  │ 4. Fallback Chain Construction                           │   │
│  └──────────────────────────────────────────────────────────┘   │
└────────────────────────────┬────────────────────────────────────┘
                             │
            ┌────────────────┼────────────────┐
            │                │                │
            ▼                ▼                ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│   TIER 1        │ │   TIER 2        │ │   TIER 3        │
│  LOCAL FAST     │ │ LOCAL QUALITY   │ │ API FALLBACK    │
│                 │ │                 │ │                 │
│ • Phi-3 Mini    │ │ • Mixtral 8x7B  │ │ • Claude 4.5    │
│ • Llama 3 8B    │ │ • Llama 3 70B   │ │ • GPT-4o        │
│                 │ │ • Codestral     │ │ • Claude Haiku  │
│                 │ │                 │ │                 │
│ Cost: $0        │ │ Cost: $0        │ │ Cost: $2.5-3/M  │
│ Speed: 50-200ms │ │ Speed: 200-2s   │ │ Speed: 1-5s     │
└────────┬────────┘ └────────┬────────┘ └────────┬────────┘
         │                   │                   │
         │                   │                   │
         └─────────┬─────────┴─────────┬─────────┘
                   │                   │
                   ▼                   │
         ┌──────────────────┐          │
         │ QUALITY CHECKER  │          │
         │                  │          │
         │ • Format valid?  │          │
         │ • Complete?      │──FAIL────┘
         │ • Coherent?      │   (fallback to next tier)
         │ • Confidence >   │
         │   threshold?     │
         └────────┬─────────┘
                  │
                  ▼ PASS
         ┌──────────────────┐
         │    RESPONSE      │
         │   + USAGE STATS  │
         └──────────────────┘
```

## Task Classification

### Simple Tasks → LOCAL_FAST (Tier 1)

**Characteristics:**
- Binary decisions (yes/no)
- Classification (1 of N categories)
- Simple extraction (topic, tags, entities)
- Validation (format checks)

**Examples:**
```
Action: classify_memory
Prompt: "Is this a decision or a fact? 'We chose React over Vue'"
Model: Phi-3 Mini (50ms, $0)

Action: extract_topic
Prompt: "Extract topic from: 'Fixed authentication bug in login flow'"
Model: Llama 3 8B (100ms, $0)

Action: yes_no_decision
Prompt: "Should we retry this action? Previous attempts: 3, Error: timeout"
Model: Phi-3 Mini (50ms, $0)
```

**Quality Requirements:** Low - Can tolerate occasional errors
**Latency Target:** < 200ms
**Cost:** $0 (local inference)

---

### Moderate Tasks → LOCAL_QUALITY (Tier 2)

**Characteristics:**
- Code generation (functions, components)
- Multi-step reasoning (2-3 steps)
- Summarization (< 500 lines)
- File editing with context

**Examples:**
```
Action: edit_file
Prompt: "Add error handling to this function: [code]"
Model: Mixtral 8x7B (500ms, $0)

Action: write_memory
Prompt: "Summarize these 5 decisions into a coherent note"
Model: Codestral 22B (800ms, $0)

Action: consolidate
Prompt: "Merge these 3 duplicate memory entries: [entries]"
Model: Mixtral 8x7B (600ms, $0)
```

**Quality Requirements:** Medium - Should be correct most of the time
**Latency Target:** 200ms - 2s
**Cost:** $0 (local inference)

---

### Complex Tasks → API_FALLBACK (Tier 3)

**Characteristics:**
- Orchestration (spawn sub-daemons)
- Error recovery and debugging
- Multi-file refactoring
- Critical decisions affecting system state
- Complex reasoning (> 3 steps)

**Examples:**
```
Action: spawn_daemon
Prompt: "ORCHESTRATE: Implement user authentication with tests"
Model: Claude Sonnet 4.5 (2s, $0.003)

Action: debug_error
Prompt: "Analyze this stack trace and suggest fixes: [trace]"
Model: GPT-4o (3s, $0.0025)

Action: multi_file_refactor
Prompt: "Refactor component hierarchy: [10 files]"
Model: Claude Sonnet 4.5 (4s, $0.006)
```

**Quality Requirements:** High - Must be correct, system-critical
**Latency Target:** 1-5s
**Cost:** $0.8-3.0 per 1M tokens

---

## Routing Decision Flow

```python
def route_request(action_type, prompt, context):
    # Step 1: Classify complexity
    complexity = classify_task(action_type, context)
    # Returns: SIMPLE | MODERATE | COMPLEX

    # Step 2: Check quality criticality
    quality_critical = (
        context.get("affects_system_state") or
        context.get("user_visible_output") or
        action_type in ["spawn_daemon", "exec"]
    )

    # Step 3: Select tier
    if quality_critical:
        tier = API_FALLBACK
    elif complexity == SIMPLE:
        tier = LOCAL_FAST
    elif complexity == MODERATE:
        tier = LOCAL_QUALITY if config.prefer_local else API_FALLBACK
    else:  # COMPLEX
        tier = API_FALLBACK

    # Step 4: Build fallback chain
    fallback_chain = []
    if tier == LOCAL_FAST:
        fallback_chain = [LOCAL_QUALITY, API_FALLBACK]
    elif tier == LOCAL_QUALITY:
        fallback_chain = [API_FALLBACK]

    return tier, fallback_chain
```

## Quality Check Mechanism

### Confidence Scoring

Each response gets a confidence score (0.0-1.0) based on:

1. **Format Validation (30%)**: Is the response in expected format (JSON for actions, code for edits)?
2. **Completeness (20%)**: Does it have all required fields/content?
3. **Coherence (30%)**: No contradictions, hallucinations, or refusals?
4. **Self-Critique (20%)**: Fast model validates the response

```python
def check_quality(response, action_type, context):
    confidence = 1.0
    issues = []

    # Format check
    if action_type requires JSON:
        if not valid_json(response):
            confidence -= 0.3
            issues.append("Invalid JSON")

    # Completeness check
    if len(response) < min_length:
        confidence -= 0.2
        issues.append("Too short")

    # Hallucination detection
    if has_refusal_markers(response):
        confidence -= 0.4
        issues.append("Possible hallucination")

    # Self-critique (optional)
    if confidence < 0.7:
        critique = ask_fast_model("Is this valid?", response)
        if "NO" in critique:
            confidence -= 0.2
            issues.append(critique)

    return QualityCheckResult(
        passed=(confidence >= quality_threshold),
        confidence=confidence,
        issues=issues
    )
```

### Fallback Triggers

Fallback to next tier happens when:
- **Quality check fails** (confidence < 0.7)
- **Model call fails** (timeout, error, crash)
- **Response empty** or malformed

## Configuration Schema

See `llm_config.yaml` for full configuration. Key sections:

### Model Definitions
```yaml
tiers:
  local_fast:
    models:
      - name: "phi3-mini"
        provider: "ollama"
        model_id: "phi3:mini"
        cost_per_1k_tokens: 0.0
        enabled: true
```

### Routing Rules
```yaml
routing:
  prefer_local: true
  quality_threshold: 0.7
  max_fallback_attempts: 2
  enable_caching: true
```

### Action Overrides
```yaml
routing:
  action_tier_overrides:
    spawn_daemon: "api_fallback"  # Always API
    orch_status: "local_fast"     # Always local
```

## Cost Analysis

### Baseline: Pure API Usage

**Assumptions:**
- 100 daemon iterations/day
- Average prompt: 1000 tokens
- Average completion: 500 tokens
- Total: 150k tokens/day
- Cost: $0.45/day ($13.50/month)

### Hybrid Architecture Savings

**Tier Distribution (estimated):**
- 40% Simple tasks → LOCAL_FAST ($0)
- 35% Moderate tasks → LOCAL_QUALITY ($0)
- 25% Complex/fallback → API_FALLBACK ($0.11/day)

**Total cost:** $0.11/day ($3.30/month)
**Savings:** $10.20/month (75.5%)

### Cost Breakdown by Action Type

| Action Type | Frequency | Pure API | Hybrid | Savings |
|-------------|-----------|----------|--------|---------|
| write_memory | 30/day | $0.135 | $0 | 100% |
| read_file | 20/day | $0.090 | $0 | 100% |
| list_files | 15/day | $0.068 | $0 | 100% |
| edit_file | 10/day | $0.045 | $0.011 | 75% |
| spawn_daemon | 5/day | $0.068 | $0.068 | 0% |
| consolidate | 10/day | $0.045 | $0.011 | 75% |
| **Total** | **100/day** | **$0.45** | **$0.11** | **75.5%** |

### Real-World Cost Scenarios

**Scenario 1: Light usage (50 iterations/day)**
- Pure API: $6.75/month
- Hybrid: $1.65/month
- Savings: $5.10/month (75%)

**Scenario 2: Medium usage (200 iterations/day)**
- Pure API: $27.00/month
- Hybrid: $6.60/month
- Savings: $20.40/month (75%)

**Scenario 3: Heavy usage (500 iterations/day)**
- Pure API: $67.50/month
- Hybrid: $16.50/month
- Savings: $51.00/month (75%)

**Break-even analysis:**
- Cost to self-host Llama 3 70B: ~$50/month (cloud GPU)
- Break-even point: ~200 iterations/day
- Below 200/day: Use Ollama on local machine ($0 marginal cost)
- Above 200/day: Cloud GPU pays for itself

## Quality vs Cost Tradeoff

```
Quality ▲
   1.0  │                                    ┌─── API (Claude)
        │                              ┌─────┤
   0.9  │                        ┌─────┤     └─── API (GPT-4)
        │                  ┌─────┤     └───────── Local 70B
   0.8  │            ┌─────┤     └───────────── Mixtral
        │      ┌─────┤     └─────────────────── Codestral
   0.7  │┌─────┤     └───────────────────────── Llama 8B
        │      └─────────────────────────────── Phi-3
   0.6  │
        └────────────────────────────────────────────────► Cost
         $0       $0        $0.0005   $0.001   $0.003
                          (local GPU) (cloud)  (API)
```

## Latency Analysis

**Tier 1 (LOCAL_FAST):**
- Phi-3 Mini: 50-100ms (4K context)
- Llama 3 8B: 100-200ms (8K context)

**Tier 2 (LOCAL_QUALITY):**
- Mixtral 8x7B: 200-800ms (32K context)
- Llama 3 70B: 500-2000ms (8K context, requires GPU)

**Tier 3 (API_FALLBACK):**
- Claude Haiku: 500-1500ms
- Claude Sonnet: 1000-3000ms
- GPT-4o: 1000-5000ms

**Critical path optimization:**
- Use async calls for non-blocking operations
- Cache frequent queries (ollama has built-in caching)
- Batch similar requests when possible

## Integration with swarm_daemon.py

### Modified call_llm Function

```python
from llm_router import LLMRouter

# Initialize router once
router = LLMRouter(config_path="llm_config.yaml")

def call_llm(prompt, verbose=False, action_type="unknown", context=None):
    """Call LLM with hybrid routing"""
    context = context or {}

    # Use router with quality check
    response, decision, quality, stats = router.route_with_quality_check(
        prompt,
        action_type=action_type,
        context=context
    )

    if verbose:
        logger.info(f"Routed to: {decision.model.name} ({decision.tier.value})")
        logger.info(f"Quality: {quality.confidence:.2f} ({quality.reasoning})")
        logger.info(f"Cost: ${stats[-1].cost:.6f}")

    return response
```

### Modified build_prompt Function

Add action type hint to prompt:

```python
def build_prompt(state, repo_root, unrestricted, last_results=None, action_type="unknown"):
    # ... existing prompt building ...

    # Pass action_type to call_llm
    return prompt, action_type
```

### Modified run_daemon Loop

```python
def run_daemon(state, repo_root, unrestricted, verbose=False):
    # ... existing setup ...

    while True:
        # ... rate limiting, kill switch ...

        prompt, action_type = build_prompt(state, repo_root, unrestricted, last_results)

        # Call with routing
        response = call_llm(
            prompt,
            verbose=verbose,
            action_type=action_type,
            context={
                "iteration": state.iteration,
                "unrestricted": unrestricted,
                "history": state.history[-3:]
            }
        )

        # ... rest of loop ...
```

## Monitoring and Metrics

### Real-time Metrics

```python
# Get cost summary
summary = router.get_cost_summary()

print(f"Total cost today: ${summary['total_cost']:.4f}")
print(f"Savings vs API-only: ${summary['savings']:.4f} ({summary['savings_pct']:.1f}%)")
print(f"Tier breakdown:")
for tier, stats in summary['tier_stats'].items():
    print(f"  {tier}: {stats['calls']} calls, ${stats['total_cost']:.4f}")
```

### Export Usage Log

```python
# Export to JSONL for analysis
router.export_usage_log("llm_usage.jsonl")

# Analyze with standard tools
# jq '.[] | select(.tier == "api_fallback")' llm_usage.jsonl
# python analyze_costs.py llm_usage.jsonl
```

### Dashboard Metrics

Track in memory database:

```bash
# Record LLM usage
./mem-db.sh write t=a topic=llm_usage \
  text="Tier: local_fast, Model: phi3, Cost: $0, Latency: 50ms, Quality: 0.85"

# Query usage patterns
./mem-db.sh query topic=llm_usage recent=24h limit=100
```

## Setup Instructions

### 1. Install Local Models (Ollama)

```bash
# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh

# Pull models
ollama pull phi3:mini        # 3.8GB
ollama pull llama3:8b        # 4.7GB
ollama pull mixtral:8x7b     # 26GB
ollama pull codestral:22b    # 12GB

# Test
ollama run phi3:mini "Hello, test"
```

### 2. Optional: Setup vLLM (for Llama 70B)

```bash
# Install vLLM
pip install vllm

# Run server
python -m vllm.entrypoints.openai.api_server \
  --model meta-llama/Meta-Llama-3-70B-Instruct \
  --port 8000 \
  --gpu-memory-utilization 0.9

# Requires: 80GB+ GPU RAM (A100 or H100)
```

### 3. Configure API Keys

```bash
# Add to ~/.bashrc or daemon environment
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
```

### 4. Test Router

```bash
# Test routing
python llm_router.py \
  --prompt "Extract topic from: Implemented async caching" \
  --action classify_memory \
  --quality-check

# Should route to LOCAL_FAST (phi3-mini)

python llm_router.py \
  --prompt "Generate a Python function to parse JSON" \
  --action edit_file \
  --quality-check

# Should route to LOCAL_QUALITY (mixtral or codestral)
```

### 5. Integrate with Daemon

```bash
# Update swarm_daemon.py imports
# (see integration section above)

# Test with local models
./swarm_daemon.py \
  --objective "Classify recent memory entries" \
  --max-iterations 5 \
  --verbose

# Monitor costs
tail -f llm_usage.jsonl | jq '.'
```

## Fallback Scenarios

### Scenario 1: Local Model Fails

```
Request: "Generate Python function"
Try: Mixtral 8x7B → TIMEOUT (180s)
Fallback: Claude Sonnet 4.5 → SUCCESS (2s, $0.003)
Result: Response from API, cost tracked
```

### Scenario 2: Quality Check Fails

```
Request: "Edit config file"
Try: Mixtral 8x7B → Quality: 0.55 (FAIL)
Fallback: Claude Sonnet 4.5 → Quality: 0.92 (PASS)
Result: Higher quality response, acceptable cost
```

### Scenario 3: All Models Fail

```
Request: "Complex orchestration"
Try: Claude Sonnet → RATE_LIMIT
Fallback: GPT-4o → RATE_LIMIT
Fallback: Claude Haiku → SUCCESS
Result: Degraded quality but completes task
```

## Future Enhancements

### 1. Adaptive Routing

Learn from past successes/failures:
- Track quality scores by action type
- Adjust tier selection based on historical performance
- A/B test different routing strategies

### 2. Model Specialization

Train/fine-tune local models for specific tasks:
- Memory classification model (LoRA adapter)
- Code generation model (domain-specific fine-tune)
- Consolidation model (trained on examples)

### 3. Batch Processing

Group similar requests:
- Batch 10 classification tasks → single prompt
- Reduce overhead, improve throughput
- Better utilize local model capacity

### 4. Smart Caching

Cache responses by prompt hash:
- Exact match: return cached response (free)
- Semantic match: use embeddings to find similar (0.9+ similarity)
- Reduces redundant calls by ~30%

### 5. Multi-Model Consensus

For critical decisions:
- Run 3 models (2 local + 1 API)
- Take majority vote or highest confidence
- Improves accuracy by 15-20%

## Conclusion

The hybrid local/API architecture provides:

- **75% cost savings** vs pure API usage
- **10x faster** for simple tasks (50ms vs 500ms)
- **Automatic quality assurance** via fallback chain
- **Graceful degradation** when local models fail
- **Zero marginal cost** for most operations

Recommended configuration:
- Enable Phi-3 Mini and Llama 3 8B (always)
- Enable Mixtral 8x7B if >50 iterations/day
- Enable Llama 3 70B if >200 iterations/day (cloud GPU)
- Always enable Claude/GPT as fallback

This architecture makes autonomous daemon operation economically viable for continuous use.
