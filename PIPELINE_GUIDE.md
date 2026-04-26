# Agent Pipeline - Implementation Guide

## Overview

The agent pipeline is the core execution loop for Netpilot's autonomous failure diagnosis and remediation system.

**Flow**:
1. **Collect** telemetry from Kubernetes (KPIs, logs, alerts)
2. **Format** telemetry for LLM consumption
3. **Diagnose** using LLM with few-shot examples
4. **Validate** diagnosis JSON response
5. **Gate** each action against SLA policies
6. **Execute** first approved action
7. **Log** complete step record

## Architecture

### Core Components

#### 1. LLMClient (Abstraction Layer)
Unified interface for multiple LLM providers.

**Supported Providers**:
- **OpenAI**: GPT-4, GPT-4 Turbo, GPT-3.5 Turbo
- **Anthropic**: Claude-3 Opus, Claude-3 Sonnet, Claude-3 Haiku

**Features**:
- Provider auto-detection based on config
- Unified message format (converted for Anthropic)
- Automatic JSON response formatting
- Error handling and logging

**Usage**:
```python
from agent.pipeline import LLMClient

client = LLMClient(
    provider="openai",
    model="gpt-4",
    api_key="sk-...",
    temperature=0.3,
    max_tokens=2000,
)

messages = [
    {"role": "system", "content": "You are a Kubernetes expert..."},
    {"role": "user", "content": "Diagnose this failure..."},
]

response = client.call(messages)  # Returns JSON string
```

#### 2. PolicyGate (Validation)
Validates proposed remediation actions against SLA policies.

**Policies**:
- ✅ Reject `noop` actions (informational only)
- ✅ Require high confidence (>0.5) for high-impact actions (rollback, reroute)
- ✅ Rate-limit rollbacks (max 3 per service per hour)
- ✅ Risk estimation (low/medium/high)

**Usage**:
```python
from agent.pipeline import PolicyGate
from agent.models import RemediationAction

gate = PolicyGate()

decision = gate.validate(
    action=action,
    telemetry=telemetry_bundle,
    diagnosis=diagnosis_result,
)

if decision.approved:
    # Execute action
    pass
```

**PolicyDecision**:
```python
@dataclass
class PolicyDecision:
    approved: bool          # Whether action is allowed
    reason: str            # Human-readable reason
    risk_level: str        # "low", "medium", "high"
```

#### 3. Executor (Action Handler)
Maps DiagnosisResult actions to Kubernetes operations.

**Action Types**:
- `restart_pod` - Delete pod to trigger Kubernetes restart
- `scale_up` - Increase deployment replicas
- `reroute_traffic` - Update circuit breaker / service mesh config
- `rollback_deploy` - Revert deployment to previous version

**Usage**:
```python
from agent.pipeline import Executor

executor = Executor()

result = await executor.execute(action)
# Returns: ExecutionResult(status, message, execution_time_ms)

if result.status == "success":
    print(f"Action completed in {result.execution_time_ms:.1f}ms")
```

#### 4. AgentPipeline (Main Loop)
Orchestrates the complete diagnosis and remediation workflow.

**Main Method**: `async run_step()`
```python
from agent.pipeline import AgentPipeline

pipeline = AgentPipeline()
step = await pipeline.run_step()
# Returns: PipelineStep with full record
```

**Output**: `PipelineStep`
```python
@dataclass
class PipelineStep:
    timestamp: str                        # ISO timestamp
    telemetry_bundle: Dict               # Full telemetry
    telemetry_snapshot: str              # LLM-formatted
    diagnosis: Dict                      # DiagnosisResult
    gate_decisions: List[Dict]           # [{"action": {...}, "decision": {...}}]
    executed_action: Optional[Dict]      # First approved action
    executor_result: Optional[Dict]      # ExecutionResult
```

### Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│ Kubernetes Cluster                                          │
│  • Services (5 microservices)                               │
│  • Prometheus (metrics)                                     │
│  • Alertmanager (alerts)                                    │
└──────────────────────┬──────────────────────────────────────┘
                       │
        ┌──────────────▼──────────────┐
        │ TelemetryCollector (async)  │
        │ • Query Prometheus (KPIs)   │
        │ • Fetch alerts              │
        │ • Collect pod logs          │
        └──────────────┬──────────────┘
                       │
        ┌──────────────▼──────────────┐
        │ TelemetryFormatter          │
        │ • to_context_window()       │
        │ • Token-aware truncation    │
        │ • ~350 tokens typical       │
        └──────────────┬──────────────┘
                       │
        ┌──────────────▼──────────────┐
        │ AgentPrompts                │
        │ • System prompt (540 tokens)│
        │ • Few-shot examples         │
        │ • Message building          │
        └──────────────┬──────────────┘
                       │
        ┌──────────────▼──────────────┐
        │ LLM (OpenAI/Anthropic)      │
        │ • Process diagnosis prompt  │
        │ • Generate DiagnosisResult  │
        │ • Return JSON               │
        └──────────────┬──────────────┘
                       │
        ┌──────────────▼──────────────┐
        │ JSON Validation             │
        │ • Parse response            │
        │ • Validate schema           │
        │ • Create DiagnosisResult    │
        └──────────────┬──────────────┘
                       │
        ┌──────────────▼──────────────┐
        │ For Each Action:            │
        │ 1. PolicyGate.validate()    │
        │ 2. If approved:             │
        │    Executor.execute()       │
        │    (break after first)      │
        └──────────────┬──────────────┘
                       │
        ┌──────────────▼──────────────┐
        │ Log Step (JSONL)            │
        │ • logs/agent_steps.jsonl    │
        │ • Full record               │
        └──────────────────────────────┘
```

## Configuration

Configure via `config.py`:

```python
from config import get_config, set_config, NetpilotConfig

config = get_config()
```

### Environment Variables

```bash
# LLM Configuration
export NETPILOT_LLM_PROVIDER=openai          # or "anthropic"
export NETPILOT_LLM_MODEL=gpt-4              # Model name
export OPENAI_API_KEY=sk-...                 # For OpenAI
export ANTHROPIC_API_KEY=sk-ant-...          # For Anthropic

# Telemetry Configuration
export PROMETHEUS_URL=http://localhost:9090
export ALERTMANAGER_URL=http://localhost:5000

# Netpilot Configuration
export NETPILOT_LOG_DIR=logs
export NETPILOT_LOG_LEVEL=INFO
export NETPILOT_DEBUG=false
```

### Config Classes

**LLMConfig**:
```python
@dataclass
class LLMConfig:
    provider: str = "openai"
    model: str = "gpt-4"
    temperature: float = 0.3
    max_tokens: int = 2000
    max_retries: int = 3
```

**TelemetryConfig**:
```python
@dataclass
class TelemetryConfig:
    prometheus_url: str = "http://localhost:9090"
    alertmanager_url: str = "http://localhost:5000"
    collection_interval: int = 30
    context_window_tokens: int = 3000
```

**PolicyGateConfig**:
```python
@dataclass
class PolicyGateConfig:
    max_blast_radius_pct: float = 50.0
    max_error_rate_pct: float = 5.0
    max_latency_p99_ms: int = 1000
    rollback_history_window_hours: int = 1
    max_rollbacks_per_window: int = 3
```

## Usage Examples

### Single Step Execution

```python
import asyncio
from agent.pipeline import AgentPipeline

async def main():
    pipeline = AgentPipeline()
    step = await pipeline.run_step()
    
    print(f"Diagnosis: {step.diagnosis['root_cause']}")
    if step.executed_action:
        print(f"Executed: {step.executed_action['action_type']}")

asyncio.run(main())
```

**Run**:
```bash
cd /home/shailesh/Networks/netpilot
export OPENAI_API_KEY=sk-...
python -m agent.pipeline
```

### Continuous Mode

```bash
# Run pipeline every 30 seconds
python -m agent.pipeline continuous
```

**Output**:
```
2026-04-27 10:15:30,123 - agent.pipeline - INFO - ================================================================================
2026-04-27 10:15:30,124 - agent.pipeline - INFO - Starting pipeline step at 2026-04-27T10:15:30.123456
2026-04-27 10:15:30,125 - agent.pipeline - INFO - Step 1: Collecting telemetry...
2026-04-27 10:15:30,225 - agent.pipeline - INFO - Collected telemetry for 5 services
2026-04-27 10:15:30,226 - agent.pipeline - INFO - Step 2: Formatting telemetry...
2026-04-27 10:15:30,227 - agent.pipeline - INFO - Telemetry formatted (3456 chars)
2026-04-27 10:15:30,228 - agent.pipeline - INFO - Step 3: Calling LLM for diagnosis...
2026-04-27 10:15:30,456 - agent.pipeline - INFO - LLM response received (1234 chars)
2026-04-27 10:15:30,457 - agent.pipeline - INFO - Step 4: Validating diagnosis...
2026-04-27 10:15:30,458 - agent.pipeline - INFO - Diagnosis: pod crash due to OOM (confidence: 0.92)
2026-04-27 10:15:30,459 - agent.pipeline - INFO - Proposed 3 actions
2026-04-27 10:15:30,460 - agent.pipeline - INFO - Step 5: Submitting actions to policy gate...
2026-04-27 10:15:30,461 - agent.pipeline - INFO -   Action 1: restart_pod for notification-service (confidence: 0.88)
2026-04-27 10:15:30,462 - agent.pipeline - INFO -     Gate decision: True (Action restart_pod approved for notification-service)
2026-04-27 10:15:30,463 - agent.pipeline - INFO -   Executing action: restart_pod
2026-04-27 10:15:30,573 - agent.pipeline - INFO -     Execution result: success (100.1ms)
2026-04-27 10:15:30,574 - agent.pipeline - INFO - Pipeline step completed successfully
```

### Log Inspection

```bash
# View agent steps
tail -f logs/agent_steps.jsonl

# Pretty print last step
python -c "
import json
with open('logs/agent_steps.jsonl') as f:
    for line in f:
        step = json.loads(line)
        print(json.dumps(step, indent=2))
        break
"
```

**Log Format** (JSONL):
```json
{
  "timestamp": "2026-04-27T10:15:30.123456",
  "telemetry_bundle": {
    "timestamp": "2026-04-27T10:15:30.123456",
    "kpis": {...},
    "logs": {...},
    "alarms": [...]
  },
  "telemetry_snapshot": "# TELEMETRY (tokens:349/3000)\n{...}",
  "diagnosis": {
    "root_cause": "notification-service pod crashed due to OOM",
    "root_cause_confidence": 0.92,
    "remediation_actions": [...]
  },
  "gate_decisions": [
    {
      "action": {
        "action_type": "restart_pod",
        "target": "notification-service",
        "confidence": 0.88,
        ...
      },
      "decision": {
        "approved": true,
        "reason": "Action restart_pod approved for notification-service",
        "risk_level": "low"
      }
    }
  ],
  "executed_action": {
    "action_type": "restart_pod",
    "target": "notification-service",
    ...
  },
  "executor_result": {
    "action_type": "restart_pod",
    "target": "notification-service",
    "status": "success",
    "message": "Pod restarted successfully",
    "execution_time_ms": 100.1
  }
}
```

## LLM Provider Setup

### OpenAI

1. **Get API Key**:
   ```bash
   # From https://platform.openai.com/api-keys
   export OPENAI_API_KEY=sk-...
   ```

2. **Configure**:
   ```bash
   export NETPILOT_LLM_PROVIDER=openai
   export NETPILOT_LLM_MODEL=gpt-4  # or gpt-4-turbo, gpt-3.5-turbo
   ```

3. **Run**:
   ```bash
   python -m agent.pipeline
   ```

### Anthropic

1. **Get API Key**:
   ```bash
   # From https://console.anthropic.com/
   export ANTHROPIC_API_KEY=sk-ant-...
   ```

2. **Configure**:
   ```bash
   export NETPILOT_LLM_PROVIDER=anthropic
   export NETPILOT_LLM_MODEL=claude-3-opus  # or claude-3-sonnet, claude-3-haiku
   ```

3. **Run**:
   ```bash
   python -m agent.pipeline
   ```

## Token Budget Analysis

For 8K context window:

| Component | Tokens | % |
|-----------|--------|-----|
| System Prompt | ~540 | 21% |
| Few-shot Examples | ~1,364 | 53% |
| Telemetry Context | ~357 | 14% |
| **Total Input** | **~2,261** | **88%** |
| **LLM Reasoning** | **~5,700** | **12%** |

**Conservative Estimates**:
- 1 token ≈ 4 characters
- System prompt: 2,169 chars = ~542 tokens
- Examples: 5,456 chars = ~1,364 tokens
- Telemetry: ~350 tokens typical

## Testing

Run tests:
```bash
python -m pytest agent/test_pipeline.py -v
```

**Test Coverage**:
- ✅ LLM provider selection (OpenAI/Anthropic)
- ✅ Policy gate validation
- ✅ Executor action handling
- ✅ Full pipeline step execution
- ✅ Logging to JSONL file
- ✅ Message formatting

## Integration with Next Phases

### Phase 3: Policy Gate (Advanced)
Extend `PolicyGate.validate()` with:
- Blast radius calculation
- Real SLA validation against Prometheus
- Deployment history analysis
- Rollback decision trees

### Phase 4: Executor (Advanced)
Enhance `Executor.execute()` with:
- Real kubectl commands
- Service mesh configuration
- Deployment rollout tracking
- Post-action telemetry collection

### Phase 5: Evaluation
Extend pipeline with:
- MTTR (Mean Time To Recovery) tracking
- FPR (False Positive Rate) calculation
- SLA compliance verification
- Scenario-based testing

## Troubleshooting

### LLM Call Failures

```bash
# Check logs
tail -f logs/agent_steps.jsonl

# Verify API key
echo $OPENAI_API_KEY

# Test LLM connection
python -c "
from agent.pipeline import LLMClient
client = LLMClient('openai', 'gpt-4', 'your-key')
print('✅ LLM client initialized')
"
```

### Telemetry Collection Issues

```bash
# Check Prometheus
curl http://localhost:9090/api/v1/query?query=up

# Check Alert Receiver
curl http://localhost:5000/alerts
```

### Policy Gate Rejecting All Actions

```bash
# Check gate decisions
python -c "
import json
with open('logs/agent_steps.jsonl') as f:
    for line in f:
        step = json.loads(line)
        for dec in step['gate_decisions']:
            print(f\"{dec['action']['action_type']}: {dec['decision']}\")
"
```

## Performance Metrics

**Typical Step Timing**:
- Telemetry collection: 100-300ms
- Telemetry formatting: 10-50ms
- LLM call: 1-5 seconds
- JSON validation: 1-5ms
- Policy gate: 10-50ms
- Executor (mock): 100-200ms
- **Total: 1-6 seconds per step**

**Token Usage**:
- Typical input: 2,261 tokens
- Typical output (diagnosis): 200-300 tokens
- Total: 2,461-2,561 tokens per step

## References

- [agent/models.py](models.py) - DiagnosisResult, RemediationAction
- [agent/prompts.py](prompts.py) - System prompt, few-shot examples
- [config.py](../config.py) - Configuration management
- [telemetry/formatter.py](../telemetry/formatter.py) - Token-aware formatting
