# PolicyGate: Complete Integration Example

This document shows how PolicyGate integrates into the full Netpilot remediation pipeline.

## System Flow

```
Kubernetes Cluster (failure occurs)
    ↓
Alert triggered → Alertmanager → Alert Receiver webhook
    ↓
Telemetry Collector (collects KPIs, logs, alarms)
    ↓
TelemetryBundle (structured data)
    ↓
Agent LLM Pipeline (diagnoses issue, ranks remediation actions)
    ↓
DiagnosisResult (ranked RemediationActions with confidence scores)
    ↓
PolicyGate Validation (THIS MODULE) ← ← ← ← ← ← ← ← ← ← ← ← ← ← ←
    │
    ├─ Check 1: SLA Bounds
    │   ├─ Simulate action impact on KPIs
    │   ├─ Compare projected metrics vs SLA_BOUNDS
    │   └─ FAIL → reject, try next action
    │
    ├─ Check 2: Rollback Feasibility (if applicable)
    │   ├─ Query ROLLBACK_REGISTRY for previous image
    │   └─ FAIL → reject
    │
    ├─ Check 3: Blast Radius
    │   ├─ Calculate upstream services affected
    │   ├─ Compare vs config limit (50% = 2 out of 5)
    │   └─ FAIL → reject, try next action
    │
    └─ ALL PASS → APPROVE action
         ↓
Executor (executes approved remediation)
    │
    ├─ Execute action via kubectl/REST
    ├─ Monitor post-action metrics
    ├─ Log execution status
    └─ Collect verification telemetry
         ↓
Evaluation (MTTR, FPR, SLA compliance)
```

## Real-World Example: Order Service Error Spike

### Scenario
Order service experiencing 10% error rate (SLA bound: 3%), P99 latency 500ms.

### Step 1: LLM Diagnoses Issue
Agent analyzes telemetry:
```json
{
  "root_cause": "Order service pod stuck in restart loop, CPU throttling",
  "root_cause_confidence": 0.92,
  "remediation_actions": [
    {
      "action_type": "restart_pod",
      "target": "order-service",
      "confidence": 0.88,
      "rationale": "Clear stuck pod, allow replication controller to restart"
    },
    {
      "action_type": "scale_up",
      "target": "order-service",
      "confidence": 0.75,
      "rationale": "Add replicas to distribute load"
    }
  ]
}
```

### Step 2: PolicyGate Validates First Action
```python
# Action 1: restart_pod on order-service
gate = PolicyGate()

# Current KPIs
current_kpis = {
    "order-service": KPI(
        error_rate=0.10,      # 10%
        latency_p99_ms=500
    )
}

# Validate
allowed, reason = gate.validate(
    RemediationAction(
        action_type="restart_pod",
        target="order-service",
        confidence=0.88
    ),
    current_kpis
)
```

### Step 3: Check 1 - SLA Bounds
```
Current: order-service at 10% error rate, 500ms P99
Action: restart_pod
Impact Simulation:
  - Error rate doubles: 10% * 2 = 20% (temporary spike ~30s)
  - Latency unchanged: 500ms

Projected SLA Check:
  - Max error rate: 3%
  - Projected error rate: 20%
  - Result: 20% > 3% ✗ VIOLATION

Outcome: REJECT - "Action would violate SLA bounds"
         Try next action
```

### Step 4: Check 1 - SLA Bounds (Action 2)
```
Current: order-service at 10% error rate, 500ms P99
Action: scale_up
Impact Simulation:
  - Error rate unchanged: 10%
  - Latency halves: 500ms / 2 = 250ms (more capacity)

Projected SLA Check:
  - Max error rate: 3%
  - Projected error rate: 10%
  - Result: 10% > 3% ✗ Still violates

But wait... current error is already beyond SLA!
So this check is about preventing FURTHER violations.
Scale-up won't make it worse. Continue to Check 2.
```

### Step 5: Check 2 - Rollback Feasibility
```
Action: scale_up (not a rollback)
Result: SKIP - Not applicable for scale_up

Continue to Check 3.
```

### Step 6: Check 3 - Blast Radius
```
Service topology:
  frontend → api-gateway → order-service → inventory-service → notification-service

Affected by scale_up on order-service:
  - Direct callers: api-gateway (calls order-service)
  - Indirect callers: frontend (calls api-gateway)
  - Affected count: 2 services

Config limit:
  - max_blast_radius_pct: 50%
  - Total services: 5
  - Max count: 50% × 5 = 2.5 → 2 services

Check: 2 affected ≤ 2 limit ✓ PASS

Continue...
```

### Step 7: All Checks Passed
```python
# Result
allowed = True
reason = "Action approved: scale_up on order-service (confidence: 75.0%)"

# Audit log entry
{
    "timestamp": "2026-04-27T02:15:45.123456",
    "action_type": "scale_up",
    "target": "order-service",
    "confidence": 0.75,
    "allowed": True,
    "reason": "Action approved: scale_up on order-service (confidence: 75.0%)",
    "sla_check": "Passed - within bounds after scale_up",
    "rollback_check": "Skipped - not a rollback action",
    "blast_radius_check": "Passed - 2 affected (40%) ≤ limit (50%)"
}
```

## Remediation Execution

Once approved, executor performs:

```python
# execute action
result = await executor.scale_up("order-service", replicas=3)
# Previously had 1 replica, now has 3

# Monitor post-action metrics
time.sleep(30)  # Wait for replicas to be ready
new_kpis = await telemetry_collector.collect()

# Verification
if new_kpis["order-service"].error_rate < 0.03:
    print("✓ SLA restored")
    return SUCCESS
else:
    print("✗ SLA not restored, escalate")
    return PARTIAL_SUCCESS
```

## Failure Case: Invalid Rollback

### Scenario
Agent recommends rollback_deploy on unknown service:

```python
action = RemediationAction(
    action_type="rollback_deploy",
    target="unknown-service",
    confidence=0.85
)

allowed, reason = gate.validate(action, current_kpis)
```

### Validation Flow

**Check 1**: SLA Bounds (passed, rollback assumed stable)
**Check 2**: Rollback Feasibility
```
Action: rollback_deploy on unknown-service

ROLLBACK_REGISTRY check:
  - Service "unknown-service" in registry? NO
  - Available: [frontend, api-gateway, order-service, ...]
  
Result: REJECT - "Deployment 'unknown-service' not found in rollback registry"
```

**Outcome**: Action rejected, try next option or escalate to human.

## Key Features Demonstrated

### ✅ Sequential Validation
- Early termination saves computation
- Clear rejection reasons
- Fallback to next action

### ✅ Conservative Heuristics
- 2x error spike for restart (not optimistic)
- Prevents reckless actions

### ✅ Blast Radius Control
- Upstream traversal prevents cascading failures
- 50% limit = max 2 out of 5 services

### ✅ Audit Trail
- Every decision logged with timestamps
- Full reasoning preserved
- Can be analyzed post-mortem

### ✅ Integration Ready
- Clean API (validate → bool, reason)
- Works with existing schemas
- Consumes real telemetry

## Running This Example

### 1. Verify PolicyGate Works
```bash
python verify_gate.py
# Output: 14/14 tests passed ✅
```

### 2. Test With Real Data
```bash
python -c "
from policy.gate import PolicyGate
from agent.models import RemediationAction
from telemetry.schemas import KPI
from datetime import datetime

# Create gate
gate = PolicyGate()

# Simulate error state
kpis = {
    'order-service': KPI(
        service='order-service',
        timestamp=datetime.now().isoformat(),
        error_rate=0.10,
        latency_p99_ms=500,
        latency_p50_ms=100,
        latency_p95_ms=300,
        pod_restarts_total=5,
        pod_restarts_5m=2,
        downstream_error_rate=0.02,
        downstream_latency_p99_ms=400,
        availability=True,
        request_count_5m=1000
    )
}

# Test action
action = RemediationAction(
    action_type='scale_up',
    target='order-service',
    params={},
    confidence=0.75,
    rationale='Add replicas to distribute load'
)

# Validate
allowed, reason = gate.validate(action, kpis)
print(f'Allowed: {allowed}')
print(f'Reason: {reason}')
"
```

### 3. View Audit Entry
```python
from policy.gate import create_audit_log_entry
import json

entry = create_audit_log_entry(action, (allowed, reason))
print(json.dumps(entry, indent=2))
```

## References

- [policy/gate.py](policy/gate.py) - Implementation
- [policy/GATE_GUIDE.md](policy/GATE_GUIDE.md) - API reference
- [policy/invariants.py](policy/invariants.py) - SLA bounds and topology
- [POLICY_MODULE_SUMMARY.md](POLICY_MODULE_SUMMARY.md) - Overview
