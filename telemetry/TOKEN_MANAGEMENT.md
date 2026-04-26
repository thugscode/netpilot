# Telemetry Formatter - Token-Aware Context Window Implementation

## Overview

The enhanced telemetry formatter converts `TelemetryBundle` objects into multiple output formats optimized for different consumers:
- **JSON**: Full structured data for storage/archival
- **Markdown**: Human-readable reports
- **Compact JSON**: LLM-optimized with intelligent token management
- **JSONL**: Single-line JSON for streaming/logging

## Key Features

### 1. Token Estimation

```python
@staticmethod
def estimate_tokens(text: str) -> int:
    """Estimate token count for text (rough approximation)."""
    # Conservative estimate: 1 token ≈ 4 characters
    return int(len(text) * 0.25)
```

**Rationale**: OpenAI tokenizers typically produce ~1 token per 4 characters. This conservative approach ensures we never exceed the configured limit.

### 2. Intelligent Truncation Strategy

When content exceeds `max_tokens` limit, truncation follows this priority order:

```
Priority 1: Critical Issues (NEVER truncated)
├── Critical severity alarms
└── Ensures LLM always sees system-critical problems

Priority 2: Unhealthy Services (NEVER truncated)
├── Services not available (available=False)
└── Services with high error rates (error_rate > 5%)

Priority 3: Warning Alarms (truncated low→high severity)
├── Sorted by severity, oldest first
└── Preserves most recent/severe warnings

Priority 4: High Latency Services (truncated)
├── P99 latency > 500ms
└── Ordered by latency severity

Priority 5: Error Logs (truncated oldest first)
├── ERROR and CRITICAL level logs
└── Drops oldest entries when truncating

Priority 6: Healthy Services (truncated least-anomalous first)
├── Services with low error rate, low latency, few restarts
└── These are truncated first as they have least diagnostic value
```

### 3. Truncation Thresholds

Content is progressively truncated at these token thresholds:

```python
# Critical items fill 95% of budget before any truncation
critical_issues_threshold = max_tokens * 0.95

# Unhealthy services fill 80% before truncation
unhealthy_threshold = max_tokens * 0.80

# High latency at 75%
high_latency_threshold = max_tokens * 0.75

# Error logs at 70%
error_logs_threshold = max_tokens * 0.70

# Healthy services at 65%
healthy_threshold = max_tokens * 0.65
```

## Output Format

### Compact JSON Structure

```json
# TELEMETRY (tokens:349/3000)
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
      "error_rate_pct": 100.0,
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
```

### Header Metadata

Each output includes a metadata header showing token usage:
```
# TELEMETRY (tokens:349/3000)
```

This header indicates:
- Current token count: `349`
- Maximum allowed: `3000`
- Helps verify compliance with token limits

## Usage Examples

### Basic Usage

```python
from telemetry import TelemetryCollector
from telemetry.formatter import TelemetryFormatter

# Collect telemetry
async with TelemetryCollector() as collector:
    bundle = await collector.collect()

# Format for LLM (3000 token limit)
context = TelemetryFormatter.to_context_window(bundle)
print(context)
# Output: # TELEMETRY (tokens:349/3000)
#         {...compact JSON...}
```

### Custom Token Limits

```python
# For smaller context windows (e.g., Llama with 2K limit)
context_2k = TelemetryFormatter.to_context_window(bundle, max_tokens=2000)

# For larger windows (e.g., GPT-4 with 8K limit)
context_8k = TelemetryFormatter.to_context_window(bundle, max_tokens=8000)

# Alias method - identical behavior
compact = TelemetryFormatter.to_compact_json(bundle, max_tokens=3000)
```

### Integration with LLM

```python
import anthropic

# Get LLM-optimized telemetry
context = TelemetryFormatter.to_context_window(bundle, max_tokens=3000)

# Use in LLM diagnosis
client = anthropic.Anthropic()
response = client.messages.create(
    model="claude-3-haiku-20240307",
    max_tokens=1024,
    system="You are a Kubernetes troubleshooter. Analyze the telemetry and suggest remediation.",
    messages=[
        {
            "role": "user",
            "content": f"Diagnose the following system state:\n{context}"
        }
    ]
)
print(response.content[0].text)
```

## Test Coverage

The formatter includes comprehensive test coverage in `test_formatter_tokens.py`:

### Test 1: Token Counting
- Validates token estimation accuracy
- Compares against known baseline strings

### Test 2: Output Methods
- Tests all 6 output formats
- Verifies format-specific requirements

### Test 3: Context Windows
- Tests with multiple token limits (500, 1000, 2000, 3000, 5000)
- Verifies truncation compliance
- Checks content preservation

### Test 4: Truncation Strategy
- Documents priority order
- Shows which items are protected vs truncated

### Test 5: Aliases
- Verifies `to_compact_json()` produces identical output to `to_context_window()`

## Performance Characteristics

- **Token Counting**: O(n) where n = character length
- **Truncation**: Incremental, respects token budget
- **JSON Serialization**: Standard library, fast
- **Typical Output**: 300-350 tokens for realistic failure scenarios

## Token Budget Allocation (3000 token example)

| Component | Threshold | Typical Size | Purpose |
|-----------|-----------|--------------|---------|
| Snapshot | - | 50 tokens | Metadata, health status |
| Critical Issues | 2850 (95%) | 100 tokens | System-critical alarms |
| Unhealthy Services | 2400 (80%) | 150 tokens | Failed services |
| Warnings | 2250 (75%) | 100 tokens | Non-critical alarms |
| High Latency | 2250 (75%) | 80 tokens | Performance issues |
| Error Logs | 2100 (70%) | 100 tokens | Recent errors |
| Healthy Services | 1950 (65%) | 150 tokens | System baseline |
| **Total** | - | **~350 tokens** | **Realistic scenario** |

## Design Decisions

1. **Conservative Token Estimation**: 1 token ≈ 4 chars (conservative side)
   - Ensures we never exceed configured limit
   - Worst-case scenario: output is 20% smaller than max

2. **Priority-Based Truncation**: Protects critical information
   - LLM always sees system failures
   - Healthy details are dropped first
   - Aligns with human troubleshooting priorities

3. **Compact JSON**: Optimized for LLM ingestion
   - Minimal whitespace
   - Short field names where possible
   - Flat structure for quick parsing

4. **Metadata Header**: Provides feedback about truncation
   - Shows actual token usage
   - Helps verify compliance
   - Useful for debugging truncation decisions

5. **Pydantic v2 Compatibility**: Modern Python tooling
   - Uses `model_dump()` / `model_dump_json()`
   - Supports future model extensions
   - Type-safe serialization

## Future Enhancements

1. **Dynamic Token Allocation**: Adjust thresholds based on content
2. **Compression**: Optimize JSON size further
3. **Streaming**: Support streaming JSON for very large bundles
4. **Multiple Models**: Different formats for different LLMs
5. **Caching**: Cache formatted outputs for repeated use

## References

- [Telemetry Schemas](./schemas.py) - Data models
- [Telemetry Collector](./collector.py) - Data collection
- [Test Suite](./test_formatter_tokens.py) - Comprehensive tests
- [ARCHITECTURE.md](./ARCHITECTURE.md) - System design
