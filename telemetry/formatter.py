"""
Formatter for converting raw telemetry into structured context for LLM processing.
"""

import json
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from schemas import TelemetryBundle, KPI, Alarm, LogEvent


class TelemetryFormatter:
    """Converts TelemetryBundle to structured context formats."""
    
    # Rough token estimates (characters / 4 = tokens)
    TOKENS_PER_CHAR = 0.25
    CONTEXT_WINDOW_TOKENS = 3000
    
    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate token count for text (rough approximation)."""
        return int(len(text) * TelemetryFormatter.TOKENS_PER_CHAR)
    
    @staticmethod
    def to_json(bundle: TelemetryBundle) -> str:
        """Convert bundle to JSON string."""
        return bundle.model_dump_json(indent=2)
    
    @staticmethod
    def to_dict(bundle: TelemetryBundle) -> Dict[str, Any]:
        """Convert bundle to dictionary."""
        return bundle.model_dump()
    
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
    def to_context_window(
        bundle: TelemetryBundle,
        max_tokens: int = 3000,
    ) -> str:
        """
        Convert bundle to LLM-optimized context window format.
        
        Intelligently truncates content to fit within token limit using priority:
        1. Critical issues (alarms)
        2. Unhealthy services
        3. High latency services
        4. Error logs (oldest first)
        5. Healthy services (least anomalous first)
        
        Args:
            bundle: TelemetryBundle to format
            max_tokens: Maximum tokens to use (default: 3000)
            
        Returns:
            Compact JSON-like string optimized for LLM context
        """
        # Build content incrementally, respecting token limit
        content = {
            "snapshot": {
                "timestamp": bundle.timestamp.isoformat(),
                "health": "HEALTHY" if bundle.is_healthy() else "DEGRADED",
                "collection_ms": round(bundle.collection_duration_ms),
            },
            "critical_issues": [],
            "warnings": [],
            "unhealthy_services": {},
            "high_latency": {},
            "healthy_services": {},
            "recent_errors": [],
        }
        
        total_tokens = TelemetryFormatter.estimate_tokens(json.dumps(content))
        
        # Priority 1: Critical alarms
        critical_alarms = [
            a for a in bundle.alarms 
            if a.status == "firing" and a.severity == "critical"
        ]
        for alarm in critical_alarms:
            if total_tokens >= max_tokens * 0.95:
                break
            issue = {
                "alert": alarm.alert_name,
                "service": alarm.service,
                "summary": alarm.summary[:150],
            }
            content["critical_issues"].append(issue)
            total_tokens = TelemetryFormatter.estimate_tokens(json.dumps(content))
        
        # Priority 2: Warning alarms
        warning_alarms = [
            a for a in bundle.alarms 
            if a.status == "firing" and a.severity != "critical"
        ]
        # Sort by severity (warning first, then info)
        warning_alarms.sort(key=lambda a: (a.severity != "warning", a.timestamp), reverse=True)
        
        for alarm in warning_alarms:
            if total_tokens >= max_tokens * 0.90:
                break
            warn = {
                "alert": alarm.alert_name,
                "service": alarm.service,
                "summary": alarm.summary[:120],
            }
            content["warnings"].append(warn)
            total_tokens = TelemetryFormatter.estimate_tokens(json.dumps(content))
        
        # Priority 3: Unhealthy services
        unhealthy = [
            (name, kpi) for name, kpi in bundle.kpis.items()
            if not kpi.available or kpi.error_rate > 0.05
        ]
        # Sort by anomaly (most unhealthy first)
        unhealthy.sort(
            key=lambda x: (x[1].available, 1.0 - x[1].error_rate),
            reverse=False
        )
        
        for name, kpi in unhealthy:
            if total_tokens >= max_tokens * 0.80:
                break
            service_info = {
                "available": kpi.available,
                "error_rate_pct": round(kpi.error_rate * 100, 1),
                "p99_ms": round(kpi.latency_p99_ms) if kpi.latency_p99_ms else None,
                "restarts_5m": kpi.pod_restart_count_5m,
            }
            content["unhealthy_services"][name] = service_info
            total_tokens = TelemetryFormatter.estimate_tokens(json.dumps(content))
        
        # Priority 4: High latency services
        high_latency = [
            (name, kpi) for name, kpi in bundle.kpis.items()
            if kpi.latency_p99_ms and kpi.latency_p99_ms > 500
            and name not in content["unhealthy_services"]
        ]
        for name, kpi in high_latency:
            if total_tokens >= max_tokens * 0.75:
                break
            content["high_latency"][name] = {
                "p99_ms": round(kpi.latency_p99_ms),
                "p95_ms": round(kpi.latency_p95_ms) if kpi.latency_p95_ms else None,
            }
            total_tokens = TelemetryFormatter.estimate_tokens(json.dumps(content))
        
        # Priority 5: Error logs (oldest first for truncation)
        error_logs: List[Tuple[str, LogEvent]] = []
        for service_name, logs in bundle.logs.items():
            for log in logs:
                if log.level and log.level in ["ERROR", "CRITICAL"]:
                    error_logs.append((service_name, log))
        
        # Sort by timestamp, oldest first (so we drop oldest on truncation)
        error_logs.sort(key=lambda x: x[1].timestamp)
        
        for service_name, log in error_logs:
            if total_tokens >= max_tokens * 0.70:
                break
            error_entry = {
                "service": service_name,
                "level": log.level,
                "message": log.message[:100],
                "timestamp": log.timestamp.isoformat(),
            }
            content["recent_errors"].append(error_entry)
            total_tokens = TelemetryFormatter.estimate_tokens(json.dumps(content))
        
        # Priority 6: Healthy services (least anomalous first)
        healthy = [
            (name, kpi) for name, kpi in bundle.kpis.items()
            if kpi.available and kpi.error_rate <= 0.05
            and name not in content["unhealthy_services"]
            and name not in content["high_latency"]
        ]
        # Sort by anomaly score (least anomalous first) for truncation priority
        # Least anomalous = low error rate, low latency, low restarts
        anomaly_score = lambda kpi: (
            kpi.error_rate,
            kpi.latency_p99_ms or 0,
            kpi.pod_restart_count_5m,
        )
        healthy.sort(key=lambda x: anomaly_score(x[1]))
        
        for name, kpi in healthy:
            if total_tokens >= max_tokens * 0.65:
                break
            service_info = {
                "error_rate_pct": round(kpi.error_rate * 100, 2),
                "requests_5m": int(kpi.request_count_5m),
                "p99_ms": round(kpi.latency_p99_ms) if kpi.latency_p99_ms else None,
            }
            content["healthy_services"][name] = service_info
            total_tokens = TelemetryFormatter.estimate_tokens(json.dumps(content))
        
        # Serialize with compact formatting
        result = json.dumps(content, separators=(',', ':'), default=str)
        
        # Add metadata
        final_tokens = TelemetryFormatter.estimate_tokens(result)
        result = f"# TELEMETRY (tokens:{final_tokens}/{max_tokens})\n{result}"
        
        return result
    
    @staticmethod
    def to_compact_json(
        bundle: TelemetryBundle,
        max_tokens: int = 3000,
    ) -> str:
        """
        Alias for to_context_window() - returns compact JSON suitable for LLM.
        
        Args:
            bundle: TelemetryBundle to format
            max_tokens: Maximum tokens to use (default: 3000)
            
        Returns:
            Compact JSON string
        """
        return TelemetryFormatter.to_context_window(bundle, max_tokens)
    
    @staticmethod
    def to_jsonl(bundle: TelemetryBundle) -> str:
        """Convert bundle to single-line JSON (for logging)."""
        data = bundle.model_dump()
        return json.dumps(data, separators=(',', ':'), default=str)


# Example output from to_context_window():
"""
# TELEMETRY (tokens:1,250/3000)
{
  "snapshot": {
    "timestamp": "2026-04-27T10:15:30.123456",
    "health": "DEGRADED",
    "collection_ms": 125
  },
  "critical_issues": [
    {
      "alert": "ServiceDown",
      "service": "notification-service",
      "summary": "Service notification-service is not responding"
    }
  ],
  "warnings": [
    {
      "alert": "HighErrorRate",
      "service": "order-service",
      "summary": "HTTP error rate 8.5% (threshold: 5%)"
    }
  ],
  "unhealthy_services": {
    "notification-service": {
      "available": false,
      "error_rate_pct": null,
      "p99_ms": null,
      "restarts_5m": 0
    },
    "order-service": {
      "available": true,
      "error_rate_pct": 8.5,
      "p99_ms": 450,
      "restarts_5m": 0
    }
  },
  "high_latency": {
    "api-gateway": {
      "p99_ms": 650,
      "p95_ms": 500
    }
  },
  "healthy_services": {
    "frontend": {
      "error_rate_pct": 0.2,
      "requests_5m": 450,
      "p99_ms": 150
    },
    "inventory-service": {
      "error_rate_pct": 0.0,
      "requests_5m": 200,
      "p99_ms": 80
    }
  },
  "recent_errors": [
    {
      "service": "notification-service",
      "level": "ERROR",
      "message": "Connection refused: Unable to connect to database",
      "timestamp": "2026-04-27T10:15:25.123456"
    }
  ]
}

TRUNCATION STRATEGY:
If content exceeds max_tokens:
1. Drop oldest ERROR logs first (oldest first for truncation priority)
2. Drop lowest-severity warnings next
3. Drop least-anomalous healthy services next
   (low error rate, low latency, few restarts = dropped first)
4. Drop high-latency services if still over limit
5. Never drop critical issues or unhealthy services
"""
