"""Game Doctor v2: normalized evidence-driven diagnostics."""
from __future__ import annotations

import re

KNOWLEDGE_BASE = [
    ("gpu_driver", re.compile(r"(driver|nvlddmkm|amdkmdag|device removed)", re.I),
     "Проблема видеодрайвера или GPU", "Обновите/переустановите драйвер GPU чистым способом."),
    ("memory", re.compile(r"(out of memory|not enough memory|0xc0000005|access violation)", re.I),
     "Память/доступ к памяти", "Проверьте RAM/VRAM, файл подкачки и стабильность памяти."),
    ("directx", re.compile(r"(directx|dxgi|d3d11|d3d12|shader)", re.I),
     "DirectX/рендеринг", "Очистите shader cache, проверьте DirectX и переключите API рендеринга, если игра поддерживает."),
    ("runtime", re.compile(r"(vcruntime|msvcp|\.net|dotnet|visual c\+\+)", re.I),
     "Отсутствует runtime", "Восстановите Microsoft Visual C++ Redistributable/.NET из официальных установщиков."),
    ("overlay", re.compile(r"(overlay|afterburner|rtss|discord hook)", re.I),
     "Конфликт overlay/hook", "Временно отключите overlays и hooks и повторите запуск."),
]


def diagnose_game(inputs: dict) -> dict:
    sources = []
    for key in ("error", "logs", "description", "windows_events", "driver_info", "game_version"):
        value = inputs.get(key)
        if value:
            sources.append((key, str(value)))
    haystack = "\n".join(v for _, v in sources)

    candidates = []
    for category, pattern, problem, recommendation in KNOWLEDGE_BASE:
        matches = pattern.findall(haystack)
        if matches:
            confidence = min(0.95, 0.55 + 0.1 * len(matches))
            candidates.append((confidence, category, problem, recommendation))

    if not candidates:
        return {
            "problem": "Причина не определена автоматически",
            "category": "unknown",
            "confidence": 0.25,
            "evidence": ["Недостаточно сигнатур в предоставленных данных."],
            "recommendations": [
                "Приложите полный crash log и точный текст ошибки.",
                "Укажите GPU/driver, версию Windows и версию игры.",
            ],
            "fix": "Требуется дополнительная диагностика.",
            "rollback": "Не применяйте необратимые изменения без резервной копии.",
            "risk": "low",
        }

    candidates.sort(reverse=True)
    confidence, category, problem, recommendation = candidates[0]
    evidence = [f"{key}: найден сигнал категории {category}" for key, text in sources if KNOWLEDGE_BASE[[x[0] for x in KNOWLEDGE_BASE].index(category)][1].search(text)]
    return {
        "problem": problem,
        "category": category,
        "confidence": round(confidence, 2),
        "evidence": evidence[:8],
        "recommendations": [recommendation, "После каждого изменения повторите воспроизводимый тест."],
        "fix": recommendation,
        "rollback": "Верните изменённые настройки/драйвер к предыдущему состоянию.",
        "risk": "low",
    }
