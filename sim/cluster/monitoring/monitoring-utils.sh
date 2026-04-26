#!/usr/bin/env bash
# Monitoring utilities for netpilot

set -e

COMMAND=${1:-help}

case "$COMMAND" in
  "help")
    echo "Netpilot Monitoring Utilities"
    echo ""
    echo "Usage: monitoring-utils.sh <command> [options]"
    echo ""
    echo "Commands:"
    echo "  ports              - Start all port-forwards"
    echo "  alerts             - View current active alerts (realtime)"
    echo "  history            - View alert history"
    echo "  critical           - View only critical alerts"
    echo "  prometheus         - Port-forward to Prometheus"
    echo "  alertmanager       - Port-forward to Alertmanager"
    echo "  receiver           - Port-forward to Alert Receiver"
    echo "  logs <component>   - View logs (prometheus|alertmanager|alert-receiver)"
    echo "  status             - Check component status"
    echo "  test-alerts        - Trigger test alerts"
    echo ""
    ;;

  "ports")
    echo "Starting port-forwards..."
    kubectl port-forward svc/prometheus 9090:9090 &
    kubectl port-forward svc/alertmanager 9093:9093 &
    kubectl port-forward svc/alert-receiver 5000:5000 &
    echo "✓ Port-forwards started"
    echo "  Prometheus:     http://localhost:9090"
    echo "  Alertmanager:   http://localhost:9093"
    echo "  Alert Receiver: http://localhost:5000"
    ;;

  "alerts")
    echo "Current Active Alerts:"
    curl -s http://localhost:5000/alerts | jq '.active_alerts'
    ;;

  "history")
    LIMIT=${2:-50}
    echo "Alert History (last $LIMIT):"
    curl -s "http://localhost:5000/alerts/history?limit=$LIMIT" | jq '.alerts'
    ;;

  "critical")
    echo "Critical Alerts Only:"
    curl -s http://localhost:5000/alerts | jq '.active_alerts[] | select(.labels.severity == "critical")'
    ;;

  "prometheus")
    echo "Port-forwarding to Prometheus (http://localhost:9090)..."
    kubectl port-forward svc/prometheus 9090:9090
    ;;

  "alertmanager")
    echo "Port-forwarding to Alertmanager (http://localhost:9093)..."
    kubectl port-forward svc/alertmanager 9093:9093
    ;;

  "receiver")
    echo "Port-forwarding to Alert Receiver (http://localhost:5000)..."
    kubectl port-forward svc/alert-receiver 5000:5000
    ;;

  "logs")
    COMPONENT=${2:-alert-receiver}
    case "$COMPONENT" in
      "prometheus"|"alertmanager"|"alert-receiver")
        echo "Logs for $COMPONENT:"
        kubectl logs -f deployment/$COMPONENT --tail=50
        ;;
      *)
        echo "Unknown component: $COMPONENT"
        echo "Valid components: prometheus, alertmanager, alert-receiver"
        exit 1
        ;;
    esac
    ;;

  "status")
    echo "Monitoring Stack Status:"
    echo ""
    echo "Deployments:"
    kubectl get deployment -n default | grep -E "prometheus|alertmanager|alert-receiver" || echo "  (none deployed)"
    echo ""
    echo "Pods:"
    kubectl get pods -n default | grep -E "prometheus|alertmanager|alert-receiver" || echo "  (none running)"
    echo ""
    echo "Services:"
    kubectl get svc -n default | grep -E "prometheus|alertmanager|alert-receiver" || echo "  (none)"
    ;;

  "test-alerts")
    echo "Triggering test alerts..."
    echo ""
    
    # Find a notification-service pod
    POD=$(kubectl get pods -l app=notification-service -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
    if [ -z "$POD" ]; then
      echo "❌ notification-service pod not found"
      exit 1
    fi
    
    echo "1️⃣  Injecting crash fault on notification-service..."
    python sim/fault_injector.py --scenario pod-crash --target notification-service
    
    echo ""
    echo "2️⃣  Waiting for alerts to fire (30-60s)..."
    sleep 10
    
    echo ""
    echo "3️⃣  Current alerts:"
    curl -s http://localhost:5000/alerts | jq '.active_alerts | length'
    echo ""
    ;;

  *)
    echo "Unknown command: $COMMAND"
    echo "Use 'monitoring-utils.sh help' for usage"
    exit 1
    ;;
esac
