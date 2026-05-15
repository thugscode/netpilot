"""
Netpilot Configuration Management

Central configuration for:
- LLM provider selection (OpenAI, Anthropic)
- Model parameters
- Collector settings
- Policy gate settings
- Executor settings
"""

import os
from dataclasses import dataclass
from typing import Literal


@dataclass
class LLMConfig:
    """LLM Configuration"""
    
    # Provider: "openai" or "anthropic"
    provider: Literal["openai", "anthropic"] = os.getenv("NETPILOT_LLM_PROVIDER", "openai")
    
    # Model name
    # OpenAI: "gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"
    # Anthropic: "claude-3-opus", "claude-3-sonnet", "claude-3-haiku"
    model: str = os.getenv("NETPILOT_LLM_MODEL", "gpt-4")
    
    # API keys
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    
    # Generation parameters
    temperature: float = 0.3  # Lower for deterministic diagnosis
    max_tokens: int = 2000    # Max tokens for LLM response
    top_p: float = 0.95       # Nucleus sampling
    
    # Retry configuration
    max_retries: int = 3
    retry_delay_ms: int = 1000
    
    def validate(self) -> None:
        """Validate configuration"""
        if self.provider not in ("openai", "anthropic"):
            raise ValueError(f"Unknown provider: {self.provider}")
        
        if self.provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY not set")
        
        if self.provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")


@dataclass
class TelemetryConfig:
    """Telemetry Collection Configuration"""
    
    # Prometheus URL for KPI queries
    prometheus_url: str = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
    
    # Alert receiver URL
    alertmanager_url: str = os.getenv("ALERTMANAGER_URL", "http://localhost:5000")
    
    # Collection interval (seconds)
    collection_interval: int = 30
    
    # Context window token limit for LLM
    context_window_tokens: int = 3000


@dataclass
class PolicyGateConfig:
    """Policy Gate Configuration"""
    
    # Maximum blast radius (% of services affected)
    # Set to 70% to allow restarting leaf services in 5-service cluster
    # (e.g., restarting notification-service affects 3/5 = 60% of services)
    max_blast_radius_pct: float = 70.0
    
    # SLA bounds (examples)
    max_error_rate_pct: float = 5.0
    max_latency_p99_ms: int = 1000
    
    # Track rollback history
    rollback_history_window_hours: int = 1
    max_rollbacks_per_window: int = 3


@dataclass
class ExecutorConfig:
    """Executor Configuration"""
    
    # Kubernetes context
    kubeconfig_path: str = os.getenv("KUBECONFIG", "~/.kube/config")
    
    # Execution timeout
    execution_timeout_seconds: int = 60
    
    # Post-action telemetry collection delay (seconds)
    post_action_delay_seconds: int = 5


@dataclass
class NetpilotConfig:
    """Central Configuration for Netpilot"""
    
    llm: LLMConfig
    telemetry: TelemetryConfig
    policy_gate: PolicyGateConfig
    executor: ExecutorConfig
    
    # Logging
    log_dir: str = os.getenv("NETPILOT_LOG_DIR", "logs")
    log_level: str = os.getenv("NETPILOT_LOG_LEVEL", "INFO")
    
    # Enable debug mode
    debug: bool = os.getenv("NETPILOT_DEBUG", "false").lower() == "true"
    
    def __post_init__(self):
        """Validate configuration after initialization"""
        self.llm.validate()


# Global configuration instance
_config: NetpilotConfig | None = None


def get_config() -> NetpilotConfig:
    """Get or create global configuration"""
    global _config
    if _config is None:
        _config = NetpilotConfig(
            llm=LLMConfig(),
            telemetry=TelemetryConfig(),
            policy_gate=PolicyGateConfig(),
            executor=ExecutorConfig(),
        )
    return _config


def set_config(config: NetpilotConfig) -> None:
    """Override global configuration (for testing)"""
    global _config
    _config = config
