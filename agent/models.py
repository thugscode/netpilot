"""
Pydantic models for agent pipeline.
"""

from typing import List, Dict, Any, Literal
from datetime import datetime
from pydantic import BaseModel, Field


class RemediationAction(BaseModel):
    """A single remediation action."""
    
    action_type: Literal["restart_pod", "scale_up", "reroute_traffic", "rollback_deploy", "noop"] = Field(
        description="Type of remediation action"
    )
    target: str = Field(
        description="Target service name"
    )
    params: Dict[str, Any] = Field(
        default_factory=dict,
        description="Action-specific parameters (e.g., replica_count for scale_up)"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in effectiveness (0.0-1.0)"
    )
    rationale: str = Field(
        description="One sentence explanation of why this action is recommended"
    )
    
    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "action_type": "restart_pod",
                    "target": "notification-service",
                    "params": {"grace_period_seconds": 30},
                    "confidence": 0.85,
                    "rationale": "Pod restart will clear accumulated state and reconnect to database."
                },
                {
                    "action_type": "scale_up",
                    "target": "order-service",
                    "params": {"replica_count": 3, "wait_seconds": 60},
                    "confidence": 0.72,
                    "rationale": "Scaling up can distribute load and mitigate cascading failures."
                }
            ]
        }


class DiagnosisResult(BaseModel):
    """LLM diagnosis result with root cause and remediation actions."""
    
    root_cause: str = Field(
        description="Most likely root cause of the system failure"
    )
    root_cause_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in root cause diagnosis (0.0-1.0)"
    )
    remediation_actions: List[RemediationAction] = Field(
        max_items=5,
        description="Up to 5 remediation actions ranked by expected impact"
    )
    
    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "root_cause": "notification-service pod crash due to database connection loss",
                    "root_cause_confidence": 0.92,
                    "remediation_actions": [
                        {
                            "action_type": "restart_pod",
                            "target": "notification-service",
                            "params": {"grace_period_seconds": 30},
                            "confidence": 0.88,
                            "rationale": "Pod restart will clear stale state and reconnect to database."
                        },
                        {
                            "action_type": "scale_up",
                            "target": "order-service",
                            "params": {"replica_count": 3},
                            "confidence": 0.65,
                            "rationale": "Scaling upstream service reduces cascading failures."
                        }
                    ]
                }
            ]
        }
