"""Неофициальный режим FunPay: клиент (парсинг), адаптер, поллинг → импорт,
автовыдача через канал движка воркфлоу. Без сети: всё на подделках."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.config import FunPaySettings
from app.domain.enums import Capability
from app.integrations.funpay.base import FunPayOrderDTO
from app.integrations.funpay.golden_key_adapter import (
    GOLDEN_KEY_SECRET,
    GoldenKeyFunPayAdapter,
)
from app.integrations.funpay.golden_key_client import (
    FloodError,
    GoldenKeyClient,
    UnauthorizedError,
    parse_funpay_datetime,
)

MAIN_PAGE_OK = """
<html><body data-app-data='{"userId": 777, "csrf-token": "CSRF-1", "locale": "ru"}'>
<div class="param-item"><div class="user-link-name">Продавец</div></div>
<a class="menu-item-logout" href="/logout">Выход</a>
</body></html>
"""
MAIN_PAGE_NO_PROFILE = "<html><body data-app-data='{}'><div></div></body></html>"

ORDERS_PAGE = """
<html><body data-app-data='{"userId": 777, "csrf-token": "CSRF-2", "locale": "ru"}'>
<div class="user-link-name">Продавец</div>
<h1 class="page-header page-header-no-hr">Мои продажи</h1>
<div class="tc-list">
  <a class="tc-item info" href="/orders/ABC123">
    <div class="tc-order">#ABC123</div>
    <div class="order-desc"><div>PZ_CONFIG_BEGINNER Конфиг для новичков</div></div>
    <div class="tc-price">149 ₽</div>
    <div class="media-user-name"><span data-href="/users/555/">Покупатель1</span></div>
    <div class="text-muted">Конфиги</div>
    <div class="tc-date-time">сегодня 14:33</div>
  </a>
  <a class="tc-item" href="/orders/XYZ789">
    <div class="tc-order">#XYZ789</div>
    <div class="order-desc"><div>Другой лот</div></div>
    <div class="tc-price">1 000 ₽</div>
    <div class="media-user-name"><span data-href="/users/999/">Покупатель2</span></div>
    <div class="text-muted">Прочее</div>
    <div class="tc-date-time">12 мая, 09:15</div>
  </a>
</div>
</body></html>
"""


class FakeResponse:
    def __init__(self, text: str = "", status: int = 200, url: str = "https://funpay.com/",
                 json_data: dict | None = None, cookies: dict | None = None) -> None:
        self.text = text
        self.status_code = status
        self.url = url
        self._json = json_data or {}
        self.cookies = type("C", (), {"get_dict": staticmethod(lambda c=cookies: c or {})})()

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._json


def make_client(main_page: FakeResponse | None = None,
                orders_page: FakeResponse | None = None) -> GoldenKeyClient:
    client = GoldenKeyClient("GOLDEN", user_agent="TestUA")

    class FakeSession:
        def get(self, url, **kwargs):
            if url.rstrip("/") == "https://funpay.com":
                return main_page or FakeResponse("")
            if "orders/trade" in url:
                return orders_page or FakeResponse("")
            return FakeResponse("")

        def post(self, url, **kwargs):
            return FakeResponse("")

        def mount(self, *a, **k):
            pass

    client.session = FakeSession()
    return client


# ---------------------------------------------------------------- клиент
def test_client_init_parses_session():
    client = make_client(main_page=FakeResponse(MAIN_PAGE_OK, cookies={"PHPSESSID": "SID-1"}))
    client.init()
    assert client.user_id == 777
    assert client.csrf_token == "CSRF-1"
    assert client.username == "Продавец"
    assert client.phpsessid == "SID-1"
    assert client.initiated


def test_client_init_unauthorized():
    client = make_client(main_page=FakeResponse(MAIN_PAGE_NO_PROFILE))
    with pytest.raises(UnauthorizedError):
        client.init()


def test_client_get_sales_parses_orders():
    client = make_client(main_page=FakeResponse(MAIN_PAGE_OK),
                         orders_page=FakeResponse(ORDERS_PAGE))
    sales = client.get_sales()
    assert [s.order_id for s in sales] == ["ABC123", "XYZ789"]
    first = sales[0]
    assert first.status == "paid"
    assert first.buyer_username == "Покупатель1"
    assert first.buyer_id == 555
    assert first.price == 149.0
    assert first.chat_id == "users-555-777"
    assert "PZ_CONFIG_BEGINNER" in first.description
    assert sales[1].status == "closed"
    assert sales[1].price == 1000.0


def test_client_send_message_success():
    client = make_client(main_page=FakeResponse(MAIN_PAGE_OK))
    client.initiated = True
    client.user_id = 777
    client.csrf_token = "CSRF-1"
    client.session.post = lambda url, **kw: FakeResponse(
        json_data={"response": {}, "objects": []})
    result = client.send_chat_message("users-555-777", "Товар")
    assert result == {"response": {}, "objects": []}


def test_client_send_message_flood_waits_then_fails(monkeypatch):
    client = make_client(main_page=FakeResponse(MAIN_PAGE_OK))
    client.initiated = True
    client.user_id = 777
    client.csrf_token = "CSRF-1"
    sleeps = []
    monkeypatch.setattr("app.integrations.funpay.golden_key_client.time.sleep",
                        lambda s: sleeps.append(s))
    client.session.post = lambda url, **kw: FakeResponse(
        json_data={"response": {"error": "Нельзя отправлять сообщения слишком часто. Подождите 7 секунд."},
                   "objects": []})
    with pytest.raises(FloodError):
        client.send_chat_message("users-555-777", "Товар")
    assert sleeps  # лимит уважаем: ждали, а не долбили


def test_parse_funpay_datetime():
    now = datetime(2026, 10, 6, 18, 0, tzinfo=timezone.utc)
    assert parse_funpay_datetime("сегодня 14:33", now).hour == 14
    assert parse_funpay_datetime("вчера 21:10", now).day == 5
    may = parse_funpay_datetime("12 мая, 09:15", now)
    assert (may.month, may.day, may.year) == (5, 12, 2026)


# --------------------------------------------------------------- адаптер
class FakeSecrets:
    def __init__(self, values: dict | None = None) -> None:
        self._values = values or {}

    def has(self, name: str) -> bool:
        return name in self._values

    def get(self, name: str) -> str | None:
        return self._values.get(name)


class FakeClient:
    """Подмена клиента: заказы + чаты без сети."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.initiated = True

    def get_sales(self, **kwargs):
        from app.integrations.funpay.golden_key_client import SaleOrder
        return [SaleOrder(
            order_id="ABC123", buyer_username="Покупатель1", buyer_id=555,
            description="PZ_CONFIG_BEGINNER Конфиг", price=149.0, currency="₽",
            status="paid", purchased_at=datetime(2026, 10, 6, 14, 33, tzinfo=timezone.utc),
            chat_id="users-555-777",
        )]

    def send_chat_message(self, chat_id: str, text: str) -> dict:
        self.sent.append((chat_id, text))
        return {"response": {}, "objects": []}

    def get_chats(self, max_chats: int = 50):
        return []


def make_adapter(fake_client: FakeClient | None = None, allowed: bool = True):
    settings = FunPaySettings(mode="golden_key", unofficial_allowed=allowed)
    secrets = FakeSecrets({GOLDEN_KEY_SECRET: "GOLDEN"} if allowed else {})
    adapter = GoldenKeyFunPayAdapter(settings, secrets)
    adapter._client = fake_client
    return adapter


def test_adapter_capabilities_on_off():
    enabled = make_adapter(FakeClient(), allowed=True)
    caps = enabled.capabilities()
    assert caps["get_orders"] == Capability.AVAILABLE
    assert caps["read_only_polling"] == Capability.AVAILABLE
    assert caps["send_message"] == Capability.AVAILABLE
    disabled = make_adapter(None, allowed=False)
    assert disabled.capabilities()["read_only_polling"] == Capability.UNSUPPORTED


def test_adapter_get_orders_maps_dtos():
    adapter = make_adapter(FakeClient())
    orders = adapter.get_orders()
    assert len(orders) == 1
    dto = orders[0]
    assert isinstance(dto, FunPayOrderDTO)
    assert dto.external_id == "ABC123" and dto.buyer_username == "Покупатель1"
    assert dto.price == 149.0


def test_adapter_deliver_order_text_sends_to_order_chat():
    fake = FakeClient()
    adapter = make_adapter(fake)
    info = adapter.deliver_order_text("ABC123", "Ваш товар: конфиг")
    assert fake.sent == [("users-555-777", "Ваш товар: конфиг")]
    assert "ABC123" in info


# ------------------------------------------------- поллинг → импорт ОС
def test_funpay_poll_feeds_importer(ctx, monkeypatch):
    from app.workers.funpay_worker import build_funpay_poll_job

    ctx.products.create(code="PZ_CONFIG_BEGINNER", name="Конфиг", type="auto",
                        price=149.0, workflow_code=None, stock_mode="none")
    ctx.config.automation_mode = "off"

    # включаем неофициальный режим и подменяем сетевой клиент подделкой
    ctx.config.funpay.mode = "golden_key"
    ctx.config.funpay.unofficial_allowed = True
    ctx.secrets.set(GOLDEN_KEY_SECRET, "GOLDEN")
    monkeypatch.setattr("app.integrations.funpay.golden_key_adapter.GoldenKeyClient",
                        lambda key, user_agent=None, timeout=15.0: FakeClient())

    job = build_funpay_poll_job(ctx)
    result = job()
    assert result["imported"] == 1
    with ctx.db.session() as session:
        from app.database import repositories
        imported = repositories.OrderRepo(session).by_funpay_id("ABC123")
        assert imported is not None and imported.product_id is not None


# --------------------------------------- автовыдача через движок воркфлоу
def make_auto_product(ctx, price=149.0):
    return ctx.products.create(code="PZ_CONFIG_BEGINNER", name="Конфиг", game="PZ",
                               type="auto", automation_level="A4", price=price,
                               minimum_price=100.0, workflow_code="auto_delivery",
                               stock_mode="stock", target_stock=1)


def test_auto_safe_delivery_calls_sink(ctx):
    ctx.config.automation_mode = "auto_safe"
    ctx.config.dry_run = False
    product = make_auto_product(ctx)
    ctx.stock.add_unit(product.id, payload_ref="содержимое конфига")
    calls = []
    ctx.engine.delivery_sink = lambda funpay_id, delivery: calls.append((funpay_id, delivery)) or {"status": "sent"}

    ctx.importer.import_order(FunPayOrderDTO(
        external_id="FP-500", buyer_username="b1",
        lot_title="PZ_CONFIG_BEGINNER", price=149.0, currency="RUB",
        purchased_at=datetime(2026, 10, 6, tzinfo=timezone.utc)))

    assert calls and calls[0][0] == "FP-500"
    with ctx.db.session() as session:
        from app.database import models
        from sqlalchemy import select
        run = session.scalar(select(models.WorkflowRun).order_by(models.WorkflowRun.id.desc()))
        assert run.status == "completed"
        assert run.context["funpay_delivery"] == {"status": "sent"}


def test_dry_run_delivery_never_calls_sink(ctx):
    ctx.config.automation_mode = "auto_safe"
    ctx.config.dry_run = True
    product = make_auto_product(ctx)
    ctx.stock.add_unit(product.id, payload_ref="секрет")
    calls = []
    ctx.engine.delivery_sink = lambda funpay_id, delivery: calls.append(1) or {"status": "sent"}

    ctx.importer.import_order(FunPayOrderDTO(
        external_id="FP-501", buyer_username="b1",
        lot_title="PZ_CONFIG_BEGINNER", price=149.0, currency="RUB",
        purchased_at=datetime(2026, 10, 6, tzinfo=timezone.utc)))

    assert calls == []  # в DRY RUN наружу ничего не уходит


def test_sink_error_recorded_but_run_completes(ctx):
    ctx.config.automation_mode = "auto_safe"
    ctx.config.dry_run = False
    product = make_auto_product(ctx)
    ctx.stock.add_unit(product.id)

    def broken_sink(funpay_id, delivery):
        raise RuntimeError("сеть легла")

    ctx.engine.delivery_sink = broken_sink
    ctx.importer.import_order(FunPayOrderDTO(
        external_id="FP-502", buyer_username="b1",
        lot_title="PZ_CONFIG_BEGINNER", price=149.0, currency="RUB",
        purchased_at=datetime(2026, 10, 6, tzinfo=timezone.utc)))
    with ctx.db.session() as session:
        from app.database import models
        from sqlalchemy import select
        run = session.scalar(select(models.WorkflowRun).order_by(models.WorkflowRun.id.desc()))
        assert run.status == "completed"  # сбой канала не уничтожает прогон
        assert run.context["funpay_delivery"]["status"] == "error"


def test_context_sink_manual_required_when_unofficial_off(ctx):
    ctx.config.dry_run = False
    outcome = ctx._funpay_delivery_sink("FP-9", {"product": "Конфиг", "payload_ref": "x"})
    assert outcome["status"] == "manual_required"
    ctx.config.dry_run = True
    outcome = ctx._funpay_delivery_sink("FP-9", {"product": "Конфиг", "payload_ref": "x"})
    assert outcome["status"] == "dry_run" and "Конфиг" in outcome["preview"]
