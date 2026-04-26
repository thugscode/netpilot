"""
Agent pipeline for autonomous Kubernetes diagnostics and remediation.

Components:
- models: DiagnosisResult, RemediationAction schemas
- prompts: System prompt, few-shot examples, validation
- pipeline: Main execution loop with LLM integration
"""

from .models import DiagnosisResult, RemediationAction
from .prompts import (
    get_system_prompt,
    get_few_shot_examples,
    build_prompt_messages,
    validate_diagnosis_json,
)
from .pipeline import (
    LLMClient,
    LLMProvider,
    PolicyGate,
    PolicyDecision,
    Executor,
    ExecutionResult,
    AgentPipeline,
    PipelineStep,
)

__all__ = [
    # Models
    "DiagnosisResult",
    "RemediationAction",
    # Prompts
    "get_system_prompt",
    "get_few_shot_examples",
    "build_prompt_messages",
    "validate_diagnosis_json",
    # Pipeline
    "LLMClient",
    "LLMProvider",
    "PolicyGate",
    "PolicyDecision",
    "Executor",
    "ExecutionResult",
    "AgentPipeline",
    "PipelineStep",
]
