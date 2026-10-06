"""Клиент официального Twitch Helix API.

Используются ТОЛЬКО публичные эндпоинты (см. docs/RESEARCH.md, раздел 5):
* ``POST /oauth2/token`` (client_credentials) — app access token;
* ``GET /helix/search/categories`` / ``GET /helix/games`` — резолв игры;
* ``GET /helix/streams`` — live-стримы игры.

Прогресса дропов, списка кампаний и клэйма в официальном публичном API НЕТ —
эти возможности декларируются как ``UNSUPPORTED`` и выполняются вручную.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import requests

from app.domain.enums import TwitchCapability
from app.domain.value_objects import CapabilityError, DomainError

log = logging.getLogger(__name__)

HELIX = "https://api.twitch.tv/helix"
OAUTH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"


@dataclass(frozen=True)
class StreamInfo:
    channel: str
    viewers: int
    started_at: str
    language: str
    game: str
    title: str


class HelixClient:
    def __init__(self, client_id: str, client_secret: str, timeout: float = 15.0) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout
        self._token: str | None = None
        self._token_expires = 0.0

    # ------------------------------------------------------- аутентификация
    def _ensure_token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        try:
            response = requests.post(
                OAUTH_TOKEN_URL,
                params={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "grant_type": "client_credentials",
                },
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise DomainError(f"Twitch OAuth недоступен: {exc}") from exc
        if response.status_code != 200:
            raise DomainError(f"Twitch OAuth HTTP {response.status_code}: проверьте client_id/secret.")
        payload = response.json()
        self._token = payload["access_token"]
        self._token_expires = time.time() + int(payload.get("expires_in", 3600))
        return self._token

    def _headers(self) -> dict:
        return {
            "Client-Id": self._client_id,
            "Authorization": f"Bearer {self._ensure_token()}",
        }

    # ------------------------------------------------------------- публичные
    def get_game_id(self, game_name: str) -> str | None:
        response = requests.get(
            f"{HELIX}/search/categories",
            headers=self._headers(),
            params={"query": game_name, "first": 5},
            timeout=self._timeout,
        )
        if response.status_code != 200:
            raise DomainError(f"Twitch search/categories HTTP {response.status_code}")
        data = response.json().get("data", [])
        if not data:
            return None
        return str(data[0]["id"])

    def get_streams(self, game_id: str, language: str | None = None, first: int = 20) -> list[StreamInfo]:
        params: dict = {"game_id": game_id, "first": min(100, max(1, first))}
        if language:
            params["language"] = language
        response = requests.get(f"{HELIX}/streams", headers=self._headers(),
                                params=params, timeout=self._timeout)
        if response.status_code != 200:
            raise DomainError(f"Twitch streams HTTP {response.status_code}")
        result: list[StreamInfo] = []
        for item in response.json().get("data", []):
            result.append(StreamInfo(
                channel=item.get("user_login", ""),
                viewers=int(item.get("viewer_count", 0)),
                started_at=item.get("started_at", ""),
                language=item.get("language", ""),
                game=item.get("game_name", ""),
                title=item.get("title", ""),
            ))
        return result

    def best_stream(self, game_name: str, language: str | None = None) -> StreamInfo | None:
        """Лучший стрим: стабильный живой канал нужной игры (максимум зрителей)."""
        game_id = self.get_game_id(game_name)
        if game_id is None:
            return None
        streams = self.get_streams(game_id, language=language, first=50)
        if not streams:
            return None
        return max(streams, key=lambda stream: stream.viewers)

    # ------------------------------------------------------------ возможности
    @staticmethod
    def capabilities() -> dict[TwitchCapability, str]:
        return {
            TwitchCapability.STREAMS: "available",
            TwitchCapability.GAMES: "available",
            TwitchCapability.DROPS_PROGRESS: "unsupported",
            TwitchCapability.CLAIM: "unsupported",
            TwitchCapability.CAMPAIGN_LIST: "unsupported",
        }

    @staticmethod
    def unsupported_message(capability: TwitchCapability) -> str:
        messages = {
            TwitchCapability.DROPS_PROGRESS: (
                "Официального публичного эндпоинта для прогресса дропов нет. "
                "Проверьте прогресс вручную в инвентаре и подтвердите его в приложении."
            ),
            TwitchCapability.CLAIM: (
                "Клэйм дропов выполняется только вручную пользователем в браузере."
            ),
            TwitchCapability.CAMPAIGN_LIST: (
                "Списка кампаний в публичном API нет — внесите кампанию вручную."
            ),
        }
        return messages.get(capability, "Не поддерживается.")
