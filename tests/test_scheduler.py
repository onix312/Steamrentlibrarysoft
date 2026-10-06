"""Планировщик: экспоненциальный backoff и лимит (ТЗ §13)."""
from __future__ import annotations

import pytest

pyside6 = pytest.importorskip("PySide6.QtCore")

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core.events import EventBus  # noqa: E402
from app.core.scheduler import BackgroundScheduler, SchedulerTask  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def _task(interval: float = 60.0, failures: int = 0) -> SchedulerTask:
    return SchedulerTask(name="t", fn=lambda: None, interval_sec=interval, failures=failures)


def test_no_backoff_without_failures(qapp):
    sched = BackgroundScheduler(EventBus(), backoff_base_sec=30, backoff_cap_sec=1800)
    task = _task(60, failures=0)
    assert sched.effective_interval(task) == 60


def test_exponential_backoff(qapp):
    sched = BackgroundScheduler(EventBus(), backoff_base_sec=30, backoff_cap_sec=1800)
    task = _task(60, failures=1)
    assert sched.effective_interval(task) == 90      # 60 + 30*2^0
    task.failures = 2
    assert sched.effective_interval(task) == 120     # 60 + 30*2^1
    task.failures = 3
    assert sched.effective_interval(task) == 180     # 60 + 30*2^2


def test_backoff_capped(qapp):
    sched = BackgroundScheduler(EventBus(), backoff_base_sec=30, backoff_cap_sec=1800)
    task = _task(60, failures=20)
    assert sched.effective_interval(task) <= 60 + 1800


def test_task_registration_and_stop(qapp):
    sched = BackgroundScheduler(EventBus())
    sched.add_task("probe", lambda: None, interval_sec=3600)
    assert "probe" in sched.tasks
    sched.set_enabled("probe", False)
    assert sched.tasks["probe"].timer is None or not sched.tasks["probe"].timer.isActive()
    sched.stop_all()
