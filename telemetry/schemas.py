"""
Pydantic schemas for telemetry data structures.
"""

from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field


class LogEvent(BaseModel):
    """A single log line from a pod."""
    
    timestamp: datetime
    service: str
    pod_name: str
    container: str = "default"
    level: Optional[str] = None  # INFO, WARNING, ERROR, etc.
    message: str


class KPI(BaseModel):
    """Key Performance Indicator for a service."""
    
    service: str
    timestamp: datetime
    
    # Request metrics
    request_count_5m: float = Field(description="Total requests in last 5 minutes")
    error_rate: float = Field(description="Percentage of requests with 5xx errors (0.0-1.0)")
    
    # Latency percentiles
    latency_p50_ms: Optional[float] = Field(None, description="Median latency in milliseconds")
    latency_p99_ms: Optional[float] = Field(None, description="P99 latency in milliseconds")
    latency_p95_ms: Optional[float] = Field(None, description="P95 latency in milliseconds")
    
    # Downstream metrics
    downstream_error_rate: Optional[float] = Field(None, description="Percentage of downstream calls with errors")
    downstream_calls_5m: Optional[float] = Field(None, description="Total downstream calls in 5m")
    
    # Pod metrics
    pod_restart_count: int = Field(default=0, description="Total pod restarts")
    pod_restart_count_5m: int = Field(default=0, description="Pod restarts in last 5 minutes")
    
    # Availability
    available: bool = Field(default=True, description="Service is responding to scrape")


class Alarm(BaseModel):
    """An alert from Alertmanager."""
    
    timestamp: datetime
    alert_name: str
    status: str = Field(description="firing or resolved")
    severity: str = Field(description="critical, warning, info")
    service: Optional[str] = None
    component: Optional[str] = None
    summary: str
    description: str
    starts_at: datetime
    ends_at: Optional[datetime] = None
    raw_alert: Dict[str, Any] = Field(default_factory=dict, description="Original alert data")


class TelemetryBundle(BaseModel):
    """Complete telemetry snapshot at a point in time."""
    
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="When this bundle was collected")
    collection_duration_ms: float = Field(description="How long collection took (milliseconds)")
    
    # Service KPIs
    kpis: Dict[str, KPI] = Field(default_factory=dict, description="KPIs keyed by service name")
    
    # Recent logs (per service)
    logs: Dict[str, List[LogEvent]] = Field(
        default_factory=dict,
        description="Recent log events keyed by service name"
    )
    
    # Current alarms
    alarms: List[Alarm] = Field(default_factory=list, description="Currently active alarms")
    
    # Collection status
    collection_errors: List[str] = Field(default_factory=list, description="Any errors during collection")
    services_monitored: List[str] = Field(default_factory=list, description="Services that were monitored")
    
    def is_healthy(self) -> bool:
        """Check if system is healthy based on KPIs and alarms."""
        # System is healthy if:
        # - No critical alarms
        # - All services available
        # - Error rates < 5%
        
        critical_alarms = [a for a in self.alarms if a.severity == "critical" and a.status == "firing"]
        if critical_alarms:
            return False
        
        for kpi in self.kpis.values():
            if not kpi.available:
                return False
            if kpi.error_rate > 0.05:  # More than 5% errors
                return False
        
        return True
    
    def get_service_summary(self) -> Dict[str, Any]:
        """Get a summary of all services."""
        summary = {
            "timestamp": self.timestamp.isoformat(),
            "healthy": self.is_healthy(),
            "services": {},
            "alarm_count": len([a for a in self.alarms if a.status == "firing"]),
            "error_count": len(self.collection_errors),
        }
        
        for service_name, kpi in self.kpis.items():
            summary["services"][service_name] = {
                "available": kpi.available,
                "error_rate": f"{kpi.error_rate*100:.2f}%",
                "p99_latency_ms": kpi.latency_p99_ms,
                "pod_restarts": kpi.pod_restart_count,
                "recent_logs": len(self.logs.get(service_name, [])),
            }
        
        return summary
