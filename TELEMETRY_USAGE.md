# Telemetry Collection - Quick Reference

## Setup

```bash
# Install dependencies
pip install -r telemetry/requirements.txt

# Ensure port-forwards are active
kubectl port-forward svc/prometheus 9090:9090 &
kubectl port-forward svc/alert-receiver 5000:5000 &
```

## One-shot Collection

```bash
# Test collector (displays summary, context window, and markdown)
python telemetry/test_collector.py
```

**Output:**
```
Testing telemetry collection...

✓ Collector initialized
  - Prometheus: http://localhost:9090
  - Alert Receiver: http://localhost:5000

Collecting telemetry...
✓ Collection completed in 125.3ms

============================================================
TELEMETRY SUMMARY
============================================================

Timestamp: 2026-04-27T10:15:30.123456
System Health: ✓ HEALTHY
Active Alarms: 0
Collection Errors: 0

Services:
  ✓ frontend
     Error Rate: 0.2%
     P99 Latency: 150ms
     Pod Restarts: 0
     Recent Logs: 5
  ✓ api-gateway
     Error Rate: 0.0%
     P99 Latency: 80ms
     Pod Restarts: 0
     Recent Logs: 3
```

## Continuous Collection Loop

```bash
# Default: 30s interval, print to stdout
python telemetry/collector.py

# Custom interval (15s)
python telemetry/collector.py --interval 15

# Write to JSONL file for archival
python telemetry/collector.py \
    --interval 30 \
    --output-file telemetry.jsonl

# All options
python telemetry/collector.py \
    --interval 10 \
    --prometheus-url http://localhost:9090 \
    --alertmanager-url http://localhost:5000 \
    --output-file telemetry.jsonl
```

**JSONL output (one bundle per line):**
```bash
tail -f telemetry.jsonl | jq '.collection_duration_ms'
tail -f telemetry.jsonl | jq '.alarms | length'
tail -f telemetry.jsonl | jq '.get_service_summary()'
```

## Programmatic Usage

### Single Collection

```python
import asyncio
from telemetry import TelemetryCollector

async def collect():
    async with TelemetryCollector() as collector:
        bundle = await collector.collect()
        return bundle

bundle = asyncio.run(collect())
print(bundle.get_service_summary())
```

### Continuous Loop

```python
import asyncio
from telemetry import TelemetryCollector, TelemetryFormatter

async def monitor():
    async with TelemetryCollector() as collector:
        iteration = 0
        while True:
            iteration += 1
            print(f"\n--- Iteration {iteration} ---")
            
            bundle = await collector.collect()
            
            # Use LLM-optimized context
            context = TelemetryFormatter.to_context_window(bundle)
            print(context)
            
            # Use for decision-making
            if not bundle.is_healthy():
                print("⚠ System is unhealthy!")
            
            await asyncio.sleep(30)

asyncio.run(monitor())
```

### Format Conversions

```python
from telemetry import TelemetryFormatter

# JSON (full structure)
json_str = TelemetryFormatter.to_json(bundle)

# Markdown report (human-readable)
markdown = TelemetryFormatter.to_markdown(bundle)

# Context window (LLM-optimized, condensed)
context = TelemetryFormatter.to_context_window(bundle)

# JSONL (single-line JSON for logging)
jsonl = TelemetryFormatter.to_jsonl(bundle)
```

## Data Structures

### TelemetryBundle
```python
bundle.timestamp           # When collected
bundle.collection_duration_ms  # How long it took
bundle.kpis               # Dict[str, KPI] - metrics per service
bundle.logs               # Dict[str, List[LogEvent]]
bundle.alarms             # List[Alarm]
bundle.collection_errors  # List[str]
bundle.services_monitored # List[str]

# Methods
bundle.is_healthy()            # Returns bool
bundle.get_service_summary()   # Returns summary dict
```

### KPI
```python
kpi.service           # Service name
kpi.timestamp         # When collected
kpi.request_count_5m  # Total requests in 5 minutes
kpi.error_rate        # Percentage (0.0-1.0)
kpi.latency_p50_ms    # Median latency
kpi.latency_p99_ms    # P99 latency
kpi.pod_restart_count      # Total restarts
kpi.pod_restart_count_5m   # Restarts in 5m
kpi.downstream_error_rate  # Downstream call errors
kpi.available         # Service is responding
```

### LogEvent
```python
log.timestamp   # When the log was written
log.service     # Service name
log.pod_name    # Pod name
log.level       # INFO, WARNING, ERROR, etc.
log.message     # Log message
```

### Alarm
```python
alarm.alert_name   # Alert name (e.g., "HighErrorRate")
alarm.status       # "firing" or "resolved"
alarm.severity     # "critical", "warning", "info"
alarm.service      # Service name (if applicable)
alarm.component    # Component (pod, http, latency, etc.)
alarm.summary      # Short description
alarm.description  # Detailed description
alarm.starts_at    # When alert started
alarm.ends_at      # When alert resolved (if resolved)
```

## Example Workflows

### Monitor System Health

```bash
# Terminal 1: Collect every 15 seconds
python telemetry/collector.py --interval 15 --output-file health.jsonl

# Terminal 2: Watch for unhealthy states
while sleep 5; do
    tail -1 health.jsonl | jq 'if .is_healthy() then "✓" else "✗" end'
done
```

### Detect Cascade Failures

```python
import asyncio
from telemetry import TelemetryCollector

async def detect_cascade():
    async with TelemetryCollector() as collector:
        while True:
            bundle = await collector.collect()
            
            # Count services with errors
            unhealthy = [
                s for s, kpi in bundle.kpis.items()
                if not kpi.available or kpi.error_rate > 0.05
            ]
            
            if len(unhealthy) >= 2:
                print(f"⚠ Potential cascade: {unhealthy}")
            
            await asyncio.sleep(10)

asyncio.run(detect_cascade())
```

### Extract Context for Remediation Agent

```python
from telemetry import TelemetryCollector, TelemetryFormatter
import json

async def get_remediation_context():
    async with TelemetryCollector() as collector:
        bundle = await collector.collect()
        
        # Condensed context for LLM/agent
        context = TelemetryFormatter.to_context_window(bundle)
        
        # Send to remediation pipeline
        agent_input = {
            "context": context,
            "alarms": [a.dict() for a in bundle.alarms if a.status == "firing"],
            "unhealthy_services": [
                (s, k.dict()) for s, k in bundle.kpis.items()
                if not k.available or k.error_rate > 0.05
            ]
        }
        
        print(json.dumps(agent_input, indent=2))

asyncio.run(get_remediation_context())
```

## Troubleshooting

### "Connection refused" errors

```bash
# Check if endpoints are accessible
curl http://localhost:9090/api/v1/query?query=up
curl http://localhost:5000/health

# Port-forward if needed
kubectl port-forward svc/prometheus 9090:9090 &
kubectl port-forward svc/alert-receiver 5000:5000 &
```

### No logs collected

```bash
# Check pod names
kubectl get pods -l app=frontend

# Check logs directly
kubectl logs -f deployment/frontend --tail=20

# Verify kubectl access
kubectl get nodes
```

### Prometheus metrics missing

```bash
# Check Prometheus targets
curl http://localhost:9090/api/v1/targets | jq .

# Verify services have prometheus annotations
kubectl get pods -o jsonpath='{.items[0].metadata.annotations}'
```

## Performance Tips

- Increase `log_tail_lines` to get more context (default: 50)
- Decrease collection interval only if performance permits (default: 30s)
- Use `--output-file` to avoid writing large bundles to stdout
- Filter services if monitoring a subset: `TelemetryCollector(services=["frontend", "api-gateway"])`

## Integration Points

The telemetry module integrates with:
- **Agent Pipeline** (`agent/pipeline.py`) - Consumes telemetry for diagnosis
- **Policy Gate** (`policy/gate.py`) - Uses telemetry for validation
- **Executor** (`executor/remediation.py`) - Uses telemetry feedback for remediation verification
