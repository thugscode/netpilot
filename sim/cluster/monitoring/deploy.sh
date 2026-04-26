#!/usr/bin/env bash
# Deploy monitoring stack to netpilot cluster

set -e

MONITORING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLUSTER_NAME="netpilot"

echo "🔧 Building alert-receiver image..."
docker build -t netpilot-alert-receiver:latest -f "$MONITORING_DIR/alert-receiver.Dockerfile" "$MONITORING_DIR"

echo "📦 Loading alert-receiver image into kind..."
kind load docker-image netpilot-alert-receiver:latest --name "$CLUSTER_NAME"

echo "📊 Deploying Prometheus..."
kubectl apply -f "$MONITORING_DIR/01-prometheus.yaml"

echo "🔔 Deploying Alertmanager..."
kubectl apply -f "$MONITORING_DIR/02-alertmanager.yaml"

echo "📨 Deploying Alert Receiver..."
kubectl apply -f "$MONITORING_DIR/03-alert-receiver.yaml"

echo ""
echo "✓ Monitoring stack deployed!"
echo ""
echo "Waiting for deployments to be ready..."
kubectl wait --for=condition=available --timeout=60s deployment/prometheus -n default || true
kubectl wait --for=condition=available --timeout=60s deployment/alertmanager -n default || true
kubectl wait --for=condition=available --timeout=60s deployment/alert-receiver -n default || true

echo ""
echo "📊 Access monitoring services:"
echo "   Prometheus:     kubectl port-forward svc/prometheus 9090:9090"
echo "   Alertmanager:   kubectl port-forward svc/alertmanager 9093:9093"
echo "   Alert Receiver: kubectl port-forward svc/alert-receiver 5000:5000"
echo ""
echo "View alerts:"
echo "   curl http://localhost:5000/alerts | jq ."
echo ""
