# Policy Gate - Implementation Guide

## Overview

The `policy/gate.py` module implements the `PolicyGate` class that validates remediation actions before execution. It enforces three validation checks in sequence:

1. **SLA Bounds** - Would the action violate service-level agreements?
2. **Rollback Feasibility** - For rollback actions, is a previous image available?
3. **Blast Radius** - Would the action affect too many services?

**Status**: Fully implemented and tested (14/14 tests passing ✅)

## Architecture

### Validation Flow

```
RemediationAction + Current KPIs
    ↓
PolicyGate.validate()
    ├─→ Check 1: SLA Bounds
    │   ├─ Simulate action impact on KPIs
    │ ├─ Compare projected metrics against SLA_BOUNDS
    │   ├─ FAIL → return (False, reason)
    │   └─ PASS → continue
    │
    ├─→ Check 2: Rollback Feasibility
    │   ├─ If action_type != rollback_deploy → PASS
    │   ├─ Verify target in ROLLBACK_REGISTRY
    │   ├─ Verify previous_image exists
    │   ├─ FAIL → return (False, reason)
    │   └─ PASS → continue
    │
    └─→ Check 3: Blast Radius
        ├─ Calculate blast_radius(target)
        ├─ Compare against config limit (converted from percentage to count)
        ├─ FAIL → return (False, reason)
        └─ PASS → return (True, "approved")
```

## API Reference

### PolicyGate Class

#### `__init__()`

Initialize PolicyGate with configuration.

```python
from policy.gate import PolicyGate

gate = PolicyGate()
```

#### `validate(action, current_kpis) -> Tuple[bool, str]`

Validate a remediation action.

**Parameters**:
- `action`: `RemediationAction` object
  - `action_type`: One of "restart_pod", "scale_up", "scale_down", "reroute_traffic", "rollback_deploy", "noop"
  - `target`: Service name (e.g., "order-service")
  - `confidence`: Float 0.0-1.0
  - `rationale`: One-line explanation

- `current_kpis`: Dict mapping service names to `KPI` objects
  - Keys: "frontend", "api-gateway", "order-service", etc.
  - Values: KPI with error_rate, latency_p99_ms, etc.

**Returns**: Tuple of (allowed: bool, reason: str)

**Example**:
```python
from agent.models import RemediationAction
from telemetry.schemas import KPI
from policy.gate import PolicyGate

gate = PolicyGate()

# Create action
action = RemediationAction(
    action_type="restart_pod",
    target="order-service",
    params={},
    confidence=0.95,
    rationale="Service stuck in error loop"
)

# Get current KPIs (from telemetry collector)
current_kpis = {
    "order-service": KPI(
        service="order-service",
        timestamp=...,
        error_rate=0.01,
        latency_p99_ms=800,
        ...
    ),
    # ... other services
}

# Validate
allowed, reason = gate.validate(action, current_kpis)

if allowed:
    print(f"✓ Action approved: {reason}")
    executor.execute(action)
else:
    print(f"✗ Action rejected: {reason}")
    # Propose alternative action or skip
```

## Validation Checks

### Check 1: SLA Bounds

Simulates the action's impact on service KPIs and verifies the projected metrics stay within SLA bounds.

**Impact Simulation Heuristics**:

| Action Type | Impact | Rationale |
|-------------|--------|-----------|
| `restart_pod` | Error rate doubles for ~30s | Pod restart causes temporary spike |
| `scale_up` | Latency halved | More replicas = lower contention |
| `scale_down` | Latency doubled | Fewer replicas = higher contention |
| `reroute_traffic` | No impact | Service mesh handles transparently |
| `rollback_deploy` | No impact | Previous image presumed stable |
| `noop` | No impact | No-op action does nothing |

**Example**:

```
Current: order-service with 1% error rate, 800ms P99
Action: restart_pod
Projected: 2% error rate (1% * 2), 800ms P99

Check: 2% ≤ SLA bound of 3% ✓
Result: PASS
```

**Failure Reason**:
```
Action would violate SLA bounds for order-service:
Error rate 5.00% exceeds max 3.00% (projected: 5.00% error, 900ms P99 latency)
```

### Check 2: Rollback Feasibility

For `rollback_deploy` actions, verifies that:
1. Service exists in ROLLBACK_REGISTRY
2. Previous image tag is available

For all other action types, this check is skipped (passes automatically).

**Example**:

```python
# rollback_deploy action
Action: rollback_deploy on order-service
Check: order-service in ROLLBACK_REGISTRY? ✓
Check: previous_image = "netpilot-order-service:v1.2.3"? ✓
Result: PASS (Rollback feasible: netpilot-order-service:v1.2.3 available for order-service)

# Non-rollback action
Action: restart_pod on api-gateway
Result: SKIP (restart_pod doesn't require rollback check)
```

**Failure Reason**:
```
No previous image available for rollback: order-service
Registry entry: {'previous_image': None, 'current_image': '...', ...}
```

### Check 3: Blast Radius

Calculates the number of upstream services that could be affected by the action and verifies it's within the configured limit.

**Calculation**:
- Uses `blast_radius(target)` from policy.invariants
- Returns count of services that depend on the target (directly or indirectly)
- Compares against `config.policy_gate.max_blast_radius_pct` (converted to service count)

**Example**:

```
Topology: frontend → api-gateway → order-service
Action: restart order-service
Affected: api-gateway, frontend (2 services)

Config: max_blast_radius_pct = 50% (out of 5 total = 2 services max)
Check: 2 ≤ 2? ✓
Result: PASS (Blast radius acceptable: 2 services affected (40.0% of total))
```

**Failure Reason**:
```
Blast radius too large: 3 services affected (60.0% of total),
exceeds max 2 (50.0% limit)
```

## Utility Functions

### explain_policy_decision()

Generate human-readable explanation of policy decision.

```python
from policy.gate import explain_policy_decision

action = RemediationAction(...)
decision = gate.validate(action, kpis)

explanation = explain_policy_decision(action, decision, verbose=True)
print(explanation)

# Output:
# ✓ APPROVED: restart_pod on order-service
#   Confidence: 95.0%
#   Rationale: Service stuck in error loop
#   Decision: Action approved: restart_pod on order-service (confidence: 95.0%)
```

### create_audit_log_entry()

Create JSONL-compatible audit log entry for policy decision.

```python
from policy.gate import create_audit_log_entry

entry = create_audit_log_entry(action, decision)
# {
#   "timestamp": "2026-04-27T01:44:25.123456",
#   "action_type": "restart_pod",
#   "target": "order-service",
#   "confidence": 0.95,
#   "allowed": true,
#   "reason": "Action approved: restart_pod on order-service (confidence: 95.0%)"
# }

# Append to policy decisions log
with open("logs/policy_decisions.jsonl", "a") as f:
    f.write(json.dumps(entry) + "\n")
```

## Configuration

### Policy Gate Config

Located in `config.py` under `PolicyGateConfig`:

```python
@dataclass
class PolicyGateConfig:
    max_blast_radius_pct: float = 50.0  # Max blast radius as % of total services
    max_error_rate_pct: float = 5.0     # Max acceptable error rate during action
    max_p99_latency_ms: int = 1000      # Max acceptable P99 latency during action
    rollback_limits: dict = field(...)  # Rate limiting for rollback actions
```

**Conversion Logic**:
- With 5 services total and 50% limit:
  - Max affected services = 5 * 0.50 = 2.5 ≈ **2 services**

## Integration Points

### With Agent Pipeline

```python
# agent/pipeline.py
from policy.gate import PolicyGate

policy_gate = PolicyGate()

for action in ranked_actions:
    allowed, reason = policy_gate.validate(action, current_kpis)
    
    if allowed:
        logger.info(f"Action approved: {reason}")
        execution_result = await executor.execute(action)
        return execution_result
    else:
        logger.warning(f"Action rejected: {reason}")
        continue  # Try next action
```

### With Telemetry Collector

```python
# Ensure latest KPIs available before validation
current_kpis = await telemetry_collector.collect()

action = await agent.diagnose(telemetry_bundle)
allowed, reason = policy_gate.validate(action, current_kpis)
```

## Test Results

### 14/14 Tests Passing ✅

**Test Suite 1: SLA Bounds Validation** (4 tests)
- ✅ Healthy service passes SLA check
- ✅ High error rate violation detected
- ✅ Scale up improves latency
- ✅ Missing KPI data rejected

**Test Suite 2: Rollback Feasibility** (3 tests)
- ✅ Non-rollback actions skip check
- ✅ Valid rollback passes
- ✅ Invalid rollback rejected

**Test Suite 3: Blast Radius Validation** (3 tests)
- ✅ Leaf service has zero radius
- ✅ Intermediate service within limit
- ✅ Config limit respected

**Test Suite 4: Full Validation Workflow** (4 tests)
- ✅ Healthy action approved
- ✅ Scale up action approved
- ✅ Rollback action approved
- ✅ Utility functions working

## Verification

Run verification script:

```bash
python verify_gate.py
```

Run comprehensive tests:

```bash
python -m pytest policy/test_gate.py -v
```

## Design Decisions

1. **Heuristic-based Impact Simulation**: Simple multipliers (2x error, 0.5x latency) are conservative estimates. Real impact is monitored post-execution.

2. **Upstream Blast Radius**: Calculates "who depends on this service" rather than "what this service depends on" because the impact flows upward through the call chain.

3. **Percentage-to-Count Conversion**: Config stores percentage limit, converted to absolute service count at runtime. Cleaner configuration while supporting dynamic service counts in future.

4. **Sequential Checks**: Early termination if any check fails. More efficient than computing all checks independently.

5. **Audit Trail**: All decisions logged with full reasoning for post-action analysis and policy tuning.

## Future Enhancements

1. **Dynamic Impact Simulation**: Learn actual action impacts from historical execution data

2. **ML-based Risk Scoring**: Train model on policy decisions to predict approval likelihood

3. **Service-specific Constraints**: Different limits per service (e.g., payment service more conservative than monitoring)

4. **Time-aware Limits**: Stricter limits during peak hours, more permissive during off-peak

5. **Blast Radius Weighting**: Weight services by criticality (production traffic > monitoring)

## Files

- **policy/gate.py** (550 lines) - Main PolicyGate implementation
- **policy/test_gate.py** (445 lines) - Comprehensive test suite
- **verify_gate.py** (300 lines) - Standalone verification script
- **policy/invariants.py** - SLA bounds, topology, rollback registry
- **policy/INVARIANTS_GUIDE.md** - Invariants documentation

## References

- [policy/invariants.py](./invariants.py) - SLA bounds and blast radius
- [agent/models.py](../agent/models.py) - RemediationAction schema
- [telemetry/schemas.py](../telemetry/schemas.py) - KPI schema
- [config.py](../config.py) - Policy configuration
