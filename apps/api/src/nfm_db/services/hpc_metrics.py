"""HPC Prometheus Metrics Definitions.

Centralizes all Prometheus metric definitions and availability flag
for the HPC orchestration system. Other HPC modules import from here
to avoid circular dependencies.

Metrics from NFM-345 (core orchestration) and NFM-346 (failover monitoring).
"""

import logging

logger = logging.getLogger(__name__)

# Prometheus availability flag
try:
    from prometheus_client import Counter, Gauge, Histogram
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger.warning("Prometheus client not available - metrics disabled")

# =============================================================================
# NFM-345 Core Orchestration Metrics
# =============================================================================

if PROMETHEUS_AVAILABLE:
    hpc_job_submissions = Counter(
        'hpc_job_submissions_total',
        'Total HPC job submissions',
        ['cluster', 'status']
    )
    hpc_job_duration = Histogram(
        'hpc_job_duration_seconds',
        'HPC job completion time',
        ['cluster']
    )
    hpc_file_transfer_bytes = Counter(
        'hpc_file_transfer_bytes_total',
        'File transfer volume',
        ['direction', 'cluster']
    )
    hpc_connection_errors = Counter(
        'hpc_connection_errors_total',
        'SSH connection errors',
        ['cluster', 'error_type']
    )
    hpc_failover_events = Counter(
        'hpc_failover_events_total',
        'Failover triggers',
        ['from_cluster', 'to_cluster']
    )
    hpc_active_connections = Gauge(
        'hpc_active_connections',
        'Number of active SSH connections',
        ['cluster']
    )

# =============================================================================
# NFM-346 Failover Monitoring Metrics
# =============================================================================

if PROMETHEUS_AVAILABLE:
    failover_total = Counter(
        'hpc_failover_total',
        'Total number of HPC cluster failovers triggered',
        ['source_cluster', 'target_cluster', 'reason']
    )
    failover_duration_seconds = Histogram(
        'hpc_failover_duration_seconds',
        'Time taken to complete failover in seconds',
        ['source_cluster', 'target_cluster'],
        buckets=[1, 5, 10, 30, 60, 120, 300, 600]
    )
    health_check_success = Gauge(
        'hpc_health_check_success',
        'HPC cluster health check status (1=healthy, 0=unhealthy)',
        ['cluster']
    )
else:
    # Mock metrics if prometheus_client is not available (for graceful degradation)
    class MockMetric:
        """Mock Prometheus metric for graceful degradation."""
        def __init__(self, *args, **kwargs):
            self._value = 0
            self._label_values = {}
            self._name = args[0] if args else 'mock_metric'

        def labels(self, **kwargs):
            label_instance = MockMetric()
            label_instance._value = self._value
            label_instance._label_values = kwargs
            label_instance._name = self._name
            return label_instance

        def inc(self, amount=1):
            self._value += amount

        def set(self, value):
            self._value = value

        def observe(self, value):
            self._value = value

        def collect(self):
            return []

        def _samples(self):
            return []

    hpc_job_submissions = MockMetric()
    hpc_job_duration = MockMetric()
    hpc_file_transfer_bytes = MockMetric()
    hpc_connection_errors = MockMetric()
    hpc_failover_events = MockMetric()
    hpc_active_connections = MockMetric()
    failover_total = MockMetric()
    failover_duration_seconds = MockMetric()
    health_check_success = MockMetric()
