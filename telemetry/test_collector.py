#!/usr/bin/env python3
"""
Quick test of the telemetry collector.
Performs a single collection and displays results.
"""

import asyncio
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from telemetry import TelemetryCollector, TelemetryFormatter


async def test_collection():
    """Test telemetry collection."""
    print("Testing telemetry collection...\n")
    
    try:
        async with TelemetryCollector(
            prometheus_url="http://localhost:9090",
            alertmanager_url="http://localhost:5000",
        ) as collector:
            print("✓ Collector initialized")
            print("  - Prometheus: http://localhost:9090")
            print("  - Alert Receiver: http://localhost:5000")
            print("")
            
            print("Collecting telemetry...")
            bundle = await collector.collect()
            print(f"✓ Collection completed in {bundle.collection_duration_ms:.1f}ms\n")
            
            # Display summary
            print("=" * 60)
            print("TELEMETRY SUMMARY")
            print("=" * 60)
            
            summary = bundle.get_service_summary()
            
            print(f"\nTimestamp: {summary['timestamp']}")
            print(f"System Health: {'✓ HEALTHY' if summary['healthy'] else '✗ DEGRADED'}")
            print(f"Active Alarms: {summary['alarm_count']}")
            print(f"Collection Errors: {summary['error_count']}")
            
            print("\nServices:")
            for service_name, service_info in summary['services'].items():
                status = "✓" if service_info['available'] else "✗"
                print(f"  {status} {service_name}")
                print(f"     Error Rate: {service_info['error_rate']}")
                print(f"     P99 Latency: {service_info['p99_latency_ms']}ms")
                print(f"     Pod Restarts: {service_info['pod_restarts']}")
                print(f"     Recent Logs: {service_info['recent_logs']}")
            
            # Display context window
            print("\n" + "=" * 60)
            print("CONTEXT WINDOW (for LLM)")
            print("=" * 60)
            print(TelemetryFormatter.to_context_window(bundle))
            
            # Display Markdown
            print("\n" + "=" * 60)
            print("MARKDOWN REPORT")
            print("=" * 60)
            print(TelemetryFormatter.to_markdown(bundle))
    
    except Exception as e:
        print(f"✗ Error: {e}")
        print("\nMake sure:")
        print("  - Prometheus is running at http://localhost:9090")
        print("  - Alert Receiver is running at http://localhost:5000")
        print("  - kubectl is configured to access the cluster")
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(test_collection())
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(0)
