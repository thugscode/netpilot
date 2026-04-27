# Netpilot - Self-Healing Kubernetes Agent System

**Status**: 75% Complete (Simulation, Telemetry, Policy Gate, Executor, & Evaluation Harness ready)

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

### Phase 2: Agent Pipeline (`agent/`) [✅ COMPLETE]
**Files created**:
- `models.py` - Pydantic models for DiagnosisResult, RemediationAction (60 lines)
- `prompts.py` - System prompt + few-shot examples for LLM (450+ lines)
- `__init__.py` - Package exports
- `README.md` - Complete API reference
- `test_prompts.py` - Comprehensive test suite (340+ lines)

**Responsibilities** [IMPLEMENTED]:
- ✅ LLM diagnosis system prompt (2,169 chars, ~540 tokens)
- ✅ Few-shot examples: pod-crash scenario (1,494 input + 1,229 output chars)
- ✅ Few-shot examples: link-degrade scenario (1,460 input + 1,163 output chars)
- ✅ DiagnosisResult schema with root_cause + ranked remediation actions
- ✅ RemediationAction schema with 5 action types: restart_pod | scale_up | reroute_traffic | rollback_deploy | noop
- ✅ Prompt message builder (system → examples → user input)
- ✅ JSON validation for LLM responses
- ✅ Full test coverage (4 test suites, all passing)

**DiagnosisResult Schema**:
```json
{
  "root_cause": "string (failure description)",
  "root_cause_confidence": 0.0-1.0,
  "remediation_actions": [
    {
      "action_type": "restart_pod|scale_up|reroute_traffic|rollback_deploy|noop",
      "target": "service_name",
      "params": {"action_specific_params"},
      "confidence": 0.0-1.0,
      "rationale": "One sentence explanation"
    }
  ]
}
```

**Integration Ready**:
- Consumes TelemetryBundle from collector
- Formats with `to_context_window()` (350 tokens typical)
- System + examples: 2,261 tokens
- LLM has ~5,700 tokens for reasoning
- Output validated as JSON before policy gate

**Test Results** (agent/test_prompts.py):
✅ Pydantic model instantiation and serialization
✅ System prompt retrieval and validation
✅ Few-shot examples parsing and instantiation
✅ JSON validation (valid/invalid cases)
✅ All 4 test suites passing

### Phase 2.5: Agent Executor (`agent/`)
**Files to create**:
- `pipeline.py` - Main agent loop (collect → diagnose → validate → submit)

**Responsibilities**:
- Continuous polling of telemetry collector
- Format telemetry with `to_context_window()`
- Call LLM with system prompt + few-shot examples
- Validate JSON response
- Submit validated actions to policy gate

### Phase 3: Policy Gate (`policy/`) [✅ COMPLETE]
**Files created**:
- `__init__.py` - Package exports with invariants and gate modules
- `invariants.py` - SLA bounds, rollback registry, blast-radius calculator (400+ lines)
- `gate.py` - PolicyGate validation engine (550+ lines)
- `test_invariants.py` - Invariants test suite (445 lines, 22 tests passing)
- `test_gate.py` - PolicyGate test suite (445 lines, ready for pytest)
- `INVARIANTS_GUIDE.md` - Invariants API reference (450 lines)
- `GATE_GUIDE.md` - PolicyGate documentation (400 lines)

**Responsibilities** [IMPLEMENTED]:
- ✅ SLA bounds validation (service-level agreement constraints)
- ✅ Blast radius calculation (impact propagation via upstream traversal)
- ✅ Rollback registry management (image tag tracking, history)
- ✅ Service topology definition (hardcoded DAG, future ConfigMap)
- ✅ Helper validators (is_within_sla, is_blast_radius_acceptable)
- ✅ Comprehensive debugging utilities (print_topology, print_sla_bounds)
- ✅ **PolicyGate validation engine** (NEW in Phase 3.5)
- ✅ **Three-stage action validation** (SLA bounds → rollback feasibility → blast radius)
- ✅ **Impact simulation heuristics** (restart doubles error rate, scale_up halves latency)
- ✅ **Audit trail and explanations** (explain_policy_decision, create_audit_log_entry)

**Test Results** [✅ 22/22 Invariants + 14/14 PolicyGate = 36/36 TOTAL]:
- Invariants:
  - SLA Bounds Loading: 5 tests passing
  - Service Topology: 3 tests passing
  - Blast Radius Calculation: 3 tests passing
  - Rollback Registry: 4 tests passing
  - SLA Validation: 4 tests passing
  - Blast Radius Constraints: 3 tests passing
- PolicyGate:
  - SLA Bounds Validation: 4 tests passing
  - Rollback Feasibility: 3 tests passing
  - Blast Radius Validation: 3 tests passing
  - Full Validation Workflow: 4 tests passing

### Phase 4: Executor (`executor/`) [✅ COMPLETE]
**Files created**:
- `__init__.py` - Package exports
- `remediation.py` - Remediation action execution (403 lines)
- `test_remediation.py` - Comprehensive tests (454 lines, 18/18 passing)
- `README.md` - Complete API reference (200+ lines)

**Responsibilities** [IMPLEMENTED]:
- ✅ Dispatch on action_type (5 action types)
- ✅ Execute kubectl commands with try/except
- ✅ restart_pod: `kubectl delete pod -l app={target}`
- ✅ scale_up: `kubectl scale deployment {target} --replicas={params['replicas']}`
- ✅ reroute_traffic: Stub (logs intent for VirtualService patching)
- ✅ rollback_deploy: `kubectl set image deployment/{target} app={previous_image}`
- ✅ noop: Log "no action taken"
- ✅ Structured error handling with RemediationError and ExecutionResult
- ✅ Batch execution for multiple actions
- ✅ Full logging with timestamps and status

**Test Results** [✅ 18/18 PASSING]:
- Restart Pod: Success, failure, timeout (3 tests)
- Scale Up: Success, missing params, failure (3 tests)
- Reroute Traffic: Stub behavior (1 test)
- Rollback Deploy: Success, not in registry, no previous image (3 tests)
- No-op: Always succeeds (1 test)
- ExecutionResult: Serialization, defaults (2 tests)
- Batch Execute: Mixed results (1 test)
- RemediationError: Error construction (1 test)
- Kubectl Integration: Command validation (3 tests)

**Integration Ready**:
- Consumes RemediationAction from agent pipeline
- Returns ExecutionResult with success/error details
- Queries ROLLBACK_REGISTRY for rollback actions
- Feeds execution results to post-action verification
- Structured logging for audit trails

### Phase 5: Evaluation (`eval/`) [✅ COMPLETE]
**Files created**:
- `harness.py` - Scenario runner with ScenarioResult, EvaluationMetrics, SLA checking (447 lines)
- `test_harness.py` - Comprehensive tests (440+ lines, 14/14 passing)
- `__init__.py` - Package exports
- `scenarios/` folder with 3 YAML scenario definitions:
  - `01-notification-crash.yaml` - Pod crash scenario
  - `02-inventory-degrade.yaml` - Network degradation scenario
  - `03-order-cascade.yaml` - Cascade failure scenario

**Responsibilities** [IMPLEMENTED]:
- ✅ Load scenario YAML with fault injection parameters
- ✅ Run failure scenarios with automated fault injection
- ✅ Poll TelemetryCollector for KPIs during recovery
- ✅ Track Mean Time To Recovery (MTTR) in seconds
- ✅ Measure action accuracy (expected vs actual remediation)
- ✅ Verify SLA compliance with detailed violation tracking
- ✅ Aggregate metrics across scenario suite
- ✅ Save results to JSON files with summary report

**Key Components** [IMPLEMENTED]:
- `ScenarioResult` dataclass: scenario_name, target_service, fault_type, success, mttr_seconds, correct_action_taken, expected_action, actual_action, sla_violations, timestamps, reason
- `EvaluationMetrics` dataclass: total_scenarios, successful_recoveries, correct_actions, average_mttr_seconds, false_positive_rate, timestamp
- `load_scenario(scenario_file)`: Load YAML with validation
- `is_sla_compliant(kpis, sla_bounds)`: Returns (is_compliant, violations)
- `run_scenario(scenario_file, poll_interval_seconds)`: Main loop - inject fault, poll until recovery or timeout, return results
- `run_scenario_suite(scenario_files)`: Run multiple scenarios, aggregate metrics
- `save_results(results, metrics, output_dir)`: Save JSON + summary report

**Test Results** [✅ 14/14 PASSING]:
- TestScenarioLoading: 4 tests (load all scenarios, nonexistent handling)
- TestScenarioResult: 3 tests (successful/failed recovery, serialization)
- TestEvaluationMetrics: 2 tests (calculation, serialization)
- TestSLACompliance: 5 tests (all compliant, error rate violation, latency violation, multiple violations, unknown services)

**Integration Ready**:
- Consumes TelemetryCollector for KPI polling
- Uses fault_injector.py for scenario injection
- Queries PolicyGate for action validation
- Tracks MTTR and action accuracy
- Verifies SLA compliance with policy bounds
- Exports results for evaluation dashboard

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
- [x] Agent pipeline (LLM diagnosis with models + prompts + examples)
- [x] Policy invariants (SLA bounds, rollback registry, blast radius)
- [x] Policy validation tests (22/22 invariants + 14/14 gate = 36/36 passing)
- [x] PolicyGate validation engine (SLA bounds → rollback → blast radius)
- [x] Policy tests in pytest (policy/tests/test_gate.py, 10/10 passing)
- [x] Executor (remediation via kubectl, 18/18 tests passing)
  - [x] restart_pod via kubectl delete pod
  - [x] scale_up via kubectl scale deployment
  - [x] reroute_traffic (stub/log)
  - [x] rollback_deploy via kubectl set image
  - [x] noop (log only)
  - [x] Error handling (try/except, structured errors)
  - [x] Batch execution
- [x] Evaluation Harness (MTTR, action accuracy, SLA metrics)
  - [x] eval/harness.py (scenario runner, 447 lines)
  - [x] eval/test_harness.py (tests, 14/14 passing)
  - [x] eval/scenarios/ (3 YAML scenario definitions)
  - [x] ScenarioResult and EvaluationMetrics dataclasses
  - [x] SLA compliance checking
  - [x] Results aggregation and reporting
- [ ] Agent executor loop (main loop for continuous diagnosis)
- [ ] Configuration & entrypoint (main.py, config.py)

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
