#!/usr/bin/env python3
"""
Telemetry collector for netpilot.

Periodically collects:
- KPIs from Prometheus (error rate, latency, pod restarts)
- Recent logs from pod log streams
- Active alarms from Alertmanager webhook endpoint

Returns TelemetryBundle on configured interval (default: 30s).
"""

import asyncio
import json
import logging
import time
import subprocess
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path

import httpx
from schemas import TelemetryBundle, KPI, LogEvent, Alarm

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class TelemetryCollector:
    """Collects telemetry from Prometheus, logs, and Alertmanager."""
    
    def __init__(
        self,
        prometheus_url: str = "http://localhost:9090",
        alertmanager_url: str = "http://localhost:5000",
        namespace: str = "default",
        log_tail_lines: int = 50,
        services: Optional[List[str]] = None,
    ):
        """
        Initialize the telemetry collector.
        
        Args:
            prometheus_url: URL to Prometheus HTTP API
            alertmanager_url: URL to Alert Receiver webhook endpoint
            namespace: Kubernetes namespace to monitor
            log_tail_lines: Number of recent log lines to fetch per pod
            services: List of services to monitor (None = auto-discover)
        """
        self.prometheus_url = prometheus_url
        self.alertmanager_url = alertmanager_url
        self.namespace = namespace
        self.log_tail_lines = log_tail_lines
        self.services = services or [
            "frontend",
            "api-gateway",
            "order-service",
            "inventory-service",
            "notification-service",
        ]
        
        self.prometheus_client = None
        self.alertmanager_client = None
    
    async def __aenter__(self):
        """Async context manager entry."""
        self.prometheus_client = httpx.AsyncClient(timeout=10.0)
        self.alertmanager_client = httpx.AsyncClient(timeout=10.0)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self.prometheus_client:
            await self.prometheus_client.aclose()
        if self.alertmanager_client:
            await self.alertmanager_client.aclose()
    
    def _run_kubectl(self, cmd: List[str]) -> str:
        """Execute a kubectl command and return stdout."""
        try:
            result = subprocess.run(
                ["kubectl"] + cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                logger.warning(f"kubectl error: {result.stderr}")
                return ""
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            logger.warning(f"kubectl command timed out: {' '.join(cmd)}")
            return ""
        except Exception as e:
            logger.error(f"Failed to run kubectl: {e}")
            return ""
    
    async def query_prometheus(self, query: str) -> Dict[str, Any]:
        """Execute a Prometheus query."""
        try:
            url = f"{self.prometheus_url}/api/v1/query"
            params = {"query": query}
            response = await self.prometheus_client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("data", {})
        except Exception as e:
            logger.error(f"Prometheus query failed: {e}")
            return {}
    
    async def collect_kpis(self) -> Dict[str, KPI]:
        """Collect KPIs from Prometheus for all services."""
        kpis = {}
        now = datetime.utcnow()
        
        for service in self.services:
            try:
                # Error rate query: (5xx errors) / (total requests)
                error_rate_result = await self.query_prometheus(
                    f'(sum(rate(service_requests_total{{service="{service}",status=~"5.."}}[5m])) / '
                    f'sum(rate(service_requests_total{{service="{service}"}}[5m]))) or on() vector(0)'
                )
                error_rate = self._extract_scalar(error_rate_result)
                
                # Total request count in 5m
                req_count_result = await self.query_prometheus(
                    f'sum(increase(service_requests_total{{service="{service}"}}[5m])) or on() vector(0)'
                )
                req_count = self._extract_scalar(req_count_result)
                
                # P50, P95, P99 latency
                p50_result = await self.query_prometheus(
                    f'histogram_quantile(0.50, sum(rate(service_request_duration_seconds_bucket{{service="{service}"}}[5m])) by (le))'
                )
                p50 = self._extract_scalar(p50_result) * 1000  # Convert to ms
                
                p95_result = await self.query_prometheus(
                    f'histogram_quantile(0.95, sum(rate(service_request_duration_seconds_bucket{{service="{service}"}}[5m])) by (le))'
                )
                p95 = self._extract_scalar(p95_result) * 1000
                
                p99_result = await self.query_prometheus(
                    f'histogram_quantile(0.99, sum(rate(service_request_duration_seconds_bucket{{service="{service}"}}[5m])) by (le))'
                )
                p99 = self._extract_scalar(p99_result) * 1000
                
                # Pod restart count (total)
                restart_count_result = await self.query_prometheus(
                    f'increase(kube_pod_container_status_restarts_total{{pod=~"{service}.*"}}[24h]) or on() vector(0)'
                )
                restart_count = int(self._extract_scalar(restart_count_result))
                
                # Pod restart count in 5m
                restart_5m_result = await self.query_prometheus(
                    f'increase(kube_pod_container_status_restarts_total{{pod=~"{service}.*"}}[5m]) or on() vector(0)'
                )
                restart_5m = int(self._extract_scalar(restart_5m_result))
                
                # Downstream error rate
                downstream_error_result = await self.query_prometheus(
                    f'(sum(rate(service_downstream_calls_total{{service="{service}",status="error"}}[5m])) / '
                    f'sum(rate(service_downstream_calls_total{{service="{service}"}}[5m]))) or on() vector(0)'
                )
                downstream_error_rate = self._extract_scalar(downstream_error_result)
                
                # Downstream call count in 5m
                downstream_count_result = await self.query_prometheus(
                    f'sum(increase(service_downstream_calls_total{{service="{service}"}}[5m])) or on() vector(0)'
                )
                downstream_count = self._extract_scalar(downstream_count_result)
                
                # Service availability (check if metrics exist)
                availability_result = await self.query_prometheus(
                    f'up{{job="kubernetes-pods",app="{service}"}}'
                )
                available = len(availability_result.get("result", [])) > 0
                
                kpi = KPI(
                    service=service,
                    timestamp=now,
                    request_count_5m=req_count,
                    error_rate=error_rate,
                    latency_p50_ms=p50 if p50 > 0 else None,
                    latency_p95_ms=p95 if p95 > 0 else None,
                    latency_p99_ms=p99 if p99 > 0 else None,
                    downstream_error_rate=downstream_error_rate,
                    downstream_calls_5m=downstream_count,
                    pod_restart_count=restart_count,
                    pod_restart_count_5m=restart_5m,
                    available=available,
                )
                
                kpis[service] = kpi
                logger.debug(f"Collected KPI for {service}: error_rate={error_rate:.2%}, p99={p99}ms")
            
            except Exception as e:
                logger.error(f"Failed to collect KPIs for {service}: {e}")
        
        return kpis
    
    async def collect_logs(self) -> Dict[str, List[LogEvent]]:
        """Collect recent logs from all service pods."""
        logs: Dict[str, List[LogEvent]] = {}
        now = datetime.utcnow()
        
        for service in self.services:
            try:
                # Get pod name for service
                pod_name = self._get_pod_name(service)
                if not pod_name:
                    logger.debug(f"No pod found for {service}")
                    continue
                
                # Get logs from pod
                log_output = self._run_kubectl([
                    "logs", pod_name,
                    "-n", self.namespace,
                    "--tail", str(self.log_tail_lines),
                    "--timestamps=true",
                ])
                
                if not log_output:
                    continue
                
                service_logs = []
                for line in log_output.split("\n"):
                    if not line.strip():
                        continue
                    
                    # Parse log line (format: timestamp message)
                    try:
                        parts = line.split(" ", 1)
                        if len(parts) >= 2:
                            timestamp_str = parts[0]
                            message = parts[1] if len(parts) > 1 else ""
                            
                            # Try to parse timestamp
                            try:
                                # Kubernetes logs use RFC3339 format
                                timestamp = datetime.fromisoformat(
                                    timestamp_str.replace("Z", "+00:00")
                                )
                            except ValueError:
                                timestamp = now
                            
                            # Try to extract log level
                            level = None
                            for lvl in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
                                if lvl in message[:50]:  # Check first 50 chars
                                    level = lvl
                                    break
                            
                            log_event = LogEvent(
                                timestamp=timestamp,
                                service=service,
                                pod_name=pod_name,
                                level=level,
                                message=message[:500],  # Limit message length
                            )
                            service_logs.append(log_event)
                    except Exception as e:
                        logger.debug(f"Failed to parse log line '{line}': {e}")
                        continue
                
                if service_logs:
                    logs[service] = service_logs
                    logger.debug(f"Collected {len(service_logs)} log lines from {service}")
            
            except Exception as e:
                logger.error(f"Failed to collect logs for {service}: {e}")
        
        return logs
    
    async def collect_alarms(self) -> List[Alarm]:
        """Fetch active alarms from Alertmanager webhook endpoint."""
        alarms = []
        now = datetime.utcnow()
        
        try:
            # Query the alert-receiver endpoint
            url = f"{self.alertmanager_url}/alerts"
            response = await self.alertmanager_client.get(url)
            response.raise_for_status()
            data = response.json()
            
            for alert_data in data.get("active_alerts", []):
                try:
                    labels = alert_data.get("labels", {})
                    annotations = alert_data.get("annotations", {})
                    
                    # Parse timestamps
                    starts_at = alert_data.get("startsAt")
                    if isinstance(starts_at, str):
                        try:
                            starts_at = datetime.fromisoformat(
                                starts_at.replace("Z", "+00:00")
                            )
                        except ValueError:
                            starts_at = now
                    else:
                        starts_at = now
                    
                    ends_at = alert_data.get("endsAt")
                    if isinstance(ends_at, str):
                        try:
                            ends_at = datetime.fromisoformat(
                                ends_at.replace("Z", "+00:00")
                            )
                            if ends_at.year == 1:  # Prometheus uses year 1 for "not ended"
                                ends_at = None
                        except ValueError:
                            ends_at = None
                    else:
                        ends_at = None
                    
                    alarm = Alarm(
                        timestamp=now,
                        alert_name=labels.get("alertname", "unknown"),
                        status=alert_data.get("status", "unknown"),
                        severity=labels.get("severity", "unknown"),
                        service=labels.get("service"),
                        component=labels.get("component"),
                        summary=annotations.get("summary", ""),
                        description=annotations.get("description", ""),
                        starts_at=starts_at,
                        ends_at=ends_at,
                        raw_alert=alert_data,
                    )
                    alarms.append(alarm)
                
                except Exception as e:
                    logger.error(f"Failed to parse alert: {e}")
                    continue
            
            logger.debug(f"Collected {len(alarms)} alarms")
        
        except Exception as e:
            logger.error(f"Failed to fetch alarms: {e}")
        
        return alarms
    
    def _get_pod_name(self, service: str) -> Optional[str]:
        """Get the pod name for a service."""
        output = self._run_kubectl([
            "get", "pods",
            "-n", self.namespace,
            "-l", f"app={service}",
            "-o", "jsonpath={.items[0].metadata.name}",
        ])
        return output if output else None
    
    def _extract_scalar(self, prometheus_result: Dict[str, Any]) -> float:
        """Extract scalar value from Prometheus query result."""
        try:
            results = prometheus_result.get("result", [])
            if results:
                value = results[0].get("value", [None, None])
                if len(value) > 1:
                    return float(value[1])
        except (ValueError, KeyError, IndexError, TypeError):
            pass
        return 0.0
    
    async def collect(self) -> TelemetryBundle:
        """Collect all telemetry data and return a bundle."""
        start_time = time.time()
        errors = []
        
        try:
            # Collect KPIs
            kpis = await self.collect_kpis()
            logger.info(f"Collected KPIs for {len(kpis)} services")
        except Exception as e:
            logger.error(f"KPI collection failed: {e}")
            errors.append(f"KPI collection: {str(e)}")
            kpis = {}
        
        try:
            # Collect logs
            logs = await self.collect_logs()
            logger.info(f"Collected logs for {len(logs)} services")
        except Exception as e:
            logger.error(f"Log collection failed: {e}")
            errors.append(f"Log collection: {str(e)}")
            logs = {}
        
        try:
            # Collect alarms
            alarms = await self.collect_alarms()
            logger.info(f"Collected {len(alarms)} alarms")
        except Exception as e:
            logger.error(f"Alarm collection failed: {e}")
            errors.append(f"Alarm collection: {str(e)}")
            alarms = []
        
        collection_duration_ms = (time.time() - start_time) * 1000
        
        bundle = TelemetryBundle(
            timestamp=datetime.utcnow(),
            collection_duration_ms=collection_duration_ms,
            kpis=kpis,
            logs=logs,
            alarms=alarms,
            collection_errors=errors,
            services_monitored=list(kpis.keys()),
        )
        
        logger.info(
            f"Telemetry collection completed in {collection_duration_ms:.1f}ms. "
            f"Health: {bundle.is_healthy()}, Alarms: {len(alarms)}"
        )
        
        return bundle


async def run_collector_loop(
    interval_seconds: int = 30,
    prometheus_url: str = "http://localhost:9090",
    alertmanager_url: str = "http://localhost:5000",
    output_file: Optional[str] = None,
):
    """
    Run the telemetry collector in a loop.
    
    Args:
        interval_seconds: Interval between collections (default: 30s)
        prometheus_url: URL to Prometheus
        alertmanager_url: URL to Alert Receiver
        output_file: Optional file to write telemetry bundles (JSONL format)
    """
    async with TelemetryCollector(
        prometheus_url=prometheus_url,
        alertmanager_url=alertmanager_url,
    ) as collector:
        iteration = 0
        
        while True:
            try:
                iteration += 1
                logger.info(f"--- Collection iteration {iteration} ---")
                
                bundle = await collector.collect()
                
                # Log summary
                summary = bundle.get_service_summary()
                logger.info(f"Summary: {json.dumps(summary, indent=2)}")
                
                # Write to file if specified
                if output_file:
                    try:
                        with open(output_file, "a") as f:
                            f.write(bundle.json() + "\n")
                    except Exception as e:
                        logger.error(f"Failed to write to output file: {e}")
                
                # Wait for next iteration
                logger.info(f"Next collection in {interval_seconds}s...")
                await asyncio.sleep(interval_seconds)
            
            except KeyboardInterrupt:
                logger.info("Collector interrupted by user")
                break
            except Exception as e:
                logger.error(f"Unexpected error in collection loop: {e}", exc_info=True)
                await asyncio.sleep(interval_seconds)


if __name__ == "__main__":
    import sys
    import argparse
    
    parser = argparse.ArgumentParser(description="Telemetry collector for netpilot")
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Collection interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--prometheus-url",
        default="http://localhost:9090",
        help="Prometheus URL (default: http://localhost:9090)",
    )
    parser.add_argument(
        "--alertmanager-url",
        default="http://localhost:5000",
        help="Alert Receiver URL (default: http://localhost:5000)",
    )
    parser.add_argument(
        "--output-file",
        help="Write telemetry bundles to JSONL file",
    )
    
    args = parser.parse_args()
    
    try:
        asyncio.run(
            run_collector_loop(
                interval_seconds=args.interval,
                prometheus_url=args.prometheus_url,
                alertmanager_url=args.alertmanager_url,
                output_file=args.output_file,
            )
        )
    except KeyboardInterrupt:
        logger.info("Collector stopped")
        sys.exit(0)
