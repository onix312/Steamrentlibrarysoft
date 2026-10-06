"""Настройка логирования: консоль + файл, без «проглатывания» ошибок."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FMT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_configured = False


def setup_logging(data_dir: Path, level: int = logging.INFO) -> Path:
    """Инициализирует логирование. Возвращает путь к файлу лога."""
    global _configured
    data_dir.mkdir(parents=True, exist_ok=True)
    log_file = data_dir / "app.log"

    root = logging.getLogger()
    root.setLevel(level)
    # убираем возможные дубли при повторном вызове
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_FMT))
    root.addHandler(console)

    file_handler = RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(_FMT))
    root.addHandler(file_handler)

    logging.getLogger("urllib3").setLevel(logging.WARNING)
    _configured = True
    return log_file
