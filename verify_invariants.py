#!/usr/bin/env python3
"""
Verification script for policy invariants

Runs comprehensive manual tests to validate invariants functionality
"""

import sys
import os

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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


def test_suite_1_sla_bounds():
    """Test Suite 1: SLA Bounds Loading"""
    print("\n" + "=" * 80)
    print("TEST SUITE 1: SLA Bounds Loading")
    print("=" * 80)
    
    tests_passed = 0
    tests_total = 0
    
    # Test 1: SLA bounds exist
    tests_total += 1
    if SLA_BOUNDS and len(SLA_BOUNDS) > 0:
        print("✓ SLA_BOUNDS loaded successfully")
        print(f"  Loaded {len(SLA_BOUNDS)} services: {', '.join(SLA_BOUNDS.keys())}")
        tests_passed += 1
    else:
        print("✗ FAILED: SLA_BOUNDS not loaded")
    
    # Test 2: All services have required metrics
    tests_total += 1
    required_metrics = {"max_error_rate", "max_p99_latency_ms"}
    all_valid = True
    for service, bounds in SLA_BOUNDS.items():
        if set(bounds.keys()) != required_metrics:
            print(f"✗ Service {service} missing metrics: {bounds.keys()}")
            all_valid = False
    
    if all_valid:
        print("✓ All services have required metrics (max_error_rate, max_p99_latency_ms)")
        tests_passed += 1
    else:
        print("✗ FAILED: Some services missing metrics")
    
    # Test 3: Reasonable SLA values
    tests_total += 1
    all_reasonable = True
    for service, bounds in SLA_BOUNDS.items():
        if not (0 < bounds["max_error_rate"] < 1):
            print(f"✗ Service {service}: error_rate {bounds['max_error_rate']} out of range")
            all_reasonable = False
        if bounds["max_p99_latency_ms"] <= 0:
            print(f"✗ Service {service}: p99_latency {bounds['max_p99_latency_ms']} not positive")
            all_reasonable = False
    
    if all_reasonable:
        print("✓ All SLA values are reasonable")
        tests_passed += 1
    else:
        print("✗ FAILED: Some SLA values unreasonable")
    
    # Test 4: get_sla_bound accessor
    tests_total += 1
    error_rate = get_sla_bound("frontend", "max_error_rate")
    p99_latency = get_sla_bound("frontend", "max_p99_latency_ms")
    
    if error_rate and p99_latency:
        print(f"✓ get_sla_bound works: frontend error_rate={error_rate}, p99={p99_latency}ms")
        tests_passed += 1
    else:
        print("✗ FAILED: get_sla_bound not working")
    
    # Test 5: get_sla_bound with invalid inputs
    tests_total += 1
    invalid_service = get_sla_bound("non-existent", "max_error_rate")
    invalid_metric = get_sla_bound("frontend", "invalid_metric")
    
    if invalid_service is None and invalid_metric is None:
        print("✓ get_sla_bound correctly returns None for invalid inputs")
        tests_passed += 1
    else:
        print("✗ FAILED: get_sla_bound should return None for invalid inputs")
    
    print(f"\nSuite 1 Results: {tests_passed}/{tests_total} passed")
    return tests_passed, tests_total


def test_suite_2_topology():
    """Test Suite 2: Service Topology"""
    print("\n" + "=" * 80)
    print("TEST SUITE 2: Service Topology")
    print("=" * 80)
    
    tests_passed = 0
    tests_total = 0
    
    # Test 1: Topology structure
    tests_total += 1
    if SERVICE_TOPOLOGY and isinstance(SERVICE_TOPOLOGY, dict):
        print(f"✓ SERVICE_TOPOLOGY is a valid dict with {len(SERVICE_TOPOLOGY)} services")
        tests_passed += 1
    else:
        print("✗ FAILED: SERVICE_TOPOLOGY not a dict")
    
    # Test 2: All references are valid
    tests_total += 1
    all_services = set(SERVICE_TOPOLOGY.keys())
    all_valid = True
    
    for service, dependencies in SERVICE_TOPOLOGY.items():
        for dep in dependencies:
            if dep not in all_services:
                print(f"✗ Service {service} references unknown {dep}")
                all_valid = False
    
    if all_valid:
        print("✓ All service references are valid")
        tests_passed += 1
    else:
        print("✗ FAILED: Invalid service references")
    
    # Test 3: No direct cycles
    tests_total += 1
    has_cycles = False
    
    for service, dependencies in SERVICE_TOPOLOGY.items():
        for dep in dependencies:
            if dep in SERVICE_TOPOLOGY:
                reverse_deps = SERVICE_TOPOLOGY.get(dep, [])
                if service in reverse_deps:
                    print(f"✗ Direct cycle detected: {service} ↔ {dep}")
                    has_cycles = True
    
    if not has_cycles:
        print("✓ No direct cycles detected (DAG structure maintained)")
        tests_passed += 1
    else:
        print("✗ FAILED: Cycles detected in topology")
    
    print(f"\nSuite 2 Results: {tests_passed}/{tests_total} passed")
    return tests_passed, tests_total


def test_suite_3_blast_radius():
    """Test Suite 3: Blast Radius Calculation"""
    print("\n" + "=" * 80)
    print("TEST SUITE 3: Blast Radius Calculation")
    print("=" * 80)
    
    tests_passed = 0
    tests_total = 0
    
    # Test 1: Blast radius for all services
    tests_total += 1
    print("Blast radius for all services:")
    all_valid = True
    
    for service in SERVICE_TOPOLOGY.keys():
        radius = blast_radius(service)
        pct = calculate_blast_radius_percentage(service)
        print(f"  {service:25s} → {radius:2d} services ({pct:5.1f}%)")
        
        if not isinstance(radius, int) or radius < 0:
            all_valid = False
        if not (0 <= pct <= 100):
            all_valid = False
    
    if all_valid:
        print("✓ Blast radius calculations valid for all services")
        tests_passed += 1
    else:
        print("✗ FAILED: Invalid blast radius values")
    
    # Test 2: Custom topology
    tests_total += 1
    custom_topology = {
        "a": ["b"],
        "b": ["c"],
        "c": [],
    }
    
    radius_b = blast_radius("b", custom_topology)
    if radius_b == 1:
        print("✓ Custom topology blast radius correct (b affects 1 service)")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Expected radius 1 for 'b', got {radius_b}")
    
    # Test 3: Invalid service
    tests_total += 1
    radius_invalid = blast_radius("non-existent")
    if radius_invalid == 0:
        print("✓ Invalid service returns blast radius 0")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Expected 0 for invalid service, got {radius_invalid}")
    
    print(f"\nSuite 3 Results: {tests_passed}/{tests_total} passed")
    return tests_passed, tests_total


def test_suite_4_rollback_registry():
    """Test Suite 4: Rollback Registry"""
    print("\n" + "=" * 80)
    print("TEST SUITE 4: Rollback Registry")
    print("=" * 80)
    
    tests_passed = 0
    tests_total = 0
    
    # Test 1: Registry structure
    tests_total += 1
    if ROLLBACK_REGISTRY and isinstance(ROLLBACK_REGISTRY, dict):
        print(f"✓ ROLLBACK_REGISTRY initialized with {len(ROLLBACK_REGISTRY)} entries")
        tests_passed += 1
    else:
        print("✗ FAILED: ROLLBACK_REGISTRY not initialized")
    
    # Test 2: Registry entry structure
    tests_total += 1
    required_fields = {"previous_image", "current_image", "rollback_count", "last_rollback"}
    all_valid = True
    
    for service, entry in ROLLBACK_REGISTRY.items():
        if not isinstance(entry, dict):
            print(f"✗ Service {service}: entry not a dict")
            all_valid = False
        elif set(entry.keys()) != required_fields:
            print(f"✗ Service {service}: missing fields {required_fields - set(entry.keys())}")
            all_valid = False
    
    if all_valid:
        print("✓ All registry entries have required fields")
        tests_passed += 1
    else:
        print("✗ FAILED: Invalid registry structure")
    
    # Test 3: get_previous_image_tag accessor
    tests_total += 1
    frontend_image = get_previous_image_tag("frontend")
    if frontend_image and isinstance(frontend_image, str) and ":" in frontend_image:
        print(f"✓ get_previous_image_tag works: frontend → {frontend_image}")
        tests_passed += 1
    else:
        print("✗ FAILED: get_previous_image_tag not working correctly")
    
    # Test 4: Invalid service returns None
    tests_total += 1
    invalid_image = get_previous_image_tag("non-existent")
    if invalid_image is None:
        print("✓ get_previous_image_tag returns None for invalid service")
        tests_passed += 1
    else:
        print("✗ FAILED: Should return None for invalid service")
    
    print(f"\nSuite 4 Results: {tests_passed}/{tests_total} passed")
    return tests_passed, tests_total


def test_suite_5_sla_validation():
    """Test Suite 5: SLA Validation"""
    print("\n" + "=" * 80)
    print("TEST SUITE 5: SLA Validation")
    print("=" * 80)
    
    tests_passed = 0
    tests_total = 0
    
    # Test 1: Healthy service
    tests_total += 1
    is_ok, violations = is_within_sla("frontend", 0.01, 200)
    if is_ok and len(violations) == 0:
        print("✓ Healthy service (1% error, 200ms) passes SLA")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Healthy service should pass SLA, violations: {violations}")
    
    # Test 2: High error rate
    tests_total += 1
    is_ok, violations = is_within_sla("frontend", 0.10, 200)
    if not is_ok and any("Error rate" in v for v in violations):
        print("✓ High error rate (10%) detected as SLA violation")
        tests_passed += 1
    else:
        print("✗ FAILED: High error rate should be detected")
    
    # Test 3: High latency
    tests_total += 1
    is_ok, violations = is_within_sla("frontend", 0.01, 700)
    if not is_ok and any("P99 latency" in v for v in violations):
        print("✓ High latency (700ms vs 500ms bound) detected as SLA violation")
        tests_passed += 1
    else:
        print("✗ FAILED: High latency should be detected")
    
    # Test 4: Both violations
    tests_total += 1
    is_ok, violations = is_within_sla("frontend", 0.10, 800)
    if not is_ok and len(violations) == 2:
        print("✓ Both error rate and latency violations detected")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Expected 2 violations, got {len(violations)}: {violations}")
    
    print(f"\nSuite 5 Results: {tests_passed}/{tests_total} passed")
    return tests_passed, tests_total


def test_suite_6_blast_radius_constraints():
    """Test Suite 6: Blast Radius Constraints"""
    print("\n" + "=" * 80)
    print("TEST SUITE 6: Blast Radius Constraints")
    print("=" * 80)
    
    tests_passed = 0
    tests_total = 0
    
    # Test 1: Permissive limit (100%)
    tests_total += 1
    is_ok, reason = is_blast_radius_acceptable("order-service", 100.0)
    if is_ok:
        print(f"✓ Permissive limit (100%): {reason}")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Permissive limit should accept all actions: {reason}")
    
    # Test 2: Strict limit (very tight)
    tests_total += 1
    is_ok, reason = is_blast_radius_acceptable("order-service", 5.0)
    print(f"  Strict limit (5%): {reason}")
    if isinstance(is_ok, bool):
        print("✓ Strict limit returns valid result")
        tests_passed += 1
    else:
        print("✗ FAILED: Should return boolean")
    
    # Test 3: Moderate limit (50%)
    tests_total += 1
    is_ok, reason = is_blast_radius_acceptable("api-gateway", 50.0)
    print(f"  Moderate limit (50%): {reason}")
    if isinstance(is_ok, bool):
        print("✓ Moderate limit returns valid result")
        tests_passed += 1
    else:
        print("✗ FAILED: Should return boolean")
    
    print(f"\nSuite 6 Results: {tests_passed}/{tests_total} passed")
    return tests_passed, tests_total


def main():
    """Run all test suites"""
    print("\n" + "=" * 80)
    print("NETPILOT POLICY INVARIANTS VERIFICATION")
    print("=" * 80)
    
    # Run all test suites
    total_passed = 0
    total_tests = 0
    
    for test_func in [
        test_suite_1_sla_bounds,
        test_suite_2_topology,
        test_suite_3_blast_radius,
        test_suite_4_rollback_registry,
        test_suite_5_sla_validation,
        test_suite_6_blast_radius_constraints,
    ]:
        passed, total = test_func()
        total_passed += passed
        total_tests += total
    
    # Summary
    print("\n" + "=" * 80)
    print("FINAL RESULTS")
    print("=" * 80)
    print(f"Total: {total_passed}/{total_tests} tests passed")
    
    if total_passed == total_tests:
        print("\n✓ ALL TESTS PASSED")
        return 0
    else:
        print(f"\n✗ {total_tests - total_passed} tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
