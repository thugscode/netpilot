"""
Netpilot Agent Pipeline

Main execution loop for autonomous failure diagnosis and remediation.

Flow:
  1. Collect telemetry from Kubernetes (KPIs, logs, alerts)
  2. Format telemetry for LLM consumption
  3. Build prompt messages (system + few-shot + current telemetry)
  4. Call LLM to diagnose root cause
  5. Parse and validate JSON response
  6. For each action in ranked order:
     - Submit to policy gate for validation
     - If approved, submit to executor
     - Log decision and stop (execute only first approved action)
  7. Log full step (telemetry, diagnosis, gate decisions, executor result)
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum

from telemetry.collector import TelemetryCollector
from telemetry.formatter import TelemetryFormatter
from telemetry.schemas import TelemetryBundle

from agent.models import DiagnosisResult, RemediationAction
from agent.prompts import (
    build_prompt_messages,
    format_user_prompt,
    validate_diagnosis_json,
)

from config import get_config


# ============================================================================
# LLM Client Abstraction
# ============================================================================


class LLMProvider(Enum):
    """Supported LLM providers"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class LLMClient:
    """Abstraction over LLM providers (OpenAI, Anthropic)"""
    
    def __init__(self, provider: str, model: str, api_key: str, temperature: float = 0.3, max_tokens: int = 2000):
        """Initialize LLM client
        
        Args:
            provider: "openai" or "anthropic"
            model: Model name (e.g., "gpt-4" or "claude-3-opus")
            api_key: API key for the provider
            temperature: Sampling temperature (0-1)
            max_tokens: Maximum tokens in response
        """
        self.provider = provider
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.logger = logging.getLogger(f"{__name__}.LLMClient")
        
        if provider == LLMProvider.OPENAI.value:
            try:
                import openai
                openai.api_key = api_key
                self.client = openai.OpenAI(api_key=api_key)
            except ImportError:
                raise ImportError("openai package required for OpenAI provider: pip install openai")
        elif provider == LLMProvider.ANTHROPIC.value:
            try:
                import anthropic
                self.client = anthropic.Anthropic(api_key=api_key)
            except ImportError:
                raise ImportError("anthropic package required for Anthropic provider: pip install anthropic")
        else:
            raise ValueError(f"Unknown LLM provider: {provider}")
    
    def call(self, messages: List[Dict[str, str]]) -> str:
        """Call LLM and get response
        
        Args:
            messages: List of message dicts with "role" and "content"
            
        Returns:
            LLM response text (JSON)
        """
        try:
            if self.provider == LLMProvider.OPENAI.value:
                return self._call_openai(messages)
            else:
                return self._call_anthropic(messages)
        except Exception as e:
            self.logger.error(f"LLM call failed: {e}")
            raise
    
    def _call_openai(self, messages: List[Dict[str, str]]) -> str:
        """Call OpenAI API"""
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            response_format={"type": "json_object"},  # Force JSON output
        )
        return response.choices[0].message.content
    
    def _call_anthropic(self, messages: List[Dict[str, str]]) -> str:
        """Call Anthropic API
        
        Anthropic uses a different message format:
        - Convert OpenAI messages to Anthropic format
        - Extract system prompt from first message if role=="system"
        """
        system_prompt = ""
        anthropic_messages = []
        
        for msg in messages:
            if msg["role"] == "system":
                system_prompt = msg["content"]
            else:
                # Convert to Anthropic format
                anthropic_messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })
        
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=anthropic_messages,
        )
        return response.content[0].text


# ============================================================================
# Policy Gate Interface
# ============================================================================


@dataclass
class PolicyDecision:
    """Decision from policy gate"""
    approved: bool
    reason: str
    risk_level: str  # "low", "medium", "high"


class PolicyGate:
    """Policy validation gate (mock implementation)
    
    Validates proposed actions against:
    - SLA bounds
    - Blast radius
    - Rollback history
    """
    
    def __init__(self):
        """Initialize policy gate"""
        self.logger = logging.getLogger(f"{__name__}.PolicyGate")
        self.rollback_history: Dict[str, List[datetime]] = {}  # service -> list of rollback timestamps
        self.config = get_config()
    
    def validate(
        self,
        action: RemediationAction,
        telemetry: TelemetryBundle,
        diagnosis: DiagnosisResult,
    ) -> PolicyDecision:
        """Validate action against policies
        
        Args:
            action: Proposed remediation action
            telemetry: Current telemetry snapshot
            diagnosis: Diagnosis with confidence
            
        Returns:
            PolicyDecision with approval status and reason
        """
        # Mock implementation - can be enhanced with real policy checks
        
        # Check 1: Don't execute noop actions
        if action.action_type == "noop":
            return PolicyDecision(
                approved=False,
                reason="noop actions are informational only",
                risk_level="low"
            )
        
        # Check 2: Require high confidence for high-impact actions
        if action.confidence < 0.5 and action.action_type in ("rollback_deploy", "reroute_traffic"):
            return PolicyDecision(
                approved=False,
                reason=f"Low confidence ({action.confidence:.2f}) for high-impact action",
                risk_level="high"
            )
        
        # Check 3: Track rollbacks to prevent cascading rollbacks
        if action.action_type == "rollback_deploy":
            if self._is_rollback_rate_limited(action.target):
                return PolicyDecision(
                    approved=False,
                    reason=f"Rollback rate limit exceeded for {action.target}",
                    risk_level="high"
                )
            self._track_rollback(action.target)
        
        # Check 4: Approve after validation
        risk_level = self._estimate_risk(action, telemetry)
        
        return PolicyDecision(
            approved=True,
            reason=f"Action {action.action_type} approved for {action.target}",
            risk_level=risk_level
        )
    
    def _is_rollback_rate_limited(self, service: str) -> bool:
        """Check if service has exceeded rollback rate limit"""
        if service not in self.rollback_history:
            return False
        
        # Check rollbacks in last 1 hour
        now = datetime.now()
        window_start = datetime.fromtimestamp(now.timestamp() - 3600)
        recent_rollbacks = [
            ts for ts in self.rollback_history[service]
            if ts > window_start
        ]
        
        return len(recent_rollbacks) >= self.config.policy_gate.max_rollbacks_per_window
    
    def _track_rollback(self, service: str) -> None:
        """Track rollback for rate limiting"""
        if service not in self.rollback_history:
            self.rollback_history[service] = []
        self.rollback_history[service].append(datetime.now())
    
    def _estimate_risk(self, action: RemediationAction, telemetry: TelemetryBundle) -> str:
        """Estimate risk level for action"""
        if action.action_type == "noop":
            return "low"
        elif action.action_type in ("restart_pod", "scale_up"):
            return "low" if action.confidence > 0.8 else "medium"
        elif action.action_type in ("reroute_traffic", "rollback_deploy"):
            return "high" if action.confidence < 0.7 else "medium"
        return "medium"


# ============================================================================
# Executor Interface
# ============================================================================


@dataclass
class ExecutionResult:
    """Result of action execution"""
    action_type: str
    target: str
    status: str  # "success", "failed", "pending"
    message: str
    execution_time_ms: float


class Executor:
    """Action executor (mock implementation)
    
    Maps RemediationActions to kubectl/REST calls and executes them.
    """
    
    def __init__(self):
        """Initialize executor"""
        self.logger = logging.getLogger(f"{__name__}.Executor")
        self.config = get_config()
    
    async def execute(self, action: RemediationAction) -> ExecutionResult:
        """Execute remediation action
        
        Args:
            action: Action to execute
            
        Returns:
            ExecutionResult with status and message
        """
        import time
        start_time = time.time()
        
        try:
            if action.action_type == "restart_pod":
                result = await self._restart_pod(action)
            elif action.action_type == "scale_up":
                result = await self._scale_up(action)
            elif action.action_type == "reroute_traffic":
                result = await self._reroute_traffic(action)
            elif action.action_type == "rollback_deploy":
                result = await self._rollback_deploy(action)
            else:
                result = ExecutionResult(
                    action_type=action.action_type,
                    target=action.target,
                    status="failed",
                    message=f"Unknown action type: {action.action_type}",
                    execution_time_ms=(time.time() - start_time) * 1000
                )
            
            return result
        except Exception as e:
            self.logger.error(f"Execution failed: {e}")
            return ExecutionResult(
                action_type=action.action_type,
                target=action.target,
                status="failed",
                message=str(e),
                execution_time_ms=(time.time() - start_time) * 1000
            )
    
    async def _restart_pod(self, action: RemediationAction) -> ExecutionResult:
        """Mock: Restart pod
        
        Real implementation would run:
          kubectl delete pod -l app={action.target} --grace-period=30
        """
        await asyncio.sleep(0.1)  # Simulate execution
        self.logger.info(f"Mock restart pod: {action.target}")
        return ExecutionResult(
            action_type="restart_pod",
            target=action.target,
            status="success",
            message=f"Pod restarted successfully",
            execution_time_ms=100.0
        )
    
    async def _scale_up(self, action: RemediationAction) -> ExecutionResult:
        """Mock: Scale up deployment
        
        Real implementation would run:
          kubectl scale deployment {action.target} --replicas={action.params['replicas']}
        """
        await asyncio.sleep(0.1)  # Simulate execution
        replicas = action.params.get("replicas", 3)
        self.logger.info(f"Mock scale up: {action.target} to {replicas} replicas")
        return ExecutionResult(
            action_type="scale_up",
            target=action.target,
            status="success",
            message=f"Scaled to {replicas} replicas",
            execution_time_ms=150.0
        )
    
    async def _reroute_traffic(self, action: RemediationAction) -> ExecutionResult:
        """Mock: Reroute traffic via circuit breaker
        
        Real implementation would update ServiceMesh config
        """
        await asyncio.sleep(0.1)  # Simulate execution
        self.logger.info(f"Mock reroute traffic: {action.target}")
        return ExecutionResult(
            action_type="reroute_traffic",
            target=action.target,
            status="success",
            message="Traffic rerouted",
            execution_time_ms=50.0
        )
    
    async def _rollback_deploy(self, action: RemediationAction) -> ExecutionResult:
        """Mock: Rollback deployment
        
        Real implementation would run:
          kubectl rollout undo deployment/{action.target}
        """
        await asyncio.sleep(0.1)  # Simulate execution
        self.logger.info(f"Mock rollback: {action.target}")
        return ExecutionResult(
            action_type="rollback_deploy",
            target=action.target,
            status="success",
            message="Deployment rolled back",
            execution_time_ms=200.0
        )


# ============================================================================
# Main Pipeline
# ============================================================================


@dataclass
class PipelineStep:
    """Complete record of a pipeline step"""
    timestamp: str
    telemetry_bundle: Dict[str, Any]  # Serialized TelemetryBundle
    telemetry_snapshot: str           # Formatted for LLM
    diagnosis: Dict[str, Any]         # Serialized DiagnosisResult
    gate_decisions: List[Dict[str, Any]]  # [{"action": {...}, "decision": {...}}]
    executed_action: Optional[Dict[str, Any]]
    executor_result: Optional[Dict[str, Any]]


class AgentPipeline:
    """Main agent pipeline"""
    
    def __init__(self):
        """Initialize pipeline"""
        self.logger = self._setup_logging()
        self.config = get_config()
        self.collector = TelemetryCollector(
            prometheus_url=self.config.telemetry.prometheus_url,
            alertmanager_url=self.config.telemetry.alertmanager_url,
        )
        self.formatter = TelemetryFormatter()
        self.llm_client = LLMClient(
            provider=self.config.llm.provider,
            model=self.config.llm.model,
            api_key=(
                self.config.llm.openai_api_key
                if self.config.llm.provider == "openai"
                else self.config.llm.anthropic_api_key
            ),
            temperature=self.config.llm.temperature,
            max_tokens=self.config.llm.max_tokens,
        )
        self.policy_gate = PolicyGate()
        self.executor = Executor()
        
        # Create logs directory
        os.makedirs(self.config.log_dir, exist_ok=True)
        self.steps_log = os.path.join(self.config.log_dir, "agent_steps.jsonl")
    
    def _setup_logging(self) -> logging.Logger:
        """Configure logging"""
        config = get_config()
        
        logger = logging.getLogger(__name__)
        logger.setLevel(getattr(logging, config.log_level))
        
        # Console handler
        handler = logging.StreamHandler()
        handler.setLevel(getattr(logging, config.log_level))
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
        return logger
    
    async def run_step(self) -> PipelineStep:
        """Execute one pipeline step
        
        Returns:
            Complete step record with telemetry, diagnosis, decisions, execution
        """
        timestamp = datetime.now().isoformat()
        self.logger.info("=" * 80)
        self.logger.info(f"Starting pipeline step at {timestamp}")
        
        try:
            # ===== Step 1: Collect telemetry =====
            self.logger.info("Step 1: Collecting telemetry...")
            telemetry = await self.collector.collect()
            self.logger.info(f"Collected telemetry for {len(telemetry.services_monitored)} services")
            
            # ===== Step 2: Format telemetry =====
            self.logger.info("Step 2: Formatting telemetry...")
            telemetry_snapshot = self.formatter.to_context_window(
                telemetry,
                max_tokens=self.config.telemetry.context_window_tokens
            )
            self.logger.info(f"Telemetry formatted ({len(telemetry_snapshot)} chars)")
            
            # ===== Step 3: Build prompt and call LLM =====
            self.logger.info("Step 3: Calling LLM for diagnosis...")
            messages = build_prompt_messages(telemetry_snapshot)
            llm_response = self.llm_client.call(messages)
            self.logger.info(f"LLM response received ({len(llm_response)} chars)")
            
            # ===== Step 4: Validate and parse diagnosis =====
            self.logger.info("Step 4: Validating diagnosis...")
            if not validate_diagnosis_json(llm_response):
                raise ValueError("LLM response failed validation")
            
            diagnosis = DiagnosisResult.model_validate_json(llm_response)
            self.logger.info(
                f"Diagnosis: {diagnosis.root_cause} "
                f"(confidence: {diagnosis.root_cause_confidence:.2f})"
            )
            self.logger.info(f"Proposed {len(diagnosis.remediation_actions)} actions")
            
            # ===== Step 5: Gate each action =====
            self.logger.info("Step 5: Submitting actions to policy gate...")
            gate_decisions = []
            executed_action = None
            executor_result = None
            
            for i, action in enumerate(diagnosis.remediation_actions):
                self.logger.info(
                    f"  Action {i+1}: {action.action_type} for {action.target} "
                    f"(confidence: {action.confidence:.2f})"
                )
                
                decision = self.policy_gate.validate(action, telemetry, diagnosis)
                gate_decisions.append({
                    "action": action.model_dump(),
                    "decision": asdict(decision)
                })
                
                self.logger.info(f"    Gate decision: {decision.approved} ({decision.reason})")
                
                # ===== Step 6: Execute first approved action =====
                if decision.approved and executed_action is None:
                    self.logger.info(f"  Executing action: {action.action_type}")
                    executor_result = await self.executor.execute(action)
                    
                    self.logger.info(
                        f"    Execution result: {executor_result.status} "
                        f"({executor_result.execution_time_ms:.1f}ms)"
                    )
                    
                    executed_action = action.model_dump()
                    break  # Only execute first approved action
            
            if executed_action is None:
                self.logger.info("No actions approved by policy gate")
            
            # ===== Step 7: Log step =====
            step = PipelineStep(
                timestamp=timestamp,
                telemetry_bundle=telemetry.model_dump(),
                telemetry_snapshot=telemetry_snapshot,
                diagnosis=diagnosis.model_dump(),
                gate_decisions=gate_decisions,
                executed_action=executed_action,
                executor_result=asdict(executor_result) if executor_result else None,
            )
            
            self._log_step(step)
            
            self.logger.info("Pipeline step completed successfully")
            self.logger.info("=" * 80)
            
            return step
        
        except Exception as e:
            self.logger.error(f"Pipeline step failed: {e}", exc_info=True)
            raise
    
    def _log_step(self, step: PipelineStep) -> None:
        """Log step to JSONL file"""
        with open(self.steps_log, "a") as f:
            # Serialize dataclass to dict, then to JSON
            step_dict = asdict(step)
            f.write(json.dumps(step_dict) + "\n")
    
    async def run_continuous(self, interval_seconds: int = 30) -> None:
        """Run pipeline continuously at fixed interval
        
        Args:
            interval_seconds: Seconds between steps
        """
        self.logger.info(f"Starting continuous pipeline (interval: {interval_seconds}s)")
        
        while True:
            try:
                await self.run_step()
            except Exception as e:
                self.logger.error(f"Pipeline step failed: {e}")
            
            await asyncio.sleep(interval_seconds)


# ============================================================================
# Entry Points
# ============================================================================


async def main():
    """Main entry point: Run one pipeline step"""
    pipeline = AgentPipeline()
    step = await pipeline.run_step()
    print(f"\nStep completed at {step.timestamp}")
    if step.executed_action:
        print(f"Executed action: {step.executed_action['action_type']}")


async def continuous():
    """Continuous mode: Run pipeline every N seconds"""
    pipeline = AgentPipeline()
    await pipeline.run_continuous(interval_seconds=30)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "continuous":
        asyncio.run(continuous())
    else:
        asyncio.run(main())
