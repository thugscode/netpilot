"""
Policy Gate: Validates remediation actions before execution

Checks in order:
1. SLA bounds - would the action violate constraints?
2. Rollback feasibility - does rollback target have previous image?
3. Blast radius - would action affect too many services?
"""

import logging
from typing import Tuple, Dict, Optional

from agent.models import RemediationAction
from telemetry.schemas import KPI
from policy.invariants import (
    blast_radius,
    ROLLBACK_REGISTRY,
    is_within_sla,
    get_previous_image_tag,
)

try:
    from config import get_config
except ImportError:
    import config
    get_config = config.get_config

logger = logging.getLogger(__name__)


class PolicyGate:
    """
    Validates remediation actions before execution.
    
    Validation checks (in order):
    1. **SLA Bounds** - Simulate action impact, verify projected KPIs stay within bounds
    2. **Rollback Feasibility** - For rollback actions, verify previous image exists
    3. **Blast Radius** - Check that affected services count doesn't exceed limit
    
    Example:
        gate = PolicyGate()
        action = RemediationAction(
            action_type="restart_pod",
            target="order-service",
            params={},
            confidence=0.95,
            rationale="Service stuck in error loop"
        )
        
        allowed, reason = gate.validate(action, current_kpis)
        if allowed:
            executor.execute(action)
        else:
            logger.warning(f"Action rejected: {reason}")
    """
    
    def __init__(self):
        """Initialize PolicyGate with configuration."""
        self.config = get_config()
    
    # ========================================================================
    # Main Validation API
    # ========================================================================
    
    def validate(
        self,
        action: RemediationAction,
        current_kpis: Dict[str, KPI],
    ) -> Tuple[bool, str]:
        """
        Validate a remediation action.
        
        Performs checks in order:
        1. SLA bounds - Would the action violate SLA constraints?
        2. Rollback feasibility - Does rollback have previous image available?
        3. Blast radius - Would action affect too many services?
        
        Args:
            action: RemediationAction to validate
                - action_type: restart_pod | scale_up | reroute_traffic | rollback_deploy | noop
                - target: service name
                - params: action-specific parameters
                - confidence: 0.0-1.0
                - rationale: one-sentence explanation
            
            current_kpis: Dict mapping service names to current KPI snapshots
                - Keys: service names ("order-service", etc.)
                - Values: KPI objects with error_rate, latency_p50/p95/p99_ms, etc.
        
        Returns:
            Tuple of (allowed: bool, reason: str)
            - If allowed=True, reason explains why action was approved
            - If allowed=False, reason explains which check failed and why
        
        Examples:
            # Action passes all checks
            (True, "Action approved: restart_pod on order-service")
            
            # SLA bounds check failed
            (False, "Action would violate SLA: P99 latency 1200ms exceeds max 1000ms")
            
            # Rollback not feasible
            (False, "No previous image available for rollback: order-service")
            
            # Blast radius too large
            (False, "Blast radius 5 exceeds max 2")
        """
        
        logger.debug(f"Validating action: {action.action_type} on {action.target}")
        
        # Check 1: SLA Bounds
        allowed, reason = self._check_sla_bounds(action, current_kpis)
        if not allowed:
            logger.warning(f"SLA bounds check failed: {reason}")
            return False, reason
        
        logger.debug("✓ SLA bounds check passed")
        
        # Check 2: Rollback Feasibility
        allowed, reason = self._check_rollback_feasibility(action)
        if not allowed:
            logger.warning(f"Rollback feasibility check failed: {reason}")
            return False, reason
        
        logger.debug("✓ Rollback feasibility check passed")
        
        # Check 3: Blast Radius
        allowed, reason = self._check_blast_radius(action)
        if not allowed:
            logger.warning(f"Blast radius check failed: {reason}")
            return False, reason
        
        logger.debug("✓ Blast radius check passed")
        
        # All checks passed
        approval_reason = (
            f"Action approved: {action.action_type} on {action.target} "
            f"(confidence: {action.confidence:.1%})"
        )
        logger.info(approval_reason)
        return True, approval_reason
    
    # ========================================================================
    # Check 1: SLA Bounds
    # ========================================================================
    
    def _check_sla_bounds(
        self,
        action: RemediationAction,
        current_kpis: Dict[str, KPI],
    ) -> Tuple[bool, str]:
        """
        Check if action would violate SLA bounds.
        
        Simulates the action's impact on target service KPIs using heuristics:
        
        - **restart_pod**: Temporarily doubles error rate (30s spike)
        - **scale_up**: Halves latency (more resources = faster)
        - **scale_down**: Doubles latency (fewer resources = slower)
        - **reroute_traffic**: No immediate impact (handled by mesh)
        - **rollback_deploy**: No immediate KPI impact (if previous image was stable)
        - **noop**: No impact
        
        Then checks if projected KPIs would violate SLA bounds for the service.
        
        Args:
            action: RemediationAction with action_type and target
            current_kpis: Dict of current KPI values for all services
        
        Returns:
            (allowed, reason) tuple
        """
        
        target = action.target
        
        # Verify we have KPI data for the target
        if target not in current_kpis:
            reason = (
                f"No KPI data available for target service '{target}'. "
                f"Available: {list(current_kpis.keys())}"
            )
            logger.warning(reason)
            return False, reason
        
        current_kpi = current_kpis[target]
        
        # Project KPIs after action impact
        projected_error_rate = current_kpi.error_rate
        projected_p99_latency = current_kpi.latency_p99_ms
        
        impact_description = self._simulate_action_impact(
            action.action_type,
            current_kpi
        )
        
        projected_error_rate = impact_description["error_rate"]
        projected_p99_latency = impact_description["latency_p99_ms"]
        
        # Check if projected KPIs violate SLA
        is_ok, violations = is_within_sla(target, projected_error_rate, projected_p99_latency)
        
        if not is_ok:
            reason = (
                f"Action would violate SLA bounds for {target}: "
                f"{'; '.join(violations)} "
                f"(projected: {projected_error_rate:.2%} error, "
                f"{projected_p99_latency:.0f}ms P99 latency)"
            )
            return False, reason
        
        reason = (
            f"SLA bounds check passed for {target}: "
            f"projected metrics within bounds "
            f"({projected_error_rate:.2%} error, "
            f"{projected_p99_latency:.0f}ms P99 latency)"
        )
        return True, reason
    
    def _simulate_action_impact(
        self,
        action_type: str,
        current_kpi: KPI,
    ) -> Dict[str, float]:
        """
        Simulate the impact of an action on KPIs using heuristics.
        
        Args:
            action_type: Type of remediation action
            current_kpi: Current KPI snapshot
        
        Returns:
            Dict with projected error_rate and latency_p99_ms
        """
        
        error_rate = current_kpi.error_rate
        p99_latency = current_kpi.latency_p99_ms
        
        if action_type == "restart_pod":
            # Pod restart causes temporary spike in errors (~30 seconds)
            # Model: error rate temporarily doubles during restart window
            error_rate = min(1.0, current_kpi.error_rate * 2)
            
        elif action_type == "scale_up":
            # Adding replicas reduces latency as load is distributed
            # Model: P99 latency halved with more capacity
            p99_latency = current_kpi.latency_p99_ms / 2
            
        elif action_type == "scale_down":
            # Removing replicas increases latency (higher contention)
            # Model: P99 latency doubled with less capacity
            p99_latency = current_kpi.latency_p99_ms * 2
            
        elif action_type == "reroute_traffic":
            # Traffic rerouting via service mesh/circuit breaker
            # Typically transparent, no immediate impact
            # Model: assume no change (handled by mesh)
            pass
            
        elif action_type == "rollback_deploy":
            # Rollback to previous image that was presumably stable
            # Model: assume no immediate negative impact
            # (if previous image was bad, this is detected during diagnostic phase)
            pass
            
        elif action_type == "noop":
            # No-op action has no impact
            pass
        
        return {
            "error_rate": error_rate,
            "latency_p99_ms": p99_latency,
        }
    
    # ========================================================================
    # Check 2: Rollback Feasibility
    # ========================================================================
    
    def _check_rollback_feasibility(
        self,
        action: RemediationAction,
    ) -> Tuple[bool, str]:
        """
        Check if rollback action has a previous image available.
        
        For rollback_deploy actions, verify:
        1. Deployment name exists in ROLLBACK_REGISTRY
        2. Previous image tag is available (not None/empty)
        
        For all other action types, this check passes (not applicable).
        
        Args:
            action: RemediationAction with action_type and target
        
        Returns:
            (allowed, reason) tuple
        """
        
        # Check only applies to rollback_deploy actions
        if action.action_type != "rollback_deploy":
            return True, f"{action.action_type} (no rollback feasibility check needed)"
        
        target = action.target
        
        # Verify deployment is in rollback registry
        if target not in ROLLBACK_REGISTRY:
            reason = (
                f"Deployment '{target}' not found in rollback registry. "
                f"Available deployments: {list(ROLLBACK_REGISTRY.keys())}"
            )
            logger.warning(reason)
            return False, reason
        
        # Verify previous image is available
        previous_image = get_previous_image_tag(target)
        
        if not previous_image:
            reason = (
                f"No previous image available for rollback on '{target}'. "
                f"Registry entry: {ROLLBACK_REGISTRY.get(target)}"
            )
            logger.warning(reason)
            return False, reason
        
        reason = f"Rollback feasible: {previous_image} available for {target}"
        return True, reason
    
    # ========================================================================
    # Check 3: Blast Radius
    # ========================================================================
    
    def _check_blast_radius(
        self,
        action: RemediationAction,
    ) -> Tuple[bool, str]:
        """
        Check if action blast radius is within acceptable limits.
        
        Blast radius = number of upstream services that could be affected
        (i.e., services that depend on this service, directly or indirectly).
        
        Example topology:
        ```
        frontend → api-gateway → order-service → inventory-service
        ```
        
        If we restart order-service:
        - Direct caller: api-gateway (affected)
        - Indirect caller: frontend (affected if api-gateway fails)
        - Blast radius: 2
        
        This action would be rejected if max_blast_radius < 2.
        
        Args:
            action: RemediationAction with target service
        
        Returns:
            (allowed, reason) tuple
        """
        
        target = action.target
        
        # Calculate blast radius (upstream services affected)
        radius = blast_radius(target)
        
        # Get configuration limit
        config = get_config()
        max_radius = config.policy_gate.max_blast_radius
        
        if radius > max_radius:
            pct = (radius / 5) * 100  # Assuming 5 services total
            reason = (
                f"Blast radius too large: {radius} services affected "
                f"({pct:.1f}% of total), exceeds max {max_radius}"
            )
            logger.warning(reason)
            return False, reason
        
        pct = (radius / 5) * 100 if radius < 5 else 100
        reason = (
            f"Blast radius acceptable: {radius} services affected "
            f"({pct:.1f}% of total), within max {max_radius}"
        )
        return True, reason


# ============================================================================
# Utility Functions
# ============================================================================


def explain_policy_decision(
    action: RemediationAction,
    decision: Tuple[bool, str],
    verbose: bool = True,
) -> str:
    """
    Generate human-readable explanation of policy decision.
    
    Args:
        action: The validated action
        decision: Tuple of (allowed, reason) from PolicyGate.validate()
        verbose: If True, include action details
    
    Returns:
        Formatted explanation string
    """
    allowed, reason = decision
    
    status = "✓ APPROVED" if allowed else "✗ REJECTED"
    
    explanation = f"\n{status}: {action.action_type} on {action.target}\n"
    
    if verbose:
        explanation += f"  Confidence: {action.confidence:.1%}\n"
        explanation += f"  Rationale: {action.rationale}\n"
    
    explanation += f"  Decision: {reason}\n"
    
    return explanation


def create_audit_log_entry(
    action: RemediationAction,
    decision: Tuple[bool, str],
) -> dict:
    """
    Create audit log entry for policy decision.
    
    Args:
        action: The validated action
        decision: Tuple of (allowed, reason) from PolicyGate.validate()
    
    Returns:
        Dict suitable for JSONL logging
    """
    from datetime import datetime
    
    allowed, reason = decision
    
    return {
        "timestamp": datetime.now().isoformat(),
        "action_type": action.action_type,
        "target": action.target,
        "confidence": action.confidence,
        "allowed": allowed,
        "reason": reason,
    }
