# Netpilot Telemetry - Complete Reference

The telemetry module collects KPIs, logs, and alarms on a configurable interval (default: 30 seconds) and returns structured TelemetryBundle objects for consumption by the agent pipeline.

## Architecture

```
┌─────────────────────────────────────────────┐
│         Netpilot Cluster                    │
│                                             │
│  ┌──────────────────────────────────────┐  │
│  │  Services (5 microservices)          │  │
│  │  - frontend, api-gateway, etc.       │  │
│  │  - Export metrics on /metrics        │  │
│  │  - Health checks on /health          │  │
│  │  - Logs to stdout                    │  │
│  └──────────────────────────────────────┘  │
│                   ▲                         │
│                   │                         │
│  ┌────────────────┼────────────────────┐   │
│  │  Prometheus    │  Alertmanager      │   │
│  │  - Scrapes     │  - Routes alerts   │   │
│  │  - Stores TSDB │  - Sends webhook   │   │
│  └────────────────┼────────────────────┘   │
│                   │                         │
│                   ▼                         │
│         Alert Receiver (webhook endpoint)   │
└─────────────────────────────────────────────┘
          ▲         ▲         ▲
          │         │         │
    ┌─────┴─────────┴─────────┴─────────┐
    │   TelemetryCollector               │
    │   (Periodic polling)               │
    │                                    │
    │  1. Query Prometheus:              │
    │     - error_rate, latency p50/p99  │
    │     - pod_restarts                 │
    │     - downstream_errors            │
    │                                    │
    │  2. Tail pod logs:                 │
    │     - kubectl logs --tail=50       │
    │     - Parse timestamps & levels    │
    │                                    │
    │  3. Fetch alarms:                  │
    │     - GET /alerts endpoint         │
    │     - Current & historical         │
    │                                    │
    │  Returns: TelemetryBundle          │
    │  ┌────────────────────────────┐   │
    │  │ {                          │   │
    │  │   kpis: {...},             │   │
    │  │   logs: {...},             │   │
    │  │   alarms: [...],           │   │
    │  │   health: true/false       │   │
    │  │ }                          │   │
    │  └────────────────────────────┘   │
    └────────────────────────────────────┘
          │
          ▼
    ┌─────────────────────────┐
    │ TelemetryFormatter      │
    ├─────────────────────────┤
    │ .to_json()              │
    │ .to_markdown()          │
    │ .to_context_window()    │  ◄── For LLM input
    │ .to_jsonl()             │
    └─────────────────────────┘
```

## Module Files

### `schemas.py` (250 lines)
**Pydantic models for telemetry data structures:**

```python
LogEvent(timestamp, service, pod_name, level, message)
KPI(service, error_rate, latency_p50/p95/p99, pod_restart_count, available, ...)
Alarm(alert_name, status, severity, service, summary, description, starts_at, ends_at)
TelemetryBundle(timestamp, kpis, logs, alarms, collection_errors, services_monitored)
```

**Key methods:**
- `TelemetryBundle.is_healthy()` - Returns bool based on alarms and error rates
- `TelemetryBundle.get_service_summary()` - Returns summary dict for each service

### `collector.py` (550 lines)
**Main collector class that queries Prometheus, logs, and alarms:**

```python
class TelemetryCollector:
    async def collect() -> TelemetryBundle
    async def collect_kpis() -> Dict[str, KPI]
    async def collect_logs() -> Dict[str, List[LogEvent]]
    async def collect_alarms() -> List[Alarm]
    
    # Prometheus queries
    - error_rate = (5xx errors) / (total requests)
    - latency_p50/p95/p99 via histogram_quantile()
    - pod_restart_count from kube metrics
    - downstream_error_rate
    - service_availability (up metric)
```

**Execution modes:**
- One-shot collection: `await collector.collect()`
- Continuous loop: `run_collector_loop(interval_seconds=30, output_file="telemetry.jsonl")`

**CLI:**
```bash
python collector.py \
    --interval 30 \
    --prometheus-url http://localhost:9090 \
    --alertmanager-url http://localhost:5000 \
    --output-file telemetry.jsonl
```

### `formatter.py` (300 lines)
**Converts TelemetryBundle to various output formats:**

| Format | Use Case | Output Type |
|--------|----------|-------------|
| `to_json()` | Full structured data | Pretty JSON string |
| `to_dict()` | Python dict access | Dictionary |
| `to_markdown()` | Human reports | Markdown string |
| `to_context_window()` | **LLM input** | Condensed text |
| `to_jsonl()` | Logging/archival | Single-line JSON |

**Context window example:**
```
## TELEMETRY SNAPSHOT (2026-04-27T10:15:30.123456)
System Health: HEALTHY

## CRITICAL ISSUES
[none]

## UNHEALTHY SERVICES
- notification-service: High error rate (8.5%)

## HIGH LATENCY
- api-gateway: P99=650ms

## RECENT ERRORS
- [notification-service] Connection refused to database
```

### `test_collector.py` (80 lines)
**Single-shot test script that displays:**
- Collector initialization
- Collection timing
- Service summary (availability, error rate, latency, restarts)
- Context window output
- Markdown report

Usage:
```bash
python telemetry/test_collector.py
```

### `__init__.py`
**Package exports:**
```python
from telemetry import (
    TelemetryBundle,
    KPI,
    LogEvent,
    Alarm,
    TelemetryCollector,
    TelemetryFormatter,
)
```

### `requirements.txt`
```
httpx          # Async HTTP client
pydantic       # Data validation
```

## Prometheus Queries

| Metric | Query | Result |
|--------|-------|--------|
| Error Rate | `(sum(rate(service_requests_total{status=~"5.."}[5m])) / sum(rate(service_requests_total[5m])))` | 0-1 (0.05 = 5%) |
| P99 Latency | `histogram_quantile(0.99, sum(rate(service_request_duration_seconds_bucket[5m])) by (le))` | Seconds |
| P95 Latency | `histogram_quantile(0.95, ...)` | Seconds |
| P50 Latency | `histogram_quantile(0.50, ...)` | Seconds |
| Pod Restarts | `increase(kube_pod_container_status_restarts_total[5m])` | Count |
| Request Count | `sum(increase(service_requests_total[5m]))` | Count |
| Downstream Errors | `(sum(rate(downstream_calls_total{status="error"}[5m])) / sum(rate(downstream_calls_total[5m])))` | 0-1 |

## KPIs Collected

Per service:
- ✅ Total requests in 5m
- ✅ Error rate (5xx / total)
- ✅ Latency percentiles (p50, p95, p99)
- ✅ Pod restart count (total & in 5m)
- ✅ Downstream error rate
- ✅ Service availability
- ✅ Recent logs (last 50 lines)
- ✅ Current alarms

## Logs Collected

- **Source**: `kubectl logs --tail=50` per pod
- **Fields**: timestamp, level, message
- **Levels detected**: INFO, WARNING, ERROR, CRITICAL
- **Limit**: 50 lines per service (configurable)

## Alarms Collected

**Source**: Alert Receiver webhook endpoint (`/alerts`)

**Fields**:
- Alert name (e.g., "HighErrorRate")
- Status (firing/resolved)
- Severity (critical/warning/info)
- Service & component labels
- Summary & description
- Start & end timestamps

## Usage Examples

### One-shot Collection

```python
import asyncio
from telemetry import TelemetryCollector, TelemetryFormatter

async def collect_once():
    async with TelemetryCollector() as collector:
        bundle = await collector.collect()
        
        # Check health
        print(f"Healthy: {bundle.is_healthy()}")
        
        # Get summary
        print(bundle.get_service_summary())
        
        # Format for LLM
        context = TelemetryFormatter.to_context_window(bundle)
        return context

context = asyncio.run(collect_once())
```

### Continuous Collection Loop

```bash
# Collect every 30 seconds, write to file
python telemetry/collector.py --interval 30 --output-file telemetry.jsonl

# Monitor in real-time
tail -f telemetry.jsonl | jq '.collection_duration_ms'
tail -f telemetry.jsonl | jq '.alarms | length'
tail -f telemetry.jsonl | jq '.services_monitored'
```

### Agent Pipeline Integration

```python
from telemetry import TelemetryCollector, TelemetryFormatter

async def agent_loop():
    async with TelemetryCollector() as collector:
        while True:
            # Collect telemetry
            bundle = await collector.collect()
            
            # Convert to LLM context
            context = TelemetryFormatter.to_context_window(bundle)
            
            # Pass to diagnosis agent
            diagnosis = diagnose(context)
            
            # Get remediation
            actions = get_remediation(diagnosis)
            
            # Execute (with validation via policy gate)
            for action in actions:
                execute_with_validation(action, bundle)
            
            await asyncio.sleep(30)
```

## Data Flow

```
┌──────────────────────┐
│ Kubernetes Cluster   │
│ - Services           │
│ - Pods               │
│ - Prometheus         │
│ - Alertmanager       │
└──────────────────────┘
          │
          │ (kubectl, HTTP)
          ▼
┌──────────────────────────────────────┐
│ TelemetryCollector                   │
│ (Queries Prometheus, kubectl logs)   │
│ • collect_kpis()                     │
│ • collect_logs()                     │
│ • collect_alarms()                   │
└──────────────────────────────────────┘
          │
          │ Returns TelemetryBundle
          ▼
┌──────────────────────────────────────┐
│ TelemetryFormatter                   │
│ (Format conversions)                 │
│ • to_json()                          │
│ • to_context_window()  ◄── LLM       │
│ • to_markdown()                      │
└──────────────────────────────────────┘
          │
          ▼
    Agent Pipeline / Storage / UI
```

## Performance

- **Typical collection time**: 100-300ms
- **Prometheus queries**: 50-100ms
- **Log tailing**: 30-50ms per service
- **Alarm fetching**: 20-30ms
- **Memory per bundle**: ~50-200KB

## Configuration

```python
TelemetryCollector(
    prometheus_url="http://localhost:9090",      # Prometheus API
    alertmanager_url="http://localhost:5000",    # Alert Receiver
    namespace="default",                          # K8s namespace
    log_tail_lines=50,                            # Log lines per pod
    services=["frontend", "api-gateway", ...],    # Services to monitor
)
```

## Files Summary

| File | Purpose | LOC |
|------|---------|-----|
| `schemas.py` | Pydantic models | 250 |
| `collector.py` | Main collector class | 550 |
| `formatter.py` | Output formatting | 300 |
| `test_collector.py` | Single-shot test | 80 |
| `__init__.py` | Package exports | 20 |
| `requirements.txt` | Dependencies | 2 |
| `README.md` | Full documentation | 400 |
| `setup.sh` | Setup script | 40 |

## Quick Start

```bash
# 1. Install
pip install -r telemetry/requirements.txt

# 2. Port-forward
kubectl port-forward svc/prometheus 9090:9090 &
kubectl port-forward svc/alert-receiver 5000:5000 &

# 3. Test
python telemetry/test_collector.py

# 4. Continuous collection
python telemetry/collector.py --interval 30 --output-file telemetry.jsonl

# 5. View in agent
python agent/pipeline.py
```

## Troubleshooting

```bash
# Check Prometheus connectivity
curl http://localhost:9090/api/v1/query?query=up

# Check Alert Receiver connectivity
curl http://localhost:5000/alerts

# Check cluster access
kubectl get pods

# Run test with verbose output
python -c "import logging; logging.basicConfig(level=logging.DEBUG); from telemetry import *; ..." 
```

## Integration with Other Components

- **agent/pipeline.py** - Consumes TelemetryBundle for diagnosis
- **policy/gate.py** - Validates actions using telemetry
- **executor/remediation.py** - Verifies remediation via post-action telemetry
- **eval/harness.py** - Collects telemetry for evaluation metrics

See [TELEMETRY_USAGE.md](TELEMETRY_USAGE.md) for more examples and workflows.
