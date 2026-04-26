#!/usr/bin/env python3
"""
Microservice with Prometheus metrics and downstream calls.
Each service calls its configured downstream neighbors.
"""

import os
import json
import logging
import time
from typing import Optional, List
from fastapi import FastAPI, HTTPException
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import httpx
import asyncio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Service configuration from environment
SERVICE_NAME = os.getenv("SERVICE_NAME", "unknown")
SERVICE_PORT = int(os.getenv("SERVICE_PORT", 8000))
DOWNSTREAM_SERVICES = os.getenv("DOWNSTREAM_SERVICES", "").split(",") if os.getenv("DOWNSTREAM_SERVICES") else []

# Clean up downstream services list
DOWNSTREAM_SERVICES = [s.strip() for s in DOWNSTREAM_SERVICES if s.strip()]

# Prometheus metrics
request_count = Counter(
    "service_requests_total",
    "Total requests",
    ["service", "endpoint", "method", "status"],
)
request_duration = Histogram(
    "service_request_duration_seconds",
    "Request duration in seconds",
    ["service", "endpoint"],
)
downstream_calls = Counter(
    "service_downstream_calls_total",
    "Total downstream calls",
    ["service", "downstream", "status"],
)
downstream_latency = Histogram(
    "service_downstream_latency_seconds",
    "Downstream call latency in seconds",
    ["service", "downstream"],
)

# Health state (simulates fault injection effects)
health_state = {"healthy": True, "error_rate": 0.0}


@app.middleware("http")
async def add_metrics(request, call_next):
    """Record metrics for each request."""
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    
    request_count.labels(
        service=SERVICE_NAME,
        endpoint=request.url.path,
        method=request.method,
        status=response.status_code,
    ).inc()
    request_duration.labels(service=SERVICE_NAME, endpoint=request.url.path).observe(duration)
    
    return response


@app.get("/health")
async def health():
    """Health check endpoint."""
    if not health_state["healthy"]:
        raise HTTPException(status_code=503, detail="Service unhealthy")
    return {
        "service": SERVICE_NAME,
        "status": "healthy",
        "timestamp": time.time(),
    }


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    from starlette.responses import Response
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/")
async def root():
    """Root endpoint returns service info."""
    return {
        "service": SERVICE_NAME,
        "timestamp": time.time(),
        "downstream_services": DOWNSTREAM_SERVICES,
    }


@app.get("/call/{service}")
async def call_downstream(service: str):
    """Call a specific downstream service."""
    if service not in DOWNSTREAM_SERVICES:
        raise HTTPException(
            status_code=400,
            detail=f"Service {service} not in downstream list",
        )
    
    start_time = time.time()
    try:
        # Build the URL (assumes service.default.svc.cluster.local)
        url = f"http://{service}.default.svc.cluster.local:8000/"
        
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            duration = time.time() - start_time
            
            downstream_calls.labels(
                service=SERVICE_NAME,
                downstream=service,
                status="success",
            ).inc()
            downstream_latency.labels(service=SERVICE_NAME, downstream=service).observe(duration)
            
            return {
                "caller": SERVICE_NAME,
                "called": service,
                "response": resp.json(),
                "latency_ms": duration * 1000,
            }
    except Exception as e:
        duration = time.time() - start_time
        downstream_calls.labels(
            service=SERVICE_NAME,
            downstream=service,
            status="error",
        ).inc()
        downstream_latency.labels(service=SERVICE_NAME, downstream=service).observe(duration)
        
        logger.error(f"Failed to call {service}: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Failed to reach {service}: {str(e)}",
        )


@app.post("/inject-fault")
async def inject_fault(fault_type: str = "crash", duration_seconds: int = 30):
    """Simulate fault injection (for testing)."""
    if fault_type == "crash":
        health_state["healthy"] = False
        logger.warning(f"Injected crash fault for {duration_seconds}s")
        
        async def recover():
            await asyncio.sleep(duration_seconds)
            health_state["healthy"] = True
            logger.info("Service recovered from crash fault")
        
        asyncio.create_task(recover())
        return {"status": "fault injected", "type": "crash", "duration_seconds": duration_seconds}
    
    elif fault_type == "error_rate":
        health_state["error_rate"] = 0.5
        logger.warning(f"Injected error rate fault at 50% for {duration_seconds}s")
        
        async def recover_error_rate():
            await asyncio.sleep(duration_seconds)
            health_state["error_rate"] = 0.0
            logger.info("Service recovered from error rate fault")
        
        asyncio.create_task(recover_error_rate())
        return {"status": "fault injected", "type": "error_rate", "rate": 0.5, "duration_seconds": duration_seconds}
    
    raise HTTPException(status_code=400, detail=f"Unknown fault type: {fault_type}")


@app.get("/cascade")
async def cascade_call():
    """Call all downstream services in sequence (demonstrates cascading failures)."""
    results = []
    for downstream in DOWNSTREAM_SERVICES:
        try:
            result = await call_downstream(downstream)
            results.append({"service": downstream, "status": "success", "data": result})
        except Exception as e:
            results.append({"service": downstream, "status": "error", "error": str(e)})
    
    return {
        "caller": SERVICE_NAME,
        "cascade_results": results,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT)
