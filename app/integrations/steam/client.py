"""Клиент официального Steam Web API.

Используются только публичные официальные методы:
* IPlayerService/GetOwnedGames — библиотека аккаунта (нужен бесплатный
  API-ключ владельца и открытый профиль);
* ISteamUser/GetPlayerSummaries — профиль.

Никаких паролей/сессий клиента Steam: только API-ключ из системного
хранилища секретов (ссылка вида ``keyring://steam/api_key``).
"""
from __future__ import annotations

import logging
import time

import requests

from app.domain.value_objects import OwnedGame
from app.integrations.steam.base import ProfileInfo, SteamLibrarySource, SteamSourceError

log = logging.getLogger(__name__)

WEB_API = "https://api.steampowered.com"


class SteamWebClient(SteamLibrarySource):
    name = "steam_web_api"

    def __init__(self, api_key: str, timeout: float = 15.0, min_interval: float = 0.35) -> None:
        if not api_key:
            raise SteamSourceError("Не задан Steam Web API ключ (Настройки → Steam).")
        self._key = api_key
        self._timeout = timeout
        self._min_interval = min_interval
        self._last_request = 0.0
        self._session = requests.Session()

    # ------------------------------------------------------------- helpers
    def _throttle(self) -> None:
        wait = self._min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)

    def _get_json(self, url: str, params: dict) -> dict:
        self._throttle()
        try:
            response = self._session.get(url, params=params, timeout=self._timeout)
        except requests.RequestException as exc:
            raise SteamSourceError(f"Сеть недоступна при запросе к Steam: {exc}") from exc
        self._last_request = time.monotonic()
        if response.status_code == 403:
            raise SteamSourceError("Steam API вернул 403: проверьте корректность API-ключа.")
        if response.status_code == 401:
            raise SteamSourceError("Steam API вернул 401: неверный ключ.")
        if response.status_code == 429:
            raise SteamSourceError("Steam API: слишком много запросов (429). Повторите позже.")
        if response.status_code >= 500:
            raise SteamSourceError(f"Steam API недоступен (HTTP {response.status_code}).")
        try:
            return response.json()
        except ValueError as exc:
            raise SteamSourceError("Steam API вернул некорректный JSON.") from exc

    # ------------------------------------------------------------ источник
    def fetch_profile(self, steam_id64: str) -> ProfileInfo:
        data = self._get_json(
            f"{WEB_API}/ISteamUser/GetPlayerSummaries/v0002/",
            {"key": self._key, "steamids": str(steam_id64)},
        )
        players = data.get("response", {}).get("players", [])
        if not players:
            raise SteamSourceError(
                f"Аккаунт {steam_id64} не найден или профиль закрыт. "
                "Откройте профиль в Steam или проверьте SteamID64."
            )
        player = players[0]
        return ProfileInfo(
            steam_id64=str(player.get("steamid", steam_id64)),
            persona_name=player.get("personaname", "?"),
            avatar_url=player.get("avatarfull", ""),
            profile_url=player.get("profileurl", ""),
            community_visibility=int(player.get("communityvisibilitystate", 0)),
            loc_country=player.get("loccountrycode", ""),
        )

    def fetch_owned_games(self, steam_id64: str) -> list[OwnedGame]:
        data = self._get_json(
            f"{WEB_API}/IPlayerService/GetOwnedGames/v0001/",
            {
                "key": self._key,
                "steamid": str(steam_id64),
                "include_appinfo": 1,
                "include_played_free_games": 1,
                "include_extended_appinfo": 1,
                "include_family_licenses": 1,
            },
        )
        response = data.get("response", {})
        raw_games = response.get("games", [])
        if "games" not in response:
            raise SteamSourceError(
                f"Профиль {steam_id64} не отдал библиотеку. Сделайте профиль/инвентарь публичным "
                "или убедитесь, что ключ принадлежит владельцу аккаунта."
            )
        result: list[OwnedGame] = []
        for raw in raw_games:
            app_id = int(raw.get("appid", 0))
            if not app_id:
                continue
            result.append(
                OwnedGame(
                    app_id=app_id,
                    name=str(raw.get("name") or f"App {app_id}"),
                    is_free=bool(raw.get("is_free", False)),
                    playtime_minutes=int(raw.get("playtime_forever", 0)),
                    family_shared=bool(raw.get("family_shared", False)),
                    extra={
                        "capsule": raw.get("capsule_filename"),
                        "sort_as": raw.get("sort_as"),
                        "has_workshop": bool(raw.get("has_workshop_files")),
                        "has_market": bool(raw.get("has_market")),
                    },
                )
            )
        return result
