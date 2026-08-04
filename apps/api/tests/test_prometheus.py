from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from nfm_db.monitoring import prometheus


def test_clear_metrics_logs_warning_when_collector_cannot_unregister(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Cleanup failures remain visible while metric reset continues."""
    registry = SimpleNamespace(
        _collector_to_names={"broken-collector": object()},
        unregister=Mock(side_effect=RuntimeError("collector is already gone")),
    )
    monkeypatch.setattr(prometheus, "REGISTRY", registry)

    with caplog.at_level("WARNING", logger="monitoring.prometheus"):
        prometheus.clear_metrics()

    assert "Could not unregister collector" in caplog.text
    registry.unregister.assert_called_once_with("broken-collector")
