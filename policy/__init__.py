"""
Policy module for Netpilot

Provides SLA validation, service topology, blast radius calculation, and action validation.

Components:
- invariants: SLA bounds, rollback registry, blast radius
- gate: PolicyGate validation engine
"""

from .invariants import (
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

from .gate import (
    PolicyGate,
    explain_policy_decision,
    create_audit_log_entry,
)

__all__ = [
    # Invariants
    "SLA_BOUNDS",
    "ROLLBACK_REGISTRY",
    "SERVICE_TOPOLOGY",
    "blast_radius",
    "calculate_blast_radius_percentage",
    "get_sla_bound",
    "get_previous_image_tag",
    "register_rollback",
    "is_within_sla",
    "is_blast_radius_acceptable",
    # Gate
    "PolicyGate",
    "explain_policy_decision",
    "create_audit_log_entry",
]
