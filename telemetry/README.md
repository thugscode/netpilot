# Telemetry Module

Collects and formats telemetry data (KPIs, logs, alarms) from the netpilot Kubernetes cluster.

## Components

### schemas.py
Pydantic models for telemetry data:
- **LogEvent**: Single log line from a pod
- **KPI**: Key Performance Indicators (error rate, latency, restarts)
- **Alarm**: Alert from Alertmanager
- **TelemetryBundle**: Complete telemetry snapshot

### collector.py
Collects telemetry on a configurable interval:
- **KPIs**: Queries Prometheus for error rate, latency (p50/p95/p99), pod restarts, downstream metrics
- **Logs**: Tails recent log lines from pod log streams via `kubectl logs`
- **Alarms**: Fetches active alarms from Alert Receiver webhook endpoint

### formatter.py
Converts telemetry bundles to various output formats:
- **JSON**: Full structured format
- **Markdown**: Human-readable report
- **Context Window**: Condensed format optimized for LLM input
- **JSONL**: Single-line JSON for logging

## Usage

### Basic Collection (One-shot)

```python
import asyncio
from telemetry import TelemetryCollector, TelemetryFormatter

async def collect_once():
    async with TelemetryCollector() as collector:
        bundle = await collector.collect()
        print(bundle.get_service_summary())

asyncio.run(collect_once())
```

### Continuous Collection

```bash
python -m telemetry.collector \
    --interval 30 \
    --prometheus-url http://localhost:9090 \
    --alertmanager-url http://localhost:5000 \
    --output-file telemetry.jsonl
```

### Format Outputs

```python
from telemetry import TelemetryFormatter

# JSON format
json_str = TelemetryFormatter.to_json(bundle)

# Markdown report
md_report = TelemetryFormatter.to_markdown(bundle)

# LLM-optimized context
context = TelemetryFormatter.to_context_window(bundle)

# Single-line JSON for logging
jsonl = TelemetryFormatter.to_jsonl(bundle)
```

## KPIs Collected per Service

| Metric | Source | Query |
|--------|--------|-------|
| Request Count (5m) | Prometheus | `sum(increase(service_requests_total[5m]))` |
| Error Rate | Prometheus | `sum(5xx errors) / sum(total requests)` |
| P50 Latency | Prometheus | `histogram_quantile(0.50, service_request_duration_seconds_bucket)` |
| P95 Latency | Prometheus | `histogram_quantile(0.95, service_request_duration_seconds_bucket)` |
| P99 Latency | Prometheus | `histogram_quantile(0.99, service_request_duration_seconds_bucket)` |
| Pod Restart Count | Prometheus | `increase(kube_pod_container_status_restarts_total[24h])` |
| Pod Restart Count (5m) | Prometheus | `increase(kube_pod_container_status_restarts_total[5m])` |
| Downstream Error Rate | Prometheus | `sum(downstream errors) / sum(downstream calls)` |
| Downstream Calls (5m) | Prometheus | `sum(increase(service_downstream_calls_total[5m]))` |
| Service Availability | Prometheus | `up{job="kubernetes-pods"}` |

## Logs Collected

- Tails last 50 lines from each service pod
- Parses timestamps and log levels (INFO, WARNING, ERROR, etc.)
- Captures service name, pod name, and message

## Alarms Collected

Fetches from Alert Receiver webhook endpoint:
- Alert name
- Status (firing/resolved)
- Severity (critical/warning/info)
- Service and component labels
- Summary and description
- Start and end timestamps

## TelemetryBundle Fields

```python
TelemetryBundle(
    timestamp: datetime,           # Collection timestamp
    collection_duration_ms: float, # How long collection took
    kpis: Dict[str, KPI],         # KPIs keyed by service
    logs: Dict[str, List[LogEvent]],  # Logs keyed by service
    alarms: List[Alarm],          # Active alarms
    collection_errors: List[str], # Any errors during collection
    services_monitored: List[str],# Services included in collection
)
```

## Context Window Format

The `to_context_window()` formatter produces condensed output:

```
## TELEMETRY SNAPSHOT (2026-04-27T10:15:30.123456)
System Health: HEALTHY
Collection Time: 125ms

## CRITICAL ISSUES
- [HighErrorRate] HTTP error rate > 5%
  Details: Service order-service error rate is 8.5%

## UNHEALTHY SERVICES
- order-service: High error rate (8.5%)

## HIGH LATENCY
- api-gateway: P99=650ms

## HEALTHY SERVICES
- frontend (120 req, 0.2% err, 150ms p99)
- notification-service (80 req, 0.0% err, 50ms p99)

## RECENT ERRORS
- [order-service] Connection refused to inventory-service
- [order-service] Timeout waiting for response

## COLLECTION ERRORS
- Alarm collection: Connection refused
```

## Configuration

### Collector Options

```python
TelemetryCollector(
    prometheus_url="http://localhost:9090",      # Prometheus HTTP API
    alertmanager_url="http://localhost:5000",    # Alert Receiver endpoint
    namespace="default",                          # Kubernetes namespace
    log_tail_lines=50,                            # Number of log lines to fetch
    services=["frontend", "api-gateway", ...],    # Services to monitor (auto-discovered if None)
)
```

### Collection Interval

Default: 30 seconds

```bash
# Custom interval
python collector.py --interval 15
```

## Requirements

- Python 3.8+
- `kubectl` (configured to access cluster)
- httpx
- pydantic

## Example Workflow

```bash
# Terminal 1: Port-forward monitoring endpoints
kubectl port-forward svc/prometheus 9090:9090 &
kubectl port-forward svc/alert-receiver 5000:5000 &

# Terminal 2: Run telemetry collector
python -m telemetry.collector --interval 10 --output-file telemetry.jsonl

# Terminal 3: Monitor output
tail -f telemetry.jsonl | jq '.collection_duration_ms'
tail -f telemetry.jsonl | jq '.alarms | length'
```

## Output Examples

### JSON Output

```json
{
  "timestamp": "2026-04-27T10:15:30.123456",
  "collection_duration_ms": 125.4,
  "kpis": {
    "frontend": {
      "service": "frontend",
      "timestamp": "2026-04-27T10:15:30.123456",
      "request_count_5m": 450.0,
      "error_rate": 0.002,
      "latency_p99_ms": 150.5,
      "available": true
    }
  },
  "logs": {
    "frontend": [
      {
        "timestamp": "2026-04-27T10:15:25.000000",
        "service": "frontend",
        "pod_name": "frontend-abc123",
        "message": "Request from 192.168.1.1"
      }
    ]
  },
  "alarms": [],
  "collection_errors": [],
  "services_monitored": ["frontend", "api-gateway", "order-service"]
}
```

### Markdown Output

The markdown format provides a human-readable report with tables for KPIs, lists of alarms and errors, and recent logs.

## Error Handling

- Non-fatal errors during collection (e.g., failed Prometheus query) are logged and included in `collection_errors`
- If a service pod cannot be found, logs for that service are skipped
- If Alertmanager endpoint is unreachable, alarms list will be empty
- Collection continues even if individual components fail

## Performance

- Typical collection time: 100-300ms (depends on number of services and log volume)
- Prometheus queries: ~50-100ms
- Log tailing: ~30-50ms per service
- Alarm fetching: ~20-30ms

## Troubleshooting

```bash
# Check if Prometheus is accessible
curl http://localhost:9090/api/v1/query?query=up

# Check if Alert Receiver is accessible
curl http://localhost:5000/alerts

# Run collector in verbose mode
python collector.py --interval 30 2>&1 | grep -E "DEBUG|ERROR"

# Check pod logs directly
kubectl logs -f <pod-name> --tail=20
```
