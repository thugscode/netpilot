"""
Tests for evaluation report generator.

Tests result loading, metrics calculation, and report formatting.
"""

import sys
import json
import tempfile
from pathlib import Path
from datetime import datetime

import pytest

# Add parent dirs to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.report import (
    load_results_from_jsonl,
    load_results_from_files,
    calculate_metrics,
)


class TestResultLoading:
    """Test result loading from different sources."""

    def test_load_from_nonexistent_jsonl(self):
        """Loading from nonexistent JSONL returns empty list."""
        results = load_results_from_jsonl("nonexistent.jsonl")
        assert results == []

    def test_load_from_nonexistent_directory(self):
        """Loading from nonexistent directory returns empty list."""
        results = load_results_from_files("nonexistent/directory")
        assert results == []

    def test_load_from_valid_jsonl(self):
        """Load results from valid JSONL file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            # Write sample results
            result1 = {
                "scenario_name": "test-scenario-1",
                "target_service": "test-service",
                "fault_type": "pod-crash",
                "success": True,
                "mttr_seconds": 45.5,
                "correct_action_taken": True,
                "expected_action": "restart_pod",
                "actual_action": "restart_pod",
                "sla_violations": [],
                "start_timestamp": "2026-04-27T12:00:00.000000",
                "end_timestamp": "2026-04-27T12:00:45.500000",
                "reason": "Recovered in time",
            }
            result2 = {
                "scenario_name": "test-scenario-2",
                "target_service": "test-service",
                "fault_type": "link-degrade",
                "success": False,
                "mttr_seconds": 300.0,
                "correct_action_taken": False,
                "expected_action": "scale_up",
                "actual_action": "restart_pod",
                "sla_violations": ["test-service: error_rate 0.10 > bound 0.05"],
                "start_timestamp": "2026-04-27T12:01:00.000000",
                "end_timestamp": "2026-04-27T12:06:00.000000",
                "reason": "Timeout",
            }
            
            f.write(json.dumps(result1) + "\n")
            f.write(json.dumps(result2) + "\n")
            f.flush()
            
            # Load and verify
            results = load_results_from_jsonl(f.name)
            
            assert len(results) == 2
            assert results[0]["scenario_name"] == "test-scenario-1"
            assert results[1]["scenario_name"] == "test-scenario-2"
            assert results[1]["mttr_seconds"] == 300.0


class TestMetricsCalculation:
    """Test metrics calculation from results."""

    def test_empty_results(self):
        """Empty results return default metrics."""
        metrics = calculate_metrics([])
        
        assert metrics["mean_mttr_seconds"] == 0.0
        assert metrics["false_positive_rate"] == 0.0
        assert metrics["sla_violation_rate"] == 0.0
        assert metrics["total_scenarios"] == 0

    def test_single_successful_scenario(self):
        """Single successful scenario with correct action."""
        results = [
            {
                "scenario_name": "test",
                "success": True,
                "mttr_seconds": 45.5,
                "correct_action_taken": True,
                "sla_violations": [],
            }
        ]
        
        metrics = calculate_metrics(results)
        
        assert metrics["total_scenarios"] == 1
        assert metrics["mean_mttr_seconds"] == 45.5
        assert metrics["false_positive_rate"] == 0.0
        assert metrics["sla_violation_rate"] == 0.0
        assert metrics["successful_recoveries"] == 1
        assert metrics["correct_actions"] == 1

    def test_multiple_scenarios_with_violations(self):
        """Multiple scenarios with mixed results."""
        results = [
            {
                "success": True,
                "mttr_seconds": 30.0,
                "correct_action_taken": True,
                "sla_violations": [],
            },
            {
                "success": True,
                "mttr_seconds": 60.0,
                "correct_action_taken": False,  # Wrong action (false positive)
                "sla_violations": [],
            },
            {
                "success": False,
                "mttr_seconds": 300.0,
                "correct_action_taken": False,
                "sla_violations": ["service: error_rate 0.10 > 0.05"],  # SLA violation
            },
        ]
        
        metrics = calculate_metrics(results)
        
        assert metrics["total_scenarios"] == 3
        assert metrics["mean_mttr_seconds"] == 130.0  # (30 + 60 + 300) / 3
        assert metrics["false_positive_rate"] == 2/3  # 2 incorrect actions
        assert metrics["sla_violation_rate"] == 1/3  # 1 scenario with violations
        assert metrics["successful_recoveries"] == 2
        assert metrics["correct_actions"] == 1

    def test_all_scenarios_with_violations(self):
        """All scenarios have SLA violations."""
        results = [
            {
                "success": False,
                "mttr_seconds": 300.0,
                "correct_action_taken": False,
                "sla_violations": ["service1: error_rate violation"],
            },
            {
                "success": False,
                "mttr_seconds": 300.0,
                "correct_action_taken": False,
                "sla_violations": ["service2: latency violation"],
            },
        ]
        
        metrics = calculate_metrics(results)
        
        assert metrics["sla_violation_rate"] == 1.0  # 100%
        assert metrics["scenarios_with_violations"] == 2

    def test_all_correct_actions(self):
        """All scenarios have correct actions."""
        results = [
            {
                "success": True,
                "mttr_seconds": 45.0,
                "correct_action_taken": True,
                "sla_violations": [],
            },
            {
                "success": True,
                "mttr_seconds": 50.0,
                "correct_action_taken": True,
                "sla_violations": [],
            },
            {
                "success": True,
                "mttr_seconds": 40.0,
                "correct_action_taken": True,
                "sla_violations": [],
            },
        ]
        
        metrics = calculate_metrics(results)
        
        assert metrics["false_positive_rate"] == 0.0
        assert metrics["correct_actions"] == 3
        assert metrics["mean_mttr_seconds"] == 45.0  # (45 + 50 + 40) / 3

    def test_all_wrong_actions(self):
        """All scenarios have incorrect actions."""
        results = [
            {
                "success": True,
                "mttr_seconds": 45.0,
                "correct_action_taken": False,
                "sla_violations": [],
            },
            {
                "success": True,
                "mttr_seconds": 50.0,
                "correct_action_taken": False,
                "sla_violations": [],
            },
        ]
        
        metrics = calculate_metrics(results)
        
        assert metrics["false_positive_rate"] == 1.0  # 100%
        assert metrics["correct_actions"] == 0


class TestMetricsEdgeCases:
    """Test edge cases in metrics calculation."""

    def test_missing_fields_default_to_false_or_empty(self):
        """Missing fields in results use sensible defaults."""
        results = [
            {
                "mttr_seconds": 50.0,
                # Missing: success, correct_action_taken, sla_violations
            }
        ]
        
        metrics = calculate_metrics(results)
        
        assert metrics["total_scenarios"] == 1
        # Should not crash, should count as not successful, not correct, no violations
        assert metrics["successful_recoveries"] == 0
        assert metrics["correct_actions"] == 0
        assert metrics["scenarios_with_violations"] == 0

    def test_scenarios_with_multiple_violations(self):
        """Scenario with multiple SLA violations counts as one violation."""
        results = [
            {
                "success": False,
                "mttr_seconds": 300.0,
                "correct_action_taken": False,
                "sla_violations": [
                    "service1: error_rate violation",
                    "service2: latency violation",
                    "service3: error_rate violation",
                ],  # Multiple violations
            }
        ]
        
        metrics = calculate_metrics(results)
        
        # Should count as 1 scenario with violations (not 3)
        assert metrics["scenarios_with_violations"] == 1
        assert metrics["sla_violation_rate"] == 1.0

    def test_zero_mttr_values(self):
        """Scenarios with zero MTTR are handled correctly."""
        results = [
            {"mttr_seconds": 0.0, "correct_action_taken": True, "sla_violations": []},
            {"mttr_seconds": 60.0, "correct_action_taken": True, "sla_violations": []},
        ]
        
        metrics = calculate_metrics(results)
        
        assert metrics["mean_mttr_seconds"] == 30.0  # (0 + 60) / 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
