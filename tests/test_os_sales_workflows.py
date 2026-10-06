"""FunPay Automation OS: склад, воркфлоу, продажи, рынок, движки (ТЗ §64)."""
from __future__ import annotations

import threading

import pytest

from app.domain.enums import (
    AutomationMode,
    OrderStatus,
    PricingStrategy,
    RunStatus,
    StockStatus,
)
from app.services.os.stock_service import StockUnavailable

AUTO_PRODUCT = dict(
    code="PZ_CONFIG_BEGINNER", name="Конфиг для новичков", game="Project Zomboid",
    type="auto", automation_level="A4", price=149.0, minimum_price=100.0,
    workflow_code="auto_delivery", stock_mode="stock", target_stock=2,
)
SEMI_PRODUCT = dict(
    code="PZ_DOCTOR", name="Диагностика сервера", game="Project Zomboid",
    type="semi_auto", automation_level="A3", price=499.0, minimum_price=300.0,
    workflow_code="semi_auto_doctor", stock_mode="none",
)


def make_product(ctx, **overrides):
    fields = {**AUTO_PRODUCT, **overrides}
    return ctx.products.create(**fields)


def make_order(ctx, price: float = 100.0) -> int:
    """Настоящий заказ в БД (нужен для FK при резервировании склада)."""
    from app.database import models, repositories
    with ctx.db.session() as session:
        client = repositories.ClientRepo(session).get_or_create("stock_tester")
        order = models.Order(client_id=client.id, price=price)
        session.add(order)
        session.flush()
        return order.id


def test_builtin_workflows_seeded(ctx):
    codes = {definition.code for definition in ctx.engine.definitions()}
    assert {"auto_delivery", "semi_auto_doctor", "server_setup", "manual_service"} <= codes


def test_stock_reserve_race_two_orders_one_unit(ctx):
    """2 одновременных заказа на 1 юнит — товар достаётся ровно одному."""
    product = make_product(ctx)
    ctx.stock.add_unit(product.id, payload_ref="payload://cfg/1")
    order_a, order_b = make_order(ctx), make_order(ctx)

    results = []
    errors = []

    def try_reserve(order_id):
        try:
            unit = ctx.stock.reserve_for_order(product.id, order_id)
            results.append((order_id, unit.id))
        except StockUnavailable:
            errors.append(order_id)

    t1 = threading.Thread(target=try_reserve, args=(order_a,))
    t2 = threading.Thread(target=try_reserve, args=(order_b,))
    t1.start(); t2.start(); t1.join(); t2.join()

    assert len(results) == 1 and len(errors) == 1
    counts = ctx.stock.counts(product.id)
    assert counts["reserved"] == 1 and counts["ready"] == 0


def test_stock_release_back_to_ready(ctx):
    product = make_product(ctx)
    unit = ctx.stock.add_unit(product.id)
    reserved = ctx.stock.reserve_for_order(product.id, order_id=make_order(ctx))
    assert reserved.status == StockStatus.RESERVED.value
    ctx.stock.release(reserved.id, reason="отмена")
    assert ctx.stock.get(reserved.id).status == StockStatus.READY.value
    assert unit.id == reserved.id


def test_replenishment_task_created_once(ctx):
    product = make_product(ctx, target_stock=3)
    ctx.stock.add_unit(product.id)  # ready=1 < target=3
    tasks = ctx.stock.check_replenishment([product])
    assert len(tasks) == 1 and tasks[0].quantity == 2
    # повторный прогон не дублирует открытую задачу
    assert ctx.stock.check_replenishment([product]) == []
    ctx.stock.close_replenishment(tasks[0].id)
    again = ctx.stock.check_replenishment([product])
    assert len(again) == 1


def test_auto_safe_full_auto_delivery(ctx):
    """AUTO_SAFE + не dry_run: заказ закрывается полностью автоматически."""
    ctx.config.automation_mode = AutomationMode.AUTO_SAFE.value
    ctx.config.dry_run = False
    product = make_product(ctx)
    ctx.stock.add_unit(product.id, payload_ref="payload://cfg/1")

    order = ctx.sales.create_product_order("buyer_auto", product.code, funpay_order_id="FP-1")
    run = ctx.engine.get_run(order.workflow_run_id)

    assert run.status == RunStatus.COMPLETED.value
    assert order.status == OrderStatus.COMPLETED.value
    unit = ctx.stock.for_product(product.id)[0]
    assert unit.status == StockStatus.SOLD.value


def test_assisted_dry_run_only_preview(ctx):
    """ASSISTED + DRY RUN: выдача — только превью, юнит не продаётся."""
    ctx.config.automation_mode = AutomationMode.ASSISTED.value
    ctx.config.dry_run = True
    product = make_product(ctx)
    ctx.stock.add_unit(product.id, payload_ref="payload://cfg/1")

    order = ctx.sales.create_product_order("buyer_dry", product.code)
    run = ctx.engine.get_run(order.workflow_run_id)

    assert run.status == RunStatus.COMPLETED.value
    assert run.context.get("delivery_preview") is not None
    unit = ctx.stock.for_product(product.id)[0]
    assert unit.status == StockStatus.RESERVED.value  # не SOLD: ничего наружу


def test_assisted_real_delivery_waits_approval(ctx):
    """ASSISTED без dry_run: внешняя выдача только после подтверждения."""
    ctx.config.automation_mode = AutomationMode.ASSISTED.value
    ctx.config.dry_run = False
    product = make_product(ctx)
    ctx.stock.add_unit(product.id)

    order = ctx.sales.create_product_order("buyer_manual", product.code)
    run = ctx.engine.get_run(order.workflow_run_id)
    assert run.status == RunStatus.WAITING_APPROVAL.value

    ctx.engine.approve(run.id)
    run = ctx.engine.get_run(run.id)
    assert run.status == RunStatus.COMPLETED.value
    assert ctx.stock.for_product(product.id)[0].status == StockStatus.SOLD.value


def test_cancel_order_releases_reserved_stock(ctx):
    ctx.config.automation_mode = AutomationMode.ASSISTED.value
    ctx.config.dry_run = False
    product = make_product(ctx)
    ctx.stock.add_unit(product.id)

    order = ctx.sales.create_product_order("buyer_cancel", product.code)
    assert ctx.stock.for_product(product.id)[0].status == StockStatus.RESERVED.value

    ctx.sales.cancel_order(order.id, reason="клиент отказался")
    order = ctx.sales._get_order(order.id)
    assert order.status == OrderStatus.CANCELLED.value
    assert ctx.stock.for_product(product.id)[0].status == StockStatus.READY.value


def test_semi_auto_manual_checkpoint_and_doctor(ctx):
    """SEMI_AUTO: ручной шаг → ввод от оператора → авто-анализ → авто-подтверждение."""
    ctx.config.automation_mode = AutomationMode.AUTO_SAFE.value
    ctx.config.dry_run = True
    product = make_product(ctx, **{k: v for k, v in SEMI_PRODUCT.items() if k != "code"},
                           code="PZ_DOCTOR_T")

    order = ctx.sales.create_product_order("buyer_semi", product.code)
    run = ctx.engine.get_run(order.workflow_run_id)
    assert run.status == RunStatus.WAITING_MANUAL.value

    ctx.engine.complete_manual(run.id, {"logs": "java.lang.OutOfMemoryError: Java heap space"})
    run = ctx.engine.get_run(run.id)
    assert run.status == RunStatus.COMPLETED.value
    assert run.context["diagnosis"]["category"] == "memory"
    assert run.context["solution"]["summary"]


def test_funpay_order_id_deduplication(ctx):
    product = make_product(ctx)
    ctx.stock.add_unit(product.id)
    ctx.sales.create_product_order("buyer1", product.code, funpay_order_id="FP-DUP")
    with pytest.raises(Exception):
        ctx.sales.create_product_order("buyer2", product.code, funpay_order_id="FP-DUP")


def test_automation_off_keeps_order_new(ctx):
    ctx.config.automation_mode = AutomationMode.OFF.value
    product = make_product(ctx)
    ctx.stock.add_unit(product.id)
    order = ctx.sales.create_product_order("buyer_off", product.code)
    assert order.workflow_run_id is None
    assert order.status == OrderStatus.NEW.value


# ------------------------------------------------------------- рынок/цены
def test_pricing_strategies(ctx):
    product = make_product(ctx, price=200.0, minimum_price=120.0)
    snapshot = ctx.market.record_snapshot("Project Zomboid", competitors=5,
                                          lowest=180.0, median=200.0, highest=350.0)
    assert ctx.market.suggest_price(product, PricingStrategy.AGGRESSIVE, snapshot) == 179.0
    assert ctx.market.suggest_price(product, PricingStrategy.BALANCED, snapshot) == 190.0
    assert ctx.market.suggest_price(product, PricingStrategy.MARKET, snapshot) == 200.0
    assert ctx.market.suggest_price(product, PricingStrategy.PREMIUM, snapshot) == 220.0


def test_pricing_respects_minimum_price(ctx):
    product = make_product(ctx, price=500.0, minimum_price=450.0)
    snapshot = ctx.market.record_snapshot("Project Zomboid", competitors=9,
                                          lowest=300.0, median=310.0, highest=400.0)
    # aggressive даст 299 — ниже минимума, значит поднимаем до 450
    assert ctx.market.suggest_price(product, PricingStrategy.AGGRESSIVE, snapshot) == 450.0


def test_opportunity_score_recommendations(ctx):
    good = ctx.market.opportunity_score(
        median_price=499.0, competitors=2, watch_or_manual_hours=0.5,
        demand_signal=80.0, stock_deficit=2, automation_pct=95.0,
    )
    assert good["recommendation"] == "CREATE PRODUCT" and good["score"] >= 70

    bad = ctx.market.opportunity_score(
        median_price=40.0, competitors=20, watch_or_manual_hours=6.0,
        demand_signal=10.0, stock_deficit=0, automation_pct=10.0,
    )
    assert bad["recommendation"] == "SKIP" and bad["score"] < 45
    assert good["value_per_hour"] > bad["value_per_hour"]


# -------------------------------------------------------------- движки
def test_server_doctor_classification():
    from app.engines.server_doctor import diagnose

    port = diagnose({"logs": "Error: connection refused on port 25565"})
    assert port["category"] == "port" and port["confidence"] >= 0.5

    memory = diagnose({"error": "java.lang.OutOfMemoryError: Java heap space"})
    assert memory["category"] == "memory"

    unknown = diagnose({"description": "что-то странное происходит"})
    assert unknown["category"] == "unknown" and unknown["confidence"] <= 0.2


def test_mod_doctor_analysis():
    from app.engines.mod_doctor import analyze

    report = analyze({
        "game": "Project Zomboid",
        "mods": [
            {"name": "ModB", "version": "1.0", "dependencies": ["ModA", "MissingLib"], "load_index": 1},
            {"name": "ModA", "version": "0.9", "dependencies": [], "load_index": 2},
            {"name": "ModC", "version": "2.0", "dependencies": [], "load_index": 3},
            {"name": "ModC", "version": "2.0", "dependencies": [], "load_index": 4},
        ],
        "known_versions": {"ModA": "1.2"},
    })
    assert any("MissingLib" in item for item in report["missing_dependencies"])
    assert any("ModA" in item for item in report["outdated"])
    assert "ModC" in report["duplicates"]
    assert any("ModA должен загружаться раньше ModB" in item for item in report["order_issues"])
    assert report["recommended_order"][0] == "ModA"  # зависимости раньше зависимых
    assert not report["healthy"]


def test_config_factory_generate():
    from app.engines.config_factory import generate

    result = generate({"preset": "hardcore", "game": "PZ"})
    filename = "PZ_hardcore.cfg"
    assert filename in result["files"]
    assert "difficulty=hardcore" in result["files"][filename]
    assert result["readme"] and result["instructions"] and result["rollback"]


def test_product_factory_creates_distinct_products(ctx):
    created = ctx.products.create_from_factory("Project Zomboid")
    codes = {product.code for product in created}
    assert len(created) >= 4
    assert "PZ_CONFIG_BEGINNER" in codes and "PZ_SERVER_SETUP" in codes
    # повторный вызов не дублирует
    assert ctx.products.create_from_factory("Project Zomboid") == []
