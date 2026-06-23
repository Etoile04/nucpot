"""
Prometheus Metrics Configuration for NFMD API

This module provides Prometheus metrics for monitoring the MD verification system:
- Request rate and latency
- Job submission and completion rate
- Database connection pool usage
- Celery queue depth
- HPC cluster status
- System resources

Run with: uvicorn src.monitoring:app --workers 4
"""

import logging
import os

from prometheus_client import Counter, Gauge, Histogram, Info, start_http_server
from prometheus_client.core import REGISTRY

logger = logging.getLogger(__name__)

# =============================================================================
# Application Info
# =============================================================================
info = Info("nucpot_api", version="1.0.0")
info.info({"environment": os.getenv("ENVIRONMENT", "development")})

# =============================================================================
# HTTP Metrics
# =============================================================================
http_requests_total = Counter(
    "nucpot_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status"]
)

http_requests_in_progress = Gauge(
    "nucpot_http_requests_in_progress",
    "HTTP requests currently in progress",
    ["method", "endpoint"]
)

http_request_duration_seconds = Histogram(
    "nucpot_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0)
)

# =============================================================================
# MD Verification Metrics
# =============================================================================
md_job_submissions_total = Counter(
    "nucpot_md_job_submissions_total",
    "Total MD verification job submissions",
    ["status"]
)

md_job_completions_total = Counter(
    "nucpot_md_job_completions_total",
    "Total MD verification job completions",
    ["status"]
)

md_job_duration_seconds = Histogram(
    "nucpot_md_job_duration_seconds",
    "MD verification job duration in seconds",
    buckets=(60, 300, 900, 1800, 3600, 7200, 14400)  # 1min, 5min, 15min, 30min, 1h, 2h, 4h
)

md_jobs_in_queue = Gauge(
    "nucpot_md_jobs_in_queue",
    "MD verification jobs currently in queue"
)

md_jobs_running = Gauge(
    "nucpot_md_jobs_running",
    "MD verification jobs currently running"
)

# =============================================================================
# HPC Integration Metrics
# =============================================================================
hpc_connection_failures_total = Counter(
    "nucpot_hpc_connection_failures_total",
    "Total HPC connection failures",
    ["cluster"]
)

hpc_job_submission_failures_total = Counter(
    "nucpot_hpc_job_submission_failures_total",
    "Total HPC job submission failures",
    ["cluster"]
)

hpc_queue_depth = Gauge(
    "nucpot_hpc_queue_depth",
    "HPC queue depth",
    ["cluster"]
)

hpc_compute_node_usage = Gauge(
    "nucpot_hpc_compute_node_usage",
    "HPC compute nodes in use",
    ["cluster"]
)

# =============================================================================
# Database Metrics
# =============================================================================
db_connection_pool_size = Gauge(
    "nucpot_db_connection_pool_size",
    "Database connection pool size"
)

db_connections_in_use = Gauge(
    "nucpot_db_connections_in_use",
    "Database connections currently in use"
)

db_query_duration_seconds = Histogram(
    "nucpot_db_query_duration_seconds",
    "Database query latency in seconds",
    ["operation"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)
)

# =============================================================================
# Celery Metrics
# =============================================================================
celery_task_queue_depth = Gauge(
    "nucpot_celery_task_queue_depth",
    "Celery task queue depth",
    ["queue"]
)

celery_workers_active = Gauge(
    "nucpot_celery_workers_active",
    "Active Celery workers"
)

celery_task_duration_seconds = Histogram(
    "nucpot_celery_task_duration_seconds",
    "Celery task duration in seconds",
    ["task_name"],
    buckets=(1, 5, 10, 30, 60, 300, 600, 1800, 3600)
)

# =============================================================================
# System Resource Metrics
# =============================================================================
system_memory_usage_bytes = Gauge(
    "nucpot_system_memory_usage_bytes",
    "System memory usage in bytes"
)

system_cpu_usage_percent = Gauge(
    "nucpot_system_cpu_usage_percent",
    "System CPU usage percentage"
)

system_disk_usage_percent = Gauge(
    "nucpot_system_disk_usage_percent",
    "System disk usage percentage",
    ["mount"]
)

# =============================================================================
# Error Metrics
# =============================================================================
error_total = Counter(
    "nucpot_errors_total",
    "Total errors",
    ["type", "severity"]
)

security_events_total = Counter(
    "nucpot_security_events_total",
    "Total security events",
    ["event_type"]
)

# =============================================================================
# Metrics Update Functions
# =============================================================================
def update_job_metrics(status: str, queue_size: int, running: int) -> None:
    """Update job-related metrics"""
    md_jobs_in_queue.set(queue_size)
    md_jobs_running.set(running)

    if status == "submitted":
        md_job_submissions_total.labels(status="submitted").inc()
    elif status == "completed":
        md_job_completions_total.labels(status="completed").inc()
    elif status == "failed":
        md_job_completions_total.labels(status="failed").inc()


def update_hpc_metrics(cluster: str, queue_depth: int, nodes_in_use: int) -> None:
    """Update HPC-related metrics"""
    hpc_queue_depth.labels(cluster=cluster).set(queue_depth)
    hpc_compute_node_usage.labels(cluster=cluster).set(nodes_in_use)


def record_hpc_failure(cluster: str, failure_type: str) -> None:
    """Record HPC failure"""
    if failure_type == "connection":
        hpc_connection_failures_total.labels(cluster=cluster).inc()
    elif failure_type == "submission":
        hpc_job_submission_failures_total.labels(cluster=cluster).inc()


# =============================================================================
# Startup Metrics Server
# =============================================================================
def start_metrics_server(port: int = 9090) -> None:
    """Start Prometheus metrics server"""
    try:
        start_http_server(port)
        logger.info(f"Prometheus metrics server started on port {port}")
    except Exception as e:
        logger.error(f"Failed to start metrics server: {e}")


# =============================================================================
# Cleanup
# =============================================================================
def clear_metrics() -> None:
    """Clear all metrics (useful for testing)"""
    for collector in [http_requests_total, md_job_submissions_total,
                     md_job_completions_total, hpc_connection_failures_total,
                     hpc_job_submission_failures_total, error_total,
                     security_events_total]:
        collector.clear()

    REGISTRY.clear()
    logger.info("All metrics cleared")


if __name__ == "__main__":
    # Test metrics server
    start_metrics_server()
    logger.info("Metrics server running. Press Ctrl+C to stop.")
