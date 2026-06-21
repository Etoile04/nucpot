"""Prometheus metrics for HPC failover monitoring (NFM-346 Phase 4.5).

Defines Prometheus metrics for tracking:
- Total failover count
- Failover duration
- Health check success rate

These metrics integrate with the existing NFM-345 Prometheus infrastructure.
"""

try:
    from prometheus_client import Counter, Histogram, Gauge
    PROMETHEUS_AVAILABLE = True
except ImportError:
    # prometheus_client is required for NFM-346
    PROMETHEUS_AVAILABLE = False
    import warnings
    warnings.warn(
        "prometheus_client not installed - metrics will be disabled. "
        "Install with: pip install prometheus_client"
    )

# =============================================================================
# Prometheus Metrics
# =============================================================================

if PROMETHEUS_AVAILABLE:
    # Counter for total number of failovers triggered
    failover_total = Counter(
        'hpc_failover_total',
        'Total number of HPC cluster failovers triggered',
        ['source_cluster', 'target_cluster', 'reason']
    )

    # Histogram for failover duration in seconds
    failover_duration_seconds = Histogram(
        'hpc_failover_duration_seconds',
        'Time taken to complete failover in seconds',
        ['source_cluster', 'target_cluster'],
        buckets=[1, 5, 10, 30, 60, 120, 300, 600]  # 1s to 10 minutes
    )

    # Gauge for health check success (1=healthy, 0=unhealthy)
    health_check_success = Gauge(
        'hpc_health_check_success',
        'HPC cluster health check status (1=healthy, 0=unhealthy)',
        ['cluster']
    )
else:
    # Mock metrics if prometheus_client is not available (for graceful degradation)
    class MockMetric:
        """Mock Prometheus metric for graceful degradation when prometheus_client is unavailable."""
        def __init__(self, *args, **kwargs):
            self._value = 0
            self._label_values = {}
            self._name = args[0] if args else 'mock_metric'
            self._documentation = args[1] if len(args) > 1 else 'Mock metric'

        def labels(self, **kwargs):
            """Create a labeled instance of the metric."""
            label_instance = MockMetric()
            label_instance._value = self._value
            label_instance._label_values = kwargs
            label_instance._name = self._name
            return label_instance

        def inc(self, amount=1):
            """Increment the metric value."""
            self._value += amount

        def set(self, value):
            """Set the metric value (for Gauge)."""
            self._value = value

        def observe(self, value):
            """Observe a value (for Histogram)."""
            self._value = value

        def collect(self):
            """Return mock samples for Prometheus scraping compatibility."""
            return []

        def _samples(self):
            """Return samples in Prometheus format."""
            return []

    failover_total = MockMetric()
    failover_duration_seconds = MockMetric()
    health_check_success = MockMetric()
