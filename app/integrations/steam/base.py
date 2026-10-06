"""Интерфейс источника библиотек Steam.

GUI и сервисы не знают, откуда приходят данные: из официального
Steam Web API, из симулятора (офлайн/демо) или из будущего источника.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.domain.value_objects import OwnedGame


@dataclass(frozen=True)
class ProfileInfo:
    steam_id64: str
    persona_name: str
    avatar_url: str = ""
    profile_url: str = ""
    community_visibility: int = 3  # 3 = публичный
    loc_country: str = ""


class SteamSourceError(Exception):
    """Ошибка источника данных Steam с человекочитаемым сообщением."""


class SteamLibrarySource(ABC):
    """Абстракция источника библиотек."""

    name: str = "abstract"

    @abstractmethod
    def fetch_profile(self, steam_id64: str) -> ProfileInfo:
        """Профиль аккаунта (имя, аватар, статус)."""

    @abstractmethod
    def fetch_owned_games(self, steam_id64: str) -> list[OwnedGame]:
        """Список игр аккаунта (включая F2P)."""

    def check_available(self) -> tuple[bool, str]:
        """(доступен ли источник, пояснение). По умолчанию — доступен."""
        return True, ""
