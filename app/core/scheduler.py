"""Локальный фоновый планировщик на QTimer + QThreadPool (ТЗ §13).

* интервалы настраиваются из конфига;
* повторы при сбоях — экспоненциальный backoff (ТЗ: не спамить запросами);
* сами задачи выполняются воркерами в пуле потоков, не блокируя GUI.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import QObject, Slot
from PySide6.QtCore import QTimer
from PySide6.QtCore import QThreadPool

from app.core.events import EventBus
from app.workers.base import FunctionWorker

log = logging.getLogger(__name__)


@dataclass
class SchedulerTask:
    name: str
    fn: Callable[[], Any]
    interval_sec: float
    enabled: bool = True
    failures: int = 0
    running: bool = False
    timer: QTimer | None = field(default=None, repr=False)


class BackgroundScheduler(QObject):
    def __init__(self, events: EventBus, backoff_base_sec: float = 30.0,
                 backoff_cap_sec: float = 1800.0, pool: QThreadPool | None = None) -> None:
        super().__init__()
        self.events = events
        self.pool = pool or QThreadPool.globalInstance()
        self.tasks: dict[str, SchedulerTask] = {}
        self._backoff_base = backoff_base_sec
        self._backoff_cap = backoff_cap_sec

    # ------------------------------------------------------------------ API
    def add_task(self, name: str, fn: Callable[[], Any], interval_sec: float, enabled: bool = True) -> None:
        task = SchedulerTask(name=name, fn=fn, interval_sec=max(10.0, float(interval_sec)), enabled=enabled)
        self.tasks[name] = task
        if enabled:
            self._start_timer(task)

    def set_enabled(self, name: str, enabled: bool) -> None:
        task = self.tasks.get(name)
        if task is None:
            return
        task.enabled = enabled
        if not enabled and task.timer is not None:
            task.timer.stop()
        elif enabled and task.timer is None:
            self._start_timer(task)

    def run_now(self, name: str) -> bool:
        task = self.tasks.get(name)
        if task is None or task.running:
            return False
        self._submit(task)
        return True

    def stop_all(self) -> None:
        for task in self.tasks.values():
            if task.timer is not None:
                task.timer.stop()

    def effective_interval(self, task: SchedulerTask) -> float:
        if task.failures <= 0:
            return task.interval_sec
        backoff = task.interval_sec + self._backoff_base * (2 ** (task.failures - 1))
        return min(backoff, task.interval_sec + self._backoff_cap)

    # -------------------------------------------------------------- внутри
    def _start_timer(self, task: SchedulerTask) -> None:
        timer = QTimer(self)
        timer.setInterval(int(task.interval_sec * 1000))
        timer.timeout.connect(lambda t=task: self._on_tick(t))
        timer.start()
        task.timer = timer
        log.info("Планировщик: задача %s каждые %.0f c", task.name, task.interval_sec)

    def _on_tick(self, task: SchedulerTask) -> None:
        if task.running:
            return  # предыдущий запуск ещё работает — не дублируем
        self._submit(task)

    def _submit(self, task: SchedulerTask) -> None:
        task.running = True
        worker = FunctionWorker(task.name, task.fn)
        worker.signals.finished.connect(lambda name, result, t=task: self._on_done(t, result))
        worker.signals.failed.connect(lambda name, error, t=task: self._on_failed(t, error))
        self.pool.start(worker)

    @Slot(str, object)
    def _on_done(self, task: SchedulerTask, result: Any) -> None:
        task.running = False
        task.failures = 0
        log.info("Планировщик: задача %s завершена", task.name)
        self.events.publish("scheduler.done", name=task.name, result=result)

    @Slot(str, str)
    def _on_failed(self, task: SchedulerTask, error: str) -> None:
        task.running = False
        task.failures += 1
        interval = self.effective_interval(task)
        log.warning("Планировщик: задача %s упала (%s). Backoff: следующая попытка через %.0f c",
                    task.name, error, interval)
        # Перезапускаем таймер с учётом backoff.
        if task.timer is not None:
            task.timer.stop()
            task.timer.setInterval(int(interval * 1000))
            task.timer.start()
        self.events.publish("scheduler.failed", name=task.name, error=error)
