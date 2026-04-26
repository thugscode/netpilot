#!/usr/bin/env bash
# Quick setup guide for telemetry module

set -e

echo "Setting up telemetry module..."
echo ""

# Check Python version
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "✓ Python version: $python_version"

# Install dependencies
echo ""
echo "Installing telemetry dependencies..."
pip install -r telemetry/requirements.txt

echo ""
echo "✓ Telemetry module setup complete!"
echo ""
echo "Quick start:"
echo ""
echo "1. Ensure monitoring services are deployed:"
echo "   cd sim/cluster/monitoring && bash deploy.sh"
echo ""
echo "2. Port-forward services:"
echo "   kubectl port-forward svc/prometheus 9090:9090 &"
echo "   kubectl port-forward svc/alert-receiver 5000:5000 &"
echo ""
echo "3. Run one-shot collection:"
echo "   python telemetry/test_collector.py"
echo ""
echo "4. Run continuous collection:"
echo "   python telemetry/collector.py --interval 30 --output-file telemetry.jsonl"
echo ""
