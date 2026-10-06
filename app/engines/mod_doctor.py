"""Mod Doctor: анализ списка модов и порядка загрузки (ТЗ §14).

Чистая функция над структурой входных данных — без обращения к сети и
без изменения файлов пользователя.
"""
from __future__ import annotations


def analyze(inputs: dict) -> dict:
    """Входы: ``game``, ``game_version``, ``mods`` (список словарей с
    ``name``, ``version``, ``dependencies``, ``load_index``), ``known_versions``.

    Выход: отсутствующие зависимости, устаревшие моды, дубликаты, конфликты
    порядка, рекомендованный порядок загрузки.
    """
    mods = inputs.get("mods") or []
    known_versions = inputs.get("known_versions") or {}
    by_name: dict[str, list[dict]] = {}
    for mod in mods:
        by_name.setdefault(str(mod.get("name", "")).strip(), []).append(mod)

    missing_dependencies: list[str] = []
    outdated: list[str] = []
    duplicates: list[str] = []
    order_issues: list[str] = []

    present = {name for name in by_name if name}
    for name, group in by_name.items():
        if len(group) > 1:
            duplicates.append(name)
        for mod in group:
            for dependency in mod.get("dependencies", []) or []:
                if dependency not in present:
                    missing_dependencies.append(f"{name} требует {dependency}")
            expected = known_versions.get(name)
            if expected and mod.get("version") and mod.get("version") != expected:
                outdated.append(f"{name}: {mod.get('version')} (актуальная {expected})")

    # порядок загрузки: зависимость должна идти раньше зависимого
    indexed = [mod for mod in mods if mod.get("load_index") is not None]
    order = {str(mod.get("name")): mod.get("load_index") for mod in indexed}
    for mod in mods:
        name = str(mod.get("name", ""))
        for dependency in mod.get("dependencies", []) or []:
            if name in order and dependency in order and order[dependency] > order[name]:
                order_issues.append(f"{dependency} должен загружаться раньше {name}")

    recommended_order = sorted(
        (str(mod.get("name")) for mod in mods if mod.get("name")),
        key=lambda name: _dependency_depth(name, by_name),
    )

    healthy = not (missing_dependencies or outdated or duplicates or order_issues)
    return {
        "game": inputs.get("game", ""),
        "healthy": healthy,
        "missing_dependencies": sorted(set(missing_dependencies)),
        "outdated": sorted(set(outdated)),
        "duplicates": sorted(set(duplicates)),
        "order_issues": sorted(set(order_issues)),
        "recommended_order": recommended_order,
    }


def _dependency_depth(name: str, by_name: dict, _depth: int = 0, _seen: frozenset[str] = frozenset()) -> int:
    """Глубина цепочки зависимостей (защита от циклов)."""
    if name in _seen or _depth > 8:
        return _depth
    deps = set()
    for mod in by_name.get(name, []):
        deps.update(mod.get("dependencies", []) or [])
    if not deps:
        return _depth
    return max(_dependency_depth(dep, by_name, _depth + 1, _seen | {name}) for dep in deps)
