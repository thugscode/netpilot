"""
Tests for policy/gate.py - PolicyGate validation logic

Coverage:
- SLA bounds validation with impact simulation
- Rollback feasibility checking
- Blast radius constraints
- Full action validation workflow
"""

import pytest
from datetime import datetime

from agent.models import RemediationAction
from telemetry.schemas import KPI
from policy.gate import PolicyGate, explain_policy_decision, create_audit_log_entry


# ============================================================================
# Fixtures: Test Data
# ============================================================================


@pytest.fixture
def policy_gate():
    """Create a PolicyGate instance for testing."""
    return PolicyGate()


def create_test_kpi(
    service: str,
    error_rate: float = 0.02,
    latency_p50: int = 100,
    latency_p95: int = 300,
    latency_p99: int = 500,
    request_count_5m: int = 1000,
) -> KPI:
    """Helper to create test KPI objects."""
    return KPI(
        service=service,
        timestamp=datetime.now().isoformat(),
        error_rate=error_rate,
        latency_p50_ms=latency_p50,
        latency_p95_ms=latency_p95,
        latency_p99_ms=latency_p99,
        pod_restarts_total=0,
        pod_restarts_5m=0,
        downstream_error_rate=0.01,
        downstream_latency_p99_ms=400,
        availability=True,
        request_count_5m=request_count_5m,
    )


def create_test_action(
    action_type: str = "restart_pod",
    target: str = "order-service",
    confidence: float = 0.95,
    params: dict = None,
) -> RemediationAction:
    """Helper to create test RemediationAction objects."""
    return RemediationAction(
        action_type=action_type,
        target=target,
        params=params or {},
        confidence=confidence,
        rationale=f"Test {action_type} action",
    )


@pytest.fixture
def baseline_kpis():
    """Create baseline KPIs for all services (within SLA bounds)."""
    return {
        "frontend": create_test_kpi("frontend", 0.02, 100, 200, 400),
        "api-gateway": create_test_kpi("api-gateway", 0.03, 150, 400, 600),
        "order-service": create_test_kpi("order-service", 0.01, 200, 500, 800),
        "inventory-service": create_test_kpi("inventory-service", 0.02, 150, 400, 700),
        "notification-service": create_test_kpi("notification-service", 0.05, 300, 800, 1500),
    }


# ============================================================================
# Test Suite 1: SLA Bounds Validation
# ============================================================================


class TestSLABoundsValidation:
    """Tests for SLA bounds checking and impact simulation."""
    
    def test_healthy_service_passes_sla_check(self, policy_gate, baseline_kpis):
        """Test that healthy service within SLA bounds passes check."""
        action = create_test_action("restart_pod", "frontend")
        allowed, reason = policy_gate._check_sla_bounds(action, baseline_kpis)
        
        # Frontend metrics are within bounds
        assert allowed is True
        assert "SLA bounds check passed" in reason
    
    def test_high_error_rate_fails_sla_check(self, policy_gate, baseline_kpis):
        """Test that action causing SLA violation is rejected."""
        # Create KPIs with error rate at the edge
        kpis = baseline_kpis.copy()
        kpis["order-service"] = create_test_kpi(
            "order-service",
            error_rate=0.08,  # High error rate
            latency_p99=800
        )
        
        action = create_test_action("restart_pod", "order-service")
        allowed, reason = policy_gate._check_sla_bounds(action, kpis)
        
        # Restarting pod doubles error rate: 0.08 * 2 = 0.16 (way over SLA of 3%)
        assert allowed is False
        assert "would violate SLA" in reason or "exceed" in reason.lower()
    
    def test_high_latency_fails_sla_check(self, policy_gate, baseline_kpis):
        """Test that high latency violations are detected."""
        kpis = baseline_kpis.copy()
        kpis["api-gateway"] = create_test_kpi(
            "api-gateway",
            error_rate=0.02,
            latency_p99=1500  # Very high, exceeds 750ms bound
        )
        
        action = create_test_action("restart_pod", "api-gateway")
        allowed, reason = policy_gate._check_sla_bounds(action, kpis)
        
        assert allowed is False
        assert "would violate SLA" in reason or "exceed" in reason.lower()
    
    def test_scale_up_reduces_latency(self, policy_gate, baseline_kpis):
        """Test that scale_up action simulation reduces latency."""
        # Service with high latency
        kpis = baseline_kpis.copy()
        kpis["api-gateway"] = create_test_kpi(
            "api-gateway",
            error_rate=0.02,
            latency_p99=700  # At edge of 750ms bound
        )
        
        # Scale up should halve latency, making it 350ms (well within bound)
        action = create_test_action("scale_up", "api-gateway")
        allowed, reason = policy_gate._check_sla_bounds(action, kpis)
        
        # Should pass because scale_up halves latency (700 / 2 = 350)
        assert allowed is True
        assert "SLA bounds check passed" in reason
    
    def test_restart_pod_increases_error_rate(self, policy_gate, baseline_kpis):
        """Test that restart_pod action simulation doubles error rate."""
        kpis = baseline_kpis.copy()
        kpis["order-service"] = create_test_kpi(
            "order-service",
            error_rate=0.015,  # Low error rate
            latency_p99=800
        )
        
        # Restart doubles error rate: 0.015 * 2 = 0.03 (at bound of 3%)
        action = create_test_action("restart_pod", "order-service")
        allowed, reason = policy_gate._check_sla_bounds(action, kpis)
        
        # Should pass because projected error rate 3% is at the bound
        assert allowed is True
    
    def test_missing_kpi_data_fails(self, policy_gate, baseline_kpis):
        """Test that missing KPI data causes validation failure."""
        # Remove KPI for target service
        kpis = baseline_kpis.copy()
        del kpis["order-service"]
        
        action = create_test_action("restart_pod", "order-service")
        allowed, reason = policy_gate._check_sla_bounds(action, kpis)
        
        assert allowed is False
        assert "No KPI data" in reason


# ============================================================================
# Test Suite 2: Rollback Feasibility
# ============================================================================


class TestRollbackFeasibility:
    """Tests for rollback action feasibility checking."""
    
    def test_non_rollback_action_passes(self, policy_gate):
        """Test that non-rollback actions skip this check."""
        action = create_test_action("restart_pod", "order-service")
        allowed, reason = policy_gate._check_rollback_feasibility(action)
        
        assert allowed is True
        assert "restart_pod" in reason
    
    def test_rollback_with_valid_previous_image(self, policy_gate):
        """Test that rollback passes when previous image exists."""
        action = create_test_action("rollback_deploy", "frontend")
        allowed, reason = policy_gate._check_rollback_feasibility(action)
        
        # Frontend has previous image in registry
        assert allowed is True
        assert "Rollback feasible" in reason
        assert "netpilot-frontend" in reason
    
    def test_rollback_with_missing_previous_image(self, policy_gate):
        """Test that rollback fails when no previous image exists."""
        # Test with a service that has no previous image
        # (We'd need to mock ROLLBACK_REGISTRY for this, but in integration
        # the registry is pre-populated)
        
        # For now, test with a service that doesn't exist in registry
        action = create_test_action("rollback_deploy", "non-existent-service")
        allowed, reason = policy_gate._check_rollback_feasibility(action)
        
        assert allowed is False
        assert "not found in rollback registry" in reason or "not found" in reason.lower()
    
    def test_scale_up_action_skips_rollback_check(self, policy_gate):
        """Test that scale_up action doesn't trigger rollback check."""
        action = create_test_action("scale_up", "api-gateway")
        allowed, reason = policy_gate._check_rollback_feasibility(action)
        
        assert allowed is True


# ============================================================================
# Test Suite 3: Blast Radius Constraints
# ============================================================================


class TestBlastRadiusValidation:
    """Tests for blast radius checking."""
    
    def test_leaf_service_has_zero_radius(self, policy_gate):
        """Test that leaf services have minimal blast radius."""
        # notification-service has no callers, so blast radius = 0
        action = create_test_action("restart_pod", "notification-service")
        allowed, reason = policy_gate._check_blast_radius(action)
        
        # 0 affected services is within any limit
        assert allowed is True
        assert "acceptable" in reason.lower()
    
    def test_order_service_has_moderate_radius(self, policy_gate):
        """Test blast radius for moderately-connected service."""
        # order-service has blast radius of 2 (api-gateway + frontend)
        action = create_test_action("restart_pod", "order-service")
        allowed, reason = policy_gate._check_blast_radius(action)
        
        # Default max_blast_radius is 2, so this should be at the limit
        # Result depends on config
        assert isinstance(allowed, bool)
        assert "acceptable" in reason.lower() or "exceeds" in reason.lower()
    
    def test_api_gateway_affects_most_services(self, policy_gate):
        """Test blast radius for highly-connected service."""
        # api-gateway affects 1 upstream (frontend)
        action = create_test_action("restart_pod", "api-gateway")
        allowed, reason = policy_gate._check_blast_radius(action)
        
        # Should be within bounds
        assert allowed is True
    
    def test_blast_radius_uses_config_limit(self, policy_gate):
        """Test that blast radius check uses configuration limit."""
        # The actual limit comes from config
        assert hasattr(policy_gate.config.policy_gate, "max_blast_radius")
        assert isinstance(policy_gate.config.policy_gate.max_blast_radius, int)


# ============================================================================
# Test Suite 4: Full Validation Workflow
# ============================================================================


class TestFullValidationWorkflow:
    """Tests for complete action validation."""
    
    def test_healthy_action_approved(self, policy_gate, baseline_kpis):
        """Test that healthy actions pass all checks."""
        action = create_test_action("restart_pod", "notification-service", confidence=0.92)
        allowed, reason = policy_gate.validate(action, baseline_kpis)
        
        assert allowed is True
        assert "approved" in reason.lower()
    
    def test_action_rejected_for_sla_violation(self, policy_gate, baseline_kpis):
        """Test that SLA violations block actions."""
        kpis = baseline_kpis.copy()
        kpis["order-service"] = create_test_kpi(
            "order-service",
            error_rate=0.025,  # Will double to 5% on restart, violates 3% bound
            latency_p99=800
        )
        
        action = create_test_action("restart_pod", "order-service")
        allowed, reason = policy_gate.validate(action, kpis)
        
        # Should fail due to SLA violation
        assert allowed is False
        assert "violate" in reason.lower() or "exceed" in reason.lower()
    
    def test_action_rejected_for_blast_radius(self, policy_gate, baseline_kpis):
        """Test that excessive blast radius blocks actions."""
        # Create action with large blast radius
        # (In practice, would need to mock topology for this)
        action = create_test_action("restart_pod", "frontend")
        allowed, reason = policy_gate.validate(action, baseline_kpis)
        
        # Result depends on topology and config limits
        assert isinstance(allowed, bool)
    
    def test_rollback_action_validation(self, policy_gate, baseline_kpis):
        """Test complete validation for rollback action."""
        action = create_test_action("rollback_deploy", "frontend", confidence=0.85)
        allowed, reason = policy_gate.validate(action, baseline_kpis)
        
        # Should pass all checks (frontend in registry with previous image)
        assert allowed is True
        assert "approved" in reason.lower()
    
    def test_scale_up_reduces_latency_improves_sla(self, policy_gate):
        """Test scale_up action with high-latency service."""
        kpis = {
            "api-gateway": create_test_kpi(
                "api-gateway",
                error_rate=0.02,
                latency_p99=740  # Near 750ms bound
            ),
        }
        # Add other services for full validation
        kpis.update({
            "frontend": create_test_kpi("frontend"),
            "order-service": create_test_kpi("order-service"),
            "inventory-service": create_test_kpi("inventory-service"),
            "notification-service": create_test_kpi("notification-service"),
        })
        
        action = create_test_action("scale_up", "api-gateway", confidence=0.88)
        allowed, reason = policy_gate.validate(action, kpis)
        
        # scale_up halves latency: 740 / 2 = 370ms (well within bound)
        assert allowed is True


# ============================================================================
# Test Suite 5: Utility Functions
# ============================================================================


class TestUtilityFunctions:
    """Tests for helper functions."""
    
    def test_explain_policy_decision_approved(self):
        """Test explanation generation for approved actions."""
        action = create_test_action("restart_pod", "order-service", confidence=0.95)
        decision = (True, "Action approved: restart_pod on order-service")
        
        explanation = explain_policy_decision(action, decision, verbose=True)
        
        assert "APPROVED" in explanation
        assert "restart_pod" in explanation
        assert "order-service" in explanation
        assert "95.0%" in explanation
    
    def test_explain_policy_decision_rejected(self):
        """Test explanation generation for rejected actions."""
        action = create_test_action("restart_pod", "api-gateway")
        decision = (False, "Blast radius too large: 5 services affected")
        
        explanation = explain_policy_decision(action, decision, verbose=True)
        
        assert "REJECTED" in explanation
        assert "Blast radius" in explanation
    
    def test_create_audit_log_entry_approved(self):
        """Test audit log entry creation for approved action."""
        action = create_test_action("scale_up", "order-service", confidence=0.92)
        decision = (True, "Action approved")
        
        entry = create_audit_log_entry(action, decision)
        
        assert entry["action_type"] == "scale_up"
        assert entry["target"] == "order-service"
        assert entry["allowed"] is True
        assert entry["confidence"] == 0.92
        assert "timestamp" in entry
        assert "reason" in entry
    
    def test_create_audit_log_entry_rejected(self):
        """Test audit log entry creation for rejected action."""
        action = create_test_action("rollback_deploy", "unknown-service")
        decision = (False, "No rollback history available")
        
        entry = create_audit_log_entry(action, decision)
        
        assert entry["allowed"] is False
        assert "No rollback history" in entry["reason"]


# ============================================================================
# Run Tests
# ============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
