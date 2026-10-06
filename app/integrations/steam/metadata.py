"""Провайдеры метаданных игр (цены, жанры, признаки).

Единая форма результата — словарь; источник либо симуляция, либо публичный
эндпоинт магазина с троттлингом.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.integrations.steam.appmeta import StoreMetaClient
from app.integrations.steam.simulator import SIM_GAMES

MetadataDict = dict


class MetadataProvider(ABC):
    @abstractmethod
    def get(self, app_id: int) -> MetadataDict | None:
        """Метаданные игры или None (недоступно/не найдено)."""


class SimulationMetadataProvider(MetadataProvider):
    def get(self, app_id: int) -> MetadataDict | None:
        meta = SIM_GAMES.get(int(app_id))
        if meta is None:
            return None
        return {
            "name": meta.name,
            "is_free": meta.is_free,
            "price": meta.price,
            "currency": "RUB",
            "discount_pct": 0,
            "genres": list(meta.genres),
            "categories": list(meta.categories),
            "release_date": str(meta.release_year),
            "header_image": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{app_id}/header.jpg",
            "store_url": f"https://store.steampowered.com/app/{app_id}/",
            "third_party_launcher": meta.third_party_launcher,
            "third_party_account": meta.third_party_account,
            "multiplayer": meta.multiplayer,
            "coop": meta.coop,
            "anticheat": meta.anticheat,
            "review_score": meta.review_score,
            "review_count": meta.review_count,
            "owners": meta.owners,
        }


class StoreMetadataProvider(MetadataProvider):
    def __init__(self, client: StoreMetaClient | None = None, min_interval: float = 1.2) -> None:
        self._client = client or StoreMetaClient(min_interval=min_interval)

    def get(self, app_id: int) -> MetadataDict | None:
        meta = self._client.get(int(app_id))
        if meta is None:
            return None
        categories = list(meta.categories)
        return {
            "name": meta.name,
            "is_free": meta.is_free,
            "price": meta.price,
            "currency": meta.currency,
            "discount_pct": meta.discount_pct,
            "genres": list(meta.genres),
            "categories": categories,
            "release_date": meta.release_date,
            "header_image": meta.header_image,
            "store_url": meta.store_url,
            "third_party_launcher": False,  # достоверно определяется только вручную/датасетом
            "third_party_account": False,
            "multiplayer": any("Multi-player" in c or "PvP" in c or "MMO" in c for c in categories),
            "coop": any("Co-op" in c for c in categories),
            "anticheat": None,
            "review_score": None,
            "review_count": None,
            "owners": None,
        }
