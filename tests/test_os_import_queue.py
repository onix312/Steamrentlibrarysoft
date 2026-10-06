"""Следующий этап: импорт заказов из FunPay, операторы и очередь,
аналитика ОС, движок правил."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.domain.enums import OrderStatus, RunStatus
from app.integrations.funpay.base import FunPayOrderDTO

BASE = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def make_dto(external_id="FP-100", lot="Продам конфиг", buyer="buyer_x", price=149.0):
    return FunPayOrderDTO(external_id=external_id, buyer_username=buyer, lot_title=lot,
                          price=price, currency="RUB", purchased_at=BASE)


def make_products(ctx):
    auto = ctx.products.create(code="PZ_CONFIG_BEGINNER", name="Конфиг для новичков",
                               game="Project Zomboid", type="auto", automation_level="A4",
                               price=149.0, minimum_price=100.0,
                               workflow_code="auto_delivery", stock_mode="stock", target_stock=2)
    manual = ctx.products.create(code="PZ_MANUAL_HELP", name="Помощь вручную",
                                 game="Project Zomboid", type="manual", automation_level="A1",
                                 price=299.0, estimated_manual_minutes=30,
                                 workflow_code=None, stock_mode="none")
    return auto, manual


# ------------------------------------------------------------------- импорт
def test_import_matches_by_product_code(ctx):
    auto, _ = make_products(ctx)
    ctx.config.automation_mode = "off"
    ctx.stock.add_unit(auto.id)
    dto = make_dto(lot="[PZ_CONFIG_BEGINNER] Конфиг для новичков, быстро")
    assert ctx.importer.import_order(dto) == "imported"
    with ctx.db.session() as session:
        from app.database import repositories
        imported = repositories.OrderRepo(session).by_funpay_id("FP-100")
        assert imported is not None and imported.product_id == auto.id
        assert imported.created_by == "funpay"


def test_import_deduplication(ctx):
    auto, _ = make_products(ctx)
    ctx.config.automation_mode = "off"
    ctx.stock.add_unit(auto.id)
    dto = make_dto(lot="PZ_CONFIG_BEGINNER")
    assert ctx.importer.import_order(dto) == "imported"
    assert ctx.importer.import_order(dto) == "duplicated"


def test_import_unmatched_and_manual_link(ctx):
    auto, _ = make_products(ctx)
    ctx.config.automation_mode = "off"
    ctx.stock.add_unit(auto.id)
    dto = make_dto(external_id="FP-200", lot="Совершенно непонятный лот без продукта")
    assert ctx.importer.import_order(dto) == "unmatched"
    with ctx.db.session() as session:
        from app.database import repositories
        lost = repositories.OrderRepo(session).by_funpay_id("FP-200")
        assert lost is not None and lost.product_id is None
        assert lost.status == OrderStatus.PROBLEM.value
        lost_id = lost.id

    # ручная привязка: старый заказ гасится, новый уходит в воркфлоу-цепочку
    ctx.config.automation_mode = "auto_safe"
    ctx.config.dry_run = False
    new_order = ctx.importer.link_order_to_product(lost_id, "PZ_CONFIG_BEGINNER")
    assert new_order.product_id == auto.id
    with ctx.db.session() as session:
        from app.database import repositories
        old = repositories.OrderRepo(session).get(lost_id)
        assert old.status == OrderStatus.CANCELLED.value
    assert new_order.status == OrderStatus.COMPLETED.value  # авто-выдача прошла


def test_import_batch_summary(ctx):
    auto, _ = make_products(ctx)
    ctx.config.automation_mode = "off"
    ctx.stock.add_unit(auto.id)
    ctx.stock.add_unit(auto.id)
    result = ctx.importer.import_orders([
        make_dto(external_id="B-1", lot="PZ_CONFIG_BEGINNER"),
        make_dto(external_id="B-1", lot="PZ_CONFIG_BEGINNER"),   # дубль
        make_dto(external_id="B-2", lot="Нет такого продукта"),  # без продукта
    ])
    assert result.imported == 1 and result.duplicated == 1
    assert result.unmatched == ["B-2"]
    assert "импортировано 1" in result.summary


# ------------------------------------------------------------ очередь/операторы
def test_queue_buckets(ctx):
    assert ctx.queue.bucket_of(None) == "waiting"
    assert ctx.queue.bucket_of(BASE - timedelta(minutes=5)) == "now"
    assert ctx.queue.bucket_of(BASE + timedelta(hours=2)) == "today"
    assert ctx.queue.bucket_of(BASE.replace(hour=0) + timedelta(days=1, hours=3)) == "tomorrow"
    assert ctx.queue.bucket_of(BASE + timedelta(days=5)) == "waiting"


def test_queue_assign_suggest_and_lifecycle(ctx):
    _, manual = make_products(ctx)
    ctx.config.automation_mode = "off"
    order = ctx.sales.create_product_order("queue_buyer", manual.code)

    op_pz = ctx.queue.add_operator("Маша", games=["Project Zomboid"])
    op_any = ctx.queue.add_operator("Петя", games=[])
    # автоподбор: заказ по игре PZ уходит профильному оператору
    assignment = ctx.queue.assign(order.id, scheduled_for=BASE + timedelta(hours=1))
    assert assignment.operator_id == op_pz.id

    view = ctx.queue.queue_view()
    assert len(view["today"]) == 1 and view["now"] == []
    item = view["today"][0]
    assert item["operator"] == "Маша" and item["order_id"] == order.id

    # перенос на завтра
    ctx.queue.reschedule(assignment.id, BASE + timedelta(days=1, hours=2))
    assert len(ctx.queue.queue_view()["tomorrow"]) == 1

    # просрочка → сейчас
    ctx.queue.reschedule(assignment.id, BASE - timedelta(minutes=1))
    assert ctx.queue.overdue_count() == 1

    ctx.queue.complete_assignment(assignment.id)
    assert ctx.queue.queue_view()["now"] == []
    assert ctx.queue.workload().get(op_pz.id, 0) == 0
    assert op_any.id != op_pz.id


# ------------------------------------------------------------------ аналитика
def test_os_analytics_summary(ctx):
    auto, manual = make_products(ctx)
    ctx.config.automation_mode = "off"
    for _ in range(3):
        ctx.sales.create_product_order("a1", auto.code)
    ctx.sales.create_product_order("a2", manual.code)

    summary = ctx.os_analytics.summary(days=30)
    assert summary["sales"] == 4
    assert summary["revenue"] == 149.0 * 3 + 299.0
    assert summary["by_type"]["auto"]["sales"] == 3
    assert summary["by_type"]["manual"]["sales"] == 1
    assert summary["manual_hours"] == pytest.approx(0.5)
    board = ctx.os_analytics.product_board()
    codes = [row["code"] for row in board]
    assert codes[0] == "PZ_CONFIG_BEGINNER"  # отсортировано по выручке


# ---------------------------------------------------------------------- правила
def test_rules_stock_below_fires_and_replenishes(ctx):
    auto, _ = make_products(ctx)  # target_stock=2, юнитов нет
    fired = ctx.rules.evaluate()
    stock_rules = [f for f in fired if f.rule.rule_id == "stock_below_target"]
    assert len(stock_rules) == 1 and stock_rules[0].entity_id == auto.id
    # применение создаёт задачу пополнения
    applied = ctx.rules.apply(fired)
    assert applied >= 1
    assert len(ctx.stock.open_replenishments()) == 1


def test_rules_approval_waiting_uses_state(ctx):
    from app.engines.rules_engine import DEFAULT_RULES, RulesEngine

    engine = RulesEngine(rules=DEFAULT_RULES)
    rule = next(r for r in DEFAULT_RULES if r.rule_id == "approval_waiting")
    state = {"waiting_runs": [{"id": 1, "minutes": 45.0}, {"id": 2, "minutes": 5.0}]}
    fired = engine.evaluate(state)
    assert [f.entity_id for f in fired if f.rule is rule] == [1]


def test_rules_refund_rate_and_unmatched(ctx):
    from app.engines.rules_engine import DEFAULT_RULES, RulesEngine

    engine = RulesEngine(rules=DEFAULT_RULES)
    state = {
        "products": [{"id": 7, "code": "X", "target": 0, "ready": 0, "sales": 2, "refunds": 3}],
        "unmatched_orders": [{"id": 11, "funpay_id": "FP-9"}],
    }
    fired_ids = {f.rule.rule_id for f in engine.evaluate(state)}
    assert "refund_rate_high" in fired_ids
    assert "unmatched_import" in fired_ids
