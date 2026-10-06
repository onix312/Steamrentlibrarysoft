"""Config Factory: генератор конфигов (ТЗ §10).

Выход — набор файлов (имя → содержимое) + README + инструкция + план отката.
Продукт полностью локальный и детерминированный: идеально для AUTO-выдачи.
"""
from __future__ import annotations

#: Пресеты серверных конфигов.
SERVER_PRESETS: dict[str, dict] = {
    "beginner": {
        "difficulty": "easy", "pvp": False, "loot": "abundant",
        "zombie_speed": "slow", "description": "Прощающий режим для новичков",
    },
    "hardcore": {
        "difficulty": "hardcore", "pvp": True, "loot": "scarce",
        "zombie_speed": "fast", "description": "Максимальная сложность, одна жизнь",
    },
    "coop": {
        "difficulty": "normal", "pvp": False, "loot": "normal",
        "zombie_speed": "normal", "description": "Сбалансированный кооп",
    },
    "realistic": {
        "difficulty": "hard", "pvp": False, "loot": "scarce",
        "zombie_speed": "normal", "description": "Реализм и выживание",
    },
    "server": {
        "max_players": 20, "view_distance": 10, "description": "Базовый серверный конфиг",
    },
}


def generate(inputs: dict) -> dict:
    """Генерирует пакет файлов. Входы: ``preset``, ``game``, ``options``.

    Выход: ``files`` (имя → текст), ``readme``, ``instructions``, ``rollback``.
    """
    preset_name = str(inputs.get("preset", "beginner"))
    preset = SERVER_PRESETS.get(preset_name, SERVER_PRESETS["beginner"])
    game = str(inputs.get("game", "game"))
    options = inputs.get("options") or {}

    config_lines = [f"# {game} config — preset: {preset_name}", f"# {preset.get('description', '')}"]
    for key, value in {**preset, **options}.items():
        if key == "description":
            continue
        config_lines.append(f"{key}={value}")
    config_text = "\n".join(config_lines) + "\n"

    readme = (
        f"# {preset_name.upper()} config для {game}\n\n"
        f"{preset.get('description', '')}\n\n"
        "## Установка\n"
        "1. Сделайте резервную копию текущего конфига.\n"
        "2. Замените файл конфига на прилагаемый.\n"
        "3. Запустите игру/сервер и проверьте настройки.\n"
    )
    instructions = (
        "Сохраните приложенный конфиг в папку настроек, перезапустите игру/сервер. "
        "Все параметры можно менять — формат стандартный."
    )
    rollback = (
        "Для отката восстановите вашу резервную копию конфига. "
        "Мы рекомендуем делать бэкап перед каждым изменением."
    )

    return {
        "files": {f"{game}_{preset_name}.cfg": config_text},
        "readme": readme,
        "instructions": instructions,
        "rollback": rollback,
        "preset": preset_name,
        "game": game,
    }
