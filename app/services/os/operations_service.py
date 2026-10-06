"""Unified operator action queue across sales, integrations, stock and Drops."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select

from app.core.clock import Clock
from app.database import models
from app.database.session import Database
from app.integrations.funpay.protocol_health import GLOBAL_FUNPAY_BREAKER, CircuitState


@dataclass(frozen=True)
class ActionItem:
    priority: int
    kind: str
    title: str
    detail: str
    section: str
    entity_id: int | None = None


class OperationsService:
    def __init__(self, db: Database, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    def actions(self, limit: int = 100) -> list[ActionItem]:
        now = self.clock.now()
        actions: list[ActionItem] = []

        if GLOBAL_FUNPAY_BREAKER.state == CircuitState.OPEN:
            actions.append(ActionItem(
                100, "integration_error", "FunPay automation paused",
                GLOBAL_FUNPAY_BREAKER.reason or "Protocol health check failed.",
                "funpay",
            ))

        with self.db.session() as session:
            for account in session.scalars(
                select(models.SteamAccount).where(models.SteamAccount.status == "error")
            ):
                actions.append(ActionItem(
                    95, "integration_error", f"Steam: {account.display_name}",
                    account.last_error or "Ошибка синхронизации.", "steam", account.id,
                ))

            for order in session.scalars(
                select(models.Order)
                .where(models.Order.status.in_(["problem", "manual_action", "new", "queued"]))
                .order_by(models.Order.purchased_at)
                .limit(30)
            ):
                priority = 96 if order.status == "problem" else 88 if order.status == "manual_action" else 65
                actions.append(ActionItem(
                    priority, "order", f"Заказ #{order.funpay_order_id or order.id}",
                    f"Статус: {order.status} · {order.price:.0f} {order.currency}",
                    "orders", order.id,
                ))

            for run in session.scalars(
                select(models.WorkflowRun)
                .where(models.WorkflowRun.status.in_(["waiting_manual", "waiting_approval", "failed"]))
                .order_by(models.WorkflowRun.id)
                .limit(30)
            ):
                priority = 97 if run.status == "failed" else 92
                actions.append(ActionItem(
                    priority, "workflow", f"Workflow #{run.id}: {run.workflow_code}",
                    f"Статус: {run.status} · шаг {run.step_index + 1}",
                    "workflows", run.id,
                ))

            for message in session.scalars(
                select(models.Message)
                .where(
                    models.Message.direction == "in",
                    models.Message.status.not_in(["read", "archived"]),
                )
                .order_by(models.Message.id.desc())
                .limit(20)
            ):
                actions.append(ActionItem(
                    86, "message", "Новое сообщение FunPay",
                    (message.body or "")[:160], "funpay", message.id,
                ))

            for task in session.scalars(
                select(models.ReplenishmentTask)
                .where(models.ReplenishmentTask.status == "open")
                .order_by(models.ReplenishmentTask.id)
                .limit(30)
            ):
                product = session.get(models.Product, task.product_id)
                actions.append(ActionItem(
                    80, "stock", f"Пополнить: {product.name if product else task.product_id}",
                    f"Нужно подготовить: {task.quantity}", "stock", task.id,
                ))

            expiring_at = now + timedelta(hours=24)
            for lease in session.scalars(
                select(models.AccessLease)
                .where(
                    models.AccessLease.status.in_(["active", "expiring"]),
                    models.AccessLease.expires_at <= expiring_at,
                )
                .order_by(models.AccessLease.expires_at)
                .limit(30)
            ):
                actions.append(ActionItem(
                    70, "lease", f"Доступ #{lease.id} скоро закончится",
                    f"Истекает: {lease.expires_at}", "leases", lease.id,
                ))

            for account in session.scalars(
                select(models.TwitchAccount)
                .where(models.TwitchAccount.status == "claim_required")
                .limit(30)
            ):
                actions.append(ActionItem(
                    84, "drops", f"Drops: claim для {account.display_name}",
                    "Нужно открыть Twitch Drops Inventory и подтвердить награду.",
                    "drops", account.id,
                ))

            for watch in session.scalars(
                select(models.WatchSession)
                .where(models.WatchSession.status == "verify_progress")
                .limit(30)
            ):
                actions.append(ActionItem(
                    82, "drops", f"Drops session #{watch.id}",
                    "Проверьте фактический прогресс кампании.", "drops_sessions", watch.id,
                ))

        actions.sort(key=lambda item: (-item.priority, item.kind, item.entity_id or 0))
        return actions[:limit]

    def summary(self) -> dict:
        actions = self.actions()
        return {
            "total": len(actions),
            "critical": sum(1 for x in actions if x.priority >= 95),
            "manual": sum(1 for x in actions if x.kind in {"workflow", "order", "message"}),
            "stock": sum(1 for x in actions if x.kind == "stock"),
            "drops": sum(1 for x in actions if x.kind == "drops"),
        }
