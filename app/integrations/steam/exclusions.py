"""Локальный датасет поддержки игр в Steam Families.

Официального публичного источника «эта игра доступна в семье» не существует,
поэтому приложение опирается на:
1) признаки в метаданных (сторонний лаунчер/аккаунт, подписка, F2P);
2) редактируемый датасет ``exclusions.json`` (seed идёт в комплекте);
3) ручные переопределения оператора (имеют наивысший приоритет).

Файл пользователя: ``<data_dir>/exclusions.json`` — создаётся из seed
при первом запуске и дальше редактируется вручную/через экран «Библиотека».
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SEED_PATH = Path(__file__).parent / "data" / "exclusions_seed.json"


@dataclass(frozen=True)
class ExclusionEntry:
    app_id: int
    name: str
    kind: str          # excluded | allowed
    reason: str
    verified: bool


class ExclusionDataset:
    def __init__(self, entries: dict[int, ExclusionEntry]) -> None:
        self._entries = entries

    @classmethod
    def load(cls, user_file: Path | None = None) -> "ExclusionDataset":
        entries: dict[int, ExclusionEntry] = {}
        # 1) seed из поставки
        cls._read_file(SEED_PATH, entries)
        # 2) пользовательский файл (перекрывает seed)
        if user_file is not None:
            if not user_file.exists():
                cls._materialize_user_file(user_file)
            cls._read_file(user_file, entries)
        return cls(entries)

    @staticmethod
    def _materialize_user_file(user_file: Path) -> None:
        try:
            user_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(SEED_PATH, user_file)
            log.info("Создан редактируемый датасет исключений: %s", user_file)
        except OSError as exc:
            log.warning("Не удалось создать %s: %s", user_file, exc)

    @staticmethod
    def _read_file(path: Path, sink: dict[int, ExclusionEntry]) -> None:
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Датасет исключений %s повреждён: %s", path, exc)
            return
        for item in raw.get("entries", []):
            try:
                entry = ExclusionEntry(
                    app_id=int(item["app_id"]),
                    name=str(item.get("name", "")),
                    kind=str(item.get("kind", "excluded")),
                    reason=str(item.get("reason", "")),
                    verified=bool(item.get("verified", False)),
                )
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("Некорректная запись в датасете исключений %s: %s", path, exc)
                continue
            if entry.kind in ("excluded", "allowed"):
                sink[entry.app_id] = entry

    def get(self, app_id: int) -> ExclusionEntry | None:
        return self._entries.get(int(app_id))

    def __len__(self) -> int:
        return len(self._entries)
