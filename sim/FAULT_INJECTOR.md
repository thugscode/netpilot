# Fault Injector CLI

CLI tool for injecting faults into the netpilot Kubernetes cluster.

## Usage

```bash
python fault_injector.py --scenario <SCENARIO> [OPTIONS]
```

## Scenarios

### 1. pod-crash
Delete a pod to trigger Kubernetes automatic restart.

```bash
python fault_injector.py --scenario pod-crash --target notification-service
```

**What happens:**
1. Finds the pod for the target service
2. Deletes the pod via `kubectl delete pod`
3. Kubernetes controller restarts the pod
4. Logs event to `events.jsonl`

**Observable effects:**
- Service becomes temporarily unavailable (~3-5s)
- Prometheus shows request spike/error spike
- Upstream services see connection errors
- Pod eventually recovers

### 2. link-degrade
Degrade the network link on a pod using traffic control (tc).

```bash
python fault_injector.py --scenario link-degrade --target order-service --duration 60
```

**What happens:**
1. Executes into the pod via `kubectl exec`
2. Adds 200ms delay + 10% packet loss using `tc netem`
3. Waits for specified duration
4. Removes traffic control rules
5. Logs event to `events.jsonl`

**Observable effects:**
- Service responds slowly (200ms+ added latency)
- Requests timeout or fail
- Metrics show increased downstream_latency_seconds
- Upstream services see degraded performance
- Cascading slowdown if service chains are involved

### 3. cascade
Trigger pod-crash on a service and watch for failures to propagate upstream.

```bash
python fault_injector.py --scenario cascade --target notification-service --watch-duration 45
```

**What happens:**
1. Deletes the target pod
2. Monitors upstream services (order-service → api-gateway → frontend) for:
   - Increased error rates (5xx status codes)
   - Unreachable services
3. Logs cascade detection events to `events.jsonl`
4. Displays cascade propagation in real-time

**Observable effects:**
- Initial fault isolated to target service
- Error rate increases at next upstream hop
- Error propagates further if chain dependencies exist
- System eventually recovers as pod restarts

## Options

- `--scenario` (required): One of `pod-crash`, `link-degrade`, `cascade`
- `--target` (default: `notification-service`): Service/pod to target
- `--duration` (default: 60): Duration for link-degrade scenario in seconds
- `--watch-duration` (default: 30): Duration to watch for cascade in seconds

## Events Log

All fault injections are logged to `events.jsonl` in JSONL format (one JSON object per line).

### Example events:

```json
{"timestamp": "2026-04-27T10:15:30.123456", "scenario": "pod-crash", "target": "notification-service", "pod_name": "notification-service-xyz", "action": "deleted"}
{"timestamp": "2026-04-27T10:15:33.456789", "scenario": "cascade", "target": "order-service", "event": "cascade_detected", "elapsed_seconds": 5}
{"timestamp": "2026-04-27T10:15:35.789012", "scenario": "link-degrade", "target": "order-service", "delay_ms": 200, "loss_percent": 10}
```

### View logs:

```bash
# Pretty-print all events
cat events.jsonl | jq .

# Filter by scenario
cat events.jsonl | jq 'select(.scenario == "cascade")'

# Show cascade propagation
cat events.jsonl | jq 'select(.event == "cascade_propagated")'
```

## Requirements

- `kubectl` configured to access the netpilot cluster
- Python 3.8+
- Dependencies: click, httpx (installed via setup script)
- Target pods must have `wget` or network tools available for monitoring

## Example Workflow

```bash
# 1. Deploy services
kubectl apply -f sim/cluster/services/

# 2. Wait for services to be ready
kubectl wait --for=condition=ready pod -l app=frontend --timeout=60s

# 3. In terminal 1: Monitor events
watch -n 1 'tail -5 sim/events.jsonl | jq .'

# 4. In terminal 2: Inject cascade failure
python sim/fault_injector.py --scenario cascade --target notification-service

# 5. Watch cascade propagate upstream to order-service → api-gateway → frontend
```

## Notes

- `link-degrade` requires network tools (tc) in the container. If the container doesn't have tc/iproute2, consider installing during deployment or using a base image that includes it.
- Pod restarts may take 5-15 seconds depending on readiness probes.
- Cascade watching checks metrics every 2 seconds for 30 seconds (by default).
- All events are appended to `events.jsonl`; the file is never truncated.
