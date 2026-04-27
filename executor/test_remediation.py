"""
Tests for executor.remediation module.

Tests each action type with mocked kubectl commands.
"""

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add parent dirs to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.models import RemediationAction
from executor.remediation import (
    execute,
    ExecutionResult,
    RemediationError,
    batch_execute,
    _restart_pod,
    _scale_up,
    _reroute_traffic,
    _rollback_deploy,
    _noop,
)


class TestRestartPod:
    """Test restart_pod action."""

    def test_restart_pod_success(self):
        """Successful pod restart via kubectl delete."""
        action = RemediationAction(
            action_type="restart_pod",
            target="notification-service",
            params={"pod_name": "notification-service-abc123"},
            confidence=0.85,
            rationale="Restart stuck pod",
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="pod \"notification-service-abc123\" deleted",
                stderr="",
            )

            result = execute(action)

            assert result.success is True
            assert result.action_type == "restart_pod"
            assert result.target == "notification-service"
            assert result.exit_code == 0
            mock_run.assert_called_once()

    def test_restart_pod_failure(self):
        """Failed pod restart - kubectl error."""
        action = RemediationAction(
            action_type="restart_pod",
            target="unknown-service",
            params={},
            confidence=0.80,
            rationale="Restart",
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="error: pods \"unknown-service-xyz\" not found",
            )

            result = execute(action)

            assert result.success is False
            assert result.action_type == "restart_pod"
            assert result.target == "unknown-service"
            assert "not found" in result.error

    def test_restart_pod_timeout(self):
        """Pod restart times out."""
        action = RemediationAction(
            action_type="restart_pod",
            target="slow-service",
            params={},
            confidence=0.75,
            rationale="Restart",
        )

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = TimeoutError("Command timed out")

            result = execute(action)

            assert result.success is False
            # Error should mention timeout (either as "timeout" or "timed out")
            assert "timeout" in result.error.lower() or "timed" in result.error.lower()


class TestScaleUp:
    """Test scale_up action."""

    def test_scale_up_success(self):
        """Successful deployment scaling."""
        action = RemediationAction(
            action_type="scale_up",
            target="order-service",
            params={"replicas": 3},
            confidence=0.80,
            rationale="Scale up to handle load",
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="deployment.apps/order-service scaled",
                stderr="",
            )

            result = execute(action)

            assert result.success is True
            assert result.action_type == "scale_up"
            assert result.exit_code == 0
            mock_run.assert_called_once()

    def test_scale_up_missing_replicas(self):
        """Scale up without replicas parameter."""
        action = RemediationAction(
            action_type="scale_up",
            target="inventory-service",
            params={},  # Missing 'replicas'
            confidence=0.75,
            rationale="Scale up",
        )

        result = execute(action)

        assert result.success is False
        assert "replicas" in result.error.lower()

    def test_scale_up_failure(self):
        """Scale up command fails."""
        action = RemediationAction(
            action_type="scale_up",
            target="unknown-service",
            params={"replicas": 5},
            confidence=0.80,
            rationale="Scale up",
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="error: deployments.apps \"unknown-service\" not found",
            )

            result = execute(action)

            assert result.success is False
            assert "not found" in result.error


class TestRerouteTraffic:
    """Test reroute_traffic action (stub)."""

    def test_reroute_traffic_stub(self):
        """Reroute traffic stub - logs intent only."""
        action = RemediationAction(
            action_type="reroute_traffic",
            target="api-gateway",
            params={"dest_service": "api-gateway-v2"},
            confidence=0.70,
            rationale="Reroute to alternate",
        )

        result = execute(action)

        assert result.success is True
        assert result.action_type == "reroute_traffic"
        assert result.exit_code == 0
        assert "STUB" in result.output or "reroute" in result.output.lower()


class TestRollbackDeploy:
    """Test rollback_deploy action."""

    def test_rollback_deploy_success(self):
        """Successful deployment rollback via kubectl set image."""
        action = RemediationAction(
            action_type="rollback_deploy",
            target="frontend",
            params={},
            confidence=0.90,
            rationale="Rollback to stable version",
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="deployment.apps/frontend image updated",
                stderr="",
            )

            result = execute(action)

            assert result.success is True
            assert result.action_type == "rollback_deploy"
            assert result.exit_code == 0

    def test_rollback_deploy_not_in_registry(self):
        """Rollback on service not in ROLLBACK_REGISTRY."""
        action = RemediationAction(
            action_type="rollback_deploy",
            target="unknown-service",
            params={},
            confidence=0.85,
            rationale="Rollback",
        )

        result = execute(action)

        assert result.success is False
        assert "not in ROLLBACK_REGISTRY" in result.error or "registry" in result.error.lower()

    def test_rollback_deploy_no_previous_image(self):
        """Rollback when no previous_image in registry entry."""
        action = RemediationAction(
            action_type="rollback_deploy",
            target="frontend",  # In registry but no previous_image
            params={},
            confidence=0.85,
            rationale="Rollback",
        )

        # Mock the registry to have no previous_image
        with patch("executor.remediation.ROLLBACK_REGISTRY", {
            "frontend": {
                "current_image": "netpilot-frontend:v2.1.0",
                "previous_image": None,
                "rollback_count": 0,
            }
        }):
            result = execute(action)

            assert result.success is False
            assert "previous_image" in result.error.lower()


class TestNoop:
    """Test noop action."""

    def test_noop_success(self):
        """No-op action logs and returns success."""
        action = RemediationAction(
            action_type="noop",
            target="any-service",
            params={},
            confidence=0.50,
            rationale="No action needed",
        )

        result = execute(action)

        assert result.success is True
        assert result.action_type == "noop"
        assert result.exit_code == 0
        assert "no action" in result.output.lower()


class TestExecutionResult:
    """Test ExecutionResult model."""

    def test_result_serialization(self):
        """ExecutionResult serializes to dict correctly."""
        result = ExecutionResult(
            success=True,
            action_type="restart_pod",
            target="test-service",
            output="Pod restarted",
            error="",
            exit_code=0,
        )

        result_dict = result.to_dict()

        assert result_dict["success"] is True
        assert result_dict["action_type"] == "restart_pod"
        assert result_dict["target"] == "test-service"
        assert result_dict["output"] == "Pod restarted"
        assert result_dict["exit_code"] == 0
        assert "timestamp" in result_dict

    def test_result_defaults(self):
        """ExecutionResult handles missing output/error."""
        result = ExecutionResult(
            success=False,
            action_type="scale_up",
            target="service",
        )

        result_dict = result.to_dict()

        assert result_dict["output"] == ""
        assert result_dict["error"] == ""
        assert result_dict["exit_code"] is None


class TestBatchExecute:
    """Test batch_execute function."""

    def test_batch_execute_mixed_results(self):
        """Execute multiple actions with mixed results."""
        actions = [
            RemediationAction(
                action_type="restart_pod",
                target="service1",
                params={},
                confidence=0.85,
                rationale="Restart",
            ),
            RemediationAction(
                action_type="noop",
                target="service2",
                params={},
                confidence=0.50,
                rationale="No action",
            ),
        ]

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="pod deleted",
                stderr="",
            )

            results = batch_execute(actions)

            assert len(results) == 2
            assert results[0].action_type == "restart_pod"
            assert results[1].action_type == "noop"
            assert all(isinstance(r, ExecutionResult) for r in results)


class TestRemediationError:
    """Test RemediationError exception."""

    def test_remediation_error_creation(self):
        """RemediationError contains action details."""
        error = RemediationError(
            "restart_pod",
            "service",
            "Pod not found",
        )

        assert error.action_type == "restart_pod"
        assert error.target == "service"
        assert error.message == "Pod not found"
        assert "restart_pod" in str(error)
        assert "service" in str(error)


class TestKubectlIntegration:
    """Test actual kubectl command construction."""

    def test_restart_pod_command(self):
        """Verify kubectl delete pod command."""
        action = RemediationAction(
            action_type="restart_pod",
            target="test-service",
            params={},
            confidence=0.80,
            rationale="Restart",
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="",
                stderr="",
            )

            execute(action)

            # Verify kubectl was called with correct args
            call_args = mock_run.call_args[0][0]
            assert "kubectl" in call_args
            assert "delete" in call_args
            assert "pod" in call_args
            assert "app=test-service" in call_args
            assert "--grace-period=0" in call_args

    def test_scale_up_command(self):
        """Verify kubectl scale deployment command."""
        action = RemediationAction(
            action_type="scale_up",
            target="test-service",
            params={"replicas": 5},
            confidence=0.80,
            rationale="Scale",
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="",
                stderr="",
            )

            execute(action)

            # Verify kubectl was called with correct args
            call_args = mock_run.call_args[0][0]
            assert "kubectl" in call_args
            assert "scale" in call_args
            assert "deployment" in call_args
            assert "test-service" in call_args
            assert "--replicas=5" in call_args

    def test_rollback_command(self):
        """Verify kubectl set image command."""
        action = RemediationAction(
            action_type="rollback_deploy",
            target="frontend",
            params={},
            confidence=0.85,
            rationale="Rollback",
        )

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="",
                stderr="",
            )

            execute(action)

            # Verify kubectl was called with correct args
            call_args = mock_run.call_args[0][0]
            assert "kubectl" in call_args
            assert "set" in call_args
            assert "image" in call_args
            assert "deployment/frontend" in call_args


if __name__ == "__main__":
    # Allow running as: python -m pytest executor/test_remediation.py -v
    pytest.main([__file__, "-v"])
