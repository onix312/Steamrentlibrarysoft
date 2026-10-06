"""Хранилище секретов.

Приоритет — системное хранилище через ``keyring`` (на Windows это
Credential Manager). В БД/конфиге хранятся только ссылки вида
``keyring://steam/api_key``.

Если ``keyring`` недоступен, используется резервное локальное хранилище с
обфускацией (НЕ криптография, а защита от случайного подглядывания);
файл создаётся с правами 0600 и в логах помечается как fallback.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

PREFIX = "keyring://"
SERVICE = "SteamRentLibrary"


class SecretStore:
    def __init__(self, fallback_dir: Path | None = None) -> None:
        self._fallback_file = fallback_dir / "secrets.bin" if fallback_dir else None
        self._backend = None
        try:
            import keyring  # type: ignore
            self._backend = keyring
            log.info("Секреты: используется системное хранилище (keyring)")
        except ImportError:
            log.warning("Пакет keyring недоступен — используется резервное локальное хранилище (только для этой машины)")

    # ------------------------------------------------------------------ API
    def set(self, name: str, value: str) -> None:
        if self._backend is not None:
            self._backend.set_password(SERVICE, name, value)
            return
        self._fallback_set(name, value)

    def get(self, name: str) -> str | None:
        if self._backend is not None:
            try:
                return self._backend.get_password(SERVICE, name)
            except Exception as exc:  # noqa: BLE001 - бэкенды падают по-разному
                log.error("keyring: не удалось получить секрет %s: %s", name, exc)
                return None
        return self._fallback_get(name)

    def delete(self, name: str) -> None:
        if self._backend is not None:
            try:
                self._backend.delete_password(SERVICE, name)
            except Exception as exc:  # noqa: BLE001
                log.warning("keyring: не удалось удалить секрет %s: %s", name, exc)
            return
        self._fallback_set(name, None)  # type: ignore[arg-type]

    def has(self, name: str) -> bool:
        return self.get(name) is not None

    @staticmethod
    def ref(name: str) -> str:
        return f"{PREFIX}{name}"

    @staticmethod
    def parse_ref(ref: str) -> str | None:
        return ref[len(PREFIX):] if ref.startswith(PREFIX) else None

    def resolve(self, ref: str) -> str | None:
        """Возвращает секрет по ссылке ``keyring://...`` либо ``None``."""
        if not ref:
            return None
        name = self.parse_ref(ref)
        if name is None:
            log.error("Некорректная ссылка на секрет: %r", ref)
            return None
        return self.get(name)

    # -------------------------------------------------------------- fallback
    @staticmethod
    def _obfuscate(data: bytes) -> str:
        key = (SERVICE + ":" + os.environ.get("USERNAME", "user")).encode()
        out = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
        return base64.b64encode(out).decode()

    @staticmethod
    def _deobfuscate(text: str) -> bytes:
        key = (SERVICE + ":" + os.environ.get("USERNAME", "user")).encode()
        data = base64.b64decode(text.encode())
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))

    def _fallback_read(self) -> dict[str, str]:
        if self._fallback_file is None or not self._fallback_file.exists():
            return {}
        try:
            return json.loads(self._deobfuscate(self._fallback_file.read_text(encoding="utf-8")))
        except (ValueError, OSError) as exc:
            log.error("Не удалось прочитать fallback-хранилище секретов: %s", exc)
            return {}

    def _fallback_write(self, data: dict[str, str]) -> None:
        if self._fallback_file is None:
            raise RuntimeError("Для секретов не настроена директория данных")
        self._fallback_file.parent.mkdir(parents=True, exist_ok=True)
        self._fallback_file.write_text(self._obfuscate(json.dumps(data).encode()), encoding="utf-8")
        try:
            os.chmod(self._fallback_file, 0o600)
        except OSError:
            pass

    def _fallback_set(self, name: str, value: str | None) -> None:
        data = self._fallback_read()
        if value is None:
            data.pop(name, None)
        else:
            data[name] = value
        self._fallback_write(data)

    def _fallback_get(self, name: str) -> str | None:
        return self._fallback_read().get(name)
