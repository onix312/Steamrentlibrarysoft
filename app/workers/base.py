"""Базовый QRunnable-воркер: долгие операции никогда не блокируют GUI.

Результаты и ошибки доставляются сигналами (автоматически маршалятся
в главный поток через очередные соединения).
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

log = logging.getLogger(__name__)


class WorkerSignals(QObject):
    started = Signal(str)          # имя задачи
    finished = Signal(str, object)  # имя задачи, результат
    failed = Signal(str, str)       # имя задачи, текст ошибки


class FunctionWorker(QRunnable):
    """Выполняет произвольную функцию в пуле потоков."""

    def __init__(self, name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.name = name
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:  # pragma: no cover - выполняется в пуле потоков
        self.signals.started.emit(self.name)
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001 - ошибка не должна убивать поток пула
            log.exception("Воркер %s завершился с ошибкой", self.name)
            self.signals.failed.emit(self.name, str(exc))
            return
        self.signals.finished.emit(self.name, result)
