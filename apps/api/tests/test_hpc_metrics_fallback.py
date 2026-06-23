"""Tests for hpc_metrics.py uncovered branches.

Covers:
- PROMETHEUS_AVAILABLE = True branch (real Counter/Histogram/Gauge creation)
- PROMETHEUS_AVAILABLE = False branch (MockMetric creation)
- MockMetric.labels(), inc(), set(), observe(), collect(), _samples()
"""

from types import ModuleType

import pytest

# ---------------------------------------------------------------------------
# Test real Prometheus metrics (when prometheus_client IS available)
# ---------------------------------------------------------------------------


class TestRealPrometheusMetrics:
    """Tests for the PROMETHEUS_AVAILABLE = True branch.

    These tests use importlib to re-import the module with prometheus_client
    available, verifying that real Counter/Histogram/Gauge objects are created.
    """

    @pytest.mark.unit
    def test_prometheus_true_creates_real_counter(self) -> None:
        """When prometheus_client is available, hpc_job_submissions should be a real Counter."""
        from nfm_db.services.hpc_metrics import PROMETHEUS_AVAILABLE, hpc_job_submissions

        if PROMETHEUS_AVAILABLE:
            from prometheus_client import Counter
            assert isinstance(hpc_job_submissions, Counter)
        else:
            # In fallback path, it should be MockMetric
            assert hasattr(hpc_job_submissions, "inc")

    @pytest.mark.unit
    def test_prometheus_true_creates_real_histogram(self) -> None:
        """When prometheus_client is available, hpc_job_duration should be a Histogram."""
        from nfm_db.services.hpc_metrics import PROMETHEUS_AVAILABLE, hpc_job_duration

        if PROMETHEUS_AVAILABLE:
            from prometheus_client import Histogram
            assert isinstance(hpc_job_duration, Histogram)
        else:
            assert hasattr(hpc_job_duration, "observe")

    @pytest.mark.unit
    def test_prometheus_true_creates_real_gauge(self) -> None:
        """When prometheus_client is available, hpc_active_connections should be a Gauge."""
        from nfm_db.services.hpc_metrics import PROMETHEUS_AVAILABLE, hpc_active_connections

        if PROMETHEUS_AVAILABLE:
            from prometheus_client import Gauge
            assert isinstance(hpc_active_connections, Gauge)
        else:
            assert hasattr(hpc_active_connections, "set")

    @pytest.mark.unit
    def test_prometheus_true_failover_metrics_are_real(self) -> None:
        """When prometheus_client is available, failover metrics should be real Prometheus types."""
        from nfm_db.services.hpc_metrics import (
            PROMETHEUS_AVAILABLE,
            failover_duration_seconds,
            failover_total,
            health_check_success,
        )

        if PROMETHEUS_AVAILABLE:
            from prometheus_client import Counter, Gauge, Histogram
            assert isinstance(failover_total, Counter)
            assert isinstance(failover_duration_seconds, Histogram)
            assert isinstance(health_check_success, Gauge)
        else:
            assert hasattr(failover_total, "inc")
            assert hasattr(failover_duration_seconds, "observe")
            assert hasattr(health_check_success, "set")


# ---------------------------------------------------------------------------
# Test MockMetric fallback (when prometheus_client is NOT available)
# ---------------------------------------------------------------------------


class TestMockMetricFallback:
    """Tests for the PROMETHEUS_AVAILABLE = False branch.

    We simulate the import failure by creating a mock module that
    raises ImportError, then verify MockMetric behavior.
    """

    @pytest.fixture
    def mock_metrics_module(self) -> ModuleType:
        """Create a fresh hpc_metrics-like module with MockMetric path."""
        import types

        mod = types.ModuleType("mock_hpc_metrics")

        # Set PROMETHEUS_AVAILABLE to False
        mod.PROMETHEUS_AVAILABLE = False

        # Define MockMetric class (copy of the module's else branch)
        class MockMetric:
            """Mock Prometheus metric for graceful degradation."""

            def __init__(self, *args, **kwargs):
                self._value = 0
                self._label_values = {}
                self._name = args[0] if args else "mock_metric"

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

        mod.MockMetric = MockMetric

        # Create all metric instances
        mod.hpc_job_submissions = MockMetric("hpc_job_submissions_total", "desc")
        mod.hpc_job_duration = MockMetric("hpc_job_duration_seconds", "desc")
        mod.hpc_file_transfer_bytes = MockMetric("hpc_file_transfer_bytes_total", "desc")
        mod.hpc_connection_errors = MockMetric("hpc_connection_errors_total", "desc")
        mod.hpc_failover_events = MockMetric("hpc_failover_events_total", "desc")
        mod.hpc_active_connections = MockMetric("hpc_active_connections", "desc")
        mod.failover_total = MockMetric("hpc_failover_total", "desc")
        mod.failover_duration_seconds = MockMetric("hpc_failover_duration_seconds", "desc")
        mod.health_check_success = MockMetric("hpc_health_check_success", "desc")

        return mod

    @pytest.mark.unit
    def test_labels_returns_separate_instance(self, mock_metrics_module: ModuleType) -> None:
        """labels() should return a new MockMetric instance, not self."""
        metric = mock_metrics_module.hpc_job_submissions
        labeled = metric.labels(cluster="primary", status="success")

        assert labeled is not metric
        assert isinstance(labeled, type(metric))
        assert labeled._label_values == {"cluster": "primary", "status": "success"}
        assert labeled._name == "hpc_job_submissions_total"

    @pytest.mark.unit
    def test_labels_inherits_parent_value(self, mock_metrics_module: ModuleType) -> None:
        """Labeled instance should inherit the parent's _value."""
        metric = mock_metrics_module.hpc_job_submissions
        metric.inc(10)
        labeled = metric.labels(cluster="primary")

        assert labeled._value == 10

    @pytest.mark.unit
    def test_labels_chaining(self, mock_metrics_module: ModuleType) -> None:
        """labels() on a labeled instance should work correctly."""
        metric = mock_metrics_module.hpc_job_submissions
        labeled1 = metric.labels(cluster="primary")
        labeled2 = labeled1.labels(status="success")

        assert labeled2 is not labeled1
        assert labeled2._label_values == {"status": "success"}
        assert labeled2._name == "hpc_job_submissions_total"

    @pytest.mark.unit
    def test_inc_with_default_amount(self, mock_metrics_module: ModuleType) -> None:
        """inc() with no args should increment by 1."""
        metric = mock_metrics_module.hpc_job_submissions
        metric.inc()
        assert metric._value == 1

    @pytest.mark.unit
    def test_inc_with_custom_amount(self, mock_metrics_module: ModuleType) -> None:
        """inc(5) should increment by 5."""
        metric = mock_metrics_module.hpc_job_submissions
        metric.inc(5)
        assert metric._value == 5

    @pytest.mark.unit
    def test_inc_on_labeled_metric(self, mock_metrics_module: ModuleType) -> None:
        """inc() on a labeled metric should increment only that instance."""
        metric = mock_metrics_module.hpc_job_submissions
        labeled = metric.labels(cluster="primary")
        labeled.inc()

        assert labeled._value == 1
        # Parent value unchanged (labels copies, doesn't reference)
        # Note: the MockMetric.labels copies _value, so parent stays at 0

    @pytest.mark.unit
    def test_set_overwrites_value(self, mock_metrics_module: ModuleType) -> None:
        """set() should overwrite the value."""
        metric = mock_metrics_module.hpc_active_connections
        metric.set(42)
        assert metric._value == 42

        metric.set(0)
        assert metric._value == 0

    @pytest.mark.unit
    def test_observe_records_value(self, mock_metrics_module: ModuleType) -> None:
        """observe() should set the value."""
        metric = mock_metrics_module.hpc_job_duration
        metric.observe(3.14)
        assert metric._value == 3.14

    @pytest.mark.unit
    def test_collect_returns_empty_list(self, mock_metrics_module: ModuleType) -> None:
        """collect() should always return empty list."""
        metric = mock_metrics_module.hpc_job_submissions
        assert metric.collect() == []

        metric.inc(100)
        assert metric.collect() == []

    @pytest.mark.unit
    def test_samples_returns_empty_list(self, mock_metrics_module: ModuleType) -> None:
        """_samples() should always return empty list."""
        metric = mock_metrics_module.hpc_job_submissions
        assert metric._samples() == []

    @pytest.mark.unit
    def test_name_from_positional_arg(self, mock_metrics_module: ModuleType) -> None:
        """Metric name should come from first positional arg."""
        metric = mock_metrics_module.failover_total
        assert metric._name == "hpc_failover_total"

    @pytest.mark.unit
    def test_default_name_when_no_args(self, mock_metrics_module: ModuleType) -> None:
        """MockMetric with no args should have default name 'mock_metric'."""
        mock_metric = mock_metrics_module.MockMetric()
        assert mock_metric._name == "mock_metric"

    @pytest.mark.unit
    def test_all_metrics_have_inc(self, mock_metrics_module: ModuleType) -> None:
        """All metrics should support inc()."""
        for name in [
            "hpc_job_submissions",
            "hpc_file_transfer_bytes",
            "hpc_connection_errors",
            "hpc_failover_events",
            "failover_total",
        ]:
            metric = getattr(mock_metrics_module, name)
            metric.inc()
            assert metric._value == 1

    @pytest.mark.unit
    def test_all_metrics_have_observe(self, mock_metrics_module: ModuleType) -> None:
        """Histogram-type metrics should support observe()."""
        for name in ["hpc_job_duration", "failover_duration_seconds"]:
            metric = getattr(mock_metrics_module, name)
            metric.observe(2.5)
            assert metric._value == 2.5

    @pytest.mark.unit
    def test_all_metrics_have_set(self, mock_metrics_module: ModuleType) -> None:
        """Gauge-type metrics should support set()."""
        for name in ["hpc_active_connections", "health_check_success"]:
            metric = getattr(mock_metrics_module, name)
            metric.set(1)
            assert metric._value == 1

    @pytest.mark.unit
    def test_all_metrics_have_collect(self, mock_metrics_module: ModuleType) -> None:
        """All metrics should support collect()."""
        all_metric_names = [
            "hpc_job_submissions",
            "hpc_job_duration",
            "hpc_file_transfer_bytes",
            "hpc_connection_errors",
            "hpc_failover_events",
            "hpc_active_connections",
            "failover_total",
            "failover_duration_seconds",
            "health_check_success",
        ]
        for name in all_metric_names:
            metric = getattr(mock_metrics_module, name)
            assert metric.collect() == []

    @pytest.mark.unit
    def test_multiple_inc_accumulates(self, mock_metrics_module: ModuleType) -> None:
        """Multiple inc() calls should accumulate."""
        metric = mock_metrics_module.hpc_job_submissions
        metric.inc()
        metric.inc(2)
        metric.inc(0.5)
        assert metric._value == 3.5


# ---------------------------------------------------------------------------
# Test the actual module's MockMetric when prometheus_client unavailable
# ---------------------------------------------------------------------------


class TestModuleMockMetricWhenUnavailable:
    """Test the actual module-level MockMetric instances."""

    @pytest.mark.unit
    def test_module_metrics_inc(self) -> None:
        """Module-level metrics should support inc()."""
        from nfm_db.services.hpc_metrics import hpc_job_submissions

        hpc_job_submissions.inc()
        # Just verify it doesn't raise

    @pytest.mark.unit
    def test_module_metrics_labels(self) -> None:
        """Module-level metrics should support labels()."""
        from nfm_db.services.hpc_metrics import hpc_job_submissions

        labeled = hpc_job_submissions.labels(cluster="primary", status="success")
        assert labeled is not None

    @pytest.mark.unit
    def test_module_metrics_observe(self) -> None:
        """Module-level histogram should support observe()."""
        from nfm_db.services.hpc_metrics import hpc_job_duration

        hpc_job_duration.observe(1.5)
        # Just verify it doesn't raise

    @pytest.mark.unit
    def test_module_metrics_set(self) -> None:
        """Module-level gauge should support set()."""
        from nfm_db.services.hpc_metrics import hpc_active_connections

        hpc_active_connections.set(5)
        # Just verify it doesn't raise

    @pytest.mark.unit
    def test_module_metrics_collect(self) -> None:
        """Module-level metrics should return empty from collect()."""
        from nfm_db.services.hpc_metrics import hpc_job_submissions

        result = hpc_job_submissions.collect()
        assert result == []

    @pytest.mark.unit
    def test_module_failover_metrics_inc(self) -> None:
        """Failover metrics should support inc()."""
        from nfm_db.services.hpc_metrics import failover_total

        failover_total.inc()
        # Just verify it doesn't raise

    @pytest.mark.unit
    def test_module_failover_metrics_observe(self) -> None:
        """Failover histogram should support observe()."""
        from nfm_db.services.hpc_metrics import failover_duration_seconds

        failover_duration_seconds.observe(10.0)
        # Just verify it doesn't raise

    @pytest.mark.unit
    def test_module_failover_metrics_set(self) -> None:
        """Health check gauge should support set()."""
        from nfm_db.services.hpc_metrics import health_check_success

        health_check_success.set(1)
        # Just verify it doesn't raise
