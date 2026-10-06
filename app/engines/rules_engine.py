"""Движок правил (ТЗ §53): декларативные «если → то» поверх данных ОС.

Правила — данные, не код: условие с параметрами + действие. Движок:
1. собирает снимок состояния через переданные сервисы;
2. вычисляет, какие правила сработали (чистая функция — тестируется);
3. применяет действия через сервисы (уведомления, задачи пополнения).

Деструктивных действий нет: правила только подсказывают и ставят задачи.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class Rule:
    rule_id: str
    name: str
    condition: str          # имя проверяющего метода
    params: dict = field(default_factory=dict)
    action: str = "notify"  # notify | replenish | flag


#: Встроенный набор правил (порога можно переопределить при создании движка).
DEFAULT_RULES: list[Rule] = [
    Rule("stock_below_target", "Дефицит цифрового склада", "stock_below",
         {"min_ready": 1}, action="replenish"),
    Rule("campaign_ending", "Кампания дропов заканчивается", "campaign_ending",
         {"hours": 24}, action="notify"),
    Rule("approval_waiting", "Воркфлоу ждёт подтверждения", "approval_waiting",
         {"minutes": 30}, action="notify"),
    Rule("refund_rate_high", "Много отмен по продукту", "refund_rate",
         {"threshold": 0.3}, action="flag"),
    Rule("unmatched_import", "Заказы без продукта", "unmatched_orders",
         {}, action="notify"),
    Rule("queue_overdue", "Просроченные назначения в очереди", "queue_overdue",
         {}, action="notify"),
]


@dataclass
class FiredRule:
    rule: Rule
    detail: str
    entity: str | None = None
    entity_id: int | None = None


class RulesEngine:
    """Вычисление правил по снимку состояния.

    Сервисы передаются лениво: движок сам строит снимок ``collect_state``,
    но в тестах можно передать готовый снимок.
    """

    def __init__(self, stock=None, drops=None, engine=None, products=None,
                 notifications=None, db=None, clock=None, queue=None,
                 rules: list[Rule] | None = None) -> None:
        self.stock = stock
        self.drops = drops
        self.engine = engine
        self.products = products
        self.notifications = notifications
        self.db = db
        self.clock = clock
        self.queue = queue
        self.rules = rules if rules is not None else list(DEFAULT_RULES)

    # -------------------------------------------------------------- снимок
    def collect_state(self) -> dict:
        from app.database import models
        from app.domain.enums import RunStatus
        from sqlalchemy import select

        state: dict = {"products": [], "ending_campaigns": [], "waiting_runs": [],
                       "unmatched_orders": [], "queue_overdue": []}
        if self.products is not None and self.stock is not None:
            for product in self.products.all(active_only=True):
                counts = self.stock.counts(product.id)
                state["products"].append({
                    "id": product.id, "code": product.code, "target": product.target_stock,
                    "ready": counts.get("ready", 0), "refunds": 0, "sales": 0,
                })
        if self.products is not None and self.db is not None:
            with self.db.session() as session:
                for item in state["products"]:
                    orders = session.scalars(select(models.Order)
                                             .where(models.Order.product_id == item["id"])).all()
                    item["sales"] = len([o for o in orders if o.status != "cancelled"])
                    item["refunds"] = len([o for o in orders if o.status == "cancelled"])
        if self.drops is not None:
            state["ending_campaigns"] = [
                {"id": campaign.id, "name": campaign.campaign_name}
                for campaign in self.drops.ending_soon()
            ]
        if self.engine is not None and self.clock is not None:
            for run in self.engine.open_runs():
                if run.status != RunStatus.WAITING_APPROVAL.value:
                    continue
                started = run.started_at
                if started is None:
                    continue
                minutes_waiting = (self.clock.now() - started).total_seconds() / 60.0
                state["waiting_runs"].append({"id": run.id, "minutes": minutes_waiting})
        if self.db is not None:
            from app.database import models
            with self.db.session() as session:
                state["unmatched_orders"] = [
                    {"id": order.id, "funpay_id": order.funpay_order_id}
                    for order in session.scalars(
                        select(models.Order).where(models.Order.status == "problem",
                                                   models.Order.product_id.is_(None)))
                ]
        if self.queue is not None:
            state["queue_overdue"] = self.queue.queue_view().get("now", [])
        return state

    # ----------------------------------------------------------- вычисление
    def evaluate(self, state: dict | None = None) -> list[FiredRule]:
        if state is None:
            state = self.collect_state()
        fired: list[FiredRule] = []
        for rule in self.rules:
            checker = getattr(self, f"_check_{rule.condition}", None)
            if checker is None:
                log.warning("Правило %s: неизвестное условие %s", rule.rule_id, rule.condition)
                continue
            fired.extend(checker(state, rule))
        return fired

    def _check_stock_below(self, state: dict, rule: Rule) -> list[FiredRule]:
        min_ready = int(rule.params.get("min_ready", 1))
        out = []
        for product in state.get("products", []):
            if product["target"] > 0 and product["ready"] < min_ready:
                out.append(FiredRule(rule, f"{product['code']}: готово {product['ready']} < цели {product['target']}",
                                     entity="product", entity_id=product["id"]))
        return out

    def _check_campaign_ending(self, state: dict, rule: Rule) -> list[FiredRule]:
        return [FiredRule(rule, f"Кампания «{c['name']}» заканчивается в течение "
                                f"{rule.params.get('hours', 24)} ч",
                          entity="drop_campaign", entity_id=c["id"])
                for c in state.get("ending_campaigns", [])]

    def _check_approval_waiting(self, state: dict, rule: Rule) -> list[FiredRule]:
        threshold = float(rule.params.get("minutes", 30))
        return [FiredRule(rule, f"Воркфлоу {run['id']} ждёт подтверждения "
                                f"{run['minutes']:.0f} мин",
                          entity="workflow_run", entity_id=run["id"])
                for run in state.get("waiting_runs", []) if run["minutes"] >= threshold]

    def _check_refund_rate(self, state: dict, rule: Rule) -> list[FiredRule]:
        threshold = float(rule.params.get("threshold", 0.3))
        out = []
        for product in state.get("products", []):
            total = product["sales"] + product["refunds"]
            if total >= 3 and product["refunds"] / total > threshold:
                out.append(FiredRule(rule, f"{product['code']}: отмен "
                                           f"{product['refunds']}/{total} — нужен разбор",
                                     entity="product", entity_id=product["id"]))
        return out

    def _check_unmatched_orders(self, state: dict, rule: Rule) -> list[FiredRule]:
        orders = state.get("unmatched_orders", [])
        if not orders:
            return []
        return [FiredRule(rule, f"Заказов без продукта: {len(orders)} — привяжите вручную",
                          entity="order", entity_id=orders[0]["id"])]

    def _check_queue_overdue(self, state: dict, rule: Rule) -> list[FiredRule]:
        items = state.get("queue_overdue", [])
        if not items:
            return []
        first = items[0]
        return [FiredRule(rule, f"Просроченных назначений: {len(items)}; первое — "
                                f"заказ {first.get('order_id')} ({first.get('operator')})",
                          entity="order_assignment", entity_id=first.get("assignment_id"))]

    # ------------------------------------------------------------ применение
    def apply(self, fired: list[FiredRule]) -> int:
        """Применяет действия правил. Возвращает число выполненных действий."""
        applied = 0
        for item in fired:
            if item.rule.action == "replenish" and self.stock is not None and self.products is not None:
                product = self.products.get(item.entity_id) if item.entity_id else None
                if product is not None:
                    tasks = self.stock.check_replenishment([product])
                    applied += len(tasks)
            elif item.rule.action in ("notify", "flag") and self.notifications is not None:
                kind = "action_required" if item.rule.action == "flag" else "info"
                self.notifications.notify(kind, f"Правило: {item.rule.name}", item.detail)
                applied += 1
            else:
                log.info("Правило %s: %s (действие не применено — нет сервиса)",
                         item.rule.rule_id, item.detail)
        return applied
