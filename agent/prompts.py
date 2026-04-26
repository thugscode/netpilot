"""
LLM prompts for diagnosis and remediation.

System prompt + few-shot examples for Kubernetes failure diagnosis.
"""

import json
from typing import Dict, Any

# System prompt - instructs the model on its task
SYSTEM_PROMPT = """You are a Kubernetes troubleshooter specializing in diagnosing microservice failures.

YOUR TASK:
1. Analyze the provided telemetry bundle (system state, metrics, logs, alarms)
2. Identify the most likely root cause of any failures
3. Recommend up to 5 remediation actions ranked by expected impact
4. Return ONLY a valid JSON object matching the DiagnosisResult schema

ROOT CAUSE ANALYSIS:
- Look for patterns: cascading failures (check downstream services), pod crashes (check restart counts), resource exhaustion, error rates
- Consider dependencies: if a downstream service fails, upstream services will fail too
- Examine error logs for concrete failure reasons
- Rate your confidence (0.0 = pure guess, 1.0 = certain)

REMEDIATION ACTIONS:
Rank actions by expected impact (most impactful first). Each action must specify:
- action_type: One of:
  * restart_pod: Restart failing service (clears state, reconnects resources)
  * scale_up: Increase replicas (distributes load, mitigates cascading failure)
  * reroute_traffic: Send traffic to healthy services (temporary mitigation)
  * rollback_deploy: Revert to previous version (if recent deployment failed)
  * noop: No action needed (system will self-recover)
- target: Service name affected
- params: Action-specific parameters (e.g., {"grace_period_seconds": 30})
- confidence: 0.0-1.0 effectiveness score
- rationale: One sentence explaining the action

CRITICAL RULES:
1. Return ONLY valid JSON (no prose, no markdown, no explanations outside JSON)
2. Validate JSON structure before returning
3. Action ranking must be by expected impact (most→least impactful)
4. Confidence scores must be realistic (0.0-1.0 range)
5. Never recommend actions on healthy services without strong justification
6. If system appears healthy, use action_type: "noop"

JSON SCHEMA (STRICT):
{
  "root_cause": "string (max 200 chars)",
  "root_cause_confidence": 0.0-1.0,
  "remediation_actions": [
    {
      "action_type": "restart_pod|scale_up|reroute_traffic|rollback_deploy|noop",
      "target": "service_name",
      "params": {object},
      "confidence": 0.0-1.0,
      "rationale": "string (one sentence)"
    }
  ]
}
"""

# Few-shot example 1: Pod crash scenario
EXAMPLE_1_INPUT = """{
  "snapshot": {
    "timestamp": "2026-04-27T10:15:30.123456",
    "health": "DEGRADED",
    "collection_ms": 125
  },
  "critical_issues": [
    {
      "alert": "ServiceDown",
      "service": "notification-service",
      "summary": "Service notification-service is not responding"
    }
  ],
  "warnings": [
    {
      "alert": "HighErrorRate",
      "service": "order-service",
      "summary": "HTTP error rate 8.5% exceeds threshold of 5%"
    }
  ],
  "unhealthy_services": {
    "notification-service": {
      "available": false,
      "error_rate_pct": 100.0,
      "p99_ms": null,
      "restarts_5m": 3
    },
    "order-service": {
      "available": true,
      "error_rate_pct": 8.5,
      "p99_ms": 450,
      "restarts_5m": 0
    }
  },
  "high_latency": {},
  "healthy_services": {
    "frontend": {"error_rate_pct": 0.2, "requests_5m": 450, "p99_ms": 150},
    "api-gateway": {"error_rate_pct": 0.5, "requests_5m": 400, "p99_ms": 200},
    "inventory-service": {"error_rate_pct": 0.0, "requests_5m": 200, "p99_ms": 80}
  },
  "recent_errors": [
    {
      "service": "notification-service",
      "level": "CRITICAL",
      "message": "Database connection lost: connection refused at postgres:5432",
      "timestamp": "2026-04-27T10:15:25.123456"
    },
    {
      "service": "order-service",
      "level": "ERROR",
      "message": "Downstream call to notification-service failed: 503 Service Unavailable",
      "timestamp": "2026-04-27T10:15:26.123456"
    }
  ]
}"""

EXAMPLE_1_OUTPUT = """{
  "root_cause": "notification-service pod crashed due to database connection loss, causing cascading failures in order-service",
  "root_cause_confidence": 0.92,
  "remediation_actions": [
    {
      "action_type": "restart_pod",
      "target": "notification-service",
      "params": {"grace_period_seconds": 30},
      "confidence": 0.88,
      "rationale": "Pod restart will clear stale database connection state and allow reconnection."
    },
    {
      "action_type": "scale_up",
      "target": "order-service",
      "params": {"replica_count": 3, "wait_seconds": 60},
      "confidence": 0.65,
      "rationale": "Scaling upstream service reduces cascading failures while notification-service recovers."
    },
    {
      "action_type": "reroute_traffic",
      "target": "order-service",
      "params": {"excluded_downstreams": ["notification-service"]},
      "confidence": 0.5,
      "rationale": "Temporary traffic reroute allows order processing without notification-service dependency."
    },
    {
      "action_type": "noop",
      "target": "notification-service",
      "params": {},
      "confidence": 0.4,
      "rationale": "System may self-recover if database becomes available again."
    }
  ]
}"""

# Few-shot example 2: Link degradation scenario
EXAMPLE_2_INPUT = """{
  "snapshot": {
    "timestamp": "2026-04-27T10:20:45.654321",
    "health": "DEGRADED",
    "collection_ms": 210
  },
  "critical_issues": [],
  "warnings": [
    {
      "alert": "HighLatency",
      "service": "api-gateway",
      "summary": "P99 latency 750ms exceeds threshold of 500ms"
    },
    {
      "alert": "HighDownstreamFailureRate",
      "service": "api-gateway",
      "summary": "Downstream error rate 12% exceeds threshold of 10%"
    }
  ],
  "unhealthy_services": {
    "api-gateway": {
      "available": true,
      "error_rate_pct": 2.5,
      "p99_ms": 750,
      "restarts_5m": 0
    }
  },
  "high_latency": {
    "api-gateway": {"p99_ms": 750, "p95_ms": 600},
    "order-service": {"p99_ms": 550, "p95_ms": 450}
  },
  "healthy_services": {
    "frontend": {"error_rate_pct": 0.1, "requests_5m": 500, "p99_ms": 180},
    "inventory-service": {"error_rate_pct": 0.0, "requests_5m": 250, "p99_ms": 95},
    "notification-service": {"error_rate_pct": 0.3, "requests_5m": 180, "p99_ms": 200}
  },
  "recent_errors": [
    {
      "service": "api-gateway",
      "level": "WARNING",
      "message": "Timeout connecting to order-service: request took 750ms (threshold: 500ms)",
      "timestamp": "2026-04-27T10:20:40.654321"
    },
    {
      "service": "order-service",
      "level": "WARNING",
      "message": "Network latency detected: packet loss 5%, latency 200ms",
      "timestamp": "2026-04-27T10:20:42.654321"
    }
  ]
}"""

EXAMPLE_2_OUTPUT = """{
  "root_cause": "Network degradation (latency and packet loss) between api-gateway and order-service causing timeout-induced failures",
  "root_cause_confidence": 0.78,
  "remediation_actions": [
    {
      "action_type": "scale_up",
      "target": "order-service",
      "params": {"replica_count": 4, "wait_seconds": 90},
      "confidence": 0.72,
      "rationale": "Additional replicas improve availability during network degradation."
    },
    {
      "action_type": "scale_up",
      "target": "api-gateway",
      "params": {"replica_count": 3},
      "confidence": 0.65,
      "rationale": "Scaling api-gateway reduces per-replica load and mitigates timeout issues."
    },
    {
      "action_type": "reroute_traffic",
      "target": "api-gateway",
      "params": {"circuit_breaker_threshold": 0.15},
      "confidence": 0.6,
      "rationale": "Circuit breaker prevents cascading failures by failing fast on timeout."
    },
    {
      "action_type": "noop",
      "target": "order-service",
      "params": {},
      "confidence": 0.5,
      "rationale": "Network degradation is transient; waiting may resolve the issue naturally."
    }
  ]
}"""


def get_system_prompt() -> str:
    """Return the system prompt for diagnosis."""
    return SYSTEM_PROMPT


def get_few_shot_examples() -> Dict[str, Dict[str, str]]:
    """Return few-shot examples (input + output pairs)."""
    return {
        "pod_crash": {
            "scenario": "Pod crash with cascading failure",
            "input": EXAMPLE_1_INPUT,
            "output": EXAMPLE_1_OUTPUT,
        },
        "link_degrade": {
            "scenario": "Network degradation causing latency",
            "input": EXAMPLE_2_INPUT,
            "output": EXAMPLE_2_OUTPUT,
        },
    }


def format_user_prompt(telemetry_context: str) -> str:
    """
    Format the user message for diagnosis.
    
    Args:
        telemetry_context: Compact telemetry JSON from formatter
        
    Returns:
        User message to send to LLM
    """
    return f"""Diagnose the following Kubernetes system state and provide remediation actions:

{telemetry_context}"""


def build_prompt_messages(telemetry_context: str) -> list:
    """
    Build the complete message list for the LLM.
    
    Includes system prompt + few-shot examples + user input.
    
    Args:
        telemetry_context: Compact telemetry JSON
        
    Returns:
        List of message dicts suitable for Claude/OpenAI API
    """
    examples = get_few_shot_examples()
    messages = []
    
    # Example 1: Pod crash
    example1 = examples["pod_crash"]
    messages.append({
        "role": "user",
        "content": f"Example 1 - {example1['scenario']}:\n\n{example1['input']}"
    })
    messages.append({
        "role": "assistant",
        "content": example1['output']
    })
    
    # Example 2: Link degradation
    example2 = examples["link_degrade"]
    messages.append({
        "role": "user",
        "content": f"Example 2 - {example2['scenario']}:\n\n{example2['input']}"
    })
    messages.append({
        "role": "assistant",
        "content": example2['output']
    })
    
    # Actual user input
    messages.append({
        "role": "user",
        "content": format_user_prompt(telemetry_context)
    })
    
    return messages


def validate_diagnosis_json(json_str: str) -> bool:
    """
    Validate that the LLM response is valid DiagnosisResult JSON.
    
    Args:
        json_str: JSON string from LLM
        
    Returns:
        True if valid, False otherwise
    """
    try:
        data = json.loads(json_str)
        # Check required fields
        assert "root_cause" in data, "Missing root_cause"
        assert "root_cause_confidence" in data, "Missing root_cause_confidence"
        assert "remediation_actions" in data, "Missing remediation_actions"
        assert isinstance(data["remediation_actions"], list), "remediation_actions must be list"
        assert len(data["remediation_actions"]) <= 5, "Too many actions (max 5)"
        
        # Validate each action
        valid_action_types = {
            "restart_pod", "scale_up", "reroute_traffic", "rollback_deploy", "noop"
        }
        for action in data["remediation_actions"]:
            assert "action_type" in action, f"Action missing action_type: {action}"
            assert action["action_type"] in valid_action_types, f"Invalid action_type: {action['action_type']}"
            assert "target" in action, f"Action missing target: {action}"
            assert "confidence" in action, f"Action missing confidence: {action}"
            assert 0.0 <= action["confidence"] <= 1.0, f"Invalid confidence: {action['confidence']}"
            assert "rationale" in action, f"Action missing rationale: {action}"
        
        return True
    except (json.JSONDecodeError, AssertionError, KeyError) as e:
        return False
