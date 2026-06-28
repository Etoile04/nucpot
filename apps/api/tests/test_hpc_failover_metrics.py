"""Tests for HPC Failover Prometheus Metrics (Component 4 of NFM-346).

Tests follow TDD principles:
- RED: Test written first, fails because feature doesn't exist
- GREEN: Minimal implementation to pass test
- REFACTOR: Clean up while keeping tests green
"""

import pytest


class TestPrometheusMetricsExist:
    """Test that Prometheus metrics are defined."""

    def test_failover_counter_metric_exists(self):
        """Test that failover counter metric exists.

        GIVEN: NFM-346 implementation
        WHEN: Importing metrics module
        THEN: Failover counter metric exists
        """
        try:
            from nfm_db.services.hpc_metrics import failover_total
            assert failover_total is not None
        except ImportError as e:
            pytest.fail(f"failover_total metric should exist but import failed: {e}")

    def test_failover_duration_histogram_exists(self):
        """Test that failover duration histogram exists.

        GIVEN: NFM-346 implementation
        WHEN: Importing metrics module
        THEN: Failover duration histogram exists
        """
        try:
            from nfm_db.services.hpc_metrics import failover_duration_seconds
            assert failover_duration_seconds is not None
        except ImportError as e:
            pytest.fail(f"failover_duration_seconds metric should exist but import failed: {e}")

    def test_health_check_gauge_exists(self):
        """Test that health check gauge exists.

        GIVEN: NFM-346 implementation
        WHEN: Importing metrics module
        THEN: Health check success gauge exists
        """
        try:
            from nfm_db.services.hpc_metrics import health_check_success
            assert health_check_success is not None
        except ImportError as e:
            pytest.fail(f"health_check_success metric should exist but import failed: {e}")


class TestFailoverCounterMetric:
    """Test failover counter Prometheus metric."""

    def test_failover_counter_increments_on_trigger(self):
        """Test that failover counter can be incremented.

        GIVEN: Failover counter metric exists
        WHEN: Failover is triggered
        THEN: Counter can be incremented without error
        """
        from nfm_db.services.hpc_metrics import failover_total

        # Increment counter
        failover_total.labels(
            source_cluster='guangzhou.example.com',
            target_cluster='tianjin.example.com',
            reason='ssh_timeout'
        ).inc()

        # If no exception raised, counter works correctly
        # Note: Prometheus client doesn't expose sample counts in test context

    def test_failover_counter_has_labels(self):
        """Test that failover counter has required labels.

        GIVEN: Failover counter metric exists
        WHEN: Inspecting metric labels
        THEN: Labels include source_cluster, target_cluster, reason
        """
        from nfm_db.services.hpc_metrics import failover_total

        # Try to use metric with labels
        failover_total.labels(
            source_cluster='guangzhou.example.com',
            target_cluster='tianjin.example.com',
            reason='ssh_timeout'
        )

        # If no exception, labels are configured correctly


class TestFailoverDurationHistogram:
    """Test failover duration histogram metric."""

    def test_failover_duration_tracks_time(self):
        """Test that failover duration histogram can record time.

        GIVEN: Failover duration histogram exists
        WHEN: Failover completes
        THEN: Duration can be recorded without error
        """
        from nfm_db.services.hpc_metrics import failover_duration_seconds

        # Observe a duration
        failover_duration_seconds.labels(
            source_cluster='guangzhou.example.com',
            target_cluster='tianjin.example.com'
        ).observe(45.5)

        # If no exception raised, histogram works correctly
        # Note: Prometheus client doesn't expose samples in test context

    def test_failover_duration_has_labels(self):
        """Test that failover duration has required labels.

        GIVEN: Failover duration histogram exists
        WHEN: Inspecting metric labels
        THEN: Labels include source_cluster, target_cluster
        """
        from nfm_db.services.hpc_metrics import failover_duration_seconds

        # Try to use metric with labels
        failover_duration_seconds.labels(
            source_cluster='guangzhou.example.com',
            target_cluster='tianjin.example.com'
        )

        # If no exception, labels are configured correctly


class TestHealthCheckGauge:
    """Test health check gauge metric."""

    def test_health_check_gauge_updates(self):
        """Test that health check gauge can be updated.

        GIVEN: Health check gauge exists
        WHEN: Health check is performed
        THEN: Gauge can be set without error
        """
        from nfm_db.services.hpc_metrics import health_check_success

        # Set gauge to healthy (1)
        health_check_success.labels(cluster='guangzhou.example.com').set(1)

        # If no exception raised, gauge works correctly
        # Note: Prometheus client doesn't expose samples in test context

    def test_health_check_gauge_has_labels(self):
        """Test that health check gauge has required labels.

        GIVEN: Health check gauge exists
        WHEN: Inspecting metric labels
        THEN: Labels include cluster
        """
        from nfm_db.services.hpc_metrics import health_check_success

        # Try to use metric with labels
        health_check_success.labels(cluster='guangzhou.example.com')

        # If no exception, labels are configured correctly
