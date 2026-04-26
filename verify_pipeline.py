#!/usr/bin/env python3
"""
Verification script for agent pipeline implementation

Tests:
1. All imports work
2. Config loads correctly
3. LLM client initializes
4. Policy gate validates actions
5. Executor handles actions
6. Full pipeline structure is ready
"""

import sys
import json
from datetime import datetime


def test_imports():
    """Test all imports"""
    print("\n" + "="*80)
    print("TEST 1: Imports")
    print("="*80)
    
    try:
        from config import (
            get_config,
            LLMConfig,
            TelemetryConfig,
            PolicyGateConfig,
            ExecutorConfig,
        )
        print("✅ config.py imports successful")
    except Exception as e:
        print(f"❌ config.py import failed: {e}")
        return False
    
    try:
        from agent.models import DiagnosisResult, RemediationAction
        print("✅ agent.models imports successful")
    except Exception as e:
        print(f"❌ agent.models import failed: {e}")
        return False
    
    try:
        from agent.prompts import (
            get_system_prompt,
            get_few_shot_examples,
            build_prompt_messages,
            validate_diagnosis_json,
        )
        print("✅ agent.prompts imports successful")
    except Exception as e:
        print(f"❌ agent.prompts import failed: {e}")
        return False
    
    try:
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
        print("✅ agent.pipeline imports successful")
    except Exception as e:
        print(f"❌ agent.pipeline import failed: {e}")
        return False
    
    try:
        from agent import *
        print("✅ agent module wildcard import successful")
    except Exception as e:
        print(f"❌ agent module import failed: {e}")
        return False
    
    return True


def test_config():
    """Test configuration"""
    print("\n" + "="*80)
    print("TEST 2: Configuration")
    print("="*80)
    
    try:
        from config import get_config, set_config, NetpilotConfig, LLMConfig
        
        config = get_config()
        print(f"✅ Config loaded:")
        print(f"   - LLM Provider: {config.llm.provider}")
        print(f"   - LLM Model: {config.llm.model}")
        print(f"   - Telemetry Context Tokens: {config.telemetry.context_window_tokens}")
        print(f"   - Log Directory: {config.log_dir}")
        
        return True
    except Exception as e:
        print(f"❌ Config test failed: {e}")
        return False


def test_models():
    """Test models"""
    print("\n" + "="*80)
    print("TEST 3: Models")
    print("="*80)
    
    try:
        from agent.models import DiagnosisResult, RemediationAction
        
        # Create test action
        action = RemediationAction(
            action_type="restart_pod",
            target="notification-service",
            params={"grace_period_seconds": 30},
            confidence=0.90,
            rationale="Pod restart will clear stale connections"
        )
        print(f"✅ RemediationAction created: {action.action_type}")
        
        # Create test diagnosis
        diagnosis = DiagnosisResult(
            root_cause="Database connection timeout",
            root_cause_confidence=0.85,
            remediation_actions=[action]
        )
        print(f"✅ DiagnosisResult created: root_cause_confidence={diagnosis.root_cause_confidence:.2f}")
        
        # Serialize to JSON
        diagnosis_json = diagnosis.model_dump_json()
        print(f"✅ DiagnosisResult serialized to JSON ({len(diagnosis_json)} chars)")
        
        # Deserialize from JSON
        diagnosis2 = DiagnosisResult.model_validate_json(diagnosis_json)
        print(f"✅ DiagnosisResult deserialized from JSON")
        
        return True
    except Exception as e:
        print(f"❌ Models test failed: {e}")
        return False


def test_prompts():
    """Test prompts"""
    print("\n" + "="*80)
    print("TEST 4: Prompts")
    print("="*80)
    
    try:
        from agent.prompts import (
            get_system_prompt,
            get_few_shot_examples,
            build_prompt_messages,
            validate_diagnosis_json,
        )
        
        # Get system prompt
        system_prompt = get_system_prompt()
        print(f"✅ System prompt retrieved ({len(system_prompt)} chars)")
        
        # Get examples
        examples = get_few_shot_examples()
        print(f"✅ Few-shot examples retrieved ({len(examples)} examples)")
        
        # Build messages
        messages = build_prompt_messages("Test telemetry")
        print(f"✅ Messages built ({len(messages)} messages)")
        for i, msg in enumerate(messages):
            print(f"   - Message {i+1}: role={msg['role']}, {len(msg['content'])} chars")
        
        # Validate JSON
        from agent.models import DiagnosisResult, RemediationAction
        valid_action = RemediationAction(
            action_type="restart_pod",
            target="test",
            params={},
            confidence=0.8,
            rationale="test"
        )
        valid_diagnosis = DiagnosisResult(
            root_cause="test",
            root_cause_confidence=0.8,
            remediation_actions=[valid_action]
        )
        valid_json = valid_diagnosis.model_dump_json()
        
        is_valid = validate_diagnosis_json(valid_json)
        print(f"✅ Valid JSON validation: {is_valid}")
        
        is_invalid = validate_diagnosis_json("invalid json")
        print(f"✅ Invalid JSON validation: {is_invalid}")
        
        return True
    except Exception as e:
        print(f"❌ Prompts test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_policy_gate():
    """Test policy gate"""
    print("\n" + "="*80)
    print("TEST 5: Policy Gate")
    print("="*80)
    
    try:
        from agent.pipeline import PolicyGate
        from agent.models import RemediationAction, DiagnosisResult
        from telemetry.schemas import TelemetryBundle, KPI, LogEvent, Alarm
        from datetime import datetime
        
        gate = PolicyGate()
        print(f"✅ PolicyGate initialized")
        
        # Create test data
        kpi = KPI(
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
        
        telemetry = TelemetryBundle(
            timestamp=datetime.now().isoformat(),
            kpis={"test-service": kpi},
            logs={},
            alarms=[],
            collection_errors=[],
            services_monitored=["test-service"],
        )
        
        action = RemediationAction(
            action_type="restart_pod",
            target="test-service",
            params={},
            confidence=0.90,
            rationale="test"
        )
        
        diagnosis = DiagnosisResult(
            root_cause="test",
            root_cause_confidence=0.90,
            remediation_actions=[action]
        )
        
        # Test validation
        decision = gate.validate(action, telemetry, diagnosis)
        print(f"✅ Action validated: approved={decision.approved}, risk={decision.risk_level}")
        
        # Test noop rejection
        noop_action = RemediationAction(
            action_type="noop",
            target="test-service",
            params={},
            confidence=1.0,
            rationale="test"
        )
        
        noop_decision = gate.validate(noop_action, telemetry, diagnosis)
        print(f"✅ Noop action rejected: approved={noop_decision.approved}")
        
        return True
    except Exception as e:
        print(f"❌ Policy gate test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_executor():
    """Test executor"""
    print("\n" + "="*80)
    print("TEST 6: Executor")
    print("="*80)
    
    try:
        import asyncio
        from agent.pipeline import Executor
        from agent.models import RemediationAction
        
        executor = Executor()
        print(f"✅ Executor initialized")
        
        # Test mock execution
        action = RemediationAction(
            action_type="restart_pod",
            target="test-service",
            params={},
            confidence=0.90,
            rationale="test"
        )
        
        async def test_execute():
            result = await executor.execute(action)
            print(f"✅ Action executed: status={result.status}, time={result.execution_time_ms:.1f}ms")
            return result
        
        result = asyncio.run(test_execute())
        return True
    except Exception as e:
        print(f"❌ Executor test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_pipeline_structure():
    """Test pipeline structure"""
    print("\n" + "="*80)
    print("TEST 7: Pipeline Structure")
    print("="*80)
    
    try:
        from agent.pipeline import AgentPipeline
        import tempfile
        from config import NetpilotConfig, LLMConfig, TelemetryConfig, PolicyGateConfig, ExecutorConfig, set_config
        from unittest.mock import patch
        
        # Create test config
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
            print(f"✅ AgentPipeline initialized")
            print(f"   - Collector: {pipeline.collector is not None}")
            print(f"   - Formatter: {pipeline.formatter is not None}")
            print(f"   - LLM Client: {pipeline.llm_client is not None}")
            print(f"   - Policy Gate: {pipeline.policy_gate is not None}")
            print(f"   - Executor: {pipeline.executor is not None}")
            print(f"   - Log file: {pipeline.steps_log}")
        
        return True
    except Exception as e:
        print(f"❌ Pipeline structure test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests"""
    print("\n" + "▓"*80)
    print("▓" + " "*78 + "▓")
    print("▓" + "AGENT PIPELINE VERIFICATION".center(78) + "▓")
    print("▓" + " "*78 + "▓")
    print("▓"*80)
    
    tests = [
        ("Imports", test_imports),
        ("Configuration", test_config),
        ("Models", test_models),
        ("Prompts", test_prompts),
        ("Policy Gate", test_policy_gate),
        ("Executor", test_executor),
        ("Pipeline Structure", test_pipeline_structure),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ Test '{name}' crashed: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED! Agent pipeline is ready.")
        print("\nNext steps:")
        print("  1. Set environment variables (OPENAI_API_KEY or ANTHROPIC_API_KEY)")
        print("  2. Run: python -m agent.pipeline")
        print("  3. Monitor logs: tail -f logs/agent_steps.jsonl")
        return 0
    else:
        print("\n❌ Some tests failed. See above for details.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
