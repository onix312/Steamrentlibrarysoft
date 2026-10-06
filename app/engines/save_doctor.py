"""Save Doctor v2: safe validation and recovery recommendations."""
from __future__ import annotations


def diagnose_save(inputs: dict) -> dict:
    symptoms = " ".join(str(inputs.get(k, "")) for k in ("error", "description", "logs")).lower()
    evidence = []
    recommendations = []
    confidence = 0.45
    category = "save_unknown"

    missing_mods = inputs.get("missing_mods") or []
    if isinstance(missing_mods, str):
        missing_mods = [missing_mods]
    game_version = inputs.get("game_version")
    save_version = inputs.get("save_version")

    if missing_mods:
        category = "mod_reference"
        confidence = 0.94
        evidence.append("missing_mods: " + ", ".join(map(str, missing_mods[:10])))
        recommendations.extend([
            "Восстановите точные версии отсутствующих модов.",
            "Загрузите копию save и удаляйте зависимости только штатным способом игры.",
        ])
    elif game_version and save_version and str(game_version) != str(save_version):
        category = "version_mismatch"
        confidence = 0.92
        evidence.extend([
            f"game_version={game_version}",
            f"save_version={save_version}",
        ])
        recommendations.append(
            "Откройте копию save на совместимой версии и выполните штатную миграцию."
        )

    if category == "save_unknown" and any(token in symptoms for token in ("corrupt", "checksum", "повреж", "invalid save")):
        category = "corruption"
        confidence = 0.85
        evidence.append("Есть признаки повреждения/ошибки целостности сохранения.")
        recommendations.extend([
            "Работайте только с копией сохранения.",
            "Проверьте последний автоматический/ручной backup.",
            "Сравните версию игры и версию сохранения.",
        ])
    elif category == "save_unknown" and any(token in symptoms for token in ("mod", "missing asset", "missing class", "workshop")):
        category = "mod_reference"
        confidence = 0.78
        evidence.append("Сохранение ссылается на отсутствующий/несовместимый мод.")
        recommendations.extend([
            "Восстановите точный набор модов/версий, с которым создавался save.",
            "После успешного запуска удаляйте зависимости штатным способом игры.",
        ])
    elif category == "save_unknown" and any(token in symptoms for token in ("version", "downgrade", "newer version")):
        category = "version_mismatch"
        confidence = 0.8
        evidence.append("Версия save не совпадает с версией игры.")
        recommendations.append("Запустите save на совместимой версии и выполните штатную миграцию.")

    if not recommendations:
        recommendations = [
            "Сделайте копию save и приложите полный лог загрузки.",
            "Укажите версию игры, DLC и список модов.",
        ]

    return {
        "problem": {
            "corruption": "Вероятно повреждение сохранения",
            "mod_reference": "Проблема ссылок на моды",
            "version_mismatch": "Несовместимость версии сохранения",
        }.get(category, "Причина проблемы сохранения не определена"),
        "category": category,
        "confidence": confidence,
        "evidence": evidence or ["Недостаточно данных для точной классификации."],
        "recommendations": recommendations,
        "fix": "\n".join(recommendations),
        "rollback": "Всегда сохраняйте исходный save неизменным; работайте с копией.",
        "risk": "medium",
        "backup_available": bool(inputs.get("backup_available")),
    }
