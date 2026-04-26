#!/usr/bin/env bash
# Setup script for fault injector

set -e

echo "Installing fault injector dependencies..."
pip install click httpx

echo "Making fault_injector.py executable..."
chmod +x fault_injector.py

echo "✓ Fault injector ready!"
echo ""
echo "Usage examples:"
echo "  python fault_injector.py --scenario pod-crash --target notification-service"
echo "  python fault_injector.py --scenario link-degrade --target order-service --duration 30"
echo "  python fault_injector.py --scenario cascade --target notification-service --watch-duration 45"
echo ""
echo "View events log:"
echo "  cat events.jsonl | jq ."
