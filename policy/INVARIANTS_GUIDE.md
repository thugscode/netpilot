# Policy Invariants - Implementation Guide

## Overview

The `policy/invariants.py` module defines the SLA constraints, service topology, and impact calculations for the Netpilot policy engine.

**Key Components**:
1. **SLA_BOUNDS** - Service-level agreement constraints per service
2. **SERVICE_TOPOLOGY** - Service dependency graph (adjacency list)
3. **ROLLBACK_REGISTRY** - Deployment rollback history and image tags
4. **blast_radius()** - Calculate impact of remediation actions

## Architecture

### Service Topology

The service topology represents service dependencies as a directed acyclic graph (DAG).

```
Frontend (entry point)
  ↓
API Gateway (orchestration)
  ├→ Order Service (business logic)
  │  ├→ Inventory Service (state)
  │  │ └→ Notification Service (side effects)
  │  └→ Notification Service
  └→ Inventory Service
     └→ Notification Service
```

**Data Structure** (adjacency list):
```python
SERVICE_TOPOLOGY = {
    "frontend": ["api-gateway"],
    "api-gateway": ["order-service", "inventory-service"],
    "order-service": ["inventory-service", "notification-service"],
    "inventory-service": ["notification-service"],
    "notification-service": [],
}
```

**Edge Semantics**: `"service_a": ["service_b"]` means "service_a calls service_b"

### SLA Bounds

Service-level agreements per service with maximum acceptable metrics.

**Metrics**:
- `max_error_rate` - Maximum error rate (0.0-1.0, e.g., 0.05 = 5%)
- `max_p99_latency_ms` - Maximum P99 latency in milliseconds

**Configuration**:
```python
SLA_BOUNDS = {
    "frontend": {
        "max_error_rate": 0.05,           # 5%
        "max_p99_latency_ms": 500,        # 500ms
    },
    "api-gateway": {
        "max_error_rate": 0.05,           # 5%
        "max_p99_latency_ms": 750,        # 750ms (higher due to aggregation)
    },
    ...
}
```

**Loading Strategy**:
1. Hardcoded defaults in `_load_sla_bounds()`
2. Future: Load from ConfigMap `netpilot-slas`
3. Override: Environment variables `NETPILOT_SLA_*`

### Rollback Registry

Tracks deployment image tags for rollback operations.

**Structure**:
```python
ROLLBACK_REGISTRY = {
    "frontend": {
        "previous_image": "netpilot-frontend:v1.2.3",
        "current_image": "netpilot-frontend:v1.2.4",
        "rollback_count": 0,
        "last_rollback": None,
    },
    ...
}
```

**Initialization**:
1. Startup: Query cluster for all deployments
2. Extract image tags for current and previous versions
3. Mock data for testing

## API Reference

### SLA Accessors

#### `get_sla_bound(service: str, metric: str) -> Optional[float]`

Get specific SLA bound for a service.

**Args**:
- `service` - Service name (e.g., "order-service")
- `metric` - Metric name ("max_error_rate" or "max_p99_latency_ms")

**Returns**: Float bound or None if not found

**Example**:
```python
from policy.invariants import get_sla_bound

max_error_rate = get_sla_bound("api-gateway", "max_error_rate")  # 0.05
max_latency = get_sla_bound("api-gateway", "max_p99_latency_ms")  # 750
```

### Rollback Registry

#### `get_previous_image_tag(deployment: str) -> Optional[str]`

Get previous image tag for rollback.

**Args**:
- `deployment` - Deployment name

**Returns**: Previous image tag or None

**Example**:
```python
from policy.invariants import get_previous_image_tag

previous = get_previous_image_tag("order-service")  # "netpilot-order-service:v1.2.3"
```

#### `register_rollback(deployment: str, previous_image: str) -> None`

Register a rollback in the registry.

**Args**:
- `deployment` - Deployment name
- `previous_image` - Previous image tag

**Side Effects**:
- Updates current_image to previous_image
- Sets previous_image to new value
- Increments rollback_count
- Records last_rollback timestamp

**Example**:
```python
from policy.invariants import register_rollback

register_rollback("order-service", "netpilot-order-service:v1.2.2")
```

### Blast Radius Calculation

#### `blast_radius(action_target: str, topology: Optional[Dict]) -> int`

Calculate number of services affected by an action.

**Algorithm**:
1. Find all services that directly call `action_target` (upstream callers)
2. Recursively find services that call those services
3. Return total count (excluding target itself)

**Args**:
- `action_target` - Target service name
- `topology` - Service dependency graph (defaults to SERVICE_TOPOLOGY)

**Returns**: Integer count of affected services

**Example**:
```python
from policy.invariants import blast_radius

# If we restart order-service, how many services are affected?
radius = blast_radius("order-service")
# Returns: 2 (api-gateway + frontend)

# With custom topology:
custom = {"a": ["b"], "b": ["c"], "c": []}
radius = blast_radius("b", custom)
# Returns: 1 (just 'a' calls 'b')
```

#### `calculate_blast_radius_percentage(action_target: str, topology: Optional[Dict]) -> float`

Calculate blast radius as percentage of total services.

**Args**:
- `action_target` - Target service name
- `topology` - Service dependency graph (defaults to SERVICE_TOPOLOGY)

**Returns**: Float percentage (0.0-100.0)

**Example**:
```python
from policy.invariants import calculate_blast_radius_percentage

# 5 services total, 2 affected = 40%
pct = calculate_blast_radius_percentage("order-service")  # 40.0
```

### Validation Helpers

#### `is_within_sla(service: str, error_rate: float, p99_latency_ms: float) -> Tuple[bool, List[str]]`

Check if service metrics are within SLA bounds.

**Args**:
- `service` - Service name
- `error_rate` - Current error rate (0.0-1.0)
- `p99_latency_ms` - Current P99 latency in milliseconds

**Returns**: Tuple of (is_within_sla, list_of_violations)

**Example**:
```python
from policy.invariants import is_within_sla

is_ok, violations = is_within_sla("api-gateway", 0.08, 800)
# is_ok = False
# violations = [
#   "Error rate 8.00% exceeds max 5.00%",
#   "P99 latency 800ms exceeds max 750ms"
# ]
```

#### `is_blast_radius_acceptable(action_target: str, max_radius_pct: float) -> Tuple[bool, str]`

Check if action blast radius is acceptable.

**Args**:
- `action_target` - Service targeted by action
- `max_radius_pct` - Maximum acceptable blast radius percentage

**Returns**: Tuple of (is_acceptable, reason)

**Example**:
```python
from policy.invariants import is_blast_radius_acceptable

is_ok, reason = is_blast_radius_acceptable("order-service", 50.0)
# is_ok = True (40% < 50%)
# reason = "Blast radius 40.0% is acceptable"
```

### Debugging

#### `print_topology() -> None`

Print service topology in human-readable format.

#### `print_sla_bounds() -> None`

Print SLA bounds per service.

#### `print_blast_radius_analysis() -> None`

Print blast radius for all services.

**Example**:
```bash
python -m policy.invariants
```

**Output**:
```
Service Topology (Dependency Graph)
====================================

  frontend → api-gateway
  api-gateway → order-service, inventory-service
  order-service → inventory-service, notification-service
  inventory-service → notification-service
  notification-service (leaf service)


SLA Bounds:
============

  frontend                Error Rate: 0.05   | P99: 500ms
  api-gateway             Error Rate: 0.05   | P99: 750ms
  order-service           Error Rate: 0.03   | P99: 1000ms
  inventory-service       Error Rate: 0.03   | P99: 800ms
  notification-service    Error Rate: 0.1    | P99: 2000ms


Blast Radius Analysis:
======================

  frontend                affects  0 services (  0.0%)
  api-gateway             affects  1 services ( 20.0%)
  order-service           affects  2 services ( 40.0%)
  inventory-service       affects  3 services ( 60.0%)
  notification-service    affects  4 services ( 80.0%)
```

## Usage Examples

### Validating a Remediation Action

```python
from policy.invariants import (
    blast_radius,
    is_blast_radius_acceptable,
    get_sla_bound,
)

# Check if restarting order-service is safe
action_target = "order-service"

# 1. Check blast radius
radius = blast_radius(action_target)
config = get_config()
max_radius_pct = config.policy_gate.max_blast_radius_pct  # 50.0%

is_ok, reason = is_blast_radius_acceptable(action_target, max_radius_pct)
if not is_ok:
    print(f"Action rejected: {reason}")
    return False

print(f"Blast radius acceptable: {reason}")
return True
```

### Checking Service Health Against SLA

```python
from policy.invariants import is_within_sla

# Check if api-gateway is meeting SLA
service = "api-gateway"
error_rate = 0.08  # 8%
p99_latency = 600  # ms

is_ok, violations = is_within_sla(service, error_rate, p99_latency)

if not is_ok:
    print(f"SLA violations for {service}:")
    for violation in violations:
        print(f"  - {violation}")
else:
    print(f"{service} is within SLA")
```

### Rollback Management

```python
from policy.invariants import (
    get_previous_image_tag,
    register_rollback,
)

# Get previous image for rollback
service = "order-service"
previous_image = get_previous_image_tag(service)

if previous_image:
    # Rollback to previous image
    print(f"Rolling back to {previous_image}")
    
    # (Execute rollback via kubectl)
    
    # Register the rollback in history
    register_rollback(service, previous_image)
else:
    print(f"No previous image available for {service}")
```

## Integration with Policy Gate

The invariants are used by `PolicyGate` (in `agent/pipeline.py`) to validate actions:

```python
from agent.pipeline import PolicyGate
from policy.invariants import blast_radius

gate = PolicyGate()

# PolicyGate uses invariants internally:
# 1. Check blast radius (uses blast_radius())
# 2. Check SLA bounds (uses is_within_sla())
# 3. Check rollback history (uses ROLLBACK_REGISTRY)
# 4. Estimate risk level (uses SLA_BOUNDS)

decision = gate.validate(action, telemetry, diagnosis)
```

## Future Enhancements

### 1. Load SLA Bounds from ConfigMap

```python
def _load_sla_bounds_from_configmap():
    """Load SLA bounds from Kubernetes ConfigMap"""
    # kubectl get configmap netpilot-slas -o json
    configmap_data = fetch_configmap("netpilot-slas")
    return parse_sla_bounds(configmap_data)
```

### 2. Load Service Topology from ConfigMap

```python
def _load_topology_from_configmap():
    """Load service topology from Kubernetes ConfigMap"""
    # kubectl get configmap netpilot-topology -o json
    configmap_data = fetch_configmap("netpilot-topology")
    return parse_topology(configmap_data)
```

### 3. Dynamic Rollback Registry

```python
def _sync_rollback_registry_with_cluster():
    """Periodically sync rollback registry with cluster"""
    # Query all deployments
    # Extract image tags
    # Update ROLLBACK_REGISTRY
    pass
```

### 4. Advanced Blast Radius

```python
def blast_radius_weighted(action_target, topology, service_criticality):
    """Calculate weighted blast radius based on service criticality"""
    # Critical services have higher weight
    # Calculate impact score
    pass
```

## Testing

Run the test suite:

```bash
python -m pytest policy/test_invariants.py -v
```

**Test Coverage**:
- ✅ SLA bounds loading and access
- ✅ Service topology validation
- ✅ Blast radius calculation
- ✅ Rollback registry management
- ✅ SLA validation
- ✅ Blast radius constraints

**Example Output**:
```
test_sla_bounds_loaded PASSED
test_get_sla_bound_valid PASSED
test_blast_radius_all_services PASSED
test_is_within_sla_healthy PASSED
...
```

## References

- [agent/pipeline.py](../agent/pipeline.py) - PolicyGate integration
- [config.py](../config.py) - Configuration management
- [PIPELINE_GUIDE.md](../PIPELINE_GUIDE.md) - Agent pipeline documentation
