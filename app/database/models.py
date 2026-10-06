"""Модели базы данных (см. docs/ARCHITECTURE.md — схема)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TZDateTime


def utcnow() -> datetime:
    from datetime import timezone

    return datetime.now(timezone.utc)


class SteamAccount(Base):
    __tablename__ = "steam_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    steam_id64: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    region: Mapped[Optional[str]] = mapped_column(String(8))
    profile_url: Mapped[Optional[str]] = mapped_column(String(512))

    family_role: Mapped[str] = mapped_column(String(16), default="adult")
    family_joined_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    family_left_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)

    games_count: Mapped[int] = mapped_column(Integer, default=0)
    paid_count: Mapped[int] = mapped_column(Integer, default=0)
    free_count: Mapped[int] = mapped_column(Integer, default=0)

    last_sync_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    account_games: Mapped[list["AccountGame"]] = relationship(back_populates="account", cascade="all, delete-orphan")
    licenses: Mapped[list["GameLicense"]] = relationship(back_populates="account")


class Game(Base):
    """Уникальная игра (один AppID — одна запись независимо от числа владельцев)."""
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(primary_key=True)
    app_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    sort_as: Mapped[Optional[str]] = mapped_column(String(256))
    is_free: Mapped[bool] = mapped_column(Boolean, default=False)
    release_date: Mapped[Optional[str]] = mapped_column(String(32))

    genres: Mapped[list] = mapped_column(JSON, default=list)
    categories: Mapped[list] = mapped_column(JSON, default=list)
    tags: Mapped[list] = mapped_column(JSON, default=list)

    price: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    discount_pct: Mapped[int] = mapped_column(Integer, default=0)
    price_updated_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)

    header_image_url: Mapped[Optional[str]] = mapped_column(String(512))
    store_url: Mapped[Optional[str]] = mapped_column(String(512))

    requires_third_party_launcher: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_third_party_account: Mapped[bool] = mapped_column(Boolean, default=False)
    has_multiplayer: Mapped[bool] = mapped_column(Boolean, default=False)
    has_coop: Mapped[bool] = mapped_column(Boolean, default=False)
    anticheat: Mapped[Optional[str]] = mapped_column(String(64))

    review_score: Mapped[Optional[int]] = mapped_column(Integer)      # 0..100
    review_count: Mapped[Optional[int]] = mapped_column(Integer)
    approx_owners: Mapped[Optional[int]] = mapped_column(Integer)

    # --- Steam Families eligibility ---
    eligibility_status: Mapped[str] = mapped_column(String(32), default="needs_check")
    eligibility_reason: Mapped[Optional[str]] = mapped_column(Text)
    eligibility_source: Mapped[Optional[str]] = mapped_column(String(16))
    eligibility_checked_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    eligibility_override: Mapped[Optional[str]] = mapped_column(String(32))  # available/unavailable/NULL

    # --- Scores ---
    demand_score: Mapped[float] = mapped_column(Float, default=0.0)
    demand_grade: Mapped[str] = mapped_column(String(8), default="D")
    demand_breakdown: Mapped[Optional[dict]] = mapped_column(JSON)
    commercial_score: Mapped[float] = mapped_column(Float, default=0.0)
    score_computed_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)

    notes: Mapped[Optional[str]] = mapped_column(Text)

    licenses: Mapped[list["GameLicense"]] = relationship(back_populates="game")


class AccountGame(Base):
    """Срез синхронизации: что именно имеет данный аккаунт."""
    __tablename__ = "account_games"
    __table_args__ = (UniqueConstraint("account_id", "app_id", name="uq_account_game"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("steam_accounts.id", ondelete="CASCADE"), index=True)
    app_id: Mapped[int] = mapped_column(ForeignKey("games.app_id"), index=True)
    source: Mapped[str] = mapped_column(String(16), default="owned")  # owned | family
    playtime_minutes: Mapped[int] = mapped_column(Integer, default=0)
    last_played_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)

    account: Mapped[SteamAccount] = relationship(back_populates="account_games")


class GameLicense(Base):
    """Одна копия игры на одном аккаунте = одна лицензия."""
    __tablename__ = "game_licenses"
    __table_args__ = (UniqueConstraint("game_id", "account_id", name="uq_license_game_account"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("steam_accounts.id"), index=True)
    source: Mapped[str] = mapped_column(String(16), default="owned")
    acquired_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    hidden_from_family: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    game: Mapped[Game] = relationship(back_populates="licenses")
    account: Mapped[SteamAccount] = relationship(back_populates="licenses")


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[int] = mapped_column(primary_key=True)
    funpay_username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(128))
    first_order_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    last_order_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    total_spent: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    problems: Mapped[Optional[str]] = mapped_column(Text)
    blacklisted: Mapped[bool] = mapped_column(Boolean, default=False)

    orders: Mapped[list["Order"]] = relationship(back_populates="client")


class Bundle(Base):
    __tablename__ = "bundles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[Optional[str]] = mapped_column(Text)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="suggested")
    suggested_reason: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    games: Mapped[list["BundleGame"]] = relationship(back_populates="bundle", cascade="all, delete-orphan")


class BundleGame(Base):
    __tablename__ = "bundle_games"
    __table_args__ = (UniqueConstraint("bundle_id", "game_id", name="uq_bundle_game"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bundle_id: Mapped[int] = mapped_column(ForeignKey("bundles.id", ondelete="CASCADE"))
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"))

    bundle: Mapped[Bundle] = relationship(back_populates="games")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    funpay_order_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    game_id: Mapped[Optional[int]] = mapped_column(ForeignKey("games.id"))
    bundle_id: Mapped[Optional[int]] = mapped_column(ForeignKey("bundles.id"))
    price: Mapped[float] = mapped_column(Float, default=0.0)
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    status: Mapped[str] = mapped_column(String(24), default="new", index=True)
    purchased_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, index=True)
    access_starts_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    access_ends_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    access_days: Mapped[Optional[int]] = mapped_column(Integer)
    assigned_license_id: Mapped[Optional[int]] = mapped_column(ForeignKey("game_licenses.id"))
    notes: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(16), default="manual")  # manual | funpay
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id"))
    workflow_run_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workflow_runs.id"))
    automation_mode: Mapped[Optional[str]] = mapped_column(String(16))

    client: Mapped[Client] = relationship(back_populates="orders")
    lease: Mapped[Optional["AccessLease"]] = relationship(back_populates="order", uselist=False)


class AccessLease(Base):
    __tablename__ = "access_leases"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id"), index=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    owner_account_id: Mapped[int] = mapped_column(ForeignKey("steam_accounts.id"))
    license_id: Mapped[int] = mapped_column(ForeignKey("game_licenses.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(TZDateTime)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime, index=True)
    terminated_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)

    order: Mapped[Optional[Order]] = relationship(back_populates="lease")

    @property
    def is_open(self) -> bool:
        return self.status in ("scheduled", "active", "expiring")


# Частичный уникальный индекс: одна лицензия — максимум одна АКТИВНАЯ аренда.
Index(
    "uq_active_lease_per_license",
    "license_id",
    unique=True,
    sqlite_where=text("status IN ('scheduled', 'active', 'expiring')"),
)


class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[Optional[int]] = mapped_column(ForeignKey("games.id"))
    bundle_id: Mapped[Optional[int]] = mapped_column(ForeignKey("bundles.id"))
    funpay_listing_id: Mapped[Optional[str]] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(256))
    short_description: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    faq: Mapped[Optional[str]] = mapped_column(Text)
    terms: Mapped[Optional[str]] = mapped_column(Text)
    post_purchase_template: Mapped[Optional[str]] = mapped_column(Text)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    access_days: Mapped[int] = mapped_column(Integer, default=30)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    sales_count: Mapped[int] = mapped_column(Integer, default=0)
    revenue: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id"))
    direction: Mapped[str] = mapped_column(String(4), default="out")
    template_key: Mapped[Optional[str]] = mapped_column(String(32))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="drafted")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    external_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)  # ID на FunPay
    chat_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)      # чат FunPay


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), index=True)
    price: Mapped[float] = mapped_column(Float)
    discount_pct: Mapped[int] = mapped_column(Integer, default=0)
    recorded_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class SyncHistory(Base):
    __tablename__ = "sync_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    started_at: Mapped[datetime] = mapped_column(TZDateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    status: Mapped[str] = mapped_column(String(16))
    details: Mapped[Optional[dict]] = mapped_column(JSON)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(16), default="app")
    action: Mapped[str] = mapped_column(String(48), index=True)
    entity: Mapped[Optional[str]] = mapped_column(String(32))
    entity_id: Mapped[Optional[str]] = mapped_column(String(32))
    details: Mapped[Optional[dict]] = mapped_column(JSON)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, index=True)
    kind: Mapped[str] = mapped_column(String(24), default="info")
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[Optional[str]] = mapped_column(Text)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


# ==================================================================
# Модуль Twitch Drops
# ==================================================================


class BrowserProfile(Base):
    __tablename__ = "browser_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    browser: Mapped[str] = mapped_column(String(16), default="chrome")  # chrome | edge | chromium
    profile_dir: Mapped[str] = mapped_column(String(256))               # напр. "Profile 01"
    executable: Mapped[Optional[str]] = mapped_column(String(512))      # переопределение пути
    notes: Mapped[Optional[str]] = mapped_column(Text)


class TwitchAccount(Base):
    __tablename__ = "twitch_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))
    twitch_login: Mapped[Optional[str]] = mapped_column(String(128))
    browser_profile_id: Mapped[Optional[int]] = mapped_column(ForeignKey("browser_profiles.id"))
    email_label: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="idle", index=True)
    login_ref: Mapped[Optional[str]] = mapped_column(String(256))   # keyring://... (секреты НЕ в БД)
    current_sku_id: Mapped[Optional[int]] = mapped_column(ForeignKey("product_skus.id"))
    reserved_order_id: Mapped[Optional[int]] = mapped_column(Integer)
    estimated_value: Mapped[float] = mapped_column(Float, default=0.0)
    last_activity_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    sold_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    profile: Mapped[Optional["BrowserProfile"]] = relationship()


class DropCampaign(Base):
    __tablename__ = "drop_campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_name: Mapped[str] = mapped_column(String(128), index=True)
    twitch_game_id: Mapped[Optional[str]] = mapped_column(String(32))
    campaign_name: Mapped[str] = mapped_column(String(256))
    starts_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    ends_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime, index=True)
    required_minutes: Mapped[int] = mapped_column(Integer, default=120)
    rewards: Mapped[list] = mapped_column(JSON, default=list)      # ["Reward 1", ...]
    reward_count: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String(24), default="manual")
    priority: Mapped[int] = mapped_column(Integer, default=0)      # ручной буст приоритета
    estimated_value: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[Optional[str]] = mapped_column(Text)


class AccountCampaign(Base):
    """Прогресс аккаунта по кампании (ожидаемый — локальный, подтверждённый — по инвентарю)."""
    __tablename__ = "account_campaigns"
    __table_args__ = (UniqueConstraint("account_id", "campaign_id", name="uq_account_campaign"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("twitch_accounts.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("drop_campaigns.id", ondelete="CASCADE"), index=True)
    expected_minutes: Mapped[int] = mapped_column(Integer, default=0)   # оценка локального таймера
    verified_minutes: Mapped[int] = mapped_column(Integer, default=0)   # подтверждено по инвентарю
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    claimed_count: Mapped[int] = mapped_column(Integer, default=0)
    claim_states: Mapped[list] = mapped_column(JSON, default=list)      # ["claimed","ready_to_claim","locked"]


class WatchSession(Base):
    __tablename__ = "watch_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("twitch_accounts.id"), index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("drop_campaigns.id"), index=True)
    channel: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(24), default="planned", index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    planned_minutes: Mapped[int] = mapped_column(Integer, default=0)
    expected_finish: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    actual_progress: Mapped[int] = mapped_column(Integer, default=0)    # подтверждённые минуты
    ended_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    browser_pid: Mapped[Optional[int]] = mapped_column(Integer)
    notes: Mapped[Optional[str]] = mapped_column(Text)


class ProductSku(Base):
    __tablename__ = "product_skus"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    required_campaigns: Mapped[dict] = mapped_column(JSON, default=dict)  # {"PoE2": 14, "GW2": 8}
    minimum_account_value: Mapped[float] = mapped_column(Float, default=0.0)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    minimum_price: Mapped[float] = mapped_column(Float, default=0.0)
    target_stock: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    manual_price_override: Mapped[Optional[float]] = mapped_column(Float)


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_name: Mapped[str] = mapped_column(String(128), index=True)
    captured_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    competitors: Mapped[int] = mapped_column(Integer, default=0)
    lowest_price: Mapped[float] = mapped_column(Float, default=0.0)
    median_price: Mapped[float] = mapped_column(Float, default=0.0)
    highest_price: Mapped[float] = mapped_column(Float, default=0.0)
    listing_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(24), default="manual")


# ==================================================================
# FunPay Automation OS: продукты, склад, workflow
# ==================================================================


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)          # SKU-like: PZ_SERVER_CONFIG
    name: Mapped[str] = mapped_column(String(256))
    game: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    category: Mapped[Optional[str]] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(16), default="manual")     # auto | semi_auto | manual
    automation_level: Mapped[str] = mapped_column(String(4), default="A0")
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    minimum_price: Mapped[float] = mapped_column(Float, default=0.0)
    estimated_manual_minutes: Mapped[int] = mapped_column(Integer, default=0)
    workflow_code: Mapped[Optional[str]] = mapped_column(String(64))    # ключ встроенного/созданного workflow
    stock_mode: Mapped[str] = mapped_column(String(16), default="none") # none | stock | account | lease
    target_stock: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    payload_template: Mapped[Optional[dict]] = mapped_column(JSON)      # для фабрик/генераторов
    notes: Mapped[Optional[str]] = mapped_column(Text)
    funpay_lot_id: Mapped[Optional[str]] = mapped_column(String(64))    # ID лота на FunPay
    funpay_lot_active: Mapped[bool] = mapped_column(Boolean, default=False)
    funpay_lot_price: Mapped[Optional[float]] = mapped_column(Float)    # цена, синхронизированная в лот


class StockUnit(Base):
    """Универсальная товарная единица цифрового склада."""
    __tablename__ = "stock_units"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    payload_ref: Mapped[Optional[str]] = mapped_column(String(256))  # keyring://... | file:... | twitch_account:12
    status: Mapped[str] = mapped_column(String(16), default="preparing", index=True)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    reserved_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    sold_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id"))
    expires_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    notes: Mapped[Optional[str]] = mapped_column(Text)


class WorkflowDef(Base):
    """Определение workflow: упорядоченные шаги в JSON."""
    __tablename__ = "workflow_defs"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    steps: Mapped[list] = mapped_column(JSON, default=list)
    # шаг: {"kind": "automatic|manual|approval|wait|validation|delivery",
    #        "name": str, "handler": str|null, "params": {...}}


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_code: Mapped[str] = mapped_column(String(64), index=True)
    order_id: Mapped[Optional[int]] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[Optional[int]] = mapped_column(ForeignKey("products.id"))
    stock_unit_id: Mapped[Optional[int]] = mapped_column(ForeignKey("stock_units.id"))
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    step_index: Mapped[int] = mapped_column(Integer, default=0)
    step_states: Mapped[list] = mapped_column(JSON, default=list)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    error: Mapped[Optional[str]] = mapped_column(Text)


class ReplenishmentTask(Base):
    __tablename__ = "replenishment_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    notes: Mapped[Optional[str]] = mapped_column(Text)


class Operator(Base):
    __tablename__ = "operators"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    skills: Mapped[list] = mapped_column(JSON, default=list)
    games: Mapped[list] = mapped_column(JSON, default=list)
    schedule: Mapped[Optional[str]] = mapped_column(String(128))
    cost_per_order: Mapped[float] = mapped_column(Float, default=0.0)
    rating: Mapped[float] = mapped_column(Float, default=0.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class OrderAssignment(Base):
    """Назначение заказа оператору + место в очереди исполнения (ТЗ §39).

    Очереди: ``сейчас`` (просрочено/немедленно), ``сегодня``, ``завтра``,
    ``ожидание`` (без даты или дальше завтрашнего дня).
    """
    __tablename__ = "order_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    operator_id: Mapped[int] = mapped_column(ForeignKey("operators.id"), index=True)
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(TZDateTime, index=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | done | cancelled
    assigned_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(TZDateTime)
    notes: Mapped[Optional[str]] = mapped_column(Text)
