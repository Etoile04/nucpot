"""task_dispatcher seam tests(C4 / NFM-2564)。

派发 seam 的契约:名字+args/kwargs/queue 透传给 broker、返回 task id
字符串、broker 错误原样传播(端点已有 CeleryError → 503 的翻译)。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from celery.exceptions import CeleryError

from nfm_db.services import task_dispatcher


@pytest.fixture
def fake_broker(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """替身 broker:记录 send_task 入参,返回确定性 task id。"""
    cap: dict[str, Any] = {"calls": []}

    def _send_task(task_name: str, *, args=None, kwargs=None, queue=None):
        cap["calls"].append(
            {"task_name": task_name, "args": args, "kwargs": kwargs, "queue": queue}
        )
        return SimpleNamespace(id="task-id-123")

    monkeypatch.setattr(task_dispatcher, "celery_app", SimpleNamespace(send_task=_send_task))
    return cap


def test_dispatch_returns_task_id_string(fake_broker: dict[str, Any]) -> None:
    assert task_dispatcher.dispatch("t.some_task") == "task-id-123"


def test_dispatch_forwards_name_args_kwargs_queue(fake_broker: dict[str, Any]) -> None:
    task_dispatcher.dispatch(
        "t.some_task",
        args=["a", 1],
        kwargs={"datasource_id": "ds-1"},
        queue="literature_processing",
    )

    call = fake_broker["calls"][0]
    assert call["task_name"] == "t.some_task"
    assert call["args"] == ["a", 1]
    assert call["kwargs"] == {"datasource_id": "ds-1"}
    assert call["queue"] == "literature_processing"


def test_broker_error_propagates_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CeleryError 原样穿透——503 的翻译职责在路由边缘,不在 seam。"""

    def _boom(*args: object, **kwargs: object) -> None:
        raise CeleryError("broker unreachable")

    monkeypatch.setattr(task_dispatcher, "celery_app", SimpleNamespace(send_task=_boom))

    with pytest.raises(CeleryError, match="broker unreachable"):
        task_dispatcher.dispatch("t.some_task")
