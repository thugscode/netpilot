# Agent Prompts & Models

## Overview

The agent module provides LLM-powered diagnosis and remediation for Kubernetes failures. It includes:

1. **Models** (`models.py`): Pydantic schemas for diagnosis results
2. **Prompts** (`prompts.py`): System instructions and few-shot examples for LLM

## Models

### RemediationAction

```python
class RemediationAction(BaseModel):
    action_type: Literal["restart_pod", "scale_up", "reroute_traffic", "rollback_deploy", "noop"]
    target: str  # Service name
    params: Dict[str, Any]  # Action-specific parameters
    confidence: float  # 0.0-1.0 effectiveness score
    rationale: str  # One sentence explanation
```

**Action Types**:
- **restart_pod**: Restart failing service (clears state, reconnects resources)
- **scale_up**: Increase replicas (distributes load, mitigates cascading failures)
- **reroute_traffic**: Send traffic to healthy services (temporary mitigation)
- **rollback_deploy**: Revert to previous deployment (if recent deploy caused issue)
- **noop**: No action needed (system will self-recover)

**Example**:
```python
action = RemediationAction(
    action_type="restart_pod",
    target="notification-service",
    params={"grace_period_seconds": 30},
    confidence=0.88,
    rationale="Pod restart will clear stale state and reconnect to database."
)
```

### DiagnosisResult

```python
class DiagnosisResult(BaseModel):
    root_cause: str  # Most likely root cause
    root_cause_confidence: float  # 0.0-1.0 confidence
    remediation_actions: List[RemediationAction]  # Up to 5 actions ranked by impact
```

**Example**:
```python
diagnosis = DiagnosisResult(
    root_cause="notification-service pod crashed due to database connection loss",
    root_cause_confidence=0.92,
    remediation_actions=[
        RemediationAction(...),  # Most impactful
        RemediationAction(...),  # Second most impactful
        # ... etc
    ]
)
```

## Prompts

### System Prompt

The system prompt instructs the LLM to:

1. **Analyze telemetry** - Identify failures from metrics, logs, alarms
2. **Diagnose root cause** - Consider cascading failures, dependencies, error patterns
3. **Rank actions** - Sort by expected impact (most→least)
4. **Return JSON only** - Validate structure, no prose outside JSON

**Key constraints**:
- Return ONLY valid DiagnosisResult JSON
- No markdown, explanations, or text outside JSON
- Action ranking by expected impact
- Realistic confidence scores (0.0-1.0)
- Never recommend actions on healthy services without justification
- Use "noop" if system appears healthy

### Few-Shot Examples

Two comprehensive examples train the model on different failure types:

#### Example 1: Pod Crash (Cascading Failure)

**Input**: 
- notification-service down (critical alert)
- order-service has high error rate (cascading failure)
- Error logs show database connection lost

**Output**:
- Root cause: Pod crashed due to database connection loss (confidence: 0.92)
- Actions ranked by impact:
  1. Restart notification-service (restart_pod, confidence: 0.88)
  2. Scale order-service (scale_up, confidence: 0.65)
  3. Reroute order-service traffic (reroute_traffic, confidence: 0.5)
  4. No action (noop, confidence: 0.4)

#### Example 2: Link Degradation

**Input**:
- api-gateway has high latency (P99: 750ms)
- order-service has high downstream error rate (12%)
- Error logs show timeouts and packet loss

**Output**:
- Root cause: Network degradation with timeouts (confidence: 0.78)
- Actions ranked by impact:
  1. Scale order-service (scale_up, confidence: 0.72)
  2. Scale api-gateway (scale_up, confidence: 0.65)
  3. Enable circuit breaker (reroute_traffic, confidence: 0.6)
  4. Wait for recovery (noop, confidence: 0.5)

## API Reference

### Prompt Functions

```python
# Get system prompt
system = get_system_prompt()

# Get few-shot examples
examples = get_few_shot_examples()
# Returns: {"pod_crash": {...}, "link_degrade": {...}}

# Format user message with telemetry
user_msg = format_user_prompt(telemetry_context)

# Build complete message list for LLM
messages = build_prompt_messages(telemetry_context)
# Returns: [user_msg_1, assistant_msg_1, user_msg_2, assistant_msg_2, user_msg_final]

# Validate LLM response
is_valid = validate_diagnosis_json(json_string)
```

### Usage Example

```python
from agent import get_system_prompt, build_prompt_messages, validate_diagnosis_json
from agent.models import DiagnosisResult
import anthropic
import json

# Collect telemetry (from telemetry module)
context = telemetry_formatter.to_context_window(bundle)

# Build LLM prompt
messages = build_prompt_messages(context)

# Call LLM
client = anthropic.Anthropic()
response = client.messages.create(
    model="claude-3-haiku-20240307",
    max_tokens=1024,
    system=get_system_prompt(),
    messages=messages
)

# Parse and validate response
response_text = response.content[0].text

if validate_diagnosis_json(response_text):
    diagnosis = DiagnosisResult(**json.loads(response_text))
    print(f"Root cause: {diagnosis.root_cause}")
    print(f"Confidence: {diagnosis.root_cause_confidence}")
    for action in diagnosis.remediation_actions:
        print(f"  - {action.action_type} {action.target}: {action.rationale}")
else:
    print("Invalid response from LLM")
```

## Token Budget

- **System prompt**: ~2,169 chars (~540 tokens)
- **Few-shot examples**: ~5,456 chars (~1,364 tokens)
- **User input** (telemetry context): ~1,429 chars (~357 tokens)
- **Total**: ~2,261 tokens (well within 8K window)

This leaves ~5,700 tokens for LLM reasoning and output.

## Integration with Pipeline

The agent module integrates with the complete pipeline:

```
TelemetryCollector (collects metrics/logs/alarms)
    ↓
TelemetryFormatter.to_context_window() (compact JSON ~350 tokens)
    ↓
Agent.build_prompt_messages() (adds system + few-shot examples)
    ↓
LLM (Claude/GPT-4) analyzes telemetry
    ↓
Agent.validate_diagnosis_json() (validates response)
    ↓
DiagnosisResult (structured output)
    ↓
PolicyGate (validates actions against SLA)
    ↓
Executor (executes approved remediation)
```

## Testing

Run the test suite:

```bash
python agent/test_prompts.py
```

Tests validate:
- ✅ Pydantic models (instantiation, serialization)
- ✅ Prompt functions (system, examples, formatting)
- ✅ JSON validation (valid/invalid cases)
- ✅ Few-shot examples (parseable, instantiable)

## Design Decisions

1. **JSON-only output**: Simplifies parsing and validation
2. **Few-shot examples**: Trained on realistic failure types (pod crash, link degrade)
3. **Confidence scores**: Forces reasoning about uncertainty
4. **Action ranking**: Prioritizes impact over ease of implementation
5. **Role-based messages**: Follows LLM best practices (system → examples → input)

## Future Enhancements

1. **Dynamic examples**: Select examples based on detected failure type
2. **Context pruning**: Drop low-signal data based on telemetry size
3. **Retry logic**: Re-prompt if validation fails
4. **Confidence calibration**: Learn from outcomes to improve confidence scores
5. **Multi-LLM support**: Different prompts for different models (GPT-4, Llama, etc.)

## References

- [models.py](./models.py) - Pydantic schemas
- [prompts.py](./prompts.py) - System prompt and examples
- [test_prompts.py](./test_prompts.py) - Test suite
- [../telemetry/formatter.py](../telemetry/formatter.py) - Telemetry formatting
