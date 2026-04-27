"""
Tests for evaluation harness.

Tests scenario loading, result tracking, and SLA compliance checking.
"""

import sys
from datetime import datetime
from pathlib import Path

import pytest

# Add parent dirs to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.harness import (
    load_scenario,
    ScenarioResult,
    EvaluationMetrics,
    is_sla_compliant,
)
from telemetry.schemas import KPI


class TestScenarioLoading:
    """Test scenario YAML loading."""

    def test_load_notification_crash_scenario(self):
        """Load notification-service pod crash scenario."""
        scenario = load_scenario("01-notification-crash.yaml")

        assert scenario["name"] == "notification-service pod crash"
        assert scenario["fault"] == "pod-crash"
        assert scenario["target"] == "notification-service"
        assert scenario["expected_action"] == "restart_pod"

    def test_load_inventory_degrade_scenario(self):
        """Load inventory-service link degradation scenario."""
        scenario = load_scenario("02-inventory-degrade.yaml")

        assert scenario["name"] == "inventory-service link degradation"
        assert scenario["fault"] == "link-degrade"
        assert scenario["target"] == "inventory-service"
        assert scenario["expected_action"] == "scale_up"

    def test_load_order_cascade_scenario(self):
        """Load order-service cascade failure scenario."""
        scenario = load_scenario("03-order-cascade.yaml")

        assert scenario["name"] == "order-service cascade failure"
        assert scenario["fault"] == "cascade"
        assert scenario["target"] == "order-service"
        assert scenario["expected_action"] == "restart_pod"

    def test_load_nonexistent_scenario(self):
        """Loading nonexistent scenario raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_scenario("nonexistent.yaml")


class TestScenarioResult:
    """Test ScenarioResult dataclass."""

    def test_successful_recovery(self):
        """Successful recovery result."""
        result = ScenarioResult(
            scenario_name="test",
            target_service="test-service",
            fault_type="pod-crash",
            success=True,
            mttr_seconds=45.5,
            correct_action_taken=True,
            expected_action="restart_pod",
            actual_action="restart_pod",
            sla_violations=[],
            start_timestamp="2026-04-27T12:00:00.000000",
            end_timestamp="2026-04-27T12:00:45.500000",
            reason="SLA recovered in 45.5s",
        )

        assert result.success is True
        assert result.mttr_seconds == 45.5
        assert result.correct_action_taken is True
        assert len(result.sla_violations) == 0

    def test_failed_recovery(self):
        """Failed recovery (timeout)."""
        result = ScenarioResult(
            scenario_name="test",
            target_service="test-service",
            fault_type="pod-crash",
            success=False,
            mttr_seconds=300.0,
            correct_action_taken=False,
            expected_action="restart_pod",
            actual_action=None,
            sla_violations=[
                "notification-service: error_rate 0.50 > bound 0.03",
            ],
            start_timestamp="2026-04-27T12:00:00.000000",
            end_timestamp="2026-04-27T12:05:00.000000",
            reason="Timeout: SLA not recovered within 300s",
        )

        assert result.success is False
        assert result.mttr_seconds == 300.0
        assert result.correct_action_taken is False
        assert len(result.sla_violations) > 0

    def test_result_serialization(self):
        """ScenarioResult serializes to dict and JSON."""
        result = ScenarioResult(
            scenario_name="test",
            target_service="test-service",
            fault_type="pod-crash",
            success=True,
            mttr_seconds=45.5,
            correct_action_taken=True,
            expected_action="restart_pod",
            actual_action="restart_pod",
            sla_violations=[],
            start_timestamp="2026-04-27T12:00:00.000000",
            end_timestamp="2026-04-27T12:00:45.500000",
            reason="SLA recovered",
        )

        # Serialize to dict
        result_dict = result.to_dict()
        assert isinstance(result_dict, dict)
        assert result_dict["success"] is True
        assert result_dict["mttr_seconds"] == 45.5

        # Serialize to JSON
        result_json = result.to_json()
        assert isinstance(result_json, str)
        assert "45.5" in result_json
        assert '"success": true' in result_json


class TestEvaluationMetrics:
    """Test EvaluationMetrics dataclass."""

    def test_metrics_calculation(self):
        """Metrics calculated correctly."""
        metrics = EvaluationMetrics(
            total_scenarios=3,
            successful_recoveries=2,
            correct_actions=2,
            average_mttr_seconds=67.3,
            false_positive_rate=1/3,
            timestamp="2026-04-27T12:00:00.000000",
        )

        assert metrics.total_scenarios == 3
        assert metrics.successful_recoveries == 2
        assert metrics.correct_actions == 2
        assert abs(metrics.average_mttr_seconds - 67.3) < 0.1
        assert abs(metrics.false_positive_rate - 1/3) < 0.01

    def test_metrics_serialization(self):
        """Metrics serialize to dict and JSON."""
        metrics = EvaluationMetrics(
            total_scenarios=3,
            successful_recoveries=2,
            correct_actions=2,
            average_mttr_seconds=67.3,
            false_positive_rate=0.33,
            timestamp="2026-04-27T12:00:00.000000",
        )

        # Serialize to dict
        metrics_dict = metrics.to_dict()
        assert isinstance(metrics_dict, dict)
        assert metrics_dict["total_scenarios"] == 3
        assert metrics_dict["successful_recoveries"] == 2

        # Serialize to JSON
        metrics_json = metrics.to_json()
        assert isinstance(metrics_json, str)
        assert '"total_scenarios": 3' in metrics_json


class TestSLACompliance:
    """Test SLA compliance checking."""

    def test_all_services_compliant(self):
        """All services within SLA bounds."""
        kpis = {
            "notification-service": KPI(
                service="notification-service",
                timestamp=datetime.now().isoformat(),
                error_rate=0.01,
                latency_p99_ms=200,
                latency_p50_ms=50,
                latency_p95_ms=100,
                pod_restarts_total=0,
                pod_restarts_5m=0,
                downstream_error_rate=0.0,
                downstream_latency_p99_ms=0,
                availability=True,
                request_count_5m=1000,
            ),
        }

        is_compliant, violations = is_sla_compliant(kpis)

        assert is_compliant is True
        assert len(violations) == 0

    def test_error_rate_violation(self):
        """Error rate exceeds SLA bound."""
        kpis = {
            "notification-service": KPI(
                service="notification-service",
                timestamp=datetime.now().isoformat(),
                error_rate=0.15,  # 15% (exceeds 10% bound)
                latency_p99_ms=200,
                latency_p50_ms=50,
                latency_p95_ms=100,
                pod_restarts_total=0,
                pod_restarts_5m=0,
                downstream_error_rate=0.0,
                downstream_latency_p99_ms=0,
                availability=True,
                request_count_5m=1000,
            ),
        }

        is_compliant, violations = is_sla_compliant(kpis)

        assert is_compliant is False
        assert len(violations) > 0
        assert "error_rate" in violations[0]

    def test_latency_violation(self):
        """P99 latency exceeds SLA bound."""
        kpis = {
            "notification-service": KPI(
                service="notification-service",
                timestamp=datetime.now().isoformat(),
                error_rate=0.01,
                latency_p99_ms=2500,  # Exceeds 2000ms bound
                latency_p50_ms=50,
                latency_p95_ms=300,
                pod_restarts_total=0,
                pod_restarts_5m=0,
                downstream_error_rate=0.0,
                downstream_latency_p99_ms=0,
                availability=True,
                request_count_5m=1000,
            ),
        }

        is_compliant, violations = is_sla_compliant(kpis)

        assert is_compliant is False
        assert len(violations) > 0
        assert "latency" in violations[0].lower()

    def test_multiple_violations(self):
        """Multiple SLA violations detected."""
        kpis = {
            "notification-service": KPI(
                service="notification-service",
                timestamp=datetime.now().isoformat(),
                error_rate=0.15,  # 15% violates 10% bound
                latency_p99_ms=2500,  # 2500ms violates 2000ms bound
                latency_p50_ms=200,
                latency_p95_ms=600,
                pod_restarts_total=5,
                pod_restarts_5m=2,
                downstream_error_rate=0.05,
                downstream_latency_p99_ms=800,
                availability=True,
                request_count_5m=500,
            ),
        }

        is_compliant, violations = is_sla_compliant(kpis)

        assert is_compliant is False
        assert len(violations) >= 2  # At least error_rate and latency

    def test_unknown_service_ignored(self):
        """Unknown services in KPIs are ignored."""
        kpis = {
            "unknown-service": KPI(
                service="unknown-service",
                timestamp=datetime.now().isoformat(),
                error_rate=0.50,  # Would violate if checked
                latency_p99_ms=5000,
                latency_p50_ms=1000,
                latency_p95_ms=3000,
                pod_restarts_total=10,
                pod_restarts_5m=5,
                downstream_error_rate=0.20,
                downstream_latency_p99_ms=4000,
                availability=False,
                request_count_5m=100,
            ),
        }

        is_compliant, violations = is_sla_compliant(kpis)

        # Should be compliant since unknown service is ignored
        assert is_compliant is True
        assert len(violations) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
