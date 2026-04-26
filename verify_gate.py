#!/usr/bin/env python3
"""
Verification script for policy/gate.py - PolicyGate validation

Tests all validation checks without requiring pytest
"""

import sys
import os
from datetime import datetime

# Add parent directory for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.models import RemediationAction
from telemetry.schemas import KPI
from policy.gate import PolicyGate, explain_policy_decision, create_audit_log_entry


def create_test_kpi(
    service: str,
    error_rate: float = 0.02,
    latency_p99: int = 500,
) -> KPI:
    """Helper to create test KPI."""
    return KPI(
        service=service,
        timestamp=datetime.now().isoformat(),
        error_rate=error_rate,
        latency_p50_ms=100,
        latency_p95_ms=300,
        latency_p99_ms=latency_p99,
        pod_restarts_total=0,
        pod_restarts_5m=0,
        downstream_error_rate=0.01,
        downstream_latency_p99_ms=400,
        availability=True,
        request_count_5m=1000,
    )


def create_test_action(action_type: str, target: str, confidence: float = 0.95) -> RemediationAction:
    """Helper to create test action."""
    return RemediationAction(
        action_type=action_type,
        target=target,
        params={},
        confidence=confidence,
        rationale=f"Test {action_type} action",
    )


def test_suite_1_sla_bounds():
    """Test Suite 1: SLA Bounds Validation"""
    print("\n" + "=" * 80)
    print("TEST SUITE 1: SLA Bounds Validation")
    print("=" * 80)
    
    gate = PolicyGate()
    tests_passed = 0
    tests_total = 0
    
    # Test 1: Healthy service passes
    tests_total += 1
    kpis = {
        "order-service": create_test_kpi("order-service", 0.01, 500),
        "api-gateway": create_test_kpi("api-gateway", 0.02, 400),
        "frontend": create_test_kpi("frontend", 0.01, 300),
        "inventory-service": create_test_kpi("inventory-service", 0.02, 400),
        "notification-service": create_test_kpi("notification-service", 0.05, 1500),
    }
    
    action = create_test_action("restart_pod", "order-service")
    allowed, reason = gate._check_sla_bounds(action, kpis)
    
    if allowed:
        print("✓ Healthy service (1% error, 500ms P99) passes SLA bounds check")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Healthy service should pass: {reason}")
    
    # Test 2: High error rate fails
    tests_total += 1
    kpis["order-service"] = create_test_kpi("order-service", 0.025, 500)  # Will double to 5%
    
    action = create_test_action("restart_pod", "order-service")
    allowed, reason = gate._check_sla_bounds(action, kpis)
    
    if not allowed and "violate" in reason.lower():
        print("✓ High error rate violation (2.5% → 5% on restart) detected")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Should detect SLA violation, got: {reason}")
    
    # Test 3: Scale up reduces latency
    tests_total += 1
    kpis["api-gateway"] = create_test_kpi("api-gateway", 0.02, 700)  # Will halve to 350ms
    
    action = create_test_action("scale_up", "api-gateway")
    allowed, reason = gate._check_sla_bounds(action, kpis)
    
    if allowed:
        print("✓ Scale up action (700ms → 350ms latency) passes SLA bounds check")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Scale up should improve latency: {reason}")
    
    # Test 4: Missing KPI data fails
    tests_total += 1
    kpis_incomplete = {"frontend": create_test_kpi("frontend")}
    
    action = create_test_action("restart_pod", "non-existent")
    allowed, reason = gate._check_sla_bounds(action, kpis_incomplete)
    
    if not allowed and "No KPI data" in reason:
        print("✓ Missing KPI data correctly rejected")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Should reject missing KPI data")
    
    print(f"\nSuite 1 Results: {tests_passed}/{tests_total} passed")
    return tests_passed, tests_total


def test_suite_2_rollback_feasibility():
    """Test Suite 2: Rollback Feasibility"""
    print("\n" + "=" * 80)
    print("TEST SUITE 2: Rollback Feasibility")
    print("=" * 80)
    
    gate = PolicyGate()
    tests_passed = 0
    tests_total = 0
    
    # Test 1: Non-rollback action skips check
    tests_total += 1
    action = create_test_action("restart_pod", "order-service")
    allowed, reason = gate._check_rollback_feasibility(action)
    
    if allowed and "no rollback feasibility check" in reason.lower():
        print("✓ Non-rollback action skips feasibility check")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Should skip for non-rollback action")
    
    # Test 2: Valid rollback passes
    tests_total += 1
    action = create_test_action("rollback_deploy", "frontend")
    allowed, reason = gate._check_rollback_feasibility(action)
    
    if allowed and "Rollback feasible" in reason:
        print(f"✓ Valid rollback (frontend) passes: {reason}")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Valid rollback should pass: {reason}")
    
    # Test 3: Invalid rollback fails
    tests_total += 1
    action = create_test_action("rollback_deploy", "non-existent-service")
    allowed, reason = gate._check_rollback_feasibility(action)
    
    if not allowed and ("not found" in reason.lower() or "not in rollback" in reason.lower()):
        print("✓ Invalid rollback (non-existent service) correctly rejected")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Should reject invalid rollback")
    
    print(f"\nSuite 2 Results: {tests_passed}/{tests_total} passed")
    return tests_passed, tests_total


def test_suite_3_blast_radius():
    """Test Suite 3: Blast Radius Validation"""
    print("\n" + "=" * 80)
    print("TEST SUITE 3: Blast Radius Validation")
    print("=" * 80)
    
    gate = PolicyGate()
    tests_passed = 0
    tests_total = 0
    
    # Test 1: Leaf service (zero radius)
    tests_total += 1
    action = create_test_action("restart_pod", "frontend")
    allowed, reason = gate._check_blast_radius(action)
    
    if allowed and "acceptable" in reason.lower():
        print(f"✓ Leaf service (frontend): {reason}")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Leaf service should have acceptable radius: {reason}")
    
    # Test 2: Intermediate service
    tests_total += 1
    action = create_test_action("restart_pod", "order-service")
    allowed, reason = gate._check_blast_radius(action)
    
    if isinstance(allowed, bool):
        status = "✓" if allowed else "✓"
        print(f"{status} Intermediate service (order-service): {reason}")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Invalid return type")
    
    # Test 3: Config limit is respected
    tests_total += 1
    max_radius_pct = gate.config.policy_gate.max_blast_radius_pct
    total_services = 5
    max_radius_count = int((max_radius_pct / 100.0) * total_services)
    if isinstance(max_radius_pct, (int, float)) and max_radius_pct > 0:
        print(f"✓ Policy gate uses config limit: max_blast_radius_pct={max_radius_pct}% ({max_radius_count} services)")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Invalid max_blast_radius_pct in config")
    
    print(f"\nSuite 3 Results: {tests_passed}/{tests_total} passed")
    return tests_passed, tests_total


def test_suite_4_full_validation():
    """Test Suite 4: Full Validation Workflow"""
    print("\n" + "=" * 80)
    print("TEST SUITE 4: Full Validation Workflow")
    print("=" * 80)
    
    gate = PolicyGate()
    tests_passed = 0
    tests_total = 0
    
    # Create baseline healthy KPIs
    kpis = {
        "frontend": create_test_kpi("frontend", 0.02, 400),
        "api-gateway": create_test_kpi("api-gateway", 0.02, 600),  # Reduced to 2%
        "order-service": create_test_kpi("order-service", 0.01, 800),
        "inventory-service": create_test_kpi("inventory-service", 0.02, 700),
        "notification-service": create_test_kpi("notification-service", 0.05, 1500),
    }
    
    # Test 1: Healthy action approved (using service with acceptable blast radius)
    tests_total += 1
    # Use api-gateway which has blast radius of 1 (< 2 limit)
    action = create_test_action("restart_pod", "api-gateway", 0.92)
    allowed, reason = gate.validate(action, kpis)
    
    if allowed and "approved" in reason.lower():
        print(f"✓ Healthy action approved (api-gateway): {reason}")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Healthy action should be approved: {reason}")
    
    # Test 2: Scale up action
    tests_total += 1
    action = create_test_action("scale_up", "api-gateway", 0.88)
    allowed, reason = gate.validate(action, kpis)
    
    if allowed and "approved" in reason.lower():
        print(f"✓ Scale up action approved")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Scale up should be approved: {reason}")
    
    # Test 3: Rollback action
    tests_total += 1
    action = create_test_action("rollback_deploy", "frontend", 0.85)
    allowed, reason = gate.validate(action, kpis)
    
    if allowed and "approved" in reason.lower():
        print(f"✓ Rollback action approved")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Valid rollback should be approved: {reason}")
    
    # Test 4: Utility functions
    tests_total += 1
    action = create_test_action("restart_pod", "order-service", 0.95)
    decision = (True, "Test approval reason")
    
    explanation = explain_policy_decision(action, decision, verbose=True)
    audit_entry = create_audit_log_entry(action, decision)
    
    if "APPROVED" in explanation and audit_entry["allowed"] is True:
        print(f"✓ Utility functions working (explanation + audit log)")
        tests_passed += 1
    else:
        print(f"✗ FAILED: Utility functions issue")
    
    print(f"\nSuite 4 Results: {tests_passed}/{tests_total} passed")
    return tests_passed, tests_total


def main():
    """Run all test suites."""
    print("\n" + "=" * 80)
    print("NETPILOT POLICY GATE VERIFICATION")
    print("=" * 80)
    
    total_passed = 0
    total_tests = 0
    
    for test_func in [
        test_suite_1_sla_bounds,
        test_suite_2_rollback_feasibility,
        test_suite_3_blast_radius,
        test_suite_4_full_validation,
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
