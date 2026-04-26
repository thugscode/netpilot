"""
Formatter for converting raw telemetry into structured context for LLM processing.
"""

import json
from datetime import datetime
from typing import Dict, Any, Optional
from schemas import TelemetryBundle


class TelemetryFormatter:
    """Converts TelemetryBundle to structured context formats."""
    
    @staticmethod
    def to_json(bundle: TelemetryBundle) -> str:
        """Convert bundle to JSON string."""
        return bundle.json(indent=2)
    
    @staticmethod
    def to_dict(bundle: TelemetryBundle) -> Dict[str, Any]:
        """Convert bundle to dictionary."""
        return json.loads(bundle.json())
    
    @staticmethod
    def to_markdown(bundle: TelemetryBundle) -> str:
        """Convert bundle to human-readable Markdown format."""
        lines = []
        
        # Header
        lines.append(f"# Telemetry Report - {bundle.timestamp.isoformat()}")
        lines.append(f"**Collection Duration:** {bundle.collection_duration_ms:.1f}ms")
        lines.append(f"**System Health:** {'✓ Healthy' if bundle.is_healthy() else '✗ Unhealthy'}")
        lines.append("")
        
        # Summary
        if bundle.collection_errors:
            lines.append("## ⚠️ Collection Errors")
            for error in bundle.collection_errors:
                lines.append(f"- {error}")
            lines.append("")
        
        # KPIs by service
        if bundle.kpis:
            lines.append("## 📊 Key Performance Indicators")
            lines.append("")
            
            for service_name, kpi in bundle.kpis.items():
                status = "✓" if kpi.available else "✗"
                lines.append(f"### {status} {service_name}")
                lines.append("")
                
                lines.append(f"| Metric | Value |")
                lines.append(f"|--------|-------|")
                lines.append(f"| Available | {'Yes' if kpi.available else 'No'} |")
                lines.append(f"| Request Count (5m) | {kpi.request_count_5m:.0f} |")
                lines.append(f"| Error Rate | {kpi.error_rate*100:.2f}% |")
                lines.append(f"| P50 Latency | {kpi.latency_p50_ms:.1f}ms" if kpi.latency_p50_ms else "| P50 Latency | N/A |")
                lines.append(f"| P99 Latency | {kpi.latency_p99_ms:.1f}ms" if kpi.latency_p99_ms else "| P99 Latency | N/A |")
                lines.append(f"| Pod Restarts (total) | {kpi.pod_restart_count} |")
                lines.append(f"| Pod Restarts (5m) | {kpi.pod_restart_count_5m} |")
                if kpi.downstream_error_rate is not None:
                    lines.append(f"| Downstream Error Rate | {kpi.downstream_error_rate*100:.2f}% |")
                if kpi.downstream_calls_5m is not None:
                    lines.append(f"| Downstream Calls (5m) | {kpi.downstream_calls_5m:.0f} |")
                
                lines.append("")
        
        # Alarms
        if bundle.alarms:
            lines.append("## 🚨 Active Alarms")
            lines.append("")
            
            firing_alarms = [a for a in bundle.alarms if a.status == "firing"]
            resolved_alarms = [a for a in bundle.alarms if a.status == "resolved"]
            
            if firing_alarms:
                lines.append("### Firing")
                for alarm in firing_alarms:
                    icon = "🔴" if alarm.severity == "critical" else "🟡"
                    lines.append(
                        f"{icon} **{alarm.alert_name}** ({alarm.severity})"
                    )
                    if alarm.service:
                        lines.append(f"   - Service: {alarm.service}")
                    if alarm.component:
                        lines.append(f"   - Component: {alarm.component}")
                    lines.append(f"   - {alarm.summary}")
                    lines.append(f"   - {alarm.description}")
                    lines.append("")
            
            if resolved_alarms:
                lines.append("### Resolved")
                for alarm in resolved_alarms:
                    lines.append(f"✓ {alarm.alert_name}")
                lines.append("")
        
        # Recent logs
        if bundle.logs:
            lines.append("## 📋 Recent Logs")
            lines.append("")
            
            for service_name, logs in bundle.logs.items():
                lines.append(f"### {service_name}")
                lines.append("")
                lines.append("```")
                for log in logs[-10:]:  # Last 10 logs
                    level_str = f"[{log.level}]" if log.level else ""
                    lines.append(f"{log.timestamp.isoformat()} {level_str} {log.message}")
                lines.append("```")
                lines.append("")
        
        return "\n".join(lines)
    
    @staticmethod
    def to_context_window(bundle: TelemetryBundle, max_tokens: int = 4096) -> str:
        """
        Convert bundle to condensed context suitable for LLM input.
        Prioritizes alarms and errors, condenses healthy metrics.
        """
        lines = []
        
        # Header with timestamp and overall health
        lines.append(f"## TELEMETRY SNAPSHOT ({bundle.timestamp.isoformat()})")
        lines.append(f"System Health: {'HEALTHY' if bundle.is_healthy() else 'DEGRADED'}")
        lines.append(f"Collection Time: {bundle.collection_duration_ms:.0f}ms")
        lines.append("")
        
        # Critical information first
        firing_alarms = [a for a in bundle.alarms if a.status == "firing"]
        critical_alarms = [a for a in firing_alarms if a.severity == "critical"]
        
        if critical_alarms:
            lines.append("## CRITICAL ISSUES")
            for alarm in critical_alarms:
                lines.append(f"- [{alarm.alert_name}] {alarm.summary}")
                lines.append(f"  Details: {alarm.description}")
        
        if firing_alarms and not critical_alarms:
            lines.append("## WARNINGS")
            for alarm in firing_alarms:
                lines.append(f"- [{alarm.alert_name}] {alarm.summary}")
        
        # Unhealthy services
        unhealthy_services = [
            (name, kpi) for name, kpi in bundle.kpis.items()
            if not kpi.available or kpi.error_rate > 0.05
        ]
        
        if unhealthy_services:
            lines.append("## UNHEALTHY SERVICES")
            for name, kpi in unhealthy_services:
                if not kpi.available:
                    lines.append(f"- {name}: UNAVAILABLE")
                if kpi.error_rate > 0.05:
                    lines.append(
                        f"- {name}: High error rate ({kpi.error_rate*100:.1f}%)"
                    )
                if kpi.pod_restart_count_5m > 2:
                    lines.append(
                        f"- {name}: High restart rate ({kpi.pod_restart_count_5m} in 5m)"
                    )
        
        # High latency services
        high_latency = [
            (name, kpi) for name, kpi in bundle.kpis.items()
            if kpi.latency_p99_ms and kpi.latency_p99_ms > 500
        ]
        
        if high_latency:
            lines.append("## HIGH LATENCY")
            for name, kpi in high_latency:
                lines.append(f"- {name}: P99={kpi.latency_p99_ms:.0f}ms")
        
        # Summary of healthy services
        healthy_services = [
            (name, kpi) for name, kpi in bundle.kpis.items()
            if kpi.available and kpi.error_rate <= 0.05
        ]
        
        if healthy_services:
            lines.append("## HEALTHY SERVICES")
            for name, kpi in healthy_services:
                metric_str = f"({kpi.request_count_5m:.0f} req, {kpi.error_rate*100:.1f}% err"
                if kpi.latency_p99_ms:
                    metric_str += f", {kpi.latency_p99_ms:.0f}ms p99"
                metric_str += ")"
                lines.append(f"- {name} {metric_str}")
        
        # Recent error logs
        error_logs = []
        for service_name, logs in bundle.logs.items():
            for log in logs:
                if log.level and log.level in ["ERROR", "CRITICAL", "WARNING"]:
                    error_logs.append((service_name, log))
        
        if error_logs:
            lines.append("## RECENT ERRORS")
            for service_name, log in error_logs[-5:]:  # Last 5 errors
                lines.append(f"- [{service_name}] {log.message}")
        
        # Collection errors
        if bundle.collection_errors:
            lines.append("## COLLECTION ERRORS")
            for error in bundle.collection_errors:
                lines.append(f"- {error}")
        
        return "\n".join(lines)
    
    @staticmethod
    def to_jsonl(bundle: TelemetryBundle) -> str:
        """Convert bundle to single-line JSON (for logging)."""
        data = json.loads(bundle.json())
        return json.dumps(data, separators=(',', ':'))
