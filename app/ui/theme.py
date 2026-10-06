"""Тёмная тема в духе Discord/Steam/Linear: карточки, пилюли, аккуратные таблицы."""
from __future__ import annotations

COLORS = {
    "bg": "#141518",
    "bg_sidebar": "#0f1013",
    "bg_card": "#1e2024",
    "bg_input": "#15161a",
    "bg_hover": "#25272c",
    "border": "#2b2e34",
    "text": "#e3e5e8",
    "text_muted": "#8f959e",
    "accent": "#5b6bef",
    "accent_hover": "#6c7bf2",
    "steam_blue": "#66c0f4",
    "success": "#2fbf71",
    "warning": "#f0b232",
    "danger": "#ef5b62",
    "info": "#4aa8e0",
    "free": "#7d8590",
}

GRADE_COLORS = {
    "S": "#f0b232",
    "A": "#2fbf71",
    "B": "#4aa8e0",
    "C": "#7d8590",
    "D": "#55585f",
    "FREE": "#3a3d43",
}

ELIGIBILITY_META = {
    "available": ("Доступна", "#2fbf71"),
    "unavailable": ("Недоступна", "#ef5b62"),
    "needs_check": ("Требует проверки", "#f0b232"),
    "third_party": ("Сторонний launcher", "#e8834a"),
    "publisher_excluded": ("Исключена издателем", "#ef5b62"),
    "free": ("Бесплатная", "#7d8590"),
    "no_sense": ("Нет смысла продавать", "#7d8590"),
}

ORDER_STATUS_META = {
    "new": ("Новый", "#4aa8e0"),
    "queued": ("В очереди", "#9aa0a6"),
    "processing": ("Ожидает обработки", "#f0b232"),
    "waiting_client": ("Ожидает клиента", "#f0b232"),
    "manual_action": ("Ручное действие", "#f0b232"),
    "steam_setup": ("Настройка Steam", "#5b6bef"),
    "ready_to_deliver": ("Готов к выдаче", "#2fbf71"),
    "delivered": ("Выдан", "#2fbf71"),
    "active": ("Активен", "#2fbf71"),
    "expiring": ("Скоро закончится", "#f0b232"),
    "completed": ("Завершён", "#7d8590"),
    "cancelled": ("Отменён", "#55585f"),
    "problem": ("Проблема", "#ef5b62"),
}

LEASE_STATUS_META = {
    "scheduled": ("Запланирована", "#4aa8e0"),
    "active": ("Активна", "#2fbf71"),
    "expiring": ("Истекает", "#f0b232"),
    "expired": ("Истекла", "#ef5b62"),
    "completed": ("Завершена", "#7d8590"),
    "cancelled": ("Отменена", "#55585f"),
}

ACCOUNT_STATUS_META = {
    "active": ("Активен", "#2fbf71"),
    "pending": ("Ожидает синхронизации", "#f0b232"),
    "limited": ("Ограниченный профиль", "#f0b232"),
    "error": ("Ошибка", "#ef5b62"),
}


def pill_css(color: str, bg: str | None = None) -> str:
    background = bg or f"{color}26"  # ~15% альфа через 8-значный hex
    return (
        f"background:{background}; color:{color}; border:1px solid {color}55;"
        "border-radius:9px; padding:2px 10px; font-size:11px; font-weight:600;"
    )


DARK_QSS = f"""
* {{ font-family: "Segoe UI", "Noto Sans", sans-serif; font-size: 13px; color: {COLORS['text']}; }}
QWidget {{ background: transparent; }}
QMainWindow, QDialog {{ background: {COLORS['bg']}; }}

QLabel {{ color: {COLORS['text']}; }}
QLabel[muted="true"] {{ color: {COLORS['text_muted']}; }}
QLabel[h1="true"] {{ font-size: 20px; font-weight: 700; }}
QLabel[h2="true"] {{ font-size: 15px; font-weight: 700; }}

QFrame[card="true"] {{
    background: {COLORS['bg_card']};
    border: 1px solid {COLORS['border']};
    border-radius: 10px;
}}

QPushButton {{
    background: {COLORS['bg_hover']};
    border: 1px solid {COLORS['border']};
    border-radius: 7px;
    padding: 6px 14px;
    font-weight: 600;
}}
QPushButton:hover {{ background: #2d3036; }}
QPushButton:pressed {{ background: #202226; }}
QPushButton:disabled {{ color: {COLORS['text_muted']}; background: #1a1b1f; }}
QPushButton[accent="true"] {{ background: {COLORS['accent']}; border: none; color: white; }}
QPushButton[accent="true"]:hover {{ background: {COLORS['accent_hover']}; }}
QPushButton[danger="true"] {{ background: {COLORS['danger']}22; border: 1px solid {COLORS['danger']}66; color: {COLORS['danger']}; }}
QPushButton[sidebar="true"] {{
    background: transparent; border: none; text-align: left;
    padding: 8px 14px; border-radius: 7px; font-weight: 600; color: {COLORS['text_muted']};
}}
QPushButton[sidebar="true"]:hover {{ background: {COLORS['bg_hover']}; color: {COLORS['text']}; }}
QPushButton[sidebar="true"][active="true"] {{ background: {COLORS['accent']}22; color: {COLORS['text']}; border-left: 3px solid {COLORS['accent']}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: {COLORS['bg_input']};
    border: 1px solid {COLORS['border']};
    border-radius: 7px;
    padding: 6px 10px;
    selection-background-color: {COLORS['accent']};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{ border: 1px solid {COLORS['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {COLORS['bg_card']}; border: 1px solid {COLORS['border']};
    selection-background-color: {COLORS['accent']}44;
}}

QTableWidget {{
    background: {COLORS['bg_card']};
    border: 1px solid {COLORS['border']};
    border-radius: 10px;
    gridline-color: {COLORS['border']};
    alternate-background-color: #212327;
}}
QTableWidget::item {{ padding: 4px 8px; }}
QTableWidget::item:selected {{ background: {COLORS['accent']}33; color: {COLORS['text']}; }}
QHeaderView::section {{
    background: #181a1e; color: {COLORS['text_muted']};
    border: none; border-bottom: 1px solid {COLORS['border']};
    padding: 7px 8px; font-weight: 700;
}}
QTableCornerButton::section {{ background: #181a1e; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #33363c; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #45484f; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #33363c; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {COLORS['border']}; background: {COLORS['bg_input']}; }}
QCheckBox::indicator:checked {{ background: {COLORS['accent']}; border-color: {COLORS['accent']}; }}

QProgressBar {{
    background: {COLORS['bg_input']}; border: 1px solid {COLORS['border']};
    border-radius: 6px; text-align: center; height: 12px; font-size: 10px;
}}
QProgressBar::chunk {{ background: {COLORS['accent']}; border-radius: 5px; }}

QTabWidget::pane {{ border: 1px solid {COLORS['border']}; border-radius: 8px; background: {COLORS['bg_card']}; }}
QTabBar::tab {{ padding: 7px 16px; color: {COLORS['text_muted']}; }}
QTabBar::tab:selected {{ color: {COLORS['text']}; border-bottom: 2px solid {COLORS['accent']}; }}

QStatusBar {{ background: {COLORS['bg_sidebar']}; color: {COLORS['text_muted']}; border-top: 1px solid {COLORS['border']}; }}
QToolTip {{ background: {COLORS['bg_card']}; color: {COLORS['text']}; border: 1px solid {COLORS['border']}; padding: 6px; }}
QMenu {{ background: {COLORS['bg_card']}; border: 1px solid {COLORS['border']}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 5px; }}
QMenu::item:selected {{ background: {COLORS['accent']}33; }}

QGroupBox {{
    border: 1px solid {COLORS['border']}; border-radius: 10px;
    margin-top: 12px; background: {COLORS['bg_card']};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {COLORS['text_muted']}; font-weight: 700; }}
"""
