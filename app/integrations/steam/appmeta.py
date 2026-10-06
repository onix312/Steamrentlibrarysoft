"""Метаданные игр из публичного endpoint магазина Steam.

``store.steampowered.com/api/appdetails`` — публичный недокументированный
эндпоинт чтения. Используем его с обязательным ограничением частоты запросов,
кэшем и деградацией при 429/503 (при ошибке данные просто не обновляются).
Никаких авторизаций и обхода защит.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

STORE_API = "https://store.steampowered.com/api/appdetails"

# Маппинг категорий магазина (выборка значимых для нас).
CATEGORY_IDS = {
    1: "Multi-player",
    2: "Single-player",
    9: "Co-op",
    38: "Online Co-op",
    39: "LAN Co-op",
    49: "PvP",
    36: "Online PvP",
    20: "MMO",
}


@dataclass(frozen=True)
class AppMeta:
    app_id: int
    name: str = ""
    is_free: bool = False
    price: float | None = None
    currency: str = "RUB"
    discount_pct: int = 0
    genres: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    release_date: str = ""
    header_image: str = ""
    store_url: str = ""


@dataclass
class StoreMetaClient:
    """Чтение метаданных с троттлингом и памятью результатов."""

    min_interval: float = 1.2
    timeout: float = 15.0
    cache_ttl_sec: float = 6 * 3600
    _cache: dict[int, tuple[float, AppMeta | None]] = field(default_factory=dict, init=False, repr=False)
    _last_request: float = field(default=0.0, init=False, repr=False)

    def get(self, app_id: int, currency: str = "RUB", language: str = "ru") -> AppMeta | None:
        now = time.monotonic()
        cached = self._cache.get(app_id)
        if cached and (now - cached[0]) < self.cache_ttl_sec:
            return cached[1]

        wait = self.min_interval - (now - self._last_request)
        if wait > 0:
            time.sleep(wait)

        try:
            response = requests.get(
                STORE_API,
                params={"appids": app_id, "cc": currency, "l": language},
                timeout=self.timeout,
            )
            self._last_request = time.monotonic()
        except requests.RequestException as exc:
            log.warning("Store metadata: сеть недоступна (%s) — пропускаем %s", exc, app_id)
            return None

        if response.status_code == 429:
            log.warning("Store metadata: 429 — увеличиваем паузу, %s пропускаем", app_id)
            self._last_request = time.monotonic() + self.min_interval * 10
            return None
        if response.status_code != 200:
            log.warning("Store metadata: HTTP %s для %s", response.status_code, app_id)
            return None

        try:
            payload = response.json()
        except ValueError:
            log.warning("Store metadata: некорректный JSON для %s", app_id)
            return None

        entry = payload.get(str(app_id), {})
        if not entry.get("success"):
            self._cache[app_id] = (time.monotonic(), None)
            return None
        data = entry.get("data", {})

        price: float | None = None
        discount = 0
        price_currency = currency
        price_overview = data.get("price_overview")
        if isinstance(price_overview, dict):
            price = float(price_overview.get("final", 0)) / 100.0
            discount = int(price_overview.get("discount_percent", 0))
            price_currency = price_overview.get("currency", currency)

        meta = AppMeta(
            app_id=app_id,
            name=data.get("name", ""),
            is_free=bool(data.get("is_free", False)),
            price=price,
            currency=price_currency,
            discount_pct=discount,
            genres=tuple(g.get("description", "") for g in data.get("genres", [])),
            categories=tuple(
                CATEGORY_IDS.get(int(c.get("id", 0)), c.get("description", ""))
                for c in data.get("categories", [])
            ),
            release_date=(data.get("release_date") or {}).get("date", ""),
            header_image=data.get("header_image", ""),
            store_url=f"https://store.steampowered.com/app/{app_id}/",
        )
        self._cache[app_id] = (time.monotonic(), meta)
        return meta
