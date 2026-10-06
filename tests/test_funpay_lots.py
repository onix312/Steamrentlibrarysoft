"""Этап: автосоздание и закрытие лотов на FunPay (неофициальный режим)."""
from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from app.core.config import FunPaySettings
from app.database import models
from app.domain.value_objects import CapabilityError
from app.integrations.funpay.golden_key_adapter import (
    GOLDEN_KEY_SECRET,
    GoldenKeyFunPayAdapter,
)
from app.integrations.funpay.golden_key_client import (
    GoldenKeyClient,
    GoldenKeyError,
    OwnLot,
)

FORM_HTML = """
<form class="form-offer-editor">
  <input name="csrf_token" value="FORM_CSRF">
  <input name="node_id" value="1701">
  <input name="offer_id" value="0">
  <input name="price" value="149">
  <input type="checkbox" name="active" checked>
  <input type="checkbox" name="auto_delivery">
  <textarea name="fields[desc][ru]">старое описание</textarea>
  <div class="form-group"><select name="game">
    <option value="7">Другая</option>
    <option value="9" selected>Project Zomboid</option>
  </select></div>
  <div class="form-group hidden"><select name="server">
    <option value="1" selected>Скрытый</option>
  </select></div>
</form>
"""

TRADE_HTML = """
<div>
  <a class="tc-item" data-offer="7770001">
    <div class="tc-desc-text">PZ конфиг новичка</div>
    <div class="tc-price" data-s="149">149 ₽</div>
    <div class="tc-amount">1 шт.</div>
    <!-- количество парсится по первому числу -->
  </a>
  <a class="tc-item warning" data-offer="7770002">
    <div class="tc-desc-text">Закрытый лот</div>
    <div class="tc-price" data-s="200">200 ₽</div>
  </a>
</div>
"""


class FakeLotClient(GoldenKeyClient):
    """Подмена клиента: формы/сохранение/списки лотов без сети."""

    def __init__(self) -> None:
        super().__init__("FAKE")
        self.initiated = True
        self.user_id = 777
        self.csrf_token = "CSRF"
        self.saved_fields: list[dict] = []
        self.lots_by_node: dict[str, list[tuple[str, str]]] = {}

    def get_lot_fields(self, lot_id=None, node_id=None):
        return {"csrf_token": "FORM_CSRF", "node_id": str(node_id or 0),
                "offer_id": str(lot_id or 0), "price": "149", "active": "on"}

    def save_lot_fields(self, fields):
        fields = dict(fields)
        fields["csrf_token"] = self.csrf_token or fields.get("csrf_token", "")
        self.saved_fields.append(fields)
        if fields.get("offer_id") in ("0", ""):
            title = fields.get("fields[summary][ru]", "")
            new_id = str(9000 + len(self.saved_fields))
            self.lots_by_node.setdefault(fields.get("node_id", ""), []).append((new_id, title))
        return {}

    def get_own_lots(self, node_id):
        return [OwnLot(lot_id, title, 149.0, True, 1)
                for lot_id, title in self.lots_by_node.get(str(node_id), [])]


# ------------------------------------------------------------- клиент: парсинг
def test_get_own_lots_parses_trade_page():
    client = FakeLotClient()
    client._get_html = lambda url: BeautifulSoup(TRADE_HTML, "html.parser")
    lots = GoldenKeyClient.get_own_lots(client, 1701)
    assert [lot.lot_id for lot in lots] == ["7770001", "7770002"]
    assert lots[0].active is True and lots[0].price == 149.0 and lots[0].amount == 1
    assert lots[1].active is False and lots[1].amount is None


def test_get_lot_fields_parses_form():
    client = FakeLotClient()
    client._get_html = lambda url: BeautifulSoup(FORM_HTML, "html.parser")
    fields = GoldenKeyClient.get_lot_fields(client, node_id=1701)
    assert fields["csrf_token"] == "FORM_CSRF"
    assert fields["node_id"] == "1701"
    assert fields["price"] == "149"
    assert fields["active"] == "on"                      # чекбокс включён
    assert "auto_delivery" not in fields                 # чекбокс выключен
    assert fields["fields[desc][ru]"] == "старое описание"
    assert fields["game"] == "9"                         # selected-опция
    assert "server" not in fields                        # скрытая группа пропущена
    assert client.csrf_token == "FORM_CSRF"              # токен обновлён


def test_save_lot_fields_raises_on_server_errors():
    client = FakeLotClient()

    class BadResponse:
        status_code = 200
        cookies = type("C", (), {"get_dict": staticmethod(lambda: {})})()

        def raise_for_status(self):
            pass

        def json(self):
            return {"errors": [["price", "Укажите цену"]]}

    class FakeSession:
        def post(self, url, **kwargs):
            return BadResponse()

    client.session = FakeSession()
    with pytest.raises(GoldenKeyError, match="Укажите цену"):
        GoldenKeyClient.save_lot_fields(client, {"offer_id": "0"})


# ------------------------------------------------- клиент: оркестрация методов
def test_create_lot_fills_fields_and_finds_id():
    client = FakeLotClient()
    lot_id = GoldenKeyClient.create_lot(client, 1701, "PZ конфиг новичка",
                                        "Описание", 149.0, payment_message="Спасибо!")
    assert lot_id is not None
    sent = client.saved_fields[-1]
    assert sent["offer_id"] == "0"
    assert sent["fields[summary][ru]"] == "PZ конфиг новичка"
    assert sent["fields[desc][ru]"] == "Описание"
    assert sent["fields[payment_msg][ru]"] == "Спасибо!"
    assert sent["price"] == "149.0"
    assert sent["active"] == "on"
    assert sent["csrf_token"] == "CSRF"   # подставлен текущий токен сессии


def test_create_lot_returns_none_when_id_not_found():
    client = FakeLotClient()
    client.get_own_lots = lambda node_id: []
    assert GoldenKeyClient.create_lot(client, 1701, "Лот", "Описание", 100.0) is None


def test_set_lot_active_deactivates_lot():
    client = FakeLotClient()
    GoldenKeyClient.set_lot_active(client, "7770001", False)
    sent = client.saved_fields[-1]
    assert sent["offer_id"] == "7770001"
    assert sent["active"] == ""
    GoldenKeyClient.set_lot_active(client, "7770001", True)
    assert client.saved_fields[-1]["active"] == "on"


def test_set_lot_price_preserves_other_fields():
    client = FakeLotClient()
    GoldenKeyClient.set_lot_price(client, "7770001", 199.0)
    sent = client.saved_fields[-1]
    assert sent["offer_id"] == "7770001"
    assert sent["price"] == "199.0"
    assert sent["active"] == "on"          # остальные поля не тронуты


def test_set_lot_price_rejects_nonpositive():
    with pytest.raises(GoldenKeyError):
        GoldenKeyClient.set_lot_price(FakeLotClient(), "7770001", 0)


# -------------------------------------------------------------------- адаптер
def make_adapter(fake_client) -> GoldenKeyFunPayAdapter:
    class Secrets:
        def has(self, name):
            return True

        def get(self, name):
            return "GOLDEN"

    settings = FunPaySettings(mode="golden_key", unofficial_allowed=True)
    adapter = GoldenKeyFunPayAdapter(settings, Secrets())
    adapter._client = fake_client
    return adapter


def test_adapter_lot_capabilities_available():
    from app.domain.enums import Capability

    adapter = make_adapter(FakeLotClient())
    caps = adapter.capabilities()
    assert caps["create_listing"] == Capability.AVAILABLE
    assert caps["update_listing"] == Capability.AVAILABLE
    assert caps["get_listings"] == Capability.AVAILABLE


def test_adapter_close_and_open_listing(ctx):
    fake = FakeLotClient()
    adapter = make_adapter(fake)
    adapter.close_listing("7770001")
    assert fake.saved_fields[-1]["active"] == ""
    adapter.open_listing("7770001")
    assert fake.saved_fields[-1]["active"] == "on"
    with pytest.raises(CapabilityError):
        adapter.get_listings(None)  # без подкатегории — честная ошибка


def test_adapter_update_listing_price():
    fake = FakeLotClient()
    adapter = make_adapter(fake)
    adapter.update_listing("7770001", price=199.0)
    assert fake.saved_fields[-1]["price"] == "199.0"
    with pytest.raises(CapabilityError):
        adapter.update_listing("7770001", title="не поддерживается")


# ------------------------------------------------------------- сервис: план
def make_product(ctx, code, game, *, active=True, stock_mode="stock",
                 lot_id=None, lot_active=False, price=149.0) -> models.Product:
    with ctx.db.session() as session:
        product = models.Product(code=code, name=f"Продукт {code}", game=game,
                                 type="auto", active=active, stock_mode=stock_mode,
                                 price=price, funpay_lot_id=lot_id,
                                 funpay_lot_active=lot_active)
        session.add(product)
        session.flush()
        return product


def seed_scenario(ctx):
    ctx.config.funpay.lot_nodes = {"Project Zomboid": 1701}
    a = make_product(ctx, "A_READY", "Project Zomboid")            # создать
    b = make_product(ctx, "B_EMPTY", "Project Zomboid",
                     lot_id="111", lot_active=True)                # закрыть (нет товара)
    c = make_product(ctx, "C_REOPEN", "Project Zomboid",
                     lot_id="222", lot_active=False)               # открыть заново
    d = make_product(ctx, "D_NONODE", "Другая игра")               # нет подкатегории
    ctx.stock.add_unit(a.id, payload_ref="payload://a")
    ctx.stock.add_unit(c.id, payload_ref="payload://c")
    ctx.stock.add_unit(d.id, payload_ref="payload://d")
    return a, b, c, d


def enable_funpay(ctx, monkeypatch, fake_client) -> None:
    ctx.config.funpay.mode = "golden_key"
    ctx.config.funpay.unofficial_allowed = True
    ctx.secrets.set(GOLDEN_KEY_SECRET, "GOLDEN")
    monkeypatch.setattr("app.integrations.funpay.golden_key_adapter.GoldenKeyClient",
                        lambda key, user_agent=None, timeout=15.0: fake_client)


def test_plan_listing_sync(ctx):
    seed_scenario(ctx)
    plan = ctx.funpay.plan_listing_sync()
    assert [p["code"] for p in plan["create"]] == ["A_READY"]
    assert [p["code"] for p in plan["close"]] == ["B_EMPTY"]
    assert [p["code"] for p in plan["open"]] == ["C_REOPEN"]
    assert [p["code"] for p in plan["skipped_no_node"]] == ["D_NONODE"]


def test_plan_ignores_service_products_without_stock_mode(ctx):
    """Продукты без складского режима публикуются и без юнитов."""
    make_product(ctx, "SERVICE", "Project Zomboid", stock_mode="none")
    ctx.config.funpay.lot_nodes = {"Project Zomboid": 1701}
    plan = ctx.funpay.plan_listing_sync()
    assert [p["code"] for p in plan["create"]] == ["SERVICE"]


def test_sync_listings_dry_run_applies_nothing(ctx, monkeypatch):
    seed_scenario(ctx)
    fake = FakeLotClient()
    enable_funpay(ctx, monkeypatch, fake)
    ctx.config.automation_mode = "auto_safe"
    ctx.config.dry_run = True

    result = ctx.funpay.sync_listings(confirmed=True)
    assert result["dry_run"] is True and result["applied"] is False
    assert fake.saved_fields == []  # ни одного внешнего вызова


def test_sync_listings_applies_in_auto_safe(ctx, monkeypatch):
    a, b, c, _d = seed_scenario(ctx)
    fake = FakeLotClient()
    enable_funpay(ctx, monkeypatch, fake)
    ctx.config.automation_mode = "auto_safe"
    ctx.config.dry_run = False
    ctx.config.funpay.lot_payment_message = "Спасибо за покупку!"

    result = ctx.funpay.sync_listings(confirmed=True)
    assert result["applied"] is True
    assert (result["created"], result["opened"], result["closed"]) == (1, 1, 1)
    assert result["errors"] == []

    with ctx.db.session() as session:
        pa = session.get(models.Product, a.id)
        pb = session.get(models.Product, b.id)
        pc = session.get(models.Product, c.id)
        assert pa.funpay_lot_id and pa.funpay_lot_active is True     # создан
        assert pa.funpay_lot_price == 149.0                          # цена зафиксирована
        assert pb.funpay_lot_id == "111" and pb.funpay_lot_active is False  # закрыт
        assert pc.funpay_lot_active is True                          # открыт заново

    close_fields = [f for f in fake.saved_fields if f.get("offer_id") == "111"]
    assert close_fields and close_fields[-1]["active"] == ""
    assert any(f.get("offer_id") == "0" and f.get("active") == "on"
               for f in fake.saved_fields)

    # повторная синхронизация идемпотентна — ничего не меняет
    again = ctx.funpay.sync_listings(confirmed=True)
    assert again["created"] == again["opened"] == again["closed"] == 0


def test_sync_listings_blocked_when_automation_off(ctx, monkeypatch):
    seed_scenario(ctx)
    fake = FakeLotClient()
    enable_funpay(ctx, monkeypatch, fake)
    ctx.config.automation_mode = "off"
    ctx.config.dry_run = False

    result = ctx.funpay.sync_listings(confirmed=True)
    assert result["applied"] is False
    assert any("выключена" in err for err in result["errors"])
    assert fake.saved_fields == []


def test_sync_listings_poller_requires_auto_sync_flag(ctx, monkeypatch):
    seed_scenario(ctx)
    fake = FakeLotClient()
    enable_funpay(ctx, monkeypatch, fake)
    ctx.config.automation_mode = "auto_safe"
    ctx.config.dry_run = False
    ctx.config.funpay.lot_auto_sync = False

    result = ctx.funpay.sync_listings(from_poller=True)
    assert result["applied"] is False and fake.saved_fields == []

    ctx.config.funpay.lot_auto_sync = True
    result = ctx.funpay.sync_listings(from_poller=True)
    assert result["applied"] is True and result["created"] == 1


def set_lot_price(ctx, product_id: int, lot_price: float | None) -> None:
    with ctx.db.session() as session:
        session.get(models.Product, product_id).funpay_lot_price = lot_price


def set_product_price(ctx, product_id: int, price: float,
                      minimum_price: float = 0.0) -> None:
    with ctx.db.session() as session:
        product = session.get(models.Product, product_id)
        product.price = price
        product.minimum_price = minimum_price


def test_plan_reprice_with_tolerance_and_min_guard(ctx):
    ctx.config.funpay.lot_nodes = {"Project Zomboid": 1701}
    p = make_product(ctx, "R1", "Project Zomboid", lot_id="333", lot_active=True)
    ctx.stock.add_unit(p.id, payload_ref="payload://r1")
    set_lot_price(ctx, p.id, 100.0)
    set_product_price(ctx, p.id, 120.0)

    plan = ctx.funpay.plan_listing_sync()
    assert [x["code"] for x in plan["reprice"]] == ["R1"]
    assert plan["reprice"][0]["lot_price"] == 100.0

    ctx.config.funpay.lot_price_tolerance_pct = 25.0   # 120 vs 100 — внутри допуска
    assert ctx.funpay.plan_listing_sync()["reprice"] == []

    ctx.config.funpay.lot_price_tolerance_pct = 0.0
    set_product_price(ctx, p.id, 90.0, minimum_price=100.0)   # ниже минимума
    plan = ctx.funpay.plan_listing_sync()
    assert plan["reprice"] == []
    assert [x["code"] for x in plan["blocked_below_min"]] == ["R1"]


def test_plan_create_blocked_below_min(ctx):
    ctx.config.funpay.lot_nodes = {"Project Zomboid": 1701}
    p = make_product(ctx, "CHEAP", "Project Zomboid", price=50.0)
    ctx.stock.add_unit(p.id, payload_ref="payload://cheap")
    set_product_price(ctx, p.id, 50.0, minimum_price=100.0)
    plan = ctx.funpay.plan_listing_sync()
    assert plan["create"] == []
    assert [x["code"] for x in plan["blocked_below_min"]] == ["CHEAP"]


def test_sync_listings_reprices_lot(ctx, monkeypatch):
    ctx.config.funpay.lot_nodes = {"Project Zomboid": 1701}
    p = make_product(ctx, "R2", "Project Zomboid", lot_id="444", lot_active=True,
                     price=180.0)
    ctx.stock.add_unit(p.id, payload_ref="payload://r2")
    set_lot_price(ctx, p.id, 149.0)
    fake = FakeLotClient()
    enable_funpay(ctx, monkeypatch, fake)
    ctx.config.automation_mode = "auto_safe"
    ctx.config.dry_run = False

    result = ctx.funpay.sync_listings(confirmed=True)
    assert result["repriced"] == 1 and result["errors"] == []
    assert fake.saved_fields[-1]["price"] == "180.0"
    assert fake.saved_fields[-1]["active"] == "on"   # лот не закрылся при смене цены
    with ctx.db.session() as session:
        assert session.get(models.Product, p.id).funpay_lot_price == 180.0

    # идемпотентность: цена уже синхронизирована
    again = ctx.funpay.sync_listings(confirmed=True)
    assert again["repriced"] == 0 and again["applied"] is False


def test_sync_listings_reprice_dry_run_applies_nothing(ctx, monkeypatch):
    ctx.config.funpay.lot_nodes = {"Project Zomboid": 1701}
    p = make_product(ctx, "R3", "Project Zomboid", lot_id="555", lot_active=True,
                     price=200.0)
    ctx.stock.add_unit(p.id, payload_ref="payload://r3")
    set_lot_price(ctx, p.id, 149.0)
    fake = FakeLotClient()
    enable_funpay(ctx, monkeypatch, fake)
    ctx.config.automation_mode = "auto_safe"
    ctx.config.dry_run = True

    result = ctx.funpay.sync_listings(confirmed=True)
    assert result["dry_run"] is True and result["repriced"] == 0
    assert fake.saved_fields == []


def test_sync_listings_reports_unknown_lot_id(ctx, monkeypatch):
    product = make_product(ctx, "LOST_ID", "Project Zomboid")
    ctx.stock.add_unit(product.id, payload_ref="payload://lost")
    ctx.config.funpay.lot_nodes = {"Project Zomboid": 1701}
    fake = FakeLotClient()
    fake.get_own_lots = lambda node_id: []  # лот сохранён, но список пуст
    enable_funpay(ctx, monkeypatch, fake)
    ctx.config.automation_mode = "auto_safe"
    ctx.config.dry_run = False

    result = ctx.funpay.sync_listings(confirmed=True)
    assert result["created"] == 0
    assert any("ID не найден" in err for err in result["errors"])
