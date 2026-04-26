#!/usr/bin/env python3
"""
Fault injection CLI for netpilot simulation.

Supports:
  - pod-crash: Delete a pod to trigger Kubernetes restart
  - link-degrade: Use tc (traffic control) to degrade network on a pod
  - cascade: Trigger pod-crash on order-service and watch downstream alarms
"""

import json
import subprocess
import sys
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import click
import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Events log file
EVENTS_LOG = Path(__file__).parent / "events.jsonl"
NAMESPACE = "default"
CLUSTER_NAME = "netpilot"


def log_event(scenario: str, target: str, details: Optional[Dict[str, Any]] = None) -> None:
    """Log a fault injection event to events.jsonl."""
    event = {
        "timestamp": datetime.utcnow().isoformat(),
        "scenario": scenario,
        "target": target,
    }
    if details:
        event.update(details)
    
    with open(EVENTS_LOG, "a") as f:
        f.write(json.dumps(event) + "\n")
    
    logger.info(f"Event logged: {event}")


def run_kubectl(cmd: List[str], check: bool = True) -> subprocess.CompletedProcess:
    """Execute a kubectl command."""
    full_cmd = ["kubectl"] + cmd
    logger.debug(f"Running: {' '.join(full_cmd)}")
    return subprocess.run(full_cmd, capture_output=True, text=True, check=check)


def get_pod_name(deployment: str) -> Optional[str]:
    """Get the pod name for a deployment."""
    try:
        result = run_kubectl([
            "get", "pods",
            "-n", NAMESPACE,
            "-l", f"app={deployment}",
            "-o", "jsonpath={.items[0].metadata.name}",
        ])
        pod_name = result.stdout.strip()
        if pod_name:
            return pod_name
        logger.error(f"No pod found for deployment {deployment}")
        return None
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to get pod name: {e.stderr}")
        return None


@click.command()
@click.option(
    "--scenario",
    type=click.Choice(["pod-crash", "link-degrade", "cascade"]),
    required=True,
    help="Fault injection scenario to execute",
)
@click.option(
    "--target",
    default="notification-service",
    help="Target service/pod name (default: notification-service)",
)
@click.option(
    "--duration",
    type=int,
    default=60,
    help="Duration in seconds for link-degrade scenario (default: 60)",
)
@click.option(
    "--watch-duration",
    type=int,
    default=30,
    help="Duration in seconds to watch for cascade effects (default: 30)",
)
def inject_fault(scenario: str, target: str, duration: int, watch_duration: int) -> None:
    """Inject faults into the netpilot cluster for testing."""
    
    if scenario == "pod-crash":
        pod_crash(target)
    elif scenario == "link-degrade":
        link_degrade(target, duration)
    elif scenario == "cascade":
        cascade_failure(target, watch_duration)


def pod_crash(target: str) -> None:
    """
    Delete a named pod so Kubernetes automatically restarts it.
    """
    click.echo(f"🔥 Triggering pod-crash on {target}...")
    
    pod_name = get_pod_name(target)
    if not pod_name:
        click.secho(f"❌ Failed to find pod for {target}", fg="red")
        sys.exit(1)
    
    try:
        # Record the pod state before deletion
        before_state = run_kubectl([
            "get", "pod", pod_name,
            "-n", NAMESPACE,
            "-o", "json",
        ])
        
        # Delete the pod
        result = run_kubectl([
            "delete", "pod", pod_name,
            "-n", NAMESPACE,
        ])
        
        click.secho(f"✓ Pod {pod_name} deleted", fg="green")
        logger.info(f"kubectl output: {result.stdout}")
        
        # Log the event
        log_event(
            scenario="pod-crash",
            target=target,
            details={
                "pod_name": pod_name,
                "action": "deleted",
                "expected_action": "kubernetes will restart pod",
            },
        )
        
        # Wait a bit for the pod to be recreated
        click.echo("⏳ Waiting for pod restart...")
        time.sleep(3)
        
        # Verify pod is restarting
        for attempt in range(10):
            result = run_kubectl([
                "get", "pod", pod_name,
                "-n", NAMESPACE,
                "-o", "jsonpath={.status.phase}",
            ], check=False)
            
            phase = result.stdout.strip()
            if phase and phase != "Terminating":
                click.secho(f"✓ Pod restarted! Phase: {phase}", fg="green")
                logger.info(f"Pod {pod_name} is now in phase: {phase}")
                break
            
            logger.debug(f"Attempt {attempt + 1}: Pod phase = {phase}")
            time.sleep(1)
    
    except subprocess.CalledProcessError as e:
        click.secho(f"❌ Error executing kubectl: {e.stderr}", fg="red")
        sys.exit(1)


def link_degrade(target: str, duration: int) -> None:
    """
    Use traffic control (tc) via kubectl exec to degrade network on a pod.
    Adds 200ms latency and 10% packet loss for the specified duration.
    """
    click.echo(f"🌐 Degrading network link on {target} for {duration}s...")
    
    pod_name = get_pod_name(target)
    if not pod_name:
        click.secho(f"❌ Failed to find pod for {target}", fg="red")
        sys.exit(1)
    
    try:
        # Add network delay and loss
        cmd = [
            "exec", pod_name,
            "-n", NAMESPACE,
            "--",
            "sh", "-c",
            "tc qdisc add dev eth0 root netem delay 200ms loss 10%",
        ]
        
        result = run_kubectl(cmd)
        click.secho(f"✓ Network degradation applied to {pod_name}", fg="green")
        logger.info(f"tc netem output: {result.stdout}")
        
        # Log the event
        log_event(
            scenario="link-degrade",
            target=target,
            details={
                "pod_name": pod_name,
                "delay_ms": 200,
                "loss_percent": 10,
                "duration_seconds": duration,
            },
        )
        
        # Wait for the specified duration
        click.echo(f"⏳ Network degraded for {duration}s...")
        time.sleep(duration)
        
        # Remove network degradation
        cmd_remove = [
            "exec", pod_name,
            "-n", NAMESPACE,
            "--",
            "sh", "-c",
            "tc qdisc del dev eth0 root",
        ]
        
        result_remove = run_kubectl(cmd_remove)
        click.secho(f"✓ Network degradation removed from {pod_name}", fg="green")
        logger.info(f"tc qdisc removal: {result_remove.stdout}")
        
    except subprocess.CalledProcessError as e:
        click.secho(f"⚠ Error with network degradation: {e.stderr}", fg="yellow")
        logger.warning(f"tc command failed, this may be expected if tc is not available: {e.stderr}")


def cascade_failure(target: str, watch_duration: int) -> None:
    """
    Trigger pod-crash on order-service and watch for downstream alarms.
    Observes metric changes and service errors propagating up the call chain.
    """
    click.echo("📊 Starting cascade failure scenario...")
    click.echo(f"   Target: {target}")
    click.echo(f"   Watch duration: {watch_duration}s")
    
    # Log cascade start
    log_event(
        scenario="cascade",
        target=target,
        details={
            "action": "cascade_started",
            "watch_duration_seconds": watch_duration,
        },
    )
    
    # Phase 1: Crash the target
    click.echo(f"\n[Phase 1] Crashing {target}...")
    pod_name = get_pod_name(target)
    if not pod_name:
        click.secho(f"❌ Failed to find pod for {target}", fg="red")
        sys.exit(1)
    
    try:
        run_kubectl(["delete", "pod", pod_name, "-n", NAMESPACE])
        click.secho(f"✓ Pod {pod_name} deleted", fg="green")
    except subprocess.CalledProcessError as e:
        click.secho(f"❌ Failed to delete pod: {e.stderr}", fg="red")
        sys.exit(1)
    
    # Phase 2: Watch for cascade effects
    click.echo(f"\n[Phase 2] Watching for cascade effects ({watch_duration}s)...\n")
    
    start_time = time.time()
    cascade_observed = {
        "order-service": False,
        "api-gateway": False,
        "frontend": False,
    }
    
    # Services to watch in order (going upstream from the target)
    watch_services = ["order-service", "api-gateway", "frontend"]
    
    try:
        while time.time() - start_time < watch_duration:
            elapsed = int(time.time() - start_time)
            
            for service in watch_services:
                if cascade_observed[service]:
                    continue
                
                pod = get_pod_name(service)
                if not pod:
                    continue
                
                # Check if service is reporting errors
                try:
                    # Try to port-forward and check metrics
                    result = run_kubectl([
                        "exec", pod,
                        "-n", NAMESPACE,
                        "--",
                        "wget", "-q", "-O", "-", "http://localhost:8000/metrics",
                    ], check=False)
                    
                    metrics_output = result.stdout
                    
                    # Check for increased error rates in metrics
                    if "service_requests_total" in metrics_output:
                        # Service is alive and responding
                        if "status=\"500\"" in metrics_output or "status=\"503\"" in metrics_output:
                            click.secho(
                                f"   [{elapsed}s] ⚠ {service} reporting errors - cascade detected!",
                                fg="yellow",
                            )
                            cascade_observed[service] = True
                            
                            # Log cascade observation
                            log_event(
                                scenario="cascade",
                                target=service,
                                details={
                                    "event": "cascade_detected",
                                    "elapsed_seconds": elapsed,
                                    "root_cause": target,
                                },
                            )
                except Exception:
                    # Service likely crashed/unreachable
                    click.secho(
                        f"   [{elapsed}s] ❌ {service} unreachable - cascade propagated!",
                        fg="red",
                    )
                    cascade_observed[service] = True
                    
                    # Log cascade propagation
                    log_event(
                        scenario="cascade",
                        target=service,
                        details={
                            "event": "cascade_propagated",
                            "elapsed_seconds": elapsed,
                            "root_cause": target,
                        },
                    )
            
            time.sleep(2)
    
    except KeyboardInterrupt:
        click.secho("\n✓ Cascade watching interrupted by user", fg="blue")
    
    # Summary
    click.echo(f"\n[Phase 3] Cascade Summary:")
    click.echo(f"   {target}: Initial fault")
    for service in watch_services:
        status = "✓ Cascade detected" if cascade_observed[service] else "○ No cascade"
        color = "red" if cascade_observed[service] else "blue"
        click.secho(f"   {service}: {status}", fg=color)
    
    # Log cascade completion
    log_event(
        scenario="cascade",
        target=target,
        details={
            "action": "cascade_completed",
            "cascade_observed": cascade_observed,
        },
    )


if __name__ == "__main__":
    inject_fault()
