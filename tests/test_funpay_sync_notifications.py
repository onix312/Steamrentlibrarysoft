"""Этап: синхронизация статусов заказов из поллинга, уведомления,
проверка соединения, правило просроченной очереди."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.events import TOPIC_NOTIFY
from app.domain.enums import OrderStatus, RunStatus, StockStatus
from app.integrations.funpay.base import FunPayOrderDTO
from app.integrations.funpay.golden_key_adapter import GOLDEN_KEY_SECRET

BASE = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def dto(external_id="FP-100", status="paid"):
    return FunPayOrderDTO(external_id=external_id, buyer_username="buyer_x",
                          lot_title="PZ_CONFIG_BEGINNER конфиг", price=149.0,
                          currency="RUB", purchased_at=BASE, status=status)


def setup_product_and_order(ctx):
    """Продукт + юнит + заказ, зависший на подтверждении выдачи (резерв жив)."""
    ctx.config.automation_mode = "assisted"
    ctx.config.dry_run = False
    product = ctx.products.create(code="PZ_CONFIG_BEGINNER", name="Конфиг", type="auto",
                                  automation_level="A4", price=149.0, minimum_price=100.0,
                                  workflow_code="auto_delivery", stock_mode="stock",
                                  target_stock=1)
    ctx.stock.add_unit(product.id, payload_ref="конфиг")
    assert ctx.importer.import_order(dto()) == "imported"
    with ctx.db.session() as session:
        from app.database import repositories
        order = repositories.OrderRepo(session).by_funpay_id("FP-100")
        order_id = order.id
        run_id = order.workflow_run_id
    run = ctx.engine.get_run(run_id)
    assert run.status == RunStatus.WAITING_APPROVAL.value  # выдача ждёт оператора
    assert ctx.stock.for_product(product.id)[0].status == StockStatus.RESERVED.value
    return product, order_id, run_id


def test_refund_sync_cancels_order_and_releases_stock(ctx):
    product, order_id, run_id = setup_product_and_order(ctx)

    synced = ctx.importer.sync_order_statuses([dto(status="refunded")])
    assert synced == {"completed": 0, "refunded": 1}

    order = ctx.sales._get_order(order_id)
    assert order.status == OrderStatus.CANCELLED.value
    assert ctx.engine.get_run(run_id).status == RunStatus.CANCELLED.value
    assert ctx.stock.for_product(product.id)[0].status == StockStatus.READY.value

    # идемпотентно: повторный поллинг возврата ничего не меняет
    assert ctx.importer.sync_order_statuses([dto(status="refunded")]) == \
        {"completed": 0, "refunded": 0}


def test_closed_sync_completes_order_and_marks_stock_sold(ctx):
    product, order_id, run_id = setup_product_and_order(ctx)

    synced = ctx.importer.sync_order_statuses([dto(status="closed")])
    assert synced == {"completed": 1, "refunded": 0}

    order = ctx.sales._get_order(order_id)
    assert order.status == OrderStatus.COMPLETED.value
    assert ctx.engine.get_run(run_id).status == RunStatus.COMPLETED.value
    # товар фактически у покупателя — юнит продан, а не возвращён в сток
    assert ctx.stock.for_product(product.id)[0].status == StockStatus.SOLD.value

    assert ctx.importer.sync_order_statuses([dto(status="closed")]) == \
        {"completed": 0, "refunded": 0}


def test_paid_status_leaves_order_alone(ctx):
    product, order_id, run_id = setup_product_and_order(ctx)
    assert ctx.importer.sync_order_statuses([dto(status="paid")]) == \
        {"completed": 0, "refunded": 0}
    assert ctx.sales._get_order(order_id).status != OrderStatus.COMPLETED.value


def test_import_and_unmatched_publish_notifications(ctx):
    captured = []
    ctx.events.subscribe(TOPIC_NOTIFY, lambda **payload: captured.append(payload))

    ctx.products.create(code="PZ_CONFIG_BEGINNER", name="Конфиг", type="auto",
                        price=149.0, workflow_code=None, stock_mode="none")
    ctx.config.automation_mode = "off"
    assert ctx.importer.import_order(dto(external_id="N-1")) == "imported"
    unmatched = FunPayOrderDTO(external_id="N-3", buyer_username="b", lot_title="непонятно",
                               price=10.0, currency="RUB", purchased_at=BASE)
    assert ctx.importer.import_order(unmatched) == "unmatched"

    titles = [item.get("title", "") for item in captured]
    assert any("Новый заказ N-1" in t for t in titles)
    assert any("N-3" in t and "без продукта" in t for t in titles)
    # уведомления также записаны в БД
    assert len(ctx.notifications.latest()) >= 2


def test_sync_events_notify_tray(ctx):
    product, order_id, _ = setup_product_and_order(ctx)
    captured = []
    ctx.events.subscribe(TOPIC_NOTIFY, lambda **payload: captured.append(payload))
    ctx.importer.sync_order_statuses([dto(status="refunded")])
    assert any("Возврат" in item.get("title", "") for item in captured)


class _FakeClient:
    initiated = False

    def init(self):
        self.initiated = True
        self.username = "Продавец"
        self.user_id = 777
        return self


def test_check_connection_ok(ctx, monkeypatch):
    ctx.config.funpay.mode = "golden_key"
    ctx.config.funpay.unofficial_allowed = True
    ctx.secrets.set(GOLDEN_KEY_SECRET, "FAKE")
    monkeypatch.setattr("app.integrations.funpay.golden_key_adapter.GoldenKeyClient",
                        lambda key, user_agent=None, timeout=15.0: _FakeClient())
    result = ctx.funpay.check_connection()
    assert result["ok"] is True and result["username"] == "Продавец"


def test_check_connection_disabled(ctx):
    result = ctx.funpay.check_connection()
    assert result["ok"] is False and "не включён" in result["message"]


def test_queue_overdue_rule_notifies(ctx):
    ctx.config.automation_mode = "off"
    product = ctx.products.create(code="PZ_MANUAL", name="Услуга", type="manual",
                                  price=299.0, workflow_code=None, stock_mode="none")
    order = ctx.sales.create_product_order("q_buyer", "PZ_MANUAL")
    ctx.queue.add_operator("Оператор-1")
    ctx.queue.assign(order.id, scheduled_for=ctx.clock.now() - timedelta(minutes=10))

    fired = [f for f in ctx.rules.evaluate() if f.rule.rule_id == "queue_overdue"]
    assert len(fired) == 1 and "заказ" in fired[0].detail.lower()

    before = len(ctx.notifications.latest())
    ctx.rules.apply(fired)
    after = ctx.notifications.latest()
    assert len(after) == before + 1
    assert "Правило" in after[0].title
