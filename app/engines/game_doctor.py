"""Game Doctor v2: normalized evidence-driven diagnostics.

The classifier combines:
- textual log/error signatures;
- structured machine facts (disk/RAM/driver/runtime/overlays);
- game-plugin evidence supplied by diagnostic_core.

It intentionally returns evidence + confidence instead of applying system tweaks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class Rule:
    category: str
    problem: str
    recommendation: str
    text_pattern: re.Pattern[str] | None = None
    structured_check: Callable[[dict[str, Any]], tuple[bool, str]] | None = None
    weight: float = 1.0


def _low_disk(data: dict[str, Any]) -> tuple[bool, str]:
    value = data.get("free_disk_gb")
    if value is None:
        return False, ""
    try:
        free = float(value)
    except (TypeError, ValueError):
        return False, ""
    return free < 10.0, f"Свободно на диске: {free:.1f} ГБ"


def _low_memory(data: dict[str, Any]) -> tuple[bool, str]:
    value = data.get("available_ram_gb")
    if value is None:
        return False, ""
    try:
        ram = float(value)
    except (TypeError, ValueError):
        return False, ""
    return ram < 2.0, f"Доступно RAM: {ram:.1f} ГБ"


def _stale_driver(data: dict[str, Any]) -> tuple[bool, str]:
    value = data.get("driver_age_days")
    if value is None:
        return False, ""
    try:
        days = int(value)
    except (TypeError, ValueError):
        return False, ""
    return days > 365, f"Возраст GPU-драйвера: {days} дней"


def _missing_runtime(data: dict[str, Any]) -> tuple[bool, str]:
    missing = data.get("missing_runtimes") or []
    if isinstance(missing, str):
        missing = [missing]
    return bool(missing), "Отсутствуют runtime: " + ", ".join(map(str, missing))


def _overlays(data: dict[str, Any]) -> tuple[bool, str]:
    overlays = data.get("active_overlays") or []
    if isinstance(overlays, str):
        overlays = [overlays]
    return len(overlays) >= 2, "Активные overlays/hooks: " + ", ".join(map(str, overlays))


RULES = [
    Rule(
        "gpu_driver",
        "Проблема видеодрайвера или GPU",
        "Обновите или чисто переустановите GPU-драйвер и повторите воспроизводимый тест.",
        re.compile(r"(driver|nvlddmkm|amdkmdag|device removed|device hung)", re.I),
        _stale_driver,
        1.2,
    ),
    Rule(
        "memory",
        "Недостаток или нестабильность памяти",
        "Проверьте RAM/VRAM, файл подкачки и стабильность памяти.",
        re.compile(r"(out of memory|not enough memory|0xc0000005|access violation)", re.I),
        _low_memory,
        1.1,
    ),
    Rule(
        "directx",
        "Ошибка DirectX/рендеринга",
        "Очистите shader cache и проверьте альтернативный API рендеринга, если он поддерживается.",
        re.compile(r"(directx|dxgi|d3d11|d3d12|shader)", re.I),
        None,
        1.0,
    ),
    Rule(
        "runtime",
        "Отсутствует или повреждён runtime",
        "Восстановите требуемый Visual C++/.NET runtime из официального установщика.",
        re.compile(r"(vcruntime|msvcp|\.net|dotnet|visual c\+\+)", re.I),
        _missing_runtime,
        1.15,
    ),
    Rule(
        "overlay",
        "Вероятный конфликт overlay/hook",
        "Временно отключите overlays/hooks и сравните результат на том же сценарии.",
        re.compile(r"(overlay|afterburner|rtss|discord hook|reshade)", re.I),
        _overlays,
        0.9,
    ),
    Rule(
        "disk",
        "Недостаточно свободного места",
        "Освободите место на диске игры/системном диске и повторите проверку файлов.",
        re.compile(r"(disk full|no space|not enough space)", re.I),
        _low_disk,
        0.9,
    ),
]


def _collect_sources(inputs: dict[str, Any]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for key in (
        "error", "logs", "description", "windows_events", "driver_info",
        "game_version", "crash_dump_summary",
    ):
        value = inputs.get(key)
        if value:
            result.append((key, str(value)))
    return result


def diagnose_game(inputs: dict) -> dict:
    sources = _collect_sources(inputs)
    haystack = "\n".join(value for _, value in sources)

    scores: dict[str, float] = {}
    evidence: dict[str, list[str]] = {}
    by_category = {rule.category: rule for rule in RULES}

    for rule in RULES:
        category_evidence: list[str] = []
        score = 0.0

        if rule.text_pattern is not None:
            for source_name, text in sources:
                matches = rule.text_pattern.findall(text)
                if matches:
                    score += min(2.0, 0.6 + 0.25 * len(matches)) * rule.weight
                    category_evidence.append(
                        f"{source_name}: обнаружены сигналы категории {rule.category}"
                    )

        if rule.structured_check is not None:
            matched, detail = rule.structured_check(inputs)
            if matched:
                score += 1.3 * rule.weight
                category_evidence.append(detail)

        if score:
            scores[rule.category] = score
            evidence[rule.category] = category_evidence

    plugin_signals = inputs.get("plugin_signals") or []
    if isinstance(plugin_signals, str):
        plugin_signals = [plugin_signals]
    for signal in plugin_signals:
        if str(signal).lower() in haystack.lower():
            scores["game_plugin"] = scores.get("game_plugin", 0.0) + 0.35
            evidence.setdefault("game_plugin", []).append(
                f"Игровой plugin signal: {signal}"
            )

    if not scores:
        return {
            "problem": "Причина не определена автоматически",
            "category": "unknown",
            "confidence": 0.25,
            "evidence": [
                "Текстовые сигнатуры и структурные проверки не дали достаточного сигнала."
            ],
            "recommendations": [
                "Приложите полный crash log и точный текст ошибки.",
                "Укажите GPU/driver, свободное место, доступную RAM и версию игры.",
            ],
            "fix": "Требуется дополнительная диагностика.",
            "rollback": "Не применяйте необратимые изменения без резервной копии.",
            "risk": "low",
        }

    category, top_score = max(scores.items(), key=lambda item: item[1])
    if category == "game_plugin":
        problem = "Проблема, специфичная для игры/её модификаций"
        recommendation = "Проверьте рекомендации игрового диагностического plugin."
    else:
        rule = by_category[category]
        problem = rule.problem
        recommendation = rule.recommendation

    confidence = min(0.97, 0.45 + top_score * 0.16)
    recommendations = [recommendation]
    recommendations.extend(inputs.get("plugin_recommendations") or [])
    recommendations.append("После каждого изменения повторите тот же воспроизводимый тест.")

    return {
        "problem": problem,
        "category": category,
        "confidence": round(confidence, 2),
        "evidence": evidence.get(category, [])[:12],
        "recommendations": list(dict.fromkeys(recommendations)),
        "fix": recommendation,
        "rollback": "Верните изменённые настройки/драйвер к предыдущему состоянию.",
        "risk": "low",
        "scores": {key: round(value, 2) for key, value in scores.items()},
    }
