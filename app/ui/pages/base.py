"""Базовая страница и общие помощники."""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget


class BasePage(QWidget):
    title: str = ""

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx

    def refresh(self) -> None:  # переопределяется страницами
        return None


def page_header(title: str, subtitle: str = "") -> QWidget:
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 12)
    layout.setSpacing(2)
    head = QLabel(title)
    head.setProperty("h1", True)
    layout.addWidget(head)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setProperty("muted", True)
        sub.setWordWrap(True)
        layout.addWidget(sub)
    return holder


def toolbar_row(*widgets) -> QWidget:
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 10)
    layout.setSpacing(8)
    for widget in widgets:
        layout.addWidget(widget)
    layout.addStretch(1)
    return holder
