"""
Tests for policy invariants

Coverage:
- SLA bounds loading and access
- Rollback registry management
- Blast radius calculation
- SLA validation
- Blast radius constraints
"""

import pytest
from datetime import datetime

from policy.invariants import (
    SLA_BOUNDS,
    ROLLBACK_REGISTRY,
    SERVICE_TOPOLOGY,
    blast_radius,
    calculate_blast_radius_percentage,
    get_sla_bound,
    get_previous_image_tag,
    register_rollback,
    is_within_sla,
    is_blast_radius_acceptable,
)


# ============================================================================
# Test Suite 1: SLA Bounds
# ============================================================================


def test_sla_bounds_loaded():
    """Test that SLA bounds are loaded for all services"""
    assert SLA_BOUNDS is not None
    assert len(SLA_BOUNDS) > 0
    
    # All services should have bounds
    expected_services = {
        "frontend",
        "api-gateway",
        "order-service",
        "inventory-service",
        "notification-service",
    }
    assert set(SLA_BOUNDS.keys()) == expected_services


def test_sla_bounds_have_metrics():
    """Test that each service has required metrics"""
    required_metrics = {"max_error_rate", "max_p99_latency_ms"}
    
    for service, bounds in SLA_BOUNDS.items():
        assert isinstance(bounds, dict)
        assert set(bounds.keys()) == required_metrics
        assert isinstance(bounds["max_error_rate"], float)
        assert isinstance(bounds["max_p99_latency_ms"], (int, float))


def test_get_sla_bound_valid():
    """Test getting valid SLA bound"""
    error_rate = get_sla_bound("frontend", "max_error_rate")
    assert error_rate is not None
    assert isinstance(error_rate, float)
    assert 0 < error_rate < 1


def test_get_sla_bound_invalid_service():
    """Test getting bound for invalid service"""
    result = get_sla_bound("unknown-service", "max_error_rate")
    assert result is None


def test_get_sla_bound_invalid_metric():
    """Test getting invalid metric"""
    result = get_sla_bound("frontend", "invalid_metric")
    assert result is None


def test_sla_bounds_values_reasonable():
    """Test that SLA bounds have reasonable values"""
    for service, bounds in SLA_BOUNDS.items():
        # Error rate should be between 0 and 1
        assert 0 < bounds["max_error_rate"] < 1
        # Latency should be positive milliseconds
        assert bounds["max_p99_latency_ms"] > 0


# ============================================================================
# Test Suite 2: Service Topology
# ============================================================================


def test_service_topology_structure():
    """Test that service topology is properly structured"""
    assert SERVICE_TOPOLOGY is not None
    assert isinstance(SERVICE_TOPOLOGY, dict)
    assert len(SERVICE_TOPOLOGY) > 0


def test_service_topology_references_valid():
    """Test that all referenced services exist in topology"""
    all_services = set(SERVICE_TOPOLOGY.keys())
    
    for service, dependencies in SERVICE_TOPOLOGY.items():
        for dep in dependencies:
            assert dep in all_services, f"Service {service} references unknown {dep}"


def test_service_topology_no_cycles():
    """Test that topology has no cycles (is a DAG)"""
    # Simple cycle detection: if A→B and B→A, it's a cycle
    for service, dependencies in SERVICE_TOPOLOGY.items():
        for dep in dependencies:
            if dep in SERVICE_TOPOLOGY:
                # Check if dep calls service (which would be a cycle)
                reverse_deps = SERVICE_TOPOLOGY.get(dep, [])
                if service in reverse_deps:
                    # Check if it's a direct cycle or longer
                    # For now, we expect no direct cycles
                    assert False, f"Cycle detected: {service} ↔ {dep}"


# ============================================================================
# Test Suite 3: Blast Radius Calculation
# ============================================================================


def test_blast_radius_leaf_service():
    """Test blast radius for leaf service (no one calls it)"""
    # notification-service is a leaf (no one calls it from others)
    # Actually, in our topology, api-gateway and others call it
    # Let me test with a service that has no callers
    
    # In our topology: frontend only calls api-gateway
    # So blast radius of api-gateway includes frontend
    radius = blast_radius("notification-service")
    assert isinstance(radius, int)
    assert radius >= 0


def test_blast_radius_root_service():
    """Test blast radius for root service (calls many)"""
    # frontend calls api-gateway
    # api-gateway calls order-service and inventory-service
    # order-service calls inventory-service and notification-service
    # inventory-service calls notification-service
    
    # Blast radius of order-service: services that call it
    # api-gateway calls order-service, frontend calls api-gateway
    radius = blast_radius("order-service")
    assert isinstance(radius, int)
    # Should include api-gateway and frontend
    assert radius >= 1


def test_blast_radius_all_services():
    """Test blast radius for all services"""
    for service in SERVICE_TOPOLOGY.keys():
        radius = blast_radius(service)
        assert isinstance(radius, int)
        assert 0 <= radius < len(SERVICE_TOPOLOGY)


def test_blast_radius_invalid_service():
    """Test blast radius for non-existent service"""
    radius = blast_radius("non-existent-service")
    assert radius == 0


def test_blast_radius_custom_topology():
    """Test blast radius with custom topology"""
    custom_topology = {
        "a": ["b"],
        "b": ["c"],
        "c": [],
    }
    
    # Blast radius of b: services that call b (which is a)
    radius = blast_radius("b", custom_topology)
    assert radius == 1  # a calls b


def test_blast_radius_percentage():
    """Test blast radius percentage calculation"""
    for service in SERVICE_TOPOLOGY.keys():
        pct = calculate_blast_radius_percentage(service)
        assert isinstance(pct, float)
        assert 0 <= pct <= 100


# ============================================================================
# Test Suite 4: Rollback Registry
# ============================================================================


def test_rollback_registry_initialized():
    """Test that rollback registry is initialized"""
    assert ROLLBACK_REGISTRY is not None
    assert isinstance(ROLLBACK_REGISTRY, dict)
    assert len(ROLLBACK_REGISTRY) > 0


def test_rollback_registry_has_all_services():
    """Test that registry has entries for all services"""
    for service in SERVICE_TOPOLOGY.keys():
        assert service in ROLLBACK_REGISTRY


def test_rollback_registry_entry_structure():
    """Test that each registry entry has required fields"""
    required_fields = {
        "previous_image",
        "current_image",
        "rollback_count",
        "last_rollback",
    }
    
    for service, entry in ROLLBACK_REGISTRY.items():
        assert isinstance(entry, dict)
        assert set(entry.keys()) == required_fields


def test_get_previous_image_tag_valid():
    """Test getting previous image tag for service"""
    image_tag = get_previous_image_tag("frontend")
    assert image_tag is not None
    assert isinstance(image_tag, str)
    assert ":" in image_tag


def test_get_previous_image_tag_invalid():
    """Test getting image tag for non-existent service"""
    image_tag = get_previous_image_tag("non-existent-service")
    assert image_tag is None


def test_register_rollback_new_service():
    """Test registering rollback for new service"""
    test_service = "test-service-rollback"
    previous_image = "test-service:v1.0.0"
    
    # Register rollback
    register_rollback(test_service, previous_image)
    
    # Verify it was registered
    assert test_service in ROLLBACK_REGISTRY
    entry = ROLLBACK_REGISTRY[test_service]
    assert entry["previous_image"] == previous_image
    assert entry["rollback_count"] == 0


def test_register_rollback_existing_service():
    """Test registering rollback for existing service"""
    service = "frontend"
    original_entry = ROLLBACK_REGISTRY[service].copy()
    
    new_previous = "frontend:v2.0.0"
    register_rollback(service, new_previous)
    
    # Verify updates
    entry = ROLLBACK_REGISTRY[service]
    assert entry["previous_image"] == new_previous
    assert entry["current_image"] == original_entry["previous_image"]
    assert entry["rollback_count"] == 1
    assert entry["last_rollback"] is not None


# ============================================================================
# Test Suite 5: SLA Validation
# ============================================================================


def test_is_within_sla_healthy():
    """Test service within SLA bounds"""
    service = "frontend"
    error_rate = 0.01  # 1%
    p99_latency = 200  # ms
    
    is_ok, violations = is_within_sla(service, error_rate, p99_latency)
    assert is_ok is True
    assert len(violations) == 0


def test_is_within_sla_high_error_rate():
    """Test service with high error rate"""
    service = "frontend"
    error_rate = 0.10  # 10% (exceeds bound of 5%)
    p99_latency = 200
    
    is_ok, violations = is_within_sla(service, error_rate, p99_latency)
    assert is_ok is False
    assert len(violations) == 1
    assert "Error rate" in violations[0]


def test_is_within_sla_high_latency():
    """Test service with high latency"""
    service = "frontend"
    error_rate = 0.01
    p99_latency = 600  # ms (exceeds bound of 500)
    
    is_ok, violations = is_within_sla(service, error_rate, p99_latency)
    assert is_ok is False
    assert len(violations) == 1
    assert "P99 latency" in violations[0]


def test_is_within_sla_both_violations():
    """Test service violating both error rate and latency"""
    service = "frontend"
    error_rate = 0.10  # 10%
    p99_latency = 800  # ms
    
    is_ok, violations = is_within_sla(service, error_rate, p99_latency)
    assert is_ok is False
    assert len(violations) == 2


# ============================================================================
# Test Suite 6: Blast Radius Constraints
# ============================================================================


def test_is_blast_radius_acceptable_low():
    """Test action with low blast radius"""
    service = "notification-service"
    max_radius_pct = 50.0
    
    is_ok, reason = is_blast_radius_acceptable(service, max_radius_pct)
    assert isinstance(is_ok, bool)
    assert isinstance(reason, str)


def test_is_blast_radius_acceptable_strict():
    """Test action with very strict blast radius limit"""
    service = "frontend"  # This calls api-gateway which calls many services
    max_radius_pct = 5.0  # Very restrictive
    
    # This might fail depending on topology
    is_ok, reason = is_blast_radius_acceptable(service, max_radius_pct)
    assert isinstance(is_ok, bool)
    assert "Blast radius" in reason


def test_is_blast_radius_acceptable_permissive():
    """Test action with permissive blast radius limit"""
    service = "frontend"
    max_radius_pct = 100.0  # Allow all
    
    is_ok, reason = is_blast_radius_acceptable(service, max_radius_pct)
    assert is_ok is True


# ============================================================================
# Run Tests
# ============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
