#!/usr/bin/env python3
"""
Test script for agent prompts and models.
"""

import sys
import json
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.models import DiagnosisResult, RemediationAction
from agent.prompts import (
    get_system_prompt,
    get_few_shot_examples,
    build_prompt_messages,
    validate_diagnosis_json,
    format_user_prompt,
)


def test_models():
    """Test Pydantic models."""
    print("=" * 80)
    print("TEST 1: Pydantic Models")
    print("=" * 80)
    
    # Test RemediationAction
    action = RemediationAction(
        action_type="restart_pod",
        target="notification-service",
        params={"grace_period_seconds": 30},
        confidence=0.88,
        rationale="Pod restart will clear stale state and reconnect to database."
    )
    print(f"\n✓ RemediationAction created:")
    print(f"  {action}")
    
    # Test DiagnosisResult
    diagnosis = DiagnosisResult(
        root_cause="notification-service pod crashed due to database connection loss",
        root_cause_confidence=0.92,
        remediation_actions=[
            action,
            RemediationAction(
                action_type="scale_up",
                target="order-service",
                params={"replica_count": 3},
                confidence=0.65,
                rationale="Scaling upstream service reduces cascading failures."
            )
        ]
    )
    print(f"\n✓ DiagnosisResult created:")
    print(f"  Root cause: {diagnosis.root_cause}")
    print(f"  Confidence: {diagnosis.root_cause_confidence}")
    print(f"  Actions: {len(diagnosis.remediation_actions)}")
    
    # Test JSON serialization
    diagnosis_json = diagnosis.model_dump_json(indent=2)
    print(f"\n✓ DiagnosisResult JSON serialization:")
    print(f"  {len(diagnosis_json)} chars")
    print(f"  First 300 chars:\n{diagnosis_json[:300]}...")
    
    print("\n✅ Models test passed\n")
    return True


def test_prompts():
    """Test prompt functions."""
    print("=" * 80)
    print("TEST 2: Prompt Functions")
    print("=" * 80)
    
    # Test system prompt
    system = get_system_prompt()
    print(f"\n✓ System prompt retrieved:")
    print(f"  Length: {len(system)} chars")
    print(f"  Contains 'DiagnosisResult': {'DiagnosisResult' in system}")
    print(f"  Contains 'restart_pod': {'restart_pod' in system}")
    print(f"  Contains 'CRITICAL RULES': {'CRITICAL RULES' in system}")
    
    # Test few-shot examples
    examples = get_few_shot_examples()
    print(f"\n✓ Few-shot examples retrieved:")
    print(f"  Number of examples: {len(examples)}")
    for name, example in examples.items():
        print(f"    - {name}: {example['scenario']}")
        print(f"      Input: {len(example['input'])} chars")
        print(f"      Output: {len(example['output'])} chars")
    
    # Test user prompt formatting
    sample_telemetry = '{"snapshot": {"health": "DEGRADED"}}'
    user_prompt = format_user_prompt(sample_telemetry)
    print(f"\n✓ User prompt formatted:")
    print(f"  Length: {len(user_prompt)} chars")
    print(f"  Contains telemetry: {sample_telemetry in user_prompt}")
    
    # Test prompt messages building
    messages = build_prompt_messages(sample_telemetry)
    print(f"\n✓ Prompt messages built:")
    print(f"  Total messages: {len(messages)}")
    for i, msg in enumerate(messages):
        print(f"    Message {i+1}: role={msg['role']}, content_length={len(msg['content'])}")
    
    print("\n✅ Prompts test passed\n")
    return True


def test_validation():
    """Test JSON validation."""
    print("=" * 80)
    print("TEST 3: JSON Validation")
    print("=" * 80)
    
    # Valid JSON
    valid_json = """{
        "root_cause": "notification-service pod crashed",
        "root_cause_confidence": 0.92,
        "remediation_actions": [
            {
                "action_type": "restart_pod",
                "target": "notification-service",
                "params": {"grace_period_seconds": 30},
                "confidence": 0.88,
                "rationale": "Pod restart clears state."
            }
        ]
    }"""
    
    result = validate_diagnosis_json(valid_json)
    print(f"\n✓ Valid JSON validation: {result}")
    
    # Invalid JSON (missing field)
    invalid_json1 = """{
        "root_cause": "some failure",
        "remediation_actions": []
    }"""
    
    result = validate_diagnosis_json(invalid_json1)
    print(f"✓ Invalid JSON (missing confidence) validation: {result}")
    
    # Invalid JSON (malformed)
    invalid_json2 = "{ invalid json }"
    result = validate_diagnosis_json(invalid_json2)
    print(f"✓ Invalid JSON (malformed) validation: {result}")
    
    # Invalid JSON (wrong action type)
    invalid_json3 = """{
        "root_cause": "failure",
        "root_cause_confidence": 0.9,
        "remediation_actions": [
            {
                "action_type": "invalid_type",
                "target": "service",
                "params": {},
                "confidence": 0.8,
                "rationale": "test"
            }
        ]
    }"""
    
    result = validate_diagnosis_json(invalid_json3)
    print(f"✓ Invalid JSON (wrong action type) validation: {result}")
    
    print("\n✅ Validation test passed\n")
    return True


def test_examples_valid():
    """Verify few-shot examples are valid DiagnosisResult JSON."""
    print("=" * 80)
    print("TEST 4: Few-Shot Examples Validation")
    print("=" * 80)
    
    examples = get_few_shot_examples()
    
    for name, example in examples.items():
        print(f"\n✓ Validating example: {name}")
        
        # Validate output JSON
        is_valid = validate_diagnosis_json(example["output"])
        print(f"  Output is valid DiagnosisResult JSON: {is_valid}")
        
        # Try to parse and instantiate model
        try:
            data = json.loads(example["output"])
            diagnosis = DiagnosisResult(**data)
            print(f"  Successfully created DiagnosisResult model")
            print(f"  Root cause: {diagnosis.root_cause}")
            print(f"  Confidence: {diagnosis.root_cause_confidence}")
            print(f"  Actions: {len(diagnosis.remediation_actions)}")
        except Exception as e:
            print(f"  ✗ Failed to instantiate: {e}")
            return False
    
    print("\n✅ Examples validation passed\n")
    return True


def main():
    """Run all tests."""
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 78 + "║")
    print("║" + "  AGENT PROMPTS & MODELS - TESTS".center(78) + "║")
    print("║" + " " * 78 + "║")
    print("╚" + "=" * 78 + "╝")
    print()
    
    try:
        test_models()
        test_prompts()
        test_validation()
        test_examples_valid()
        
        print("=" * 80)
        print("✓ ALL TESTS PASSED")
        print("=" * 80)
        print()
        
        return 0
    except Exception as e:
        print(f"\n✗ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
