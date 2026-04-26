"""
Policy Invariants for Netpilot

Defines:
- SLA_BOUNDS: Service-level agreement constraints
- SERVICE_TOPOLOGY: Service dependency graph
- ROLLBACK_REGISTRY: Deployment rollback history
- blast_radius(): Impact calculation for remediation actions
"""

import sys
import logging
from typing import Dict, List, Set, Optional, Tuple
from datetime import datetime, timedelta

# Add parent directory to path for standalone execution
if __name__ == "__main__":
    sys.path.insert(0, "..")

from config import get_config


logger = logging.getLogger(__name__)


# ============================================================================
# Service Topology (Hardcoded for Now, Load from ConfigMap Later)
# ============================================================================

SERVICE_TOPOLOGY: Dict[str, List[str]] = {
    # Service dependency graph (adjacency list)
    # Represents: "service X calls service Y"
    # Structure:
    # frontend
    #   └─→ api-gateway
    #        ├─→ order-service
    #        │    ├─→ inventory-service
    #        │    │    └─→ notification-service
    #        │    └─→ notification-service
    #        └─→ inventory-service
    #             └─→ notification-service
    "frontend": ["api-gateway"],
    "api-gateway": ["order-service", "inventory-service"],
    "order-service": ["inventory-service", "notification-service"],
    "inventory-service": ["notification-service"],
    "notification-service": [],  # Leaf service, no downstream calls
}


# ============================================================================
# SLA Bounds (Loaded from Config)
# ============================================================================

def _load_sla_bounds() -> Dict[str, Dict[str, float]]:
    """Load SLA bounds from configuration
    
    Returns dict mapping service → {metric → bound}
    """
    config = get_config()
    policy_config = config.policy_gate
    
    # Default SLA bounds for all services
    # In production, these would come from ConfigMap or database
    sla_bounds = {
        "frontend": {
            "max_error_rate": 0.05,  # 5%
            "max_p99_latency_ms": 500,
        },
        "api-gateway": {
            "max_error_rate": 0.05,  # 5%
            "max_p99_latency_ms": 750,  # Higher due to aggregation
        },
        "order-service": {
            "max_error_rate": 0.03,  # 3% (stricter for business logic)
            "max_p99_latency_ms": 1000,
        },
        "inventory-service": {
            "max_error_rate": 0.03,  # 3% (stricter for critical data)
            "max_p99_latency_ms": 800,
        },
        "notification-service": {
            "max_error_rate": 0.10,  # 10% (more forgiving for non-critical)
            "max_p99_latency_ms": 2000,
        },
    }
    
    logger.info(f"Loaded SLA bounds for {len(sla_bounds)} services")
    return sla_bounds


# Load SLA bounds at module initialization
SLA_BOUNDS: Dict[str, Dict[str, float]] = _load_sla_bounds()


# ============================================================================
# Rollback Registry (Populated at Startup)
# ============================================================================

def _initialize_rollback_registry() -> Dict[str, Dict[str, any]]:
    """Initialize rollback registry from cluster
    
    Queries Kubernetes to get previous image tags for all deployments.
    In production, this would query:
      kubectl get deploy -A -o json | parse image history
    
    For now, returns mock data based on service names.
    
    Returns:
        Dict mapping deployment_name → {
            "previous_image": "image:tag",
            "current_image": "image:tag",
            "rollback_count": int,
            "last_rollback": datetime or None
        }
    """
    rollback_registry = {}
    
    # Mock initialization - in production, query from cluster
    services = list(SERVICE_TOPOLOGY.keys())
    
    for service in services:
        rollback_registry[service] = {
            "previous_image": f"netpilot-{service}:v1.2.3",
            "current_image": f"netpilot-{service}:v1.2.4",
            "rollback_count": 0,
            "last_rollback": None,
        }
    
    logger.info(f"Initialized rollback registry for {len(rollback_registry)} deployments")
    return rollback_registry


# Rollback registry populated at module initialization
ROLLBACK_REGISTRY: Dict[str, Dict[str, any]] = _initialize_rollback_registry()


# ============================================================================
# Accessors
# ============================================================================

def get_sla_bound(service: str, metric: str) -> Optional[float]:
    """Get SLA bound for a service
    
    Args:
        service: Service name
        metric: "max_error_rate" or "max_p99_latency_ms"
        
    Returns:
        SLA bound or None if not found
    """
    if service not in SLA_BOUNDS:
        logger.warning(f"No SLA bounds for service: {service}")
        return None
    
    if metric not in SLA_BOUNDS[service]:
        logger.warning(f"No {metric} bound for service: {service}")
        return None
    
    return SLA_BOUNDS[service][metric]


def get_previous_image_tag(deployment: str) -> Optional[str]:
    """Get previous image tag for deployment (for rollback)
    
    Args:
        deployment: Deployment name
        
    Returns:
        Previous image tag or None
    """
    if deployment not in ROLLBACK_REGISTRY:
        logger.warning(f"No rollback history for deployment: {deployment}")
        return None
    
    return ROLLBACK_REGISTRY[deployment]["previous_image"]


def register_rollback(deployment: str, previous_image: str) -> None:
    """Register a rollback in the registry
    
    Args:
        deployment: Deployment name
        previous_image: Previous image tag to restore
    """
    if deployment not in ROLLBACK_REGISTRY:
        ROLLBACK_REGISTRY[deployment] = {
            "previous_image": previous_image,
            "current_image": None,
            "rollback_count": 0,
            "last_rollback": None,
        }
    else:
        registry_entry = ROLLBACK_REGISTRY[deployment]
        registry_entry["current_image"] = registry_entry["previous_image"]
        registry_entry["previous_image"] = previous_image
        registry_entry["rollback_count"] = registry_entry.get("rollback_count", 0) + 1
        registry_entry["last_rollback"] = datetime.now()
    
    logger.info(f"Registered rollback for {deployment}: {previous_image}")


# ============================================================================
# Blast Radius Calculation
# ============================================================================

def blast_radius(action_target: str, topology: Optional[Dict[str, List[str]]] = None) -> int:
    """Calculate blast radius (number of affected services)
    
    The blast radius is the count of services that could be affected by an action
    on a particular target service.
    
    Calculation:
    1. Identify all downstream services (services that depend on target)
    2. Recursively identify their downstreams
    3. Return total count (excluding target itself)
    
    Args:
        action_target: Service name targeted by remediation action
        topology: Service dependency graph (uses SERVICE_TOPOLOGY if None)
        
    Returns:
        Integer count of potentially affected services
        
    Example:
        action_target="inventory-service"
        topology = {
            "frontend": ["api-gateway"],
            "api-gateway": ["order-service", "inventory-service"],
            "order-service": ["inventory-service", "notification-service"],
            "inventory-service": ["notification-service"],
            "notification-service": []
        }
        
        If we restart inventory-service, what services are affected?
        - Services that call inventory-service: api-gateway, order-service
        - Services that call those services: frontend (calls api-gateway)
        - Total affected: 3 services (frontend, api-gateway, order-service)
    """
    if topology is None:
        topology = SERVICE_TOPOLOGY
    
    if action_target not in topology:
        logger.warning(f"Service not found in topology: {action_target}")
        return 0
    
    # Find all services that depend on action_target (upstream callers)
    affected = _find_upstream_services(action_target, topology)
    
    # Find all services that those depend on (recursive upstream)
    all_affected = set(affected)
    for service in affected:
        all_affected.update(_find_upstream_services(service, topology))
    
    # Remove target itself if present
    all_affected.discard(action_target)
    
    logger.info(
        f"Blast radius for {action_target}: {len(all_affected)} services "
        f"({', '.join(sorted(all_affected))})"
    )
    
    return len(all_affected)


def _find_upstream_services(target: str, topology: Dict[str, List[str]]) -> Set[str]:
    """Find all services that depend on (call) the target service
    
    Args:
        target: Service to find callers for
        topology: Service dependency graph
        
    Returns:
        Set of services that call the target
    """
    upstream = set()
    
    # Iterate through all services and find those that call target
    for service, dependencies in topology.items():
        if target in dependencies:
            upstream.add(service)
    
    return upstream


def calculate_blast_radius_percentage(
    action_target: str,
    topology: Optional[Dict[str, List[str]]] = None
) -> float:
    """Calculate blast radius as percentage of total services
    
    Args:
        action_target: Service targeted by remediation action
        topology: Service dependency graph (uses SERVICE_TOPOLOGY if None)
        
    Returns:
        Percentage (0.0-100.0) of services potentially affected
    """
    if topology is None:
        topology = SERVICE_TOPOLOGY
    
    radius = blast_radius(action_target, topology)
    total_services = len(topology)
    
    if total_services == 0:
        return 0.0
    
    percentage = (radius / total_services) * 100.0
    return percentage


# ============================================================================
# Validation Helpers
# ============================================================================

def is_within_sla(service: str, error_rate: float, p99_latency_ms: float) -> Tuple[bool, List[str]]:
    """Check if service metrics are within SLA
    
    Args:
        service: Service name
        error_rate: Current error rate (0.0-1.0)
        p99_latency_ms: Current P99 latency in milliseconds
        
    Returns:
        Tuple of (is_within_sla, list of violations)
    """
    violations = []
    
    # Check error rate
    max_error_rate = get_sla_bound(service, "max_error_rate")
    if max_error_rate is not None and error_rate > max_error_rate:
        violations.append(
            f"Error rate {error_rate:.2%} exceeds max {max_error_rate:.2%}"
        )
    
    # Check latency
    max_latency = get_sla_bound(service, "max_p99_latency_ms")
    if max_latency is not None and p99_latency_ms > max_latency:
        violations.append(
            f"P99 latency {p99_latency_ms:.0f}ms exceeds max {max_latency:.0f}ms"
        )
    
    return (len(violations) == 0, violations)


def is_blast_radius_acceptable(action_target: str, max_radius_pct: float) -> Tuple[bool, str]:
    """Check if action blast radius is acceptable
    
    Args:
        action_target: Service targeted by remediation action
        max_radius_pct: Maximum acceptable blast radius percentage
        
    Returns:
        Tuple of (is_acceptable, reason)
    """
    radius_pct = calculate_blast_radius_percentage(action_target)
    
    if radius_pct <= max_radius_pct:
        return (True, f"Blast radius {radius_pct:.1f}% is acceptable")
    else:
        return (False, f"Blast radius {radius_pct:.1f}% exceeds max {max_radius_pct:.1f}%")


# ============================================================================
# Debugging & Display
# ============================================================================

def print_topology() -> None:
    """Print service topology in human-readable format"""
    print("\nService Topology (Dependency Graph):")
    print("=====================================\n")
    
    for service, dependencies in SERVICE_TOPOLOGY.items():
        if dependencies:
            deps_str = " → " + ", ".join(dependencies)
        else:
            deps_str = " (leaf service)"
        print(f"  {service}{deps_str}")
    
    print()


def print_sla_bounds() -> None:
    """Print SLA bounds in human-readable format"""
    print("\nSLA Bounds:")
    print("============\n")
    
    for service, bounds in SLA_BOUNDS.items():
        error_rate = bounds.get("max_error_rate", "N/A")
        latency = bounds.get("max_p99_latency_ms", "N/A")
        print(f"  {service:25} Error Rate: {error_rate:6} | P99: {latency:6}ms")
    
    print()


def print_blast_radius_analysis() -> None:
    """Print blast radius for each service"""
    print("\nBlast Radius Analysis:")
    print("======================\n")
    
    for service in SERVICE_TOPOLOGY.keys():
        radius = blast_radius(service)
        radius_pct = calculate_blast_radius_percentage(service)
        print(f"  {service:25} affects {radius:2} services ({radius_pct:5.1f}%)")
    
    print()


if __name__ == "__main__":
    """Standalone debugging"""
    print_topology()
    print_sla_bounds()
    print_blast_radius_analysis()
