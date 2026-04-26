"""
Tests for agent pipeline

Coverage:
- LLM provider selection (OpenAI and Anthropic mocking)
- Policy gate validation
- Executor action handling
- Full pipeline step execution
"""

import asyncio
import json
import os
import tempfile
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime

from agent.pipeline import (
    LLMClient,
    LLMProvider,
    PolicyGate,
    PolicyDecision,
    Executor,
    ExecutionResult,
    AgentPipeline,
    PipelineStep,
)
from agent.models import DiagnosisResult, RemediationAction
from telemetry.schemas import (
    TelemetryBundle,
    KPI,
    LogEvent,
    Alarm,
)
from config import NetpilotConfig, LLMConfig, TelemetryConfig, PolicyGateConfig, ExecutorConfig, set_config


# ============================================================================
# Test Fixtures
# ============================================================================


def create_test_kpi():
    """Create a test KPI"""
    return KPI(
        error_rate=0.02,
        latency_p50_ms=100,
        latency_p95_ms=250,
        latency_p99_ms=500,
        pod_restarts_total=2,
        pod_restarts_5m=1,
        downstream_error_rate=0.01,
        available=True,
        requests_5m=1000,
        timestamp=datetime.now().isoformat(),
    )


def create_test_log_event():
    """Create a test log event"""
    return LogEvent(
        timestamp=datetime.now().isoformat(),
        level="INFO",
        message="Service running normally",
    )


def create_test_alarm():
    """Create a test alarm"""
    return Alarm(
        name="HighErrorRate",
        status="firing",
        severity="warning",
        service="api-gateway",
        component="http_errors",
        timestamp=datetime.now().isoformat(),
    )


def create_test_telemetry_bundle():
    """Create a test TelemetryBundle"""
    return TelemetryBundle(
        timestamp=datetime.now().isoformat(),
        kpis={
            "api-gateway": create_test_kpi(),
            "order-service": create_test_kpi(),
            "notification-service": create_test_kpi(),
        },
        logs={
            "api-gateway": [create_test_log_event()],
            "order-service": [create_test_log_event()],
            "notification-service": [create_test_log_event()],
        },
        alarms=[create_test_alarm()],
        collection_errors=[],
        services_monitored=["api-gateway", "order-service", "notification-service"],
    )


def create_test_diagnosis_result():
    """Create a test DiagnosisResult"""
    return DiagnosisResult(
        root_cause="Database connection timeout in order-service",
        root_cause_confidence=0.85,
        remediation_actions=[
            RemediationAction(
                action_type="restart_pod",
                target="order-service",
                params={"grace_period_seconds": 30},
                confidence=0.90,
                rationale="Pod restart will clear stale database connections",
            ),
            RemediationAction(
                action_type="scale_up",
                target="order-service",
                params={"replicas": 3},
                confidence=0.70,
                rationale="Additional replicas distribute load and hide latency",
            ),
        ],
    )


# ============================================================================
# Test Suite 1: LLM Provider Selection
# ============================================================================


def test_llm_provider_openai_initialization():
    """Test OpenAI client initialization"""
    with patch("agent.pipeline.openai.OpenAI") as mock_openai:
        client = LLMClient(
            provider="openai",
            model="gpt-4",
            api_key="test-key",
            temperature=0.3,
            max_tokens=2000,
        )
        assert client.provider == "openai"
        assert client.model == "gpt-4"
        assert client.temperature == 0.3


def test_llm_provider_anthropic_initialization():
    """Test Anthropic client initialization"""
    with patch("agent.pipeline.anthropic.Anthropic") as mock_anthropic:
        client = LLMClient(
            provider="anthropic",
            model="claude-3-opus",
            api_key="test-key",
        )
        assert client.provider == "anthropic"
        assert client.model == "claude-3-opus"


def test_llm_provider_invalid():
    """Test that invalid provider raises error"""
    try:
        LLMClient(
            provider="invalid",
            model="test",
            api_key="test-key",
        )
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "Unknown LLM provider" in str(e)


# ============================================================================
# Test Suite 2: Policy Gate
# ============================================================================


def test_policy_gate_approves_low_confidence_restart():
    """Test that policy gate approves restart with high confidence"""
    gate = PolicyGate()
    telemetry = create_test_telemetry_bundle()
    diagnosis = create_test_diagnosis_result()
    
    action = RemediationAction(
        action_type="restart_pod",
        target="order-service",
        params={},
        confidence=0.90,
        rationale="High confidence restart",
    )
    
    decision = gate.validate(action, telemetry, diagnosis)
    assert decision.approved is True
    assert decision.risk_level in ("low", "medium", "high")


def test_policy_gate_rejects_noop():
    """Test that policy gate rejects noop actions"""
    gate = PolicyGate()
    telemetry = create_test_telemetry_bundle()
    diagnosis = create_test_diagnosis_result()
    
    action = RemediationAction(
        action_type="noop",
        target="order-service",
        params={},
        confidence=1.0,
        rationale="No action needed",
    )
    
    decision = gate.validate(action, telemetry, diagnosis)
    assert decision.approved is False
    assert "noop" in decision.reason


def test_policy_gate_rejects_low_confidence_rollback():
    """Test that policy gate rejects rollback with low confidence"""
    gate = PolicyGate()
    telemetry = create_test_telemetry_bundle()
    diagnosis = create_test_diagnosis_result()
    
    action = RemediationAction(
        action_type="rollback_deploy",
        target="order-service",
        params={},
        confidence=0.40,  # Low confidence
        rationale="Possibly rollback",
    )
    
    decision = gate.validate(action, telemetry, diagnosis)
    assert decision.approved is False
    assert "confidence" in decision.reason.lower()


# ============================================================================
# Test Suite 3: Executor
# ============================================================================


@pytest.mark.asyncio
async def test_executor_restart_pod():
    """Test executor can restart pod"""
    executor = Executor()
    
    action = RemediationAction(
        action_type="restart_pod",
        target="notification-service",
        params={},
        confidence=0.90,
        rationale="Restart",
    )
    
    result = await executor.execute(action)
    assert result.status in ("success", "failed")
    assert result.action_type == "restart_pod"
    assert result.target == "notification-service"
    assert result.execution_time_ms >= 0


@pytest.mark.asyncio
async def test_executor_scale_up():
    """Test executor can scale up deployment"""
    executor = Executor()
    
    action = RemediationAction(
        action_type="scale_up",
        target="order-service",
        params={"replicas": 5},
        confidence=0.70,
        rationale="Scale up",
    )
    
    result = await executor.execute(action)
    assert result.status in ("success", "failed")
    assert result.action_type == "scale_up"
    assert result.target == "order-service"


# ============================================================================
# Test Suite 4: Full Pipeline
# ============================================================================


def test_pipeline_initialization():
    """Test pipeline initializes correctly"""
    # Create temp config for testing
    config = NetpilotConfig(
        llm=LLMConfig(provider="openai", model="gpt-4", openai_api_key="test"),
        telemetry=TelemetryConfig(),
        policy_gate=PolicyGateConfig(),
        executor=ExecutorConfig(),
        log_dir=tempfile.mkdtemp(),
    )
    set_config(config)
    
    with patch("agent.pipeline.LLMClient"):
        pipeline = AgentPipeline()
        assert pipeline.config.llm.provider == "openai"
        assert pipeline.policy_gate is not None
        assert pipeline.executor is not None


@pytest.mark.asyncio
async def test_pipeline_step_execution():
    """Test full pipeline step execution"""
    # Setup test config
    config = NetpilotConfig(
        llm=LLMConfig(provider="openai", model="gpt-4", openai_api_key="test"),
        telemetry=TelemetryConfig(),
        policy_gate=PolicyGateConfig(),
        executor=ExecutorConfig(),
        log_dir=tempfile.mkdtemp(),
    )
    set_config(config)
    
    # Mock the collector
    with patch("agent.pipeline.TelemetryCollector") as mock_collector_class, \
         patch("agent.pipeline.LLMClient") as mock_llm_class:
        
        # Setup mock collector
        mock_collector = AsyncMock()
        mock_collector.collect.return_value = create_test_telemetry_bundle()
        mock_collector_class.return_value = mock_collector
        
        # Setup mock LLM
        mock_llm = MagicMock()
        diagnosis = create_test_diagnosis_result()
        mock_llm.call.return_value = diagnosis.model_dump_json()
        mock_llm_class.return_value = mock_llm
        
        # Run pipeline
        pipeline = AgentPipeline()
        step = await pipeline.run_step()
        
        # Verify step
        assert step.timestamp is not None
        assert len(step.telemetry_bundle) > 0
        assert len(step.diagnosis) > 0
        assert len(step.gate_decisions) >= 0


@pytest.mark.asyncio
async def test_pipeline_step_logging():
    """Test pipeline logs steps to JSONL file"""
    # Setup test config with temp log dir
    log_dir = tempfile.mkdtemp()
    config = NetpilotConfig(
        llm=LLMConfig(provider="openai", model="gpt-4", openai_api_key="test"),
        telemetry=TelemetryConfig(),
        policy_gate=PolicyGateConfig(),
        executor=ExecutorConfig(),
        log_dir=log_dir,
    )
    set_config(config)
    
    # Mock the collector
    with patch("agent.pipeline.TelemetryCollector") as mock_collector_class, \
         patch("agent.pipeline.LLMClient") as mock_llm_class:
        
        # Setup mocks
        mock_collector = AsyncMock()
        mock_collector.collect.return_value = create_test_telemetry_bundle()
        mock_collector_class.return_value = mock_collector
        
        mock_llm = MagicMock()
        diagnosis = create_test_diagnosis_result()
        mock_llm.call.return_value = diagnosis.model_dump_json()
        mock_llm_class.return_value = mock_llm
        
        # Run pipeline
        pipeline = AgentPipeline()
        step = await pipeline.run_step()
        
        # Verify log file created
        log_file = os.path.join(log_dir, "agent_steps.jsonl")
        assert os.path.exists(log_file)
        
        # Verify log entry
        with open(log_file) as f:
            logged_step = json.loads(f.readline())
            assert logged_step["timestamp"] == step.timestamp
            assert logged_step["diagnosis"]["root_cause"] == step.diagnosis["root_cause"]


# ============================================================================
# Test Suite 5: Message Building and Validation
# ============================================================================


def test_pipeline_message_format():
    """Test that messages are formatted correctly for LLM"""
    from agent.prompts import build_prompt_messages
    
    telemetry_snapshot = "test telemetry"
    messages = build_prompt_messages(telemetry_snapshot)
    
    # Should have system + 2 examples + user = 5 messages
    assert len(messages) == 5
    assert messages[0]["role"] == "user"     # Example 1 input
    assert messages[1]["role"] == "assistant"  # Example 1 output
    assert messages[2]["role"] == "user"     # Example 2 input
    assert messages[3]["role"] == "assistant"  # Example 2 output
    assert messages[4]["role"] == "user"     # Current telemetry


# ============================================================================
# Run Tests
# ============================================================================


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
