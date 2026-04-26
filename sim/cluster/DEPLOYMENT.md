# Netpilot Cluster Setup

## Service Topology

```
frontend
   └── api-gateway
        ├── order-service
        │    ├── inventory-service
        │    │    └── notification-service
        │    └── notification-service
        └── inventory-service
             └── notification-service
```

This enables observable cascading failures: when notification-service fails, all upstream services are affected.

## Build & Deploy

### 1. Build the microservice image

```bash
cd sim/cluster/services/
docker build -t netpilot-microservice:latest .
```

### 2. Create kind cluster

```bash
kind create cluster --config kind-config.yaml
```

### 3. Load image into kind

```bash
kind load docker-image netpilot-microservice:latest --name netpilot
```

### 4. Deploy services

```bash
kubectl apply -f sim/cluster/services/01-frontend.yaml
kubectl apply -f sim/cluster/services/02-api-gateway.yaml
kubectl apply -f sim/cluster/services/03-order-service.yaml
kubectl apply -f sim/cluster/services/04-inventory-service.yaml
kubectl apply -f sim/cluster/services/05-notification-service.yaml
```

Or apply all at once:
```bash
kubectl apply -f sim/cluster/services/
```

## Service Endpoints

Each service runs on port 8000:

- `GET /` - Service info
- `GET /health` - Health check
- `GET /metrics` - Prometheus metrics
- `GET /call/{service}` - Call a specific downstream service
- `GET /cascade` - Call all downstream services
- `POST /inject-fault` - Inject fault (query params: `fault_type=crash|error_rate`, `duration_seconds=30`)

### Example: Trigger cascading failure

```bash
# Make frontend call api-gateway → order-service → notification-service
kubectl port-forward svc/frontend 8000:8000 &
curl http://localhost:8000/cascade

# Inject crash fault into notification-service
curl -X POST http://localhost:8000/call/api-gateway \
  -d "service=notification-service&fault_type=crash&duration_seconds=30"
```

## Metrics Exported

Each service exports:
- `service_requests_total` - Request count by endpoint, method, status
- `service_request_duration_seconds` - Request latency histogram
- `service_downstream_calls_total` - Downstream call count by status
- `service_downstream_latency_seconds` - Downstream call latency

Scrape metrics via Prometheus or direct curl:
```bash
kubectl port-forward svc/notification-service 8000:8000 &
curl http://localhost:8000/metrics
```
