# Netpilot Monitoring - Quick Reference

## Deploy Monitoring Stack

```bash
cd sim/cluster/monitoring/
bash deploy.sh
```

## Access Services

```bash
# Terminal 1: Forward ports
bash monitoring-utils.sh ports

# Terminal 2-4: Open UIs
open http://localhost:9090   # Prometheus
open http://localhost:9093   # Alertmanager
open http://localhost:5000   # Alert Receiver
```

## View Alerts via API

```bash
# Current active alerts
curl http://localhost:5000/alerts | jq .

# Only firing alerts
curl http://localhost:5000/alerts/active | jq .

# Historical alerts (last 50)
curl http://localhost:5000/alerts/history?limit=50 | jq .

# Filter by severity
curl http://localhost:5000/alerts | jq '.active_alerts[] | select(.labels.severity == "critical")'
```

## Quick Commands

```bash
# View status
bash monitoring-utils.sh status

# View alerts realtime
bash monitoring-utils.sh alerts

# View logs
bash monitoring-utils.sh logs alert-receiver
bash monitoring-utils.sh logs prometheus
bash monitoring-utils.sh logs alertmanager

# Trigger test cascade failure
bash monitoring-utils.sh test-alerts
```

## Trigger Faults & Watch Alerts

### Pod Crash (Quick)
```bash
# Terminal 1: Monitor alerts
watch -n 1 'curl -s http://localhost:5000/alerts | jq ".active_alerts | length"'

# Terminal 2: Crash a pod
python sim/fault_injector.py --scenario pod-crash --target notification-service

# Expected: ServiceDown alert fires in ~2 minutes
```

### Network Degradation (Latency)
```bash
# Terminal 1: Monitor HighLatency alerts
watch -n 1 'curl -s http://localhost:5000/alerts | jq ".active_alerts[] | select(.labels.alertname == \"HighLatency\")"'

# Terminal 2: Degrade network
python sim/fault_injector.py --scenario link-degrade --target order-service --duration 60

# Expected: HighLatency alert fires within 2 minutes
```

### Cascade Failure (Full Chain)
```bash
# Terminal 1: Monitor cascading alerts
watch -n 2 'curl -s http://localhost:5000/alerts | jq .active_alerts'

# Terminal 2: Trigger cascade
python sim/fault_injector.py --scenario cascade --target notification-service --watch-duration 60

# Expected: Cascade propagates from notification-service → order-service → api-gateway → frontend
```

## Alert Rules Reference

| Alert | Condition | Threshold | Severity |
|-------|-----------|-----------|----------|
| **HighPodRestartRate** | Pod restarts in 5m | > 2 restarts | 🔴 Critical |
| **HighErrorRate** | HTTP 5xx errors | > 5% | 🟡 Warning |
| **HighLatency** | P99 latency | > 500ms | 🟡 Warning |
| **ServiceDown** | Metric scrape failure | 2 minutes | 🔴 Critical |
| **HighDownstreamFailureRate** | Downstream errors | > 10% | 🟡 Warning |

## Prometheus Queries

```promql
# Total requests per service
sum(rate(service_requests_total[5m])) by (service)

# Error rate per service
(sum(rate(service_requests_total{status=~"5.."}[5m])) by (service) / sum(rate(service_requests_total[5m])) by (service))

# P99 latency per service
histogram_quantile(0.99, sum(rate(service_request_duration_seconds_bucket[5m])) by (service, le))

# Downstream call latency
histogram_quantile(0.99, sum(rate(service_downstream_latency_seconds_bucket[5m])) by (service, downstream, le))

# Service availability
up{job="kubernetes-pods"}
```

## Alert Webhook Payload Example

```json
{
  "status": "firing",
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "HighErrorRate",
        "severity": "warning",
        "component": "http",
        "service": "order-service"
      },
      "annotations": {
        "summary": "High HTTP error rate detected",
        "description": "Service order-service error rate is 8.5% (threshold: 5%)"
      },
      "startsAt": "2026-04-27T10:15:20Z",
      "endsAt": "0001-01-01T00:00:00Z"
    }
  ],
  "groupLabels": {
    "alertname": "HighErrorRate",
    "service": "order-service"
  },
  "commonLabels": {
    "alertname": "HighErrorRate",
    "severity": "warning",
    "service": "order-service"
  },
  "commonAnnotations": {
    "summary": "High HTTP error rate detected",
    "description": "Service order-service error rate is 8.5%"
  },
  "externalURL": "http://alertmanager:9093",
  "version": "4",
  "groupKey": "{}:{alertname=\"HighErrorRate\",service=\"order-service\"}"
}
```

## Files Reference

| File | Purpose |
|------|---------|
| `deploy.sh` | Deploy entire monitoring stack |
| `monitoring-utils.sh` | Utility commands for monitoring |
| `01-prometheus.yaml` | Prometheus Deployment + ConfigMap |
| `02-alertmanager.yaml` | Alertmanager Deployment + ConfigMap |
| `03-alert-receiver.yaml` | Alert Receiver Deployment |
| `prometheus.yml` | Prometheus config (Kubernetes auto-discovery) |
| `alert-rules.yml` | Alert rule definitions |
| `alertmanager.yml` | Alertmanager routing + webhook config |
| `alert-receiver.py` | Flask app to receive webhook alerts |
| `alert-receiver.Dockerfile` | Docker image for alert receiver |
| `MONITORING.md` | Full documentation |

## Troubleshooting

```bash
# Check if pods are running
kubectl get pods | grep -E "prometheus|alertmanager|alert-receiver"

# Check service endpoints
kubectl get svc | grep -E "prometheus|alertmanager|alert-receiver"

# Check logs
kubectl logs -f deployment/alert-receiver
kubectl logs -f deployment/prometheus
kubectl logs -f deployment/alertmanager

# Check if metrics are being scraped
curl http://localhost:9090/api/v1/targets | jq .

# Test alert receiver connectivity from alertmanager pod
kubectl exec -it $(kubectl get pods -l app=alertmanager -o jsonpath='{.items[0].metadata.name}') -- \
  wget -O - http://alert-receiver:5000/health
```

## Performance Notes

- Prometheus scrape interval: 15s
- Alert evaluation: 30s
- Most alerts fire within 2 minutes (alert.for + evaluation time)
- Cascade failure propagation: 5-10s per hop (depends on network)
