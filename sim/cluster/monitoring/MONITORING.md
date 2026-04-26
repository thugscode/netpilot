# Monitoring Stack Documentation

## Overview

Prometheus + Alertmanager deployment for netpilot with custom alert rules and webhook-based alert delivery to a REST endpoint.

## Components

### 1. Prometheus
- Scrapes metrics from all annotated pods
- Evaluates alert rules every 30 seconds
- Stores time-series data for 24 hours
- Pod manifest: [01-prometheus.yaml](01-prometheus.yaml)

### 2. Alertmanager
- Routes alerts based on severity and component
- Sends critical/cascade alerts to webhook receiver
- Pod manifest: [02-alertmanager.yaml](02-alertmanager.yaml)

### 3. Alert Receiver
- REST service that receives webhook alerts from Alertmanager
- Exposes `/alerts` endpoint with current active alerts
- Logs all alerts to JSONL file
- Pod manifest: [03-alert-receiver.yaml](03-alert-receiver.yaml)

## Alert Rules

### 1. **HighPodRestartRate** (Critical)
```
Pod restart count > 2 in 5 minutes
```
- **Trigger**: `increase(kube_pod_container_status_restarts_total[5m]) > 2`
- **Wait**: 1 minute
- **Severity**: Critical

### 2. **HighErrorRate** (Warning)
```
HTTP error rate > 5% for 2 minutes
```
- **Trigger**: `(5xx errors) / (total requests) > 0.05`
- **Window**: 5 minutes
- **Wait**: 2 minutes
- **Severity**: Warning

### 3. **HighLatency** (Warning)
```
P99 latency > 500ms for 2 minutes
```
- **Trigger**: `histogram_quantile(0.99, request_duration) > 0.5s`
- **Window**: 5 minutes
- **Wait**: 2 minutes
- **Severity**: Warning

### 4. **ServiceDown** (Critical)
```
Service not sending metrics for 2 minutes
```
- **Trigger**: `up{job="kubernetes-pods"} == 0`
- **Wait**: 2 minutes
- **Severity**: Critical

### 5. **HighDownstreamFailureRate** (Warning)
```
Downstream call failures > 10% for 1 minute
```
- **Trigger**: `(downstream errors) / (downstream calls) > 0.1`
- **Window**: 5 minutes
- **Wait**: 1 minute
- **Severity**: Warning

## Deployment

### Build & Deploy

```bash
cd sim/cluster/monitoring/
bash deploy.sh
```

This script will:
1. Build the alert-receiver Docker image
2. Load it into the kind cluster
3. Deploy Prometheus ConfigMap + Deployment
4. Deploy Alertmanager ConfigMap + Deployment
5. Deploy Alert Receiver Deployment

### Manual Deployment

```bash
# Build alert receiver image
docker build -t netpilot-alert-receiver:latest -f alert-receiver.Dockerfile .

# Load into kind
kind load docker-image netpilot-alert-receiver:latest --name netpilot

# Deploy components
kubectl apply -f 01-prometheus.yaml
kubectl apply -f 02-alertmanager.yaml
kubectl apply -f 03-alert-receiver.yaml
```

## Alert API Endpoints

### Get Current Active Alerts
```bash
curl http://localhost:5000/alerts
```

**Response:**
```json
{
  "timestamp": "2026-04-27T10:15:30.123456",
  "active_alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "HighErrorRate",
        "severity": "warning",
        "service": "notification-service"
      },
      "annotations": {
        "summary": "High HTTP error rate detected",
        "description": "Service notification-service error rate is 8.5% (threshold: 5%)"
      },
      "startsAt": "2026-04-27T10:15:20Z"
    }
  ],
  "alert_count": 1,
  "total_received": 42
}
```

### Get Only Firing Alerts
```bash
curl http://localhost:5000/alerts/active
```

### Get Historical Alerts
```bash
curl http://localhost:5000/alerts/history?limit=50
```

### Service Health
```bash
curl http://localhost:5000/health
```

## Usage Examples

### 1. Port Forward to Access UIs

**Prometheus:**
```bash
kubectl port-forward svc/prometheus 9090:9090 &
open http://localhost:9090
```

**Alertmanager:**
```bash
kubectl port-forward svc/alertmanager 9093:9093 &
open http://localhost:9093
```

**Alert Receiver:**
```bash
kubectl port-forward svc/alert-receiver 5000:5000 &
open http://localhost:5000
```

### 2. Trigger Alerts

**High error rate:**
```bash
# From within a pod, make many requests that fail
for i in {1..100}; do curl http://notification-service:8000/invalid; done
```

**High latency:**
```bash
# Use fault injector to add network delay
python sim/fault_injector.py --scenario link-degrade --target order-service --duration 60
```

**Cascade failure:**
```bash
# Crash notification-service and watch alerts propagate
python sim/fault_injector.py --scenario cascade --target notification-service
```

### 3. Monitor Alerts in Real-time

**Watch active alerts:**
```bash
watch -n 2 'curl -s http://localhost:5000/alerts | jq ".active_alerts | length"'
```

**Stream alerts to file:**
```bash
tail -f /tmp/netpilot-alerts.jsonl | jq .
```

**Filter alerts by severity:**
```bash
curl -s http://localhost:5000/alerts | jq '.active_alerts[] | select(.labels.severity == "critical")'
```

## Configuration

### Prometheus Scrape Config
- Scrapes pods with annotation `prometheus.io/scrape: "true"`
- Uses port from `prometheus.io/port` annotation
- Metrics path from `prometheus.io/path` annotation (default: `/metrics`)
- Evaluation interval: 15 seconds
- Scrape interval: 15 seconds

### Alertmanager Routing
- Critical alerts → webhook receiver
- Cascade failure alerts → webhook receiver
- Other alerts → silent (no action)
- Group wait: 10 seconds
- Repeat interval: 1 hour

### Alert Receiver Storage
- In-memory storage of current/historical alerts
- JSONL file log: `/tmp/netpilot-alerts.jsonl`
- Each webhook call appends to JSONL

## Testing

### Trigger High Error Rate

```bash
# Port-forward to frontend
kubectl port-forward svc/frontend 8000:8000 &

# Call cascade endpoint many times (will eventually hit errors)
for i in {1..50}; do curl http://localhost:8000/cascade 2>/dev/null | jq '.cascade_results[] | select(.status == "error")'; done
```

### Check Alert Firing

```bash
# Wait ~2 minutes for alert evaluation + firing threshold
sleep 120

# Check alerts
curl http://localhost:5000/alerts | jq '.active_alerts[] | .labels.alertname'
```

### View Prometheus Graph

1. Open http://localhost:9090
2. Go to **Alerts** tab
3. View firing alerts
4. Click on **Graph** to visualize metrics

## Troubleshooting

### Alerts not firing?
1. Check Prometheus targets: http://localhost:9090/targets
2. Verify metrics are being scraped
3. Check alert rule expressions: http://localhost:9090/alerts

### Webhook not receiving?
1. Check Alertmanager logs: `kubectl logs -f deployment/alertmanager`
2. Verify alert-receiver service: `kubectl get svc alert-receiver`
3. Test connectivity: `kubectl exec -it <alertmanager-pod> -- wget http://alert-receiver:5000/health`

### No metrics from services?
1. Verify service pods have annotations (check deployment manifests)
2. Check if metrics endpoint works: `kubectl exec -it <service-pod> -- curl http://localhost:8000/metrics`
3. Verify RBAC permissions for Prometheus

## Clean Up

```bash
# Delete monitoring stack
kubectl delete -f 01-prometheus.yaml
kubectl delete -f 02-alertmanager.yaml
kubectl delete -f 03-alert-receiver.yaml
```
