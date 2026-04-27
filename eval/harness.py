"""
Evaluation harness for Netpilot.

Runs failure scenarios and measures:
- MTTR (Mean Time To Recovery)
- Correct action accuracy (true positive / false positive)
- SLA violations during recovery
"""

import asyncio
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List, Tuple

import yaml

# Add parent dirs to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sim.fault_injector import inject_fault
from telemetry.collector import TelemetryCollector
from telemetry.schemas import TelemetryBundle, KPI
from policy.invariants import SLA_BOUNDS
from agent.pipeline import AgentPipeline

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [eval] %(message)s"
)
logger = logging.getLogger(__name__)


@dataclass
class ScenarioResult:
    """Result of running a single scenario."""
    
    scenario_name: str
    target_service: str
    fault_type: str
    success: bool  # Whether recovery occurred within timeout
    mttr_seconds: float  # Mean Time To Recovery
    correct_action_taken: bool  # Expected action was executed
    expected_action: str
    actual_action: Optional[str]
    sla_violations: List[str]  # List of SLA violations during recovery
    start_timestamp: str
    end_timestamp: str
    reason: str  # Detailed reason for success/failure
    
    def to_dict(self) -> Dict:
        """Serialize to dictionary."""
        return asdict(self)
    
    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)


@dataclass
class EvaluationMetrics:
    """Overall evaluation metrics across scenarios."""
    
    total_scenarios: int
    successful_recoveries: int  # Scenarios where SLA recovered within timeout
    correct_actions: int  # Scenarios where expected action was taken
    average_mttr_seconds: float
    false_positive_rate: float  # (incorrect actions) / (total scenarios)
    timestamp: str
    
    def to_dict(self) -> Dict:
        """Serialize to dictionary."""
        return asdict(self)
    
    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)


def load_scenario(scenario_file: str) -> Dict:
    """
    Load scenario YAML file.
    
    Args:
        scenario_file: Path to scenario YAML (relative to eval/scenarios/)
    
    Returns:
        Dict with scenario configuration
    
    Raises:
        FileNotFoundError: If scenario file not found
        yaml.YAMLError: If YAML parsing fails
    """
    scenario_path = Path(__file__).parent / "scenarios" / scenario_file
    
    if not scenario_path.exists():
        raise FileNotFoundError(f"Scenario file not found: {scenario_path}")
    
    with open(scenario_path, "r") as f:
        scenario = yaml.safe_load(f)
    
    logger.info(f"Loaded scenario: {scenario.get('name', scenario_file)}")
    return scenario


async def collect_kpis() -> Dict[str, KPI]:
    """
    Collect current KPIs from telemetry.
    
    Returns:
        Dict mapping service name → KPI
    """
    async with TelemetryCollector() as collector:
        bundle = await collector.collect()
    
    return bundle.kpis


def is_sla_compliant(kpis: Dict[str, KPI], sla_bounds: Dict = None) -> Tuple[bool, List[str]]:
    """
    Check if all KPIs are within SLA bounds.
    
    Args:
        kpis: Dict of service KPIs
        sla_bounds: Optional custom SLA bounds (default: from policy/invariants)
    
    Returns:
        (is_compliant, list_of_violations)
    """
    if sla_bounds is None:
        sla_bounds = SLA_BOUNDS
    
    violations = []
    
    for service, kpi in kpis.items():
        if service not in sla_bounds:
            continue
        
        bounds = sla_bounds[service]
        
        # Check error rate
        if kpi.error_rate > bounds["max_error_rate"]:
            violations.append(
                f"{service}: error_rate {kpi.error_rate:.1%} > "
                f"bound {bounds['max_error_rate']:.1%}"
            )
        
        # Check P99 latency
        if kpi.latency_p99_ms is not None:
            if kpi.latency_p99_ms > bounds["max_p99_latency_ms"]:
                violations.append(
                    f"{service}: p99_latency {kpi.latency_p99_ms}ms > "
                    f"bound {bounds['max_p99_latency_ms']}ms"
                )
    
    return len(violations) == 0, violations


async def run_scenario(
    scenario_file: str,
    poll_interval_seconds: int = 10,
) -> ScenarioResult:
    """
    Run a failure scenario and measure recovery metrics.
    
    Args:
        scenario_file: Path to scenario YAML (relative to eval/scenarios/)
        poll_interval_seconds: How often to check for recovery (default: 10s)
    
    Returns:
        ScenarioResult with MTTR, action accuracy, SLA status
    """
    # Load scenario configuration
    scenario = load_scenario(scenario_file)
    
    scenario_name = scenario.get("name", scenario_file)
    target_service = scenario.get("target")
    fault_type = scenario.get("fault")
    expected_action = scenario.get("expected_action")
    expected_target = scenario.get("expected_target", target_service)
    timeout_seconds = scenario.get("timeout_seconds", 300)
    
    logger.info(f"Starting scenario: {scenario_name}")
    logger.info(f"  Fault: {fault_type} on {target_service}")
    logger.info(f"  Expected action: {expected_action} on {expected_target}")
    logger.info(f"  Timeout: {timeout_seconds}s")
    
    start_time = time.time()
    start_timestamp = datetime.now().isoformat()
    sla_violations_history = []
    actual_action_taken = None
    
    # Step 1: Inject fault
    logger.info(f"Injecting fault: {fault_type} on {target_service}")
    
    try:
        inject_fault(
            scenario=fault_type,
            target=target_service,
            duration=scenario.get("duration_seconds", 60),
            watch_duration=scenario.get("watch_duration_seconds", 45),
        )
    except Exception as e:
        logger.error(f"Fault injection failed: {e}")
        return ScenarioResult(
            scenario_name=scenario_name,
            target_service=target_service,
            fault_type=fault_type,
            success=False,
            mttr_seconds=0.0,
            correct_action_taken=False,
            expected_action=expected_action,
            actual_action=None,
            sla_violations=["Fault injection failed"],
            start_timestamp=start_timestamp,
            end_timestamp=datetime.now().isoformat(),
            reason=f"Fault injection failed: {str(e)}",
        )
    
    logger.info("Fault injected, waiting for recovery...")
    
    # Step 2: Wait for initial degradation
    await asyncio.sleep(5)
    
    # Step 3: Run pipeline loop until recovery or timeout
    pipeline = AgentPipeline()
    recovery_time = None
    
    while time.time() - start_time < timeout_seconds:
        try:
            # Collect current KPIs
            kpis = await collect_kpis()
            
            # Check SLA compliance
            is_compliant, violations = is_sla_compliant(kpis)
            
            if violations:
                sla_violations_history.extend(violations)
            
            logger.info(
                f"SLA check: {'✓ COMPLIANT' if is_compliant else '✗ VIOLATED'} "
                f"({len(violations)} violations)"
            )
            
            if is_compliant and recovery_time is None:
                recovery_time = time.time() - start_time
                logger.info(f"✓ SLA recovered in {recovery_time:.1f}s")
                break
            
            # Run one agent pipeline step
            logger.info("Running agent pipeline step...")
            step = await pipeline.run_step()
            
            # Track action taken
            if step.executed_action and actual_action_taken is None:
                actual_action_taken = step.executed_action.get("action_type")
                logger.info(f"Action executed: {actual_action_taken}")
            
            # Wait before next poll
            await asyncio.sleep(poll_interval_seconds)
            
        except Exception as e:
            logger.error(f"Error during scenario run: {e}")
            sla_violations_history.append(f"Error: {str(e)}")
    
    # Step 4: Determine if scenario succeeded
    end_time = time.time()
    end_timestamp = datetime.now().isoformat()
    total_time = end_time - start_time
    
    if recovery_time is None:
        # Timeout occurred
        success = False
        mttr = total_time
        reason = f"Timeout: SLA not recovered within {timeout_seconds}s"
    else:
        success = True
        mttr = recovery_time
        reason = f"SLA recovered in {recovery_time:.1f}s"
    
    # Check if correct action was taken
    correct_action = (
        actual_action_taken is not None and
        actual_action_taken == expected_action
    )
    
    logger.info(f"Scenario result: {reason}")
    logger.info(f"  Correct action: {correct_action} "
               f"(expected {expected_action}, got {actual_action_taken})")
    
    return ScenarioResult(
        scenario_name=scenario_name,
        target_service=target_service,
        fault_type=fault_type,
        success=success,
        mttr_seconds=mttr,
        correct_action_taken=correct_action,
        expected_action=expected_action,
        actual_action=actual_action_taken,
        sla_violations=sla_violations_history,
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
        reason=reason,
    )


async def run_scenario_suite(
    scenario_files: List[str],
    poll_interval_seconds: int = 10,
) -> Tuple[List[ScenarioResult], EvaluationMetrics]:
    """
    Run multiple scenarios and collect aggregate metrics.
    
    Args:
        scenario_files: List of scenario YAML filenames
        poll_interval_seconds: Poll interval for recovery checks
    
    Returns:
        (list of ScenarioResult, EvaluationMetrics)
    """
    logger.info(f"Running scenario suite with {len(scenario_files)} scenarios")
    
    results = []
    
    for scenario_file in scenario_files:
        try:
            result = await run_scenario(
                scenario_file,
                poll_interval_seconds=poll_interval_seconds,
            )
            results.append(result)
            logger.info(f"Completed: {result.scenario_name}")
            
            # Wait between scenarios to allow cluster to stabilize
            await asyncio.sleep(30)
            
        except Exception as e:
            logger.error(f"Failed to run scenario {scenario_file}: {e}")
    
    # Calculate aggregate metrics
    successful_recoveries = sum(1 for r in results if r.success)
    correct_actions = sum(1 for r in results if r.correct_action_taken)
    average_mttr = sum(r.mttr_seconds for r in results) / len(results) if results else 0.0
    false_positive_rate = (len(results) - correct_actions) / len(results) if results else 0.0
    
    metrics = EvaluationMetrics(
        total_scenarios=len(results),
        successful_recoveries=successful_recoveries,
        correct_actions=correct_actions,
        average_mttr_seconds=average_mttr,
        false_positive_rate=false_positive_rate,
        timestamp=datetime.now().isoformat(),
    )
    
    logger.info("Scenario suite completed")
    logger.info(f"  Successful recoveries: {successful_recoveries}/{len(results)}")
    logger.info(f"  Correct actions: {correct_actions}/{len(results)}")
    logger.info(f"  Average MTTR: {average_mttr:.1f}s")
    logger.info(f"  False positive rate: {false_positive_rate:.1%}")
    
    return results, metrics


def save_results(
    results: List[ScenarioResult],
    metrics: EvaluationMetrics,
    output_dir: str = "eval/results",
) -> None:
    """
    Save evaluation results to JSON files and consolidated JSONL.
    
    Args:
        results: List of ScenarioResult
        metrics: EvaluationMetrics
        output_dir: Directory to save results
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Save individual results
    for i, result in enumerate(results):
        result_file = output_path / f"result_{i:02d}_{timestamp}.json"
        with open(result_file, "w") as f:
            f.write(result.to_json())
        logger.info(f"Saved result: {result_file}")
    
    # Save to consolidated JSONL file
    jsonl_file = Path("eval/results.jsonl")
    logger.info(f"Appending {len(results)} results to {jsonl_file}")
    with open(jsonl_file, "a") as f:
        for result in results:
            # Convert ScenarioResult to dict and write as single line JSON
            result_dict = result.to_dict()
            f.write(json.dumps(result_dict) + "\n")
    logger.info(f"Updated {jsonl_file}")
    
    # Save aggregate metrics
    metrics_file = output_path / f"metrics_{timestamp}.json"
    with open(metrics_file, "w") as f:
        f.write(metrics.to_json())
    logger.info(f"Saved metrics: {metrics_file}")
    
    # Save summary report
    summary_file = output_path / f"summary_{timestamp}.txt"
    with open(summary_file, "w") as f:
        f.write("=" * 70 + "\n")
        f.write("NETPILOT EVALUATION RESULTS\n")
        f.write("=" * 70 + "\n\n")
        f.write(f"Timestamp: {metrics.timestamp}\n")
        f.write(f"Total Scenarios: {metrics.total_scenarios}\n")
        f.write(f"Successful Recoveries: {metrics.successful_recoveries}/{metrics.total_scenarios}\n")
        f.write(f"Correct Actions: {metrics.correct_actions}/{metrics.total_scenarios}\n")
        f.write(f"Average MTTR: {metrics.average_mttr_seconds:.1f}s\n")
        f.write(f"False Positive Rate: {metrics.false_positive_rate:.1%}\n\n")
        
        f.write("INDIVIDUAL RESULTS\n")
        f.write("-" * 70 + "\n")
        for result in results:
            f.write(f"\nScenario: {result.scenario_name}\n")
            f.write(f"  Fault: {result.fault_type} on {result.target_service}\n")
            f.write(f"  Success: {'✓' if result.success else '✗'}\n")
            f.write(f"  MTTR: {result.mttr_seconds:.1f}s\n")
            f.write(f"  Correct action: {'✓' if result.correct_action_taken else '✗'} "
                   f"(expected {result.expected_action}, got {result.actual_action})\n")
            f.write(f"  SLA violations: {len(result.sla_violations)}\n")
            if result.sla_violations:
                for violation in result.sla_violations[:3]:  # Show first 3
                    f.write(f"    - {violation}\n")
    
    logger.info(f"Saved summary: {summary_file}")


if __name__ == "__main__":
    # Example: Run all scenarios in eval/scenarios/
    
    scenario_files = [
        "01-notification-crash.yaml",
        "02-inventory-degrade.yaml",
        "03-order-cascade.yaml",
    ]
    
    try:
        results, metrics = asyncio.run(run_scenario_suite(scenario_files))
        save_results(results, metrics)
    except KeyboardInterrupt:
        logger.info("Evaluation interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Evaluation failed: {e}", exc_info=True)
        sys.exit(1)
