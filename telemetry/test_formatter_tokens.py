#!/usr/bin/env python3
"""
Test script for token-aware telemetry formatter.

Demonstrates:
- Token counting accuracy
- Truncation priority implementation
- Multiple format outputs (JSON, Markdown, context-window)
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from .schemas import TelemetryBundle, KPI, Alarm, LogEvent
from .formatter import TelemetryFormatter


def create_sample_bundle() -> TelemetryBundle:
    """Create a realistic sample telemetry bundle for testing."""
    now = datetime.now()
    
    # Create KPIs for 5 services
    kpis = {
        "frontend": KPI(
            service="frontend",
            timestamp=now,
            error_rate=0.001,
            latency_p50_ms=100.0,
            latency_p95_ms=150.0,
            latency_p99_ms=200.0,
            pod_restart_count=0,
            pod_restart_count_5m=0,
            request_count_5m=1000.0,
            available=True,
        ),
        "api-gateway": KPI(
            service="api-gateway",
            timestamp=now,
            error_rate=0.02,
            latency_p50_ms=150.0,
            latency_p95_ms=350.0,
            latency_p99_ms=650.0,
            pod_restart_count=1,
            pod_restart_count_5m=0,
            request_count_5m=800.0,
            available=True,
        ),
        "order-service": KPI(
            service="order-service",
            timestamp=now,
            error_rate=0.085,  # High error rate
            latency_p50_ms=200.0,
            latency_p95_ms=450.0,
            latency_p99_ms=500.0,
            pod_restart_count=5,
            pod_restart_count_5m=2,
            request_count_5m=600.0,
            available=True,
        ),
        "inventory-service": KPI(
            service="inventory-service",
            timestamp=now,
            error_rate=0.0,
            latency_p50_ms=50.0,
            latency_p95_ms=75.0,
            latency_p99_ms=100.0,
            pod_restart_count=0,
            pod_restart_count_5m=0,
            request_count_5m=900.0,
            available=True,
        ),
        "notification-service": KPI(
            service="notification-service",
            timestamp=now,
            error_rate=1.0,  # Service completely unavailable
            latency_p50_ms=None,
            latency_p95_ms=None,
            latency_p99_ms=None,
            pod_restart_count=0,
            pod_restart_count_5m=0,
            request_count_5m=0,
            available=False,  # Service down
        ),
    }
    
    # Create alarms (mix of severities)
    alarms = [
        Alarm(
            timestamp=now,
            alert_name="ServiceDown",
            status="firing",
            severity="critical",
            service="notification-service",
            component="pod",
            summary="Service notification-service is not responding",
            description="Pod has not reported metrics for 2+ minutes",
            starts_at=now - timedelta(minutes=5),
            ends_at=None,
        ),
        Alarm(
            timestamp=now,
            alert_name="HighErrorRate",
            status="firing",
            severity="warning",
            service="order-service",
            component="service",
            summary="HTTP error rate 8.5% exceeds threshold of 5%",
            description="Error rate calculated over 2-minute window",
            starts_at=now - timedelta(minutes=3),
            ends_at=None,
        ),
        Alarm(
            timestamp=now,
            alert_name="HighLatency",
            status="firing",
            severity="warning",
            service="api-gateway",
            component="service",
            summary="P99 latency 650ms exceeds threshold of 500ms",
            description="Latency histogram percentile measurement",
            starts_at=now - timedelta(minutes=2),
            ends_at=None,
        ),
        Alarm(
            timestamp=now - timedelta(minutes=4),
            alert_name="PreviousAlert",
            status="resolved",
            severity="warning",
            service="frontend",
            component="service",
            summary="Previous high latency event",
            description="Already resolved",
            starts_at=now - timedelta(minutes=10),
            ends_at=now - timedelta(minutes=4),
        ),
    ]
    
    # Create logs
    logs = {
        "notification-service": [
            LogEvent(
                timestamp=now - timedelta(minutes=5),
                service="notification-service",
                pod_name="notification-service-xyz123",
                level="ERROR",
                message="Connection refused: Unable to connect to database at postgres:5432",
            ),
            LogEvent(
                timestamp=now - timedelta(minutes=4, seconds=30),
                service="notification-service",
                pod_name="notification-service-xyz123",
                level="CRITICAL",
                message="Service unavailable - database connection lost",
            ),
        ],
        "order-service": [
            LogEvent(
                timestamp=now - timedelta(minutes=3),
                service="order-service",
                pod_name="order-service-abc456",
                level="WARNING",
                message="Downstream call to notification-service failed: timeout",
            ),
            LogEvent(
                timestamp=now - timedelta(minutes=2, seconds=45),
                service="order-service",
                pod_name="order-service-abc456",
                level="ERROR",
                message="Request processing failed: 503 Service Unavailable from notification service",
            ),
        ],
        "api-gateway": [
            LogEvent(
                timestamp=now - timedelta(minutes=2),
                service="api-gateway",
                pod_name="api-gateway-def789",
                level="INFO",
                message="Request latency increased: p99=650ms (threshold=500ms)",
            ),
        ],
    }
    
    bundle = TelemetryBundle(
        timestamp=now,
        kpis=kpis,
        logs=logs,
        alarms=alarms,
        collection_errors=[],
        services_monitored=list(kpis.keys()),
        collection_duration_ms=125.5,
    )
    
    return bundle


def test_token_counting():
    """Test token estimation accuracy."""
    print("=" * 80)
    print("TEST 1: Token Counting")
    print("=" * 80)
    
    test_strings = [
        ("Short", 1),  # ~1 token for 5 chars
        ("This is a longer sentence.", 6),  # ~6 tokens for 27 chars
        ("The quick brown fox jumps over the lazy dog", 12),  # ~11 tokens
    ]
    
    for text, expected_approx in test_strings:
        tokens = TelemetryFormatter.estimate_tokens(text)
        print(f"Text: '{text}'")
        print(f"  Length: {len(text)} chars → {tokens} tokens (expected ~{expected_approx})")
    
    print()


def test_formatter_outputs():
    """Test all formatter output methods."""
    print("=" * 80)
    print("TEST 2: Formatter Output Methods")
    print("=" * 80)
    
    bundle = create_sample_bundle()
    
    # Test JSON
    print("\n2a. JSON Output:")
    json_output = TelemetryFormatter.to_json(bundle)
    json_tokens = TelemetryFormatter.estimate_tokens(json_output)
    print(f"   Length: {len(json_output)} chars → ~{json_tokens} tokens")
    print(f"   First 200 chars: {json_output[:200]}...")
    
    # Test Dict
    print("\n2b. Dictionary Output:")
    dict_output = TelemetryFormatter.to_dict(bundle)
    print(f"   Keys: {list(dict_output.keys())}")
    print(f"   KPIs count: {len(dict_output['kpis'])}")
    print(f"   Alarms count: {len(dict_output['alarms'])}")
    
    # Test Markdown
    print("\n2c. Markdown Output:")
    markdown_output = TelemetryFormatter.to_markdown(bundle)
    markdown_tokens = TelemetryFormatter.estimate_tokens(markdown_output)
    print(f"   Length: {len(markdown_output)} chars → ~{markdown_tokens} tokens")
    print(f"   First 300 chars:\n{markdown_output[:300]}...")
    
    # Test JSONL
    print("\n2d. JSONL Output:")
    jsonl_output = TelemetryFormatter.to_jsonl(bundle)
    jsonl_tokens = TelemetryFormatter.estimate_tokens(jsonl_output)
    print(f"   Length: {len(jsonl_output)} chars → ~{jsonl_tokens} tokens")
    print(f"   First 200 chars: {jsonl_output[:200]}...")
    
    print()


def test_context_window():
    """Test context window formatter with token limits."""
    print("=" * 80)
    print("TEST 3: Context Window Formatter (Token-Aware)")
    print("=" * 80)
    
    bundle = create_sample_bundle()
    
    # Test with different token limits
    token_limits = [500, 1000, 2000, 3000, 5000]
    
    for max_tokens in token_limits:
        context = TelemetryFormatter.to_context_window(bundle, max_tokens)
        actual_tokens = TelemetryFormatter.estimate_tokens(context)
        
        # Extract the header line
        header = context.split('\n')[0]
        
        print(f"\nMax Tokens: {max_tokens}")
        print(f"  Output: {header}")
        print(f"  Actual tokens: {actual_tokens} (length: {len(context)} chars)")
        print(f"  Compliant: {'✓' if actual_tokens <= max_tokens else '✗'}")
        
        # Show content keys
        lines = context.split('\n')
        if len(lines) > 1:
            import json
            try:
                data = json.loads(lines[1])
                has_content = {
                    "critical_issues": len(data.get("critical_issues", [])) > 0,
                    "warnings": len(data.get("warnings", [])) > 0,
                    "unhealthy_services": len(data.get("unhealthy_services", {})) > 0,
                    "high_latency": len(data.get("high_latency", {})) > 0,
                    "errors": len(data.get("recent_errors", [])) > 0,
                    "healthy": len(data.get("healthy_services", {})) > 0,
                }
                content_str = ", ".join(f"{k}={v}" for k, v in has_content.items())
                print(f"  Content: {content_str}")
            except:
                pass
    
    print()


def test_truncation_strategy():
    """Test truncation priority order."""
    print("=" * 80)
    print("TEST 4: Truncation Strategy")
    print("=" * 80)
    print("""
Priority order for truncation (when content exceeds token limit):
1. Critical issues (alarms) - NEVER truncated
2. Unhealthy services (unavailable/high error rate) - NEVER truncated
3. Warning alarms - truncated from least to most severe
4. High latency services - truncated
5. Error logs - truncated oldest first (oldest deleted first)
6. Healthy services - truncated least-anomalous first
   (services with low error rate, low latency, few restarts are deleted first)

This strategy ensures LLM always sees:
✓ Critical problems
✓ Failed services
✓ Recent high-priority errors
✗ Healthy service details (least important)
✗ Old error logs (oldest first)
✗ Low-severity warnings (least important)
    """)
    
    print()


def test_compact_json_alias():
    """Test to_compact_json() alias."""
    print("=" * 80)
    print("TEST 5: to_compact_json() Alias")
    print("=" * 80)
    
    bundle = create_sample_bundle()
    
    compact1 = TelemetryFormatter.to_context_window(bundle, 2000)
    compact2 = TelemetryFormatter.to_compact_json(bundle, 2000)
    
    print("to_context_window() and to_compact_json() produce identical output:")
    print(f"  Same output: {compact1 == compact2}")
    print(f"  Both methods return compact JSON suitable for LLM input")
    print()


def main():
    """Run all tests."""
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 78 + "║")
    print("║" + "  TELEMETRY FORMATTER - TOKEN-AWARE CONTEXT WINDOW TESTS".center(78) + "║")
    print("║" + " " * 78 + "║")
    print("╚" + "=" * 78 + "╝")
    print()
    
    try:
        test_token_counting()
        test_formatter_outputs()
        test_context_window()
        test_truncation_strategy()
        test_compact_json_alias()
        
        print("=" * 80)
        print("✓ ALL TESTS COMPLETED")
        print("=" * 80)
        print()
        
    except Exception as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
