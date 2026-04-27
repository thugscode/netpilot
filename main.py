#!/usr/bin/env python3
"""
Netpilot - Self-Healing Kubernetes Agent System

Main entrypoint for continuous diagnosis and remediation loop.

Architecture:
  Kubernetes Cluster (with Prometheus + Alertmanager)
    ↓
  TelemetryCollector (polls KPIs + alarms)
    ↓
  AgentPipeline (LLM diagnosis + action ranking)
    ↓
  PolicyGate (validates remediation actions)
    ↓
  Executor (applies kubectl commands)
    ↓
  Verification Loop (tracks MTTR + SLA compliance)
"""

import asyncio
import logging
import sys
import signal
from pathlib import Path
from datetime import datetime
from typing import Optional

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent))

from config import get_config, NetpilotConfig
from telemetry.collector import TelemetryCollector
from telemetry.schemas import TelemetryBundle
from agent.pipeline import AgentPipeline
from agent.models import DiagnosisResult, RemediationAction
from policy.gate import PolicyGate
from policy.invariants import SLA_BOUNDS
from executor.remediation import execute, ExecutionResult, RemediationError
from eval.report import calculate_metrics, print_table

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [netpilot] %(message)s"
)
logger = logging.getLogger(__name__)


class NetpilotAgent:
    """Main Netpilot autonomous agent system."""
    
    def __init__(self, config: NetpilotConfig):
        """Initialize agent with configuration."""
        self.config = config
        self.running = False
        self.telemetry_collector = None
        self.agent_pipeline = None
        self.policy_gate = None
        
        # Statistics
        self.total_iterations = 0
        self.diagnoses_made = 0
        self.actions_approved = 0
        self.actions_executed = 0
        self.actions_failed = 0
    
    async def initialize(self) -> None:
        """Initialize all components."""
        logger.info("Initializing Netpilot agent...")
        
        try:
            # Initialize telemetry collector
            self.telemetry_collector = TelemetryCollector(
                prometheus_url=self.config.telemetry.prometheus_url,
                alertmanager_url=self.config.telemetry.alertmanager_url,
            )
            logger.info(f"✓ Telemetry collector ready (Prometheus: {self.config.telemetry.prometheus_url})")
            
            # Initialize agent pipeline
            self.agent_pipeline = AgentPipeline(self.config.llm)
            logger.info(f"✓ Agent pipeline ready ({self.config.llm.provider}/{self.config.llm.model})")
            
            # Initialize policy gate
            self.policy_gate = PolicyGate(SLA_BOUNDS)
            logger.info("✓ Policy gate ready")
            
            logger.info("Netpilot agent initialized successfully")
            
        except Exception as e:
            logger.error(f"Initialization failed: {e}", exc_info=True)
            raise
    
    async def collect_telemetry(self) -> Optional[TelemetryBundle]:
        """Collect current telemetry snapshot."""
        try:
            logger.debug("Collecting telemetry...")
            bundle = await self.telemetry_collector.collect()
            
            if not bundle:
                logger.warning("Telemetry bundle empty")
                return None
            
            # Log summary
            services_monitored = len(bundle.services_monitored)
            alarms = len(bundle.alarms)
            logger.debug(
                f"Telemetry collected: {services_monitored} services, "
                f"{alarms} alarms, {len(bundle.collection_errors)} errors"
            )
            
            return bundle
            
        except Exception as e:
            logger.error(f"Telemetry collection failed: {e}", exc_info=True)
            return None
    
    async def diagnose(self, bundle: TelemetryBundle) -> Optional[DiagnosisResult]:
        """Run LLM-based diagnosis."""
        try:
            logger.debug("Running diagnosis...")
            
            # Format telemetry for LLM
            context = bundle.to_context_window(self.config.telemetry.context_window_tokens)
            
            # Run diagnosis
            diagnosis = await self.agent_pipeline.diagnose(context)
            
            if diagnosis:
                logger.info(
                    f"✓ Diagnosis: {diagnosis.root_cause} "
                    f"(confidence: {diagnosis.root_cause_confidence:.1%})"
                )
                self.diagnoses_made += 1
                
                # Log remediation actions
                for i, action in enumerate(diagnosis.remediation_actions[:3], 1):
                    logger.info(
                        f"  {i}. {action.action_type} on {action.target} "
                        f"(confidence: {action.confidence:.1%})"
                    )
            else:
                logger.debug("No diagnosis generated (system healthy)")
            
            return diagnosis
            
        except Exception as e:
            logger.error(f"Diagnosis failed: {e}", exc_info=True)
            return None
    
    async def validate_and_execute(self, diagnosis: DiagnosisResult) -> None:
        """Validate and execute remediation actions."""
        if not diagnosis or not diagnosis.remediation_actions:
            return
        
        # Get current KPIs for validation
        try:
            bundle = await self.collect_telemetry()
            if not bundle:
                logger.warning("Cannot validate actions without current KPIs")
                return
            kpis = bundle.kpis
        except Exception as e:
            logger.warning(f"Failed to get KPIs for validation: {e}")
            return
        
        # Try each action in order of confidence
        for i, action in enumerate(diagnosis.remediation_actions):
            logger.info(f"Evaluating action {i+1}: {action.action_type} on {action.target}")
            
            try:
                # Validate with policy gate
                is_allowed, reason = self.policy_gate.validate(action, kpis)
                
                if not is_allowed:
                    logger.warning(f"  ✗ Blocked by policy: {reason}")
                    continue
                
                logger.info(f"  ✓ Approved by policy gate")
                self.actions_approved += 1
                
                # Execute action
                logger.info(f"  → Executing {action.action_type}...")
                result = execute(action)
                
                if result.success:
                    logger.info(f"  ✓ Executed successfully")
                    self.actions_executed += 1
                    
                    # Wait for system to stabilize
                    logger.info(f"  ⏳ Waiting {self.config.executor.post_action_delay_seconds}s for stabilization...")
                    await asyncio.sleep(self.config.executor.post_action_delay_seconds)
                    
                    # Collect post-action telemetry
                    post_bundle = await self.collect_telemetry()
                    if post_bundle:
                        if post_bundle.is_healthy():
                            logger.info("  ✓ System recovered to healthy state")
                        else:
                            logger.warning("  ⚠ System still unhealthy, may need further action")
                    
                    break  # Stop after first successful action
                else:
                    logger.error(f"  ✗ Execution failed: {result.error}")
                    self.actions_failed += 1
                    
            except RemediationError as e:
                logger.error(f"  ✗ Remediation error: {e}")
                self.actions_failed += 1
            except Exception as e:
                logger.error(f"  ✗ Unexpected error: {e}", exc_info=True)
                self.actions_failed += 1
    
    async def run_iteration(self) -> None:
        """Run one diagnosis-remediation iteration."""
        self.total_iterations += 1
        logger.info(f"\n[Iteration {self.total_iterations}] Starting diagnostic cycle...")
        
        # Collect telemetry
        bundle = await self.collect_telemetry()
        if not bundle:
            logger.warning("Skipping iteration due to telemetry collection failure")
            return
        
        # Check if system is healthy
        if bundle.is_healthy():
            logger.info("✓ System healthy - no action needed")
            return
        
        # Run diagnosis
        diagnosis = await self.diagnose(bundle)
        if not diagnosis:
            return
        
        # Validate and execute
        await self.validate_and_execute(diagnosis)
    
    async def run_loop(self, poll_interval_seconds: int = None) -> None:
        """Run continuous diagnosis loop."""
        if poll_interval_seconds is None:
            poll_interval_seconds = self.config.telemetry.collection_interval
        
        logger.info(f"Starting main loop (polling every {poll_interval_seconds}s)...")
        self.running = True
        
        try:
            while self.running:
                try:
                    # Run one iteration
                    await self.run_iteration()
                    
                    # Wait for next iteration
                    logger.debug(f"Sleeping for {poll_interval_seconds}s...")
                    await asyncio.sleep(poll_interval_seconds)
                    
                except KeyboardInterrupt:
                    logger.info("Received interrupt signal")
                    break
                except Exception as e:
                    logger.error(f"Iteration failed: {e}", exc_info=True)
                    # Continue on error, don't crash
                    await asyncio.sleep(poll_interval_seconds)
        
        finally:
            self.running = False
            logger.info("Main loop stopped")
    
    def print_statistics(self) -> None:
        """Print agent statistics."""
        logger.info("\n" + "=" * 70)
        logger.info("NETPILOT STATISTICS")
        logger.info("=" * 70)
        logger.info(f"Total iterations: {self.total_iterations}")
        logger.info(f"Diagnoses made: {self.diagnoses_made}")
        logger.info(f"Actions approved: {self.actions_approved}")
        logger.info(f"Actions executed: {self.actions_executed}")
        logger.info(f"Actions failed: {self.actions_failed}")
        if self.diagnoses_made > 0:
            logger.info(f"Approval rate: {self.actions_approved / self.diagnoses_made:.1%}")
        if self.actions_approved > 0:
            logger.info(f"Success rate: {self.actions_executed / self.actions_approved:.1%}")
        logger.info("=" * 70)


def handle_signal(signum, frame):
    """Handle shutdown signals."""
    logger.info(f"Received signal {signum}, shutting down...")
    sys.exit(0)


async def main():
    """Main entrypoint."""
    # Parse configuration
    try:
        config = get_config()
        logger.info(f"Configuration loaded: {config.llm.provider}/{config.llm.model}")
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    
    # Create agent
    agent = NetpilotAgent(config)
    
    # Set up signal handlers
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)
    
    try:
        # Initialize
        await agent.initialize()
        
        # Run main loop
        poll_interval = config.telemetry.collection_interval
        await agent.run_loop(poll_interval)
        
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        agent.print_statistics()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown complete")
        sys.exit(0)
