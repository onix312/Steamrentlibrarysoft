"""Персистентность (ТЗ §70): перезапуск приложения не теряет данные."""
from __future__ import annotations

from datetime import datetime, timezone

from app.app_context import build_context
from app.core.clock import FixedClock


def test_restart_keeps_products_stock_orders_and_runs(tmp_path):
    clock = FixedClock(datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc))
    ctx = build_context(tmp_path / "data", clock=clock)
    ctx.config.automation_mode = "assisted"
    ctx.config.dry_run = True

    product = ctx.products.create(
        code="PZ_CONFIG_BEGINNER", name="Конфиг", type="auto",
        automation_level="A4", price=149.0, workflow_code="auto_delivery",
        stock_mode="stock", target_stock=1,
    )
    ctx.stock.add_unit(product.id, payload_ref="payload://cfg/1")
    order = ctx.sales.create_product_order("persistent_buyer", product.code)
    run_id = order.workflow_run_id
    ctx.shutdown()

    # «перезапуск»: новый контекст на той же директории данных
    ctx2 = build_context(tmp_path / "data", clock=clock)
    product2 = ctx2.products.by_code("PZ_CONFIG_BEGINNER")
    assert product2 is not None and product2.id == product.id
    assert ctx2.stock.counts(product2.id)["reserved"] == 1

    order2 = ctx2.sales._get_order(order.id)
    assert order2 is not None and order2.workflow_run_id == run_id
    run = ctx2.engine.get_run(run_id)
    assert run is not None  # прогон пережил перезапуск
    # встроенные воркфлоу не дублируются при повторном сиде
    assert len(ctx2.engine.definitions()) == 4

    # отмена после перезапуска корректно освобождает склад
    ctx2.sales.cancel_order(order.id, reason="перезапуск")
    assert ctx2.stock.counts(product2.id)["ready"] == 1
    ctx2.shutdown()
