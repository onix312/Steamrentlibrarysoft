"""Shared diagnostics: attachment parsing and game-specific knowledge plugins."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Iterable


MAX_ATTACHMENT_CHARS = 250_000

GAME_PLUGINS: dict[str, dict] = {
    "Project Zomboid": {
        "signals": ["console.txt", "coop-console.txt", "workshop", "mod id", "server.ini"],
        "recommendations": ["Проверьте console.txt и порядок Workshop ID/Mod ID."],
    },
    "Minecraft": {
        "signals": ["latest.log", "crash-reports", "forge", "fabric", "paper", "spigot"],
        "recommendations": ["Сопоставьте loader, версию Java, моды/плагины и версию сервера."],
    },
    "7 Days to Die": {
        "signals": ["output_log", "dedicated server", "eac", "world"],
        "recommendations": ["Проверьте serverconfig.xml и совместимость мира после обновления."],
    },
    "Ready or Not": {
        "signals": ["ue4", "unreal", "pak", "mod.io"],
        "recommendations": ["Проверьте UE crash log и временно исключите сторонние pak-моды."],
    },
    "Phasmophobia": {
        "signals": ["unity", "player.log", "voice", "microphone"],
        "recommendations": ["Проверьте Unity Player.log и устройства ввода/голосовой подсистемы."],
    },
    "Rust": {
        "signals": ["oxide", "umod", "server.cfg", "rcon"],
        "recommendations": ["Проверьте server.cfg, плагины uMod/Oxide и актуальность server build."],
    },
}


def attachment_text(attachments: Iterable[dict]) -> str:
    chunks: list[str] = []
    remaining = MAX_ATTACHMENT_CHARS
    for item in attachments:
        if remaining <= 0:
            break
        name = str(item.get("name") or item.get("filename") or "attachment")
        content = item.get("content")
        if content is None:
            continue
        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="replace")
        text = str(content)[:remaining]
        chunks.append(f"--- {name} ---\n{text}")
        remaining -= len(text)
    return "\n".join(chunks)


def enrich_case(case: Any) -> Any:
    inputs = dict(case.inputs)
    extra = attachment_text(case.attachments)
    if extra:
        current = str(inputs.get("logs") or "")
        inputs["logs"] = (current + "\n" + extra).strip()

    plugin = GAME_PLUGINS.get(case.game)
    if plugin:
        metadata = dict(case.metadata)
        metadata["game_plugin"] = case.game
        metadata["plugin_signals"] = list(plugin["signals"])
        inputs["plugin_signals"] = list(plugin["signals"])
        inputs["plugin_recommendations"] = list(plugin["recommendations"])
        return replace(case, inputs=inputs, metadata=metadata)
    return replace(case, inputs=inputs)


def plugin_recommendations(case: Any) -> list[str]:
    return list((case.inputs.get("plugin_recommendations") or []))
