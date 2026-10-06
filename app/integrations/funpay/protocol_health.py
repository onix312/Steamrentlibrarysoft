"""FunPay parser contract checks and global circuit breaker."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from bs4 import BeautifulSoup


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"


@dataclass(frozen=True)
class HealthCheck:
    name: str
    ok: bool
    detail: str


class ProtocolCircuitBreaker:
    def __init__(self, failure_threshold: int = 1, cooldown_minutes: int = 30) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self.failures = 0
        self.opened_at: datetime | None = None
        self.reason = ""

    @property
    def state(self) -> CircuitState:
        if self.opened_at and datetime.now(timezone.utc) - self.opened_at < self.cooldown:
            return CircuitState.OPEN
        if self.opened_at:
            self.reset()
        return CircuitState.CLOSED

    def trip(self, reason: str) -> None:
        self.failures += 1
        self.reason = reason
        if self.failures >= self.failure_threshold:
            self.opened_at = datetime.now(timezone.utc)

    def reset(self) -> None:
        self.failures = 0
        self.opened_at = None
        self.reason = ""

    def ensure_available(self) -> None:
        if self.state == CircuitState.OPEN:
            raise RuntimeError(f"FunPay automation paused by protocol circuit breaker: {self.reason}")


GLOBAL_FUNPAY_BREAKER = ProtocolCircuitBreaker()


def validate_auth_html(html: str) -> HealthCheck:
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find("div", {"class": "user-link-name"})
    body = soup.find("body")
    raw = body.get("data-app-data") if body else None
    if not node or not raw:
        return HealthCheck("auth", False, "missing user-link-name or data-app-data")
    try:
        data = json.loads(raw)
    except ValueError:
        return HealthCheck("auth", False, "invalid data-app-data JSON")
    ok = bool(data.get("userId") and data.get("csrf-token"))
    return HealthCheck("auth", ok, "ok" if ok else "missing userId/csrf-token")


def validate_orders_html(html: str) -> HealthCheck:
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select("a.tc-item")
    if not items:
        # Empty order history is valid if the trade container still exists.
        marker = soup.select_one(".trade-list, .tc-list, [data-type='orders']")
        return HealthCheck("orders", marker is not None, "empty order list" if marker else "order container missing")
    required = ("tc-order", "tc-price")
    for item in items[:3]:
        for cls in required:
            if item.find("div", {"class": cls}) is None:
                return HealthCheck("orders", False, f"missing .{cls}")
    return HealthCheck("orders", True, f"{len(items)} rows")


def validate_chat_json(payload: dict) -> HealthCheck:
    objects = payload.get("objects")
    ok = isinstance(objects, list) or isinstance(payload.get("data"), dict)
    return HealthCheck("chat", ok, "ok" if ok else "runner chat payload shape changed")


def validate_runner_json(payload: dict) -> HealthCheck:
    ok = isinstance(payload, dict) and ("objects" in payload or "data" in payload or "request" in payload)
    return HealthCheck("runner", ok, "ok" if ok else "runner payload shape changed")


def validate_lots_html(html: str) -> HealthCheck:
    soup = BeautifulSoup(html, "html.parser")
    has_lots = bool(soup.select(".tc-item, .offer, [data-offer]"))
    empty_marker = bool(soup.select(".trade-list, .tc-list, form"))
    return HealthCheck("lots", has_lots or empty_marker, "ok" if (has_lots or empty_marker) else "lots markup missing")


def evaluate_contracts(*checks: HealthCheck) -> list[HealthCheck]:
    failed = [check for check in checks if not check.ok]
    if failed:
        GLOBAL_FUNPAY_BREAKER.trip("; ".join(f"{x.name}: {x.detail}" for x in failed))
    else:
        GLOBAL_FUNPAY_BREAKER.reset()
    return list(checks)
