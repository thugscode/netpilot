"""
Policy module for Netpilot

Provides SLA validation, service topology, and blast radius calculation.

Components:
- invariants: SLA bounds, rollback registry, blast radius
- gate: Policy validation engine (in PolicyGate from pipeline.py)
"""

from .invariants import (
    SLA_BOUNDS,
    ROLLBACK_REGISTRY,
    SERVICE_TOPOLOGY,
    blast_radius,
    get_sla_bound,
    get_previous_image_tag,
    register_rollback,
)

__all__ = [
    "SLA_BOUNDS",
    "ROLLBACK_REGISTRY",
    "SERVICE_TOPOLOGY",
    "blast_radius",
    "get_sla_bound",
    "get_previous_image_tag",
    "register_rollback",
]
