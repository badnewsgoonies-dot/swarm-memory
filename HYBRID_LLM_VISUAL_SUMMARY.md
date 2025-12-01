# Hybrid LLM Architecture - Visual Summary

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         SWARM DAEMON SYSTEM                                  │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    DAEMON LOOP (run_daemon)                         │   │
│  │                                                                      │   │
│  │  1. Build Prompt (objective + context + history)                    │   │
│  │  2. Classify Action Type (simple/moderate/complex)                  │   │
│  │  3. Call LLM Router ────────────────────┐                           │   │
│  │  4. Parse Response                      │                           │   │
│  │  5. Execute Action                      │                           │   │
│  │  6. Update State                        │                           │   │
│  │  7. Record Stats                        │                           │   │
│  └──────────────────────────────────────────┼──────────────────────────┘   │
│                                             │                               │
└─────────────────────────────────────────────┼───────────────────────────────┘
                                              │
                                              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           LLM ROUTER                                         │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │ STEP 1: TASK CLASSIFICATION                                        │    │
│  │   Input: action_type, prompt, context                              │    │
│  │   Output: SIMPLE | MODERATE | COMPLEX                              │    │
│  │                                                                     │    │
│  │   Rules:                                                            │    │
│  │   • classification, yes/no, extraction      → SIMPLE               │    │
│  │   • code gen, reasoning, summarization      → MODERATE             │    │
│  │   • orchestration, multi-file, critical     → COMPLEX              │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                 ▼                                            │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │ STEP 2: TIER SELECTION                                             │    │
│  │                                                                     │    │
│  │   SIMPLE    → LOCAL_FAST      (Phi-3, Llama 8B)                    │    │
│  │   MODERATE  → LOCAL_QUALITY   (Mixtral, Codestral)                 │    │
│  │   COMPLEX   → API_FALLBACK    (Claude, GPT-4)                      │    │
│  │                                                                     │    │
│  │   Overrides:                                                        │    │
│  │   • quality_critical=true     → API_FALLBACK (always)              │    │
│  │   • prefer_local=false        → skip to API                        │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                 ▼                                            │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │ STEP 3: MODEL SELECTION                                            │    │
│  │   Get first enabled model in selected tier                         │    │
│  │   Build fallback chain (next tiers)                                │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                 ▼                                            │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │ STEP 4: CALL MODEL                                                 │    │
│  │   Execute: ollama | vllm | claude | openai                         │    │
│  │   Track: tokens, cost, latency                                     │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                 ▼                                            │
│  ┌────────────────────────────────────────────────────────────────────┐    │
│  │ STEP 5: QUALITY CHECK                                              │    │
│  │                                                                     │    │
│  │   Checks:                            Score:                        │    │
│  │   ✓ Format valid (JSON/code)         0.3                           │    │
│  │   ✓ Complete (has all fields)        0.2                           │    │
│  │   ✓ Coherent (no hallucinations)     0.3                           │    │
│  │   ✓ Self-critique (fast model)       0.2                           │    │
│  │                                      ────                           │    │
│  │                            Total:    1.0 (confidence)               │    │
│  │                                                                     │    │
│  │   Pass if: confidence >= 0.7                                       │    │
│  └────────────────────────────────────────────────────────────────────┘    │
│                                 ▼                                            │
│                        ┌────────┴────────┐                                  │
│                        │                 │                                  │
│                   PASS │                 │ FAIL                             │
│                        ▼                 ▼                                  │
│               ┌────────────┐    ┌──────────────┐                           │
│               │   RETURN   │    │  FALLBACK    │                           │
│               │  RESPONSE  │    │  NEXT TIER   │                           │
│               └────────────┘    └──────┬───────┘                           │
│                                        │                                    │
│                                        └──────► (repeat from STEP 4)        │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Cost Comparison Flow

```
                    PURE API ARCHITECTURE
                    ═══════════════════════

Every Request → Claude Sonnet 4.5 → $3 per 1M tokens

                         ▼

100 iterations/day × 1500 tokens avg × $3/1M = $0.45/day
                                              = $13.50/month
                                              = $162/year


                    HYBRID ARCHITECTURE
                    ═══════════════════

Request → Router → Tier Selection
                       │
                       ├─→ 40% SIMPLE → Phi-3 Mini (LOCAL) → $0
                       │
                       ├─→ 35% MODERATE → Mixtral (LOCAL) → $0
                       │
                       └─→ 25% COMPLEX → Claude (API) → $3/1M
                                          +5% fallback

                         ▼

(40 × $0) + (35 × $0) + (25 × $0.45) + (5% fallback × $0.45)
= $0 + $0 + $0.11 + $0.02
= $0.13/day
= $3.90/month
= $46.80/year

SAVINGS: $115.20/year (71%)
```

## Decision Tree

```
                          START
                            │
                            ▼
                  ┌──────────────────┐
                  │ Classify Action  │
                  │   Complexity     │
                  └────────┬─────────┘
                           │
         ┌─────────────────┼─────────────────┐
         │                 │                 │
         ▼                 ▼                 ▼
    ┌────────┐       ┌──────────┐      ┌──────────┐
    │SIMPLE  │       │MODERATE  │      │COMPLEX   │
    │        │       │          │      │          │
    │• list  │       │• edit    │      │• spawn   │
    │• tag   │       │• gen     │      │• orch    │
    │• y/n   │       │• reason  │      │• critical│
    └───┬────┘       └─────┬────┘      └─────┬────┘
        │                  │                  │
        ▼                  ▼                  ▼
  ┌──────────┐       ┌──────────┐      ┌──────────┐
  │ TIER 1   │       │ TIER 2   │      │ TIER 3   │
  │ LOCAL    │       │ LOCAL    │      │   API    │
  │  FAST    │       │ QUALITY  │      │ FALLBACK │
  └────┬─────┘       └─────┬────┘      └─────┬────┘
       │                   │                  │
       ▼                   ▼                  ▼
  ┌─────────┐         ┌─────────┐        ┌─────────┐
  │ Phi-3   │         │Mixtral  │        │ Claude  │
  │ Llama8B │         │Codestral│        │ GPT-4   │
  └────┬────┘         └─────┬───┘        └─────┬───┘
       │                    │                   │
       ▼                    ▼                   ▼
   50-200ms             200-2000ms           1000-5000ms
      $0                    $0              $0.002-0.006
```

## Quality Check Process

```
                    RESPONSE RECEIVED
                            │
                            ▼
        ┌───────────────────────────────────┐
        │     FORMAT VALIDATION             │
        │                                   │
        │  ┌──────────────────────────┐    │
        │  │ JSON for actions?        │────┼──→ Valid? +0.3
        │  │ Code syntax valid?       │    │    Invalid? -0.3
        │  └──────────────────────────┘    │
        └─────────────────┬─────────────────┘
                          │
                          ▼
        ┌───────────────────────────────────┐
        │   COMPLETENESS CHECK              │
        │                                   │
        │  ┌──────────────────────────┐    │
        │  │ Length > min?            │────┼──→ Yes? +0.2
        │  │ Has required fields?     │    │    No? -0.2
        │  └──────────────────────────┘    │
        └─────────────────┬─────────────────┘
                          │
                          ▼
        ┌───────────────────────────────────┐
        │   COHERENCE CHECK                 │
        │                                   │
        │  ┌──────────────────────────┐    │
        │  │ No refusals?             │────┼──→ Good? +0.3
        │  │ No hallucinations?       │    │    Issues? -0.4
        │  │ Makes sense?             │    │
        │  └──────────────────────────┘    │
        └─────────────────┬─────────────────┘
                          │
                          ▼
        ┌───────────────────────────────────┐
        │   SELF-CRITIQUE (optional)        │
        │                                   │
        │  ┌──────────────────────────┐    │
        │  │ Ask fast model:          │    │
        │  │ "Is this valid?"         │────┼──→ Yes? +0.2
        │  │                          │    │    No? -0.2
        │  └──────────────────────────┘    │
        └─────────────────┬─────────────────┘
                          │
                          ▼
                  ┌──────────────┐
                  │ CONFIDENCE   │
                  │   SCORE      │
                  │  (0.0-1.0)   │
                  └──────┬───────┘
                         │
                         ▼
              ┌──────────┴──────────┐
              │                     │
         >= 0.7                  < 0.7
              │                     │
              ▼                     ▼
         ┌─────────┐          ┌──────────┐
         │  PASS   │          │   FAIL   │
         │ RETURN  │          │ FALLBACK │
         └─────────┘          └──────────┘
```

## Cost vs Quality Trade-off

```
Quality
   │
1.0│                                    ╔═══════════════╗
   │                               ╔════╣ Claude Sonnet ║
   │                          ╔════╣    ╚═══════════════╝
0.9│                     ╔════╣    ║
   │                ╔════╣    ║    ╚════ GPT-4o
   │           ╔════╣    ║    ╚══════════ Llama 70B
0.8│      ╔════╣    ║    ╚═══════════════ Mixtral 8x7b
   │ ╔════╣    ║    ╚════════════════════ Codestral
0.7│╔╣    ║    ╚═════════════════════════ Llama 8B
   ││╚════╩═══════════════════════════════ Phi-3 Mini
0.6││
   │└─────────────────────────────────────────────────────►
   0     $0        $0        $0       $0.0005  $0.001 $0.003
              (local CPU)        (local GPU) (cloud) (API)
                                                       Cost

Legend:
  ╔═══╗  API models (pay per token)
  ║   ║  Local models (one-time setup)
  ╚═══╝
```

## Tier Distribution (Real Usage)

```
LIGHT USAGE (50 iterations/day)
═══════════════════════════════

Tier 1: ████████████ 40%  (20 calls) - $0.00
Tier 2: ██████████   35%  (17 calls) - $0.00
Tier 3: █████        25%  (13 calls) - $0.04

Total: $0.04/day | $1.20/month


MEDIUM USAGE (200 iterations/day)
══════════════════════════════════

Tier 1: ████████ 30%  (60 calls) - $0.00
Tier 2: ████████████ 45%  (90 calls) - $0.00
Tier 3: █████        25%  (50 calls) - $0.15

Total: $0.15/day | $4.50/month


HEAVY USAGE (500 iterations/day)
═════════════════════════════════

Tier 1: ████ 20%  (100 calls) - $0.00
Tier 2: ████████████ 50%  (250 calls) - $0.00
Tier 3: ██████       30%  (150 calls) - $0.45

Total: $0.45/day | $13.50/month
```

## Latency Comparison

```
Response Time Distribution (ms)

Phi-3 Mini:      ▓▓▓▓░░░░░░░░░░░░░░░░  50-100ms
Llama 8B:        ▓▓▓▓▓▓░░░░░░░░░░░░░░  100-200ms
Mixtral:         ▓▓▓▓▓▓▓▓▓▓▓░░░░░░░░░  200-800ms
Codestral:       ▓▓▓▓▓▓▓▓▓░░░░░░░░░░░  200-600ms
Llama 70B:       ▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░░  500-2000ms
Claude Haiku:    ▓▓▓▓▓▓▓▓▓▓░░░░░░░░░░  500-1500ms
Claude Sonnet:   ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░░░░  1000-3000ms
GPT-4o:          ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓░░  1000-5000ms

0    500   1000  1500  2000  2500  3000  3500  4000  4500  5000


Speedup vs Claude (avg):
Phi-3:     25x faster  ████████████████████████
Llama 8B:  12x faster  ████████████
Mixtral:    5x faster  █████
Llama 70B:  2x faster  ██
```

## Fallback Chain Example

```
REQUEST: "Generate Python function to parse JSON"
ACTION: edit_file
COMPLEXITY: moderate

Attempt 1: Mixtral 8x7B (LOCAL_QUALITY)
┌─────────────────────────────────────┐
│ Model: mixtral:8x7b                 │
│ Time: 800ms                         │
│ Cost: $0                            │
│                                     │
│ Response: [code with syntax error]  │
│                                     │
│ Quality Check:                      │
│   Format: ✓ (valid Python)          │
│   Complete: ✓ (has function)        │
│   Coherent: ✗ (syntax error)        │
│   Critique: ✗ (failed validation)   │
│                                     │
│ Confidence: 0.55 < 0.7 → FAIL       │
└─────────────────────────────────────┘
              │
              ▼ FALLBACK
              │
Attempt 2: Claude Sonnet 4.5 (API_FALLBACK)
┌─────────────────────────────────────┐
│ Model: claude-sonnet-4.5            │
│ Time: 2100ms                        │
│ Cost: $0.003                        │
│                                     │
│ Response: [correct code]            │
│                                     │
│ Quality Check:                      │
│   Format: ✓ (valid Python)          │
│   Complete: ✓ (has function)        │
│   Coherent: ✓ (clean code)          │
│   Critique: ✓ (passed validation)   │
│                                     │
│ Confidence: 0.92 >= 0.7 → PASS      │
└─────────────────────────────────────┘
              │
              ▼
         RETURN RESPONSE

Total: 2 attempts, $0.003, 2.9s
Fallback justified: Quality improved from 0.55 → 0.92
```

## Model Capacity Comparison

```
Model Characteristics Matrix

                    Size   Speed   Quality  Cost   Use Case
                    ────   ─────   ───────  ────   ────────
Phi-3 Mini          3.8GB  ★★★★★   ★★       FREE   Classification
Llama 3 8B          4.7GB  ★★★★    ★★★      FREE   Simple tasks
Mixtral 8x7B        26GB   ★★★     ★★★★     FREE   Code gen
Codestral 22B       12GB   ★★★     ★★★★     FREE   Code specialist
Llama 3 70B         40GB   ★★      ★★★★★    FREE*  Quality local
Claude Haiku        API    ★★★     ★★★★     $     Budget API
Claude Sonnet       API    ★★      ★★★★★    $$$   Premium API
GPT-4o              API    ★★      ★★★★★    $$    Alternative

* Requires cloud GPU ($50/month) or powerful local GPU (48GB+ VRAM)

Recommendations by Tier:
  Tier 1: Phi-3 Mini + Llama 8B (always enable)
  Tier 2: Mixtral + Codestral (enable for 50+ iter/day)
  Tier 3: Claude Haiku → Sonnet (enable as fallback)
```

## Cost Projection (1 Year)

```
Usage Pattern: 100 iterations/day, 365 days

PURE API (Claude Sonnet 4.5)
────────────────────────────
  Jan  Feb  Mar  Apr  May  Jun  Jul  Aug  Sep  Oct  Nov  Dec
  $13  $13  $13  $13  $13  $13  $13  $13  $13  $13  $13  $13
  ████████████████████████████████████████████████████████████
                    Total: $162/year


HYBRID (Ollama Local + API Fallback)
─────────────────────────────────────
  Jan  Feb  Mar  Apr  May  Jun  Jul  Aug  Sep  Oct  Nov  Dec
  $4   $4   $4   $4   $4   $4   $4   $4   $4   $4   $4   $4
  ███████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
                    Total: $47/year


                    SAVINGS: $115/year (71%)
```

## Setup Checklist

```
PRE-DEPLOYMENT CHECKLIST
═══════════════════════

[ ] Hardware
    [ ] Check GPU: nvidia-smi (NVIDIA) or rocm-smi (AMD)
    [ ] Check RAM: >= 16GB for Mixtral, 8GB for Phi-3
    [ ] Check disk: >= 50GB free for models

[ ] Software
    [ ] Install Ollama: curl -fsSL ollama.com/install.sh | sh
    [ ] Test Ollama: ollama run phi3:mini "test"
    [ ] Install Python deps: pip install pyyaml requests

[ ] Models
    [ ] Pull Phi-3 Mini: ollama pull phi3:mini (3.8GB)
    [ ] Pull Llama 8B: ollama pull llama3:8b (4.7GB)
    [ ] Pull Mixtral: ollama pull mixtral:8x7b (26GB)
    [ ] Pull Codestral: ollama pull codestral:22b (12GB)

[ ] API Keys
    [ ] Set ANTHROPIC_API_KEY in environment
    [ ] Set OPENAI_API_KEY (optional)
    [ ] Test: claude -p "test" (should work)

[ ] Configuration
    [ ] Copy llm_config.yaml to project
    [ ] Adjust quality_threshold (default: 0.7)
    [ ] Set daily_budget (default: $10)
    [ ] Enable/disable models as needed

[ ] Testing
    [ ] Test router: python llm_router.py --help
    [ ] Run simulation: python cost_analysis.py --simulate
    [ ] Test classification: (see README_HYBRID_LLM.md)
    [ ] Test code generation: (see README_HYBRID_LLM.md)

[ ] Integration
    [ ] Backup swarm_daemon.py
    [ ] Add router import
    [ ] Modify call_llm()
    [ ] Add stats tracking
    [ ] Test with --max-iterations 5

[ ] Monitoring
    [ ] Check daemon_hybrid.log
    [ ] Track costs in memory DB
    [ ] Export usage log: router.export_usage_log()
    [ ] Review tier distribution weekly
```

## Success Metrics

```
EXPECTED RESULTS (after 1 week)
═══════════════════════════════

Cost Savings:
  ✓ Total cost < $5/week
  ✓ Savings vs API > 70%
  ✓ No budget overruns

Performance:
  ✓ Avg latency < 500ms
  ✓ 95% quality pass rate
  ✓ < 10% fallback rate

Distribution:
  ✓ 60%+ local calls
  ✓ 40%- API calls
  ✓ Tier 1: 20-40%
  ✓ Tier 2: 30-50%
  ✓ Tier 3: 20-40%

Quality:
  ✓ No critical failures
  ✓ Avg confidence > 0.8
  ✓ Self-critique pass > 90%
```
