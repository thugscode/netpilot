# Netpilot - Self-Healing Kubernetes Agent System

**Status**: Partially Complete (Simulation & Telemetry infrastructure ready)

## Project Overview

Netpilot is an autonomous agent system that diagnoses and remediates failures in microservices running on Kubernetes. It uses LLM-guided diagnosis, policy-based validation, and automated remediation to maintain system SLAs.

**Architecture**:
```
Kubernetes Cluster
    ├── Services (5 microservices with metrics)
    ├── Prometheus (metrics collection & alert rules)
    └── Alertmanager (alert routing & webhook)
         ↓
    Telemetry Collector
         ↓
    Agent Pipeline (diagnose → rank → validate → execute)
         ↓
    Policy Gate (SLA validation, rollback registry)
         ↓
    Executor (remediation actions via kubectl/REST)
         ↓
    Evaluation Harness (MTTR, FPR, SLA metrics)
```

## ✅ Completed Components

### 1. Simulation Infrastructure (`sim/`)

#### 1.1 Kind Cluster Configuration
**File**: `sim/cluster/kind-config.yaml`
- 1 control-plane node
- 2 worker nodes
- Kubernetes v1.27.0
- Ready for Prometheus + Alertmanager + services

#### 1.2 Microservices (5 services)
**Location**: `sim/cluster/services/`

Services with inter-service call dependencies:
```
frontend
  └─→ api-gateway
       ├─→ order-service
       │    ├─→ inventory-service
       │    │    └─→ notification-service
       │    └─→ notification-service
       └─→ inventory-service
            └─→ notification-service
```

**Each service** (`app.py` running FastAPI):
- HTTP server on port 8000
- **Metrics**: `/metrics` endpoint with Prometheus client
  - `service_requests_total` - request counts by status
  - `service_request_duration_seconds` - latency histogram
  - `service_downstream_calls_total` - downstream call tracking
  - `service_downstream_latency_seconds` - downstream latency
- **Health**: `/health` endpoint, liveness/readiness probes
- **Endpoints**:
  - `GET /` - Service info
  - `GET /call/{service}` - Call specific downstream
  - `GET /cascade` - Call all downstreams (shows cascading failures)
  - `POST /inject-fault` - Fault injection (crash, error_rate)
- **Containerized**: Dockerfile includes fastapi, httpx, prometheus-client

**Deployment Manifests** (`01-frontend.yaml` through `05-notification-service.yaml`):
- Kubernetes Deployment + Service per service
- Pod annotations for Prometheus scraping
- Resource limits (requests: 100m CPU, 512Mi RAM)
- Health probes configured

#### 1.3 Fault Injector CLI
**File**: `sim/fault_injector.py`

Three fault scenarios:
1. **pod-crash** - Delete pod to trigger Kubernetes restart
   - Finds pod via label selector
   - Logs event to `events.jsonl`
   - Verifies pod restart
   
2. **link-degrade** - Add network delay + packet loss
   - Uses `tc netem delay 200ms loss 10%`
   - Runs for specified duration via `kubectl exec`
   - Automatic cleanup
   - Logs degradation config to `events.jsonl`
   
3. **cascade** - Trigger pod-crash + watch failure propagation
   - Crashes target pod
   - Monitors upstream services for errors
   - Tracks cascade propagation with timing
   - Logs each cascade hop to `events.jsonl`

**Event Log Format** (`events.jsonl`):
```json
{
  "timestamp": "2026-04-27T10:15:30.123456",
  "scenario": "pod-crash",
  "target": "notification-service",
  "pod_name": "notification-service-xyz",
  "action": "deleted"
}
```

**Usage**:
```bash
python sim/fault_injector.py --scenario pod-crash --target notification-service
python sim/fault_injector.py --scenario link-degrade --target order-service --duration 60
python sim/fault_injector.py --scenario cascade --target notification-service --watch-duration 45
```

### 2. Monitoring Stack (`sim/cluster/monitoring/`)

#### 2.1 Prometheus
**Files**: `prometheus.yml`, `01-prometheus.yaml`
- Auto-discovery of Kubernetes pods via service discovery
- Scrapes `/metrics` from annotated pods (15s interval)
- Stores time-series for 24 hours
- ServiceAccount with RBAC for Kubernetes API access
- Deployment manifest with ConfigMap integration

#### 2.2 Alert Rules
**File**: `alert-rules.yml`

**5 Alert Rules**:
1. **HighPodRestartRate** (Critical)
   - Trigger: Pod restarts > 2 in 5 minutes
   - Wait: 1 minute
   
2. **HighErrorRate** (Warning)
   - Trigger: HTTP error rate > 5% for 2 minutes
   - Metric: `(5xx errors) / (total requests)`
   
3. **HighLatency** (Warning)
   - Trigger: P99 latency > 500ms for 2 minutes
   - Metric: `histogram_quantile(0.99, ...)`
   
4. **ServiceDown** (Critical)
   - Trigger: Metrics not received for 2 minutes
   - Metric: `up{job="kubernetes-pods"} == 0`
   
5. **HighDownstreamFailureRate** (Warning)
   - Trigger: Downstream errors > 10% for 1 minute
   - Metric: `(downstream errors) / (downstream calls)`

#### 2.3 Alertmanager
**Files**: `alertmanager.yml`, `02-alertmanager.yaml`
- Routes critical/cascade alerts to webhook receiver
- Groups alerts by name, service, severity
- 10s group_wait, 1h repeat interval
- Webhook receiver at `http://alert-receiver:5000/webhook`

#### 2.4 Alert Receiver (Custom)
**Files**: `alert-receiver.py`, `03-alert-receiver.yaml`, `alert-receiver.Dockerfile`
- Flask service on port 5000
- Receives webhook alerts from Alertmanager
- Stores current & historical alerts in memory + JSONL file
- **Endpoints**:
  - `POST /webhook` - Receive alerts
  - `GET /alerts` - Current active alerts (JSON)
  - `GET /alerts/active` - Only firing alerts
  - `GET /alerts/history?limit=50` - Historical alerts
  - `GET /health` - Health check

**Alert Storage**:
- In-memory for quick access
- JSONL file at `/tmp/netpilot-alerts.jsonl` for persistence

#### 2.5 Deployment & Utilities
**Files**: 
- `deploy.sh` - One-command deployment of monitoring stack
- `monitoring-utils.sh` - Utilities (ports, alerts, logs, status, test)
- `MONITORING.md` - Full documentation
- `QUICK-REFERENCE.md` - Quick start guide

**Deployment**:
```bash
cd sim/cluster/monitoring/
bash deploy.sh
```

### 3. Telemetry Collection (`telemetry/`)

#### 3.1 Schemas
**File**: `telemetry/schemas.py`

**Data Models** (Pydantic):
- `LogEvent` - pod logs with timestamp, level, message
- `KPI` - per-service KPIs (error_rate, latency_p50/p95/p99, pod_restarts, downstream_metrics, availability)
- `Alarm` - alerts from Alertmanager (name, status, severity, service, component)
- `TelemetryBundle` - complete snapshot with:
  - `kpis: Dict[str, KPI]`
  - `logs: Dict[str, List[LogEvent]]`
  - `alarms: List[Alarm]`
  - `collection_errors: List[str]`
  - `services_monitored: List[str]`
  - Methods: `is_healthy()`, `get_service_summary()`

#### 3.2 Collector
**File**: `telemetry/collector.py`

**TelemetryCollector Class**:
- Collects on configurable interval (default: 30s)
- Uses async/await for non-blocking collection
- **KPI Queries** (Prometheus):
  - Error rate: `(5xx errors) / (total requests)`
  - Latency: `histogram_quantile(0.50/0.95/0.99, ...)`
  - Pod restarts (total & 5m)
  - Downstream error rates
  - Service availability
- **Log Collection**: `kubectl logs --tail=50` per pod
  - Parses timestamps & log levels
  - Captures last 50 lines per service
- **Alarm Collection**: `GET /alerts` from Alert Receiver
  - Fetches current & historical alarms
  - Parses timestamps & labels

**Typical Collection Time**: 100-300ms

**CLI Usage**:
```bash
python telemetry/collector.py \
    --interval 30 \
    --prometheus-url http://localhost:9090 \
    --alertmanager-url http://localhost:5000 \
    --output-file telemetry.jsonl
```

**Programmatic Usage**:
```python
import asyncio
from telemetry import TelemetryCollector

async def collect():
    async with TelemetryCollector() as collector:
        bundle = await collector.collect()
    return bundle

asyncio.run(collect())
```

#### 3.3 Formatter (Token-Aware LLM Integration)
**File**: `telemetry/formatter.py`

**Output Formats**:
1. `to_json()` - Full structured JSON (~1432 tokens for typical bundle)
2. `to_dict()` - Python dictionary
3. `to_markdown()` - Human-readable report with tables (~627 tokens typical)
4. **`to_context_window(max_tokens=3000)`** - LLM-optimized compact JSON with intelligent truncation
5. **`to_compact_json(max_tokens=3000)`** - Alias for `to_context_window()`
6. `to_jsonl()` - Single-line JSON for logging (~1083 tokens typical)

**Token-Aware Context Window Features** (NEW):
- **Token Estimation**: Automatic token counting (1 token ≈ 4 chars)
- **Intelligent Truncation** - Priority-based when exceeding limit:
  1. Critical alarms - NEVER truncated
  2. Unhealthy services - NEVER truncated
  3. Warning alarms - truncated (least to most severe)
  4. High latency services - truncated
  5. Error logs - truncated (oldest first)
  6. Healthy services - truncated (least-anomalous first)
- **Compact JSON Output** - Optimized for LLM consumption
- **Metadata Header** - Shows token usage: `# TELEMETRY (tokens:349/3000)`

**Context Window Example** (Compact JSON):
```json
# TELEMETRY (tokens:349/3000)
{
  "snapshot": {
    "timestamp": "2026-04-27T10:15:30.123456",
    "health": "DEGRADED",
    "collection_ms": 125
  },
  "critical_issues": [
    {"alert": "ServiceDown", "service": "notification-service", "summary": "..."}
  ],
  "warnings": [
    {"alert": "HighErrorRate", "service": "order-service", "summary": "..."}
  ],
  "unhealthy_services": {
    "notification-service": {"available": false, "error_rate_pct": 100.0, "p99_ms": null},
    "order-service": {"available": true, "error_rate_pct": 8.5, "p99_ms": 450}
  },
  "high_latency": {
    "api-gateway": {"p99_ms": 650, "p95_ms": 500}
  },
  "healthy_services": {
    "frontend": {"error_rate_pct": 0.2, "requests_5m": 450, "p99_ms": 150}
  },
  "recent_errors": [
    {"service": "notification-service", "level": "ERROR", "message": "Connection refused...", "timestamp": "..."}
  ]
}
```

**Token Management**:
- Default limit: 3000 tokens (conservative for most LLM context windows)
- Typical output: 300-350 tokens for realistic failure scenarios
- Guaranteed compliance: Never exceeds configured token limit
- Test coverage: Validated against 500-5000 token limits

#### 3.4 Testing & Documentation
**Files**:
- `test_collector.py` - Single-shot collection test
- `test_formatter_tokens.py` - Token-aware formatter test suite (5 test suites)
- `README.md` - Complete API reference
- `ARCHITECTURE.md` - System design & data flow
- `requirements.txt` - Dependencies (httpx, pydantic)
- `setup.sh` - Quick setup script

**Test Commands**:
```bash
# Basic telemetry collection test
python telemetry/test_collector.py

# Token-aware formatter tests (comprehensive)
python telemetry/test_formatter_tokens.py
```

**Formatter Test Coverage**:
✅ Token counting accuracy (5-5000 tokens)
✅ All output formats (JSON, Dict, Markdown, JSONL, compact JSON)
✅ Truncation strategy (priority-based content preservation)
✅ Context window compliance (output ≤ max_tokens)
✅ Alias methods (to_compact_json = to_context_window)

## 🔄 Integration Points

### Data Flow
```
1. Kubernetes Services (generate metrics)
   ↓
2. Prometheus (scrapes, stores)
   ↓
3. Alertmanager (evaluates rules, routes)
   ↓
4. Alert Receiver (webhook endpoint)
   ↓
5. TelemetryCollector (queries Prometheus + Alert Receiver + kubectl logs)
   ↓
6. TelemetryBundle (structured telemetry)
   ↓
7. TelemetryFormatter (multiple output formats)
   ↓
8. [NEXT] Agent Pipeline (diagnosis)
```

## 📋 TODO: Remaining Components

### Phase 2: Agent Pipeline (`agent/`) [READY TO START]
**Files to create**:
- `pipeline.py` - Main agent loop (ingest → diagnose → rank → submit)
- `prompts.py` - System prompt + few-shot examples for LLM
- `models.py` - Pydantic models for DiagnosisResult, RemediationAction

**Responsibilities**:
- Consume TelemetryBundle
- Call LLM for diagnosis (using `to_context_window()` format - now token-aware!)
- Rank candidate actions by feasibility
- Format actions for policy validation

**Status**: ✅ Telemetry integration ready with token-managed context windows (max 3000 tokens)

### Phase 3: Policy Gate (`policy/`)
**Files to create**:
- `gate.py` - PolicyGate class with validate(action) → (allowed, reason)
- `invariants.py` - SLA bounds, rollback registry, blast-radius calculator
- `tests/test_gate.py` - Unit tests

**Responsibilities**:
- Validate proposed actions against SLAs
- Check blast radius (how many services affected)
- Track rollback history
- Approve/reject actions

### Phase 4: Executor (`executor/`)
**Files to create**:
- `remediation.py` - Maps approved actions to kubectl/REST calls

**Responsibilities**:
- Execute approved remediation actions
- Track execution status
- Collect post-action telemetry for verification

### Phase 5: Evaluation (`eval/`)
**Files to create**:
- `harness.py` - Runs scenario suite, collects MTTR/FPR/SLA metrics
- `scenarios/` - YAML definitions of injected failure scenarios
- `report.py` - Generates evaluation summary

**Responsibilities**:
- Run repeatable failure scenarios
- Measure Mean Time To Recovery (MTTR)
- Track False Positive Rate (FPR)
- Verify SLA compliance

### Phase 6: Configuration & Entrypoint
**Files to create**:
- `config.py` - Central config (LLM model, polling interval, SLA thresholds)
- `main.py` - Entrypoint: starts collector loop + agent loop

## 📊 Deployment Checklist

- [x] Kind cluster configuration
- [x] 5 microservices with metrics
- [x] Fault injector (pod-crash, link-degrade, cascade)
- [x] Prometheus + alert rules
- [x] Alertmanager + webhook receiver
- [x] Telemetry collector (KPIs, logs, alarms)
- [x] Telemetry formatter (JSON, Markdown, context-window, JSONL)
- [x] Token-aware formatter (compact JSON, intelligent truncation, ~3000 token limit)
- [ ] Agent pipeline (LLM diagnosis)
- [ ] Policy gate (action validation)
- [ ] Executor (remediation)
- [ ] Evaluation harness (metrics)
- [ ] Configuration & entrypoint
- [ ] Integration tests
- [ ] Documentation (README.md, ARCHITECTURE.md, etc.)

## 🚀 Quick Start

### 1. Set up kind cluster
```bash
kind create cluster --config sim/cluster/kind-config.yaml
```

### 2. Build & deploy services
```bash
cd sim/cluster/services/
docker build -t netpilot-microservice:latest .
kind load docker-image netpilot-microservice:latest --name netpilot
kubectl apply -f *.yaml
```

### 3. Deploy monitoring
```bash
cd sim/cluster/monitoring/
bash deploy.sh
```

### 4. Port-forward services
```bash
kubectl port-forward svc/prometheus 9090:9090 &
kubectl port-forward svc/alert-receiver 5000:5000 &
kubectl port-forward svc/frontend 8000:8000 &
```

### 5. Inject faults & observe
```bash
# Terminal 1: Monitor telemetry
python telemetry/test_collector.py

# Terminal 2: Inject failure
python sim/fault_injector.py --scenario cascade --target notification-service

# Watch alerts propagate & recovery
```

## 📁 Directory Structure

```
netpilot/
├── AGENTS.md                          ← This file
├── TELEMETRY_USAGE.md                 ← Telemetry quick reference
│
├── sim/                               ← Simulation infrastructure
│   ├── cluster/
│   │   ├── kind-config.yaml           ← Kind cluster 1 CP + 2 workers
│   │   ├── DEPLOYMENT.md              ← Service deployment guide
│   │   ├── services/                  ← 5 microservices
│   │   │   ├── app.py                 ← FastAPI + Prometheus metrics
│   │   │   ├── Dockerfile
│   │   │   ├── 01-frontend.yaml       ← Service manifests
│   │   │   ├── 02-api-gateway.yaml
│   │   │   ├── 03-order-service.yaml
│   │   │   ├── 04-inventory-service.yaml
│   │   │   └── 05-notification-service.yaml
│   │   └── monitoring/                ← Prometheus + Alertmanager
│   │       ├── deploy.sh
│   │       ├── monitoring-utils.sh
│   │       ├── prometheus.yml
│   │       ├── alert-rules.yml
│   │       ├── alertmanager.yml
│   │       ├── 01-prometheus.yaml
│   │       ├── 02-alertmanager.yaml
│   │       ├── 03-alert-receiver.yaml
│   │       ├── alert-receiver.py
│   │       ├── alert-receiver.Dockerfile
│   │       ├── MONITORING.md
│   │       └── QUICK-REFERENCE.md
│   ├── fault_injector.py              ← Fault injection CLI
│   ├── FAULT_INJECTOR.md
│   ├── setup-fault-injector.sh
│   └── events.jsonl                   ← Fault injection event log
│
├── telemetry/                         ← Telemetry collection & formatting
│   ├── schemas.py                     ← Pydantic models
│   ├── collector.py                   ← Main collector (KPIs, logs, alarms)
│   ├── formatter.py                   ← Output formatting
│   ├── test_collector.py              ← Single-shot test
│   ├── __init__.py
│   ├── requirements.txt
│   ├── README.md
│   ├── ARCHITECTURE.md
│   └── setup.sh
│
├── agent/                             ← [TODO] Agent pipeline
│   ├── pipeline.py
│   ├── prompts.py
│   └── models.py
│
├── policy/                            ← [TODO] Policy gate
│   ├── gate.py
│   ├── invariants.py
│   └── tests/
│       └── test_gate.py
│
├── executor/                          ← [TODO] Remediation executor
│   └── remediation.py
│
├── eval/                              ← [TODO] Evaluation harness
│   ├── harness.py
│   ├── scenarios/
│   ├── report.py
│   └── results/
│
├── config.py                          ← [TODO] Central configuration
├── main.py                            ← [TODO] Entrypoint
├── requirements.txt                   ← [TODO] Python dependencies
└── README.md                          ← [TODO] Project README
```

## 🔑 Key Design Decisions

1. **Kubernetes-native**: Uses kubectl for pod operations, native service discovery
2. **Async collection**: TelemetryCollector uses asyncio for non-blocking I/O
3. **LLM-optimized context**: `to_context_window()` format prioritizes critical info
4. **Event-driven**: Fault injector + event log enables reproducible testing
5. **Policy-gated execution**: Actions validated before execution
6. **Multi-format telemetry**: JSON, Markdown, context-window, JSONL for different consumers

## 📚 Documentation

- [sim/cluster/DEPLOYMENT.md](sim/cluster/DEPLOYMENT.md) - Service deployment
- [sim/FAULT_INJECTOR.md](sim/FAULT_INJECTOR.md) - Fault injection usage
- [sim/cluster/monitoring/MONITORING.md](sim/cluster/monitoring/MONITORING.md) - Monitoring setup
- [sim/cluster/monitoring/QUICK-REFERENCE.md](sim/cluster/monitoring/QUICK-REFERENCE.md) - Quick start
- [telemetry/README.md](telemetry/README.md) - Telemetry API reference
- [telemetry/ARCHITECTURE.md](telemetry/ARCHITECTURE.md) - Telemetry system design
- [TELEMETRY_USAGE.md](TELEMETRY_USAGE.md) - Telemetry quick start & examples

## 🧪 Testing

### Simulation Testing
```bash
# Test fault injection
python sim/fault_injector.py --scenario pod-crash --target notification-service

# Watch cascade
python sim/fault_injector.py --scenario cascade --target notification-service

# Observe alerts & metrics
python telemetry/test_collector.py
```

### Next Steps (Phase 2+)
- Unit tests for policy gate (policy/tests/test_gate.py)
- Integration tests for agent pipeline
- Evaluation scenarios (eval/scenarios/)
- End-to-end system tests

---

**Last Updated**: 2026-04-27
**Completion Status**: ~40% (Simulation + Telemetry)
**Next Phase**: Agent Pipeline (diagnosis & remediation)
