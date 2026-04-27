"""
Executor module: Remediates Kubernetes failures by running approved actions.

Dispatches on action_type and executes via kubectl:
- restart_pod: Delete pod to trigger Kubernetes restart
- scale_up: Scale deployment to more replicas
- reroute_traffic: Patch VirtualService (stub for now)
- rollback_deploy: Rollback deployment to previous image
- noop: No-op (log only)

All operations wrapped in try/except with structured error responses.
"""

import subprocess
import logging
import sys
from typing import Dict, Tuple, Any, Optional
from datetime import datetime
from pathlib import Path

# Add parent dirs to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.models import RemediationAction
from policy.invariants import ROLLBACK_REGISTRY

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [executor] %(message)s"
)
logger = logging.getLogger(__name__)


class RemediationError(Exception):
    """Custom exception for remediation execution failures."""
    
    def __init__(self, action_type: str, target: str, message: str):
        self.action_type = action_type
        self.target = target
        self.message = message
        super().__init__(f"[{action_type}:{target}] {message}")


class ExecutionResult:
    """Structured result of remediation execution."""
    
    def __init__(
        self,
        success: bool,
        action_type: str,
        target: str,
        output: Optional[str] = None,
        error: Optional[str] = None,
        exit_code: Optional[int] = None,
    ):
        self.success = success
        self.action_type = action_type
        self.target = target
        self.output = output or ""
        self.error = error or ""
        self.exit_code = exit_code
        self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "success": self.success,
            "action_type": self.action_type,
            "target": self.target,
            "output": self.output,
            "error": self.error,
            "exit_code": self.exit_code,
            "timestamp": self.timestamp,
        }


def execute(action: RemediationAction) -> ExecutionResult:
    """
    Execute a remediation action.

    Args:
        action: RemediationAction with type, target, params, confidence, rationale

    Returns:
        ExecutionResult with success status, output/error, and exit code
    """
    logger.info(
        f"Executing {action.action_type} on {action.target} "
        f"(confidence: {action.confidence:.1%}, rationale: {action.rationale})"
    )

    try:
        if action.action_type == "restart_pod":
            return _restart_pod(action)
        elif action.action_type == "scale_up":
            return _scale_up(action)
        elif action.action_type == "reroute_traffic":
            return _reroute_traffic(action)
        elif action.action_type == "rollback_deploy":
            return _rollback_deploy(action)
        elif action.action_type == "noop":
            return _noop(action)
        else:
            raise RemediationError(
                action.action_type,
                action.target,
                f"Unknown action type: {action.action_type}",
            )
    except RemediationError as e:
        logger.error(f"Remediation failed: {e}")
        return ExecutionResult(
            success=False,
            action_type=action.action_type,
            target=action.target,
            error=str(e),
        )
    except Exception as e:
        logger.error(f"Unexpected error executing {action.action_type}: {e}")
        return ExecutionResult(
            success=False,
            action_type=action.action_type,
            target=action.target,
            error=f"Unexpected error: {str(e)}",
        )


def _restart_pod(action: RemediationAction) -> ExecutionResult:
    """
    Restart pod by deleting it (Kubernetes will auto-restart).

    Command: kubectl delete pod -l app={target} --grace-period=0

    Args:
        action: RemediationAction with action_type="restart_pod"

    Returns:
        ExecutionResult with kubectl output or error
    """
    logger.info(f"Restarting pod for service: {action.target}")

    cmd = [
        "kubectl",
        "delete",
        "pod",
        "-l",
        f"app={action.target}",
        "--grace-period=0",
        "--force=true",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            logger.info(f"✓ Pod restart successful for {action.target}")
            logger.debug(f"kubectl output: {result.stdout}")
            return ExecutionResult(
                success=True,
                action_type="restart_pod",
                target=action.target,
                output=result.stdout,
                exit_code=0,
            )
        else:
            error_msg = result.stderr or result.stdout
            logger.error(f"✗ Pod restart failed: {error_msg}")
            raise RemediationError("restart_pod", action.target, error_msg)

    except subprocess.TimeoutExpired as e:
        error_msg = f"kubectl command timed out: {str(e)}"
        logger.error(f"✗ {error_msg}")
        raise RemediationError("restart_pod", action.target, error_msg)
    except FileNotFoundError as e:
        error_msg = f"kubectl not found in PATH: {str(e)}"
        logger.error(f"✗ {error_msg}")
        raise RemediationError("restart_pod", action.target, error_msg)


def _scale_up(action: RemediationAction) -> ExecutionResult:
    """
    Scale up deployment by increasing replicas.

    Command: kubectl scale deployment {target} --replicas={params['replicas']}

    Args:
        action: RemediationAction with action_type="scale_up", params['replicas']

    Returns:
        ExecutionResult with kubectl output or error
    """
    if "replicas" not in action.params:
        raise RemediationError(
            "scale_up",
            action.target,
            "Missing 'replicas' parameter",
        )

    replicas = action.params["replicas"]
    logger.info(f"Scaling up {action.target} to {replicas} replicas")

    cmd = [
        "kubectl",
        "scale",
        "deployment",
        action.target,
        f"--replicas={replicas}",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            logger.info(f"✓ Scale up successful: {action.target} → {replicas} replicas")
            logger.debug(f"kubectl output: {result.stdout}")
            return ExecutionResult(
                success=True,
                action_type="scale_up",
                target=action.target,
                output=result.stdout,
                exit_code=0,
            )
        else:
            error_msg = result.stderr or result.stdout
            logger.error(f"✗ Scale up failed: {error_msg}")
            raise RemediationError("scale_up", action.target, error_msg)

    except subprocess.TimeoutExpired as e:
        error_msg = f"kubectl command timed out: {str(e)}"
        logger.error(f"✗ {error_msg}")
        raise RemediationError("scale_up", action.target, error_msg)
    except FileNotFoundError as e:
        error_msg = f"kubectl not found in PATH: {str(e)}"
        logger.error(f"✗ {error_msg}")
        raise RemediationError("scale_up", action.target, error_msg)


def _reroute_traffic(action: RemediationAction) -> ExecutionResult:
    """
    Reroute traffic by patching VirtualService (stub).

    For now, this logs the intent. Full implementation would:
    - Patch Istio VirtualService
    - Update traffic weights
    - Monitor rerouting

    Command: kubectl patch virtualservice {target} -p '{...}'

    Args:
        action: RemediationAction with action_type="reroute_traffic"

    Returns:
        ExecutionResult with log entry (success simulated)
    """
    dest_service = action.params.get("dest_service", "unknown")
    
    logger.info(
        f"[STUB] Rerouting traffic from {action.target} to {dest_service}"
    )
    logger.info(
        f"[STUB] Would patch VirtualService: "
        f"kubectl patch virtualservice {action.target} "
        f"-p '{{\"spec\":{{\"hosts\":[{{\"{dest_service}\"}}]}}}}'  "
    )

    # Simulated stub - log intent but don't execute
    return ExecutionResult(
        success=True,
        action_type="reroute_traffic",
        target=action.target,
        output=f"Traffic reroute to {dest_service} (STUB - not executed)",
        exit_code=0,
    )


def _rollback_deploy(action: RemediationAction) -> ExecutionResult:
    """
    Rollback deployment to previous image.

    Queries ROLLBACK_REGISTRY for previous_image and rolls back via:
    Command: kubectl set image deployment/{target} app={previous_image}

    Args:
        action: RemediationAction with action_type="rollback_deploy"

    Returns:
        ExecutionResult with kubectl output or error
    """
    if action.target not in ROLLBACK_REGISTRY:
        raise RemediationError(
            "rollback_deploy",
            action.target,
            f"Service {action.target} not in ROLLBACK_REGISTRY",
        )

    rollback_entry = ROLLBACK_REGISTRY[action.target]
    if "previous_image" not in rollback_entry or not rollback_entry["previous_image"]:
        raise RemediationError(
            "rollback_deploy",
            action.target,
            "No previous_image available in ROLLBACK_REGISTRY",
        )

    previous_image = rollback_entry["previous_image"]
    logger.info(f"Rolling back {action.target} to image: {previous_image}")

    cmd = [
        "kubectl",
        "set",
        "image",
        f"deployment/{action.target}",
        f"app={previous_image}",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode == 0:
            logger.info(f"✓ Rollback successful: {action.target} → {previous_image}")
            logger.debug(f"kubectl output: {result.stdout}")
            return ExecutionResult(
                success=True,
                action_type="rollback_deploy",
                target=action.target,
                output=result.stdout,
                exit_code=0,
            )
        else:
            error_msg = result.stderr or result.stdout
            logger.error(f"✗ Rollback failed: {error_msg}")
            raise RemediationError("rollback_deploy", action.target, error_msg)

    except subprocess.TimeoutExpired as e:
        error_msg = f"kubectl command timed out: {str(e)}"
        logger.error(f"✗ {error_msg}")
        raise RemediationError("rollback_deploy", action.target, error_msg)
    except FileNotFoundError as e:
        error_msg = f"kubectl not found in PATH: {str(e)}"
        logger.error(f"✗ {error_msg}")
        raise RemediationError("rollback_deploy", action.target, error_msg)


def _noop(action: RemediationAction) -> ExecutionResult:
    """
    No-op action - log and return success.

    Args:
        action: RemediationAction with action_type="noop"

    Returns:
        ExecutionResult with success=True, no output
    """
    logger.info(f"No action taken for {action.target}")
    
    return ExecutionResult(
        success=True,
        action_type="noop",
        target=action.target,
        output="No action taken",
        exit_code=0,
    )


def batch_execute(actions: list) -> list:
    """
    Execute multiple remediation actions sequentially.

    Args:
        actions: List of RemediationAction objects

    Returns:
        List of ExecutionResult objects
    """
    logger.info(f"Executing batch of {len(actions)} remediation actions")
    
    results = []
    for action in actions:
        result = execute(action)
        results.append(result)
        logger.info(f"Action {action.action_type} on {action.target}: "
                   f"{'SUCCESS' if result.success else 'FAILED'}")
    
    return results


if __name__ == "__main__":
    # Simple test: verify imports work
    logger.info("executor.remediation module loaded successfully")
