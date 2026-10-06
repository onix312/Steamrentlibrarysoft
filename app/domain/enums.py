"""Доменные перечисления. Не зависят от Qt, SQLAlchemy и интеграций."""
from __future__ import annotations

from enum import StrEnum


class AccountStatus(StrEnum):
    ACTIVE = "active"
    PENDING = "pending"          # добавлен, но ещё не синхронизирован
    LIMITED = "limited"          # ограничения аккаунта (регион/приватность)
    ERROR = "error"


class FamilyRole(StrEnum):
    ORGANIZER = "organizer"
    ADULT = "adult"
    CHILD = "child"
    NOT_IN_FAMILY = "not_in_family"


class EligibilityStatus(StrEnum):
    """Поддержка игры в Steam Families."""
    AVAILABLE = "available"                    # Доступна
    UNAVAILABLE = "unavailable"                # Недоступна
    NEEDS_CHECK = "needs_check"                # Требует проверки
    THIRD_PARTY_LAUNCHER = "third_party"       # Сторонний launcher
    PUBLISHER_EXCLUDED = "publisher_excluded"  # Исключена издателем
    FREE = "free"                              # Бесплатная
    NO_SENSE = "no_sense"                      # Не имеет смысла продавать


class EligibilitySource(StrEnum):
    RULE = "rule"              # автоматическое правило (признаки из метаданных)
    DATASET = "dataset"        # локальный датасет исключений
    MANUAL = "manual"          # ручное решение оператора
    SIMULATION = "simulation"  # симулированные данные


class DemandGrade(StrEnum):
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    FREE = "FREE"


class OrderStatus(StrEnum):
    NEW = "new"
    QUEUED = "queued"
    PROCESSING = "processing"
    WAITING_CLIENT = "waiting_client"
    MANUAL_ACTION = "manual_action"
    STEAM_SETUP = "steam_setup"
    READY_TO_DELIVER = "ready_to_deliver"
    DELIVERED = "delivered"
    ACTIVE = "active"
    EXPIRING = "expiring"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    PROBLEM = "problem"


class LeaseStatus(StrEnum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    EXPIRING = "expiring"      # вычисляется, но хранится для фильтрации
    EXPIRED = "expired"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


OPEN_LEASE_STATUSES = (LeaseStatus.SCHEDULED, LeaseStatus.ACTIVE, LeaseStatus.EXPIRING)


class LicenseSource(StrEnum):
    OWNED = "owned"        # куплена на этом аккаунте
    FAMILY = "family"      # доступна только через семью (не наша копия)


class ListingStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"        # текст готов, публикация вручную на FunPay
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class BundleStatus(StrEnum):
    SUGGESTED = "suggested"
    ACTIVE = "active"
    ARCHIVED = "archived"


class MessageDirection(StrEnum):
    OUT = "out"
    IN = "in"


class MessageStatus(StrEnum):
    DRAFTED = "drafted"        # сгенерировано, ждёт подтверждения
    APPROVED = "approved"      # оператор подтвердил (готово к отправке)
    SENT = "sent"
    MANUAL = "manual"          # оператор отправил сам вне приложения
    RECEIVED = "received"      # входящее с маркетплейса


class Capability(StrEnum):
    """Возможность интеграции (по ТЗ)."""
    AVAILABLE = "available"    # реализована и безопасна
    MANUAL = "manual"          # выполняется оператором вручную
    UNSUPPORTED = "unsupported"


class SyncKind(StrEnum):
    STEAM = "steam"
    FUNPAY = "funpay"
    ELIGIBILITY = "eligibility"
    DEMAND = "demand"
    BACKUP = "backup"


class SyncStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    PARTIAL = "partial"


class NotificationKind(StrEnum):
    ORDER = "order"
    LEASE = "lease"
    LICENSE = "license"
    SYNC = "sync"
    ERROR = "error"
    ACTION_REQUIRED = "action_required"
    INFO = "info"


class Actor(StrEnum):
    USER = "user"
    APP = "app"
    SCHEDULER = "scheduler"


class AuditAction(StrEnum):
    ACCOUNT_ADDED = "account.added"
    ACCOUNT_REMOVED = "account.removed"
    SYNC_STARTED = "sync.started"
    SYNC_FINISHED = "sync.finished"
    SYNC_FAILED = "sync.failed"
    GAME_IMPORTED = "game.imported"
    ELIGIBILITY_SET = "eligibility.set"
    ELIGIBILITY_OVERRIDDEN = "eligibility.overridden"
    ORDER_CREATED = "order.created"
    ORDER_STATUS_CHANGED = "order.status_changed"
    LEASE_CREATED = "lease.created"
    LEASE_EXTENDED = "lease.extended"
    LEASE_COMPLETED = "lease.completed"
    LEASE_CANCELLED = "lease.cancelled"
    LEASE_EXPIRED = "lease.expired"
    LICENSE_ASSIGNED = "license.assigned"
    LICENSE_RELEASED = "license.released"
    CLIENT_CREATED = "client.created"
    CLIENT_BLACKLISTED = "client.blacklisted"
    LISTING_CREATED = "listing.created"
    LISTING_UPDATED = "listing.updated"
    LISTING_STATUS_CHANGED = "listing.status_changed"
    BUNDLE_CREATED = "bundle.created"
    MESSAGE_GENERATED = "message.generated"
    MESSAGE_APPROVED = "message.approved"
    MESSAGE_SENT = "message.sent"
    PRICE_CHANGED = "price.changed"
    SETTINGS_CHANGED = "settings.changed"
    NOTE_UPDATED = "note.updated"
    FUNPAY_ACTION_REQUIRED = "funpay.action_required"
    ERROR = "error"


# ==================================================================
# Модуль Twitch Drops
# ==================================================================

class TwitchAccountStatus(StrEnum):
    IDLE = "idle"
    READY = "ready"
    WATCHING = "watching"
    CLAIM_REQUIRED = "claim_required"
    COMPLETE = "complete"
    LISTED = "listed"
    RESERVED = "reserved"
    SOLD = "sold"
    PROBLEM = "problem"


class DropCampaignStatus(StrEnum):
    UPCOMING = "upcoming"
    ACTIVE = "active"
    ENDING_SOON = "ending_soon"   # осталось < 24 ч
    ENDED = "ended"


class WatchSessionStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    INTERRUPTED = "interrupted"
    VERIFY_PROGRESS = "verify_progress"
    CLAIM_REQUIRED = "claim_required"
    DONE = "done"


class PricingStrategy(StrEnum):
    AGGRESSIVE = "aggressive"   # lowest - 1
    BALANCED = "balanced"       # median - 5%
    MARKET = "market"           # median
    PREMIUM = "premium"         # median + 10%


class SellRecommendation(StrEnum):
    SELL_NOW = "sell_now"
    CONTINUE_FARMING = "continue_farming"
    ADD_NEXT_CAMPAIGN = "add_next_campaign"
    HOLD = "hold"


class ClaimState(StrEnum):
    LOCKED = "locked"
    READY_TO_CLAIM = "ready_to_claim"
    CLAIMED = "claimed"


class TwitchCapability(StrEnum):
    STREAMS = "streams"              # Helix /streams
    GAMES = "games"                  # Helix /games
    DROPS_PROGRESS = "drops_progress"  # официального API нет
    CLAIM = "claim"                    # официального API нет
    CAMPAIGN_LIST = "campaign_list"    # официального API нет


# ==================================================================
# FunPay Automation OS: ядро (продукты, склад, workflow)
# ==================================================================

class ProductType(StrEnum):
    AUTO = "auto"
    SEMI_AUTO = "semi_auto"
    MANUAL = "manual"


class AutomationLevel(StrEnum):
    A0 = "A0"  # полностью ручной
    A1 = "A1"  # программа только помогает
    A2 = "A2"  # программа делает основную часть
    A3 = "A3"  # требуется approve
    A4 = "A4"  # автоматическая выдача
    A5 = "A5"  # автоматический workflow


class StockStatus(StrEnum):
    PREPARING = "preparing"
    READY = "ready"
    LISTED = "listed"
    RESERVED = "reserved"
    SOLD = "sold"
    EXPIRED = "expired"
    PROBLEM = "problem"


class WorkflowStepKind(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"
    APPROVAL = "approval"
    WAIT = "wait"
    VALIDATION = "validation"
    DELIVERY = "delivery"


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_MANUAL = "waiting_manual"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AutomationMode(StrEnum):
    """Safety switch (ТЗ §57)."""
    OFF = "off"
    ASSISTED = "assisted"      # внешние действия требуют approve
    AUTO_SAFE = "auto_safe"    # разрешённые безопасные действия — сами


class ReplenishmentStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    CANCELLED = "cancelled"


class DropAccountStatus(StrEnum):
    IDLE = "idle"
    WATCHING = "watching"
    CLAIM_REQUIRED = "claim_required"
    COMPLETE = "complete"
    READY = "ready"
    LISTED = "listed"
    RESERVED = "reserved"
    SOLD = "sold"
    PROBLEM = "problem"
