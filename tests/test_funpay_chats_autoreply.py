"""Этап: чаты (список/история), входящие сообщения, автоответы."""
from __future__ import annotations

from datetime import datetime, timezone

from app.core.config import FunPaySettings
from app.integrations.funpay.golden_key_adapter import (
    GOLDEN_KEY_SECRET,
    GoldenKeyFunPayAdapter,
)
from app.integrations.funpay.golden_key_client import GoldenKeyClient

CHATS_HTML = """
<div>
  <a class="contact-item unread" data-id="users-555-777" data-node-msg="10" data-user-msg="9">
    <div class="media-user-name">Buyer1</div>
    <div class="contact-item-message">!статус?</div>
  </a>
  <a class="contact-item" data-id="users-111-777" data-node-msg="5" data-user-msg="4">
    <div class="media-user-name">Buyer2</div>
    <div class="contact-item-message">ок</div>
  </a>
  <a class="contact-item unread" data-id="users-222-777" data-node-msg="1" data-user-msg="0">
    <div class="media-user-name">DeletedChat</div>
  </a>
</div>
"""

MSG_IN_HTML = ('<div class="chat-msg-item"><div class="media-user-name"><a>Buyer1</a></div>'
               '<div class="chat-msg-text">!статус как там заказ</div></div>')
MSG_ME_HTML = ('<div class="chat-msg-item"><div class="media-user-name"><a>Продавец</a></div>'
               '<div class="chat-msg-text">Здравствуйте!</div></div>')
MSG_SYSTEM_HTML = '<div class="chat-msg-item"><div role="alert">Заказ оплачен</div></div>'


class FakeResponse:
    def __init__(self, json_data=None, status=200):
        self._json = json_data or {}
        self.status_code = status
        self.url = "https://funpay.com/chat/history"
        self.cookies = type("C", (), {"get_dict": staticmethod(lambda: {})})()

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


def make_inited_client() -> GoldenKeyClient:
    client = GoldenKeyClient("GOLDEN", user_agent="TestUA")
    client.initiated = True
    client.user_id = 777
    client.username = "Продавец"
    client.csrf_token = "CSRF"

    class FakeSession:
        def get(self, url, **kwargs):
            if "chat/history" in url:
                return FakeResponse({"chat": {"node": {"id": "users-555-777"}, "messages": [
                    {"id": 1, "author": 0, "html": MSG_SYSTEM_HTML},
                    {"id": 2, "author": 555, "html": MSG_IN_HTML},
                    {"id": 3, "author": 777, "html": MSG_ME_HTML},
                ]}})
            return FakeResponse({})

        def post(self, url, **kwargs):
            return FakeResponse({})

        def mount(self, *a, **k):
            pass

    client.session = FakeSession()
    return client


# ------------------------------------------------------------------ клиент
def test_get_chats_parses_shortcuts():
    client = make_inited_client()
    client.runner = lambda objects, request=None: {
        "objects": [{"type": "chat_bookmarks", "data": {"html": CHATS_HTML}}]}
    chats = client.get_chats()
    assert [c.chat_id for c in chats] == ["users-555-777", "users-111-777"]
    assert chats[0].unread is True and chats[0].name == "Buyer1"
    assert chats[0].last_message == "!статус?"
    assert chats[1].unread is False


def test_get_chat_history_parses_messages():
    client = make_inited_client()
    messages = client.get_chat_history("users-555-777")
    assert [m.message_id for m in messages] == [1, 2, 3]
    system, incoming, mine = messages
    assert system.author_id == 0 and system.text == "Заказ оплачен"
    assert incoming.author_id == 555 and incoming.by_me is False
    assert incoming.text == "!статус как там заказ"
    assert mine.by_me is True


# ------------------------------------------------------------------ адаптер
class FakeChatClient:
    """Чаты + продажи + отправка без сети."""

    def __init__(self):
        self.initiated = True
        self.user_id = 777
        self.sent = []

    def get_chats(self, max_chats=50):
        return []

    def get_sales(self, **kwargs):
        from app.integrations.funpay.golden_key_client import SaleOrder
        return [SaleOrder("FP-1", "Buyer1", 555, "лот", 149.0, "₽", "paid",
                          datetime(2026, 10, 6, tzinfo=timezone.utc), "users-555-777")]

    def send_chat_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        return {"response": {}, "objects": []}


def make_enabled_adapter(fake_client) -> GoldenKeyFunPayAdapter:
    class Secrets:
        def has(self, name):
            return True

        def get(self, name):
            return "GOLDEN"

    settings = FunPaySettings(mode="golden_key", unofficial_allowed=True)
    adapter = GoldenKeyFunPayAdapter(settings, Secrets())
    adapter._client = fake_client
    return adapter


def test_adapter_get_messages_unread_only(ctx, monkeypatch):
    from app.integrations.funpay.golden_key_client import ChatInfo

    fake = FakeChatClient()
    fake.get_chats = lambda max_chats=50: [
        ChatInfo("users-555-777", "Buyer1", "!статус?", True, 10, 9),
        ChatInfo("users-111-777", "Buyer2", "ок", False, 5, 4),
    ]
    fake.get_chat_history = lambda chat_id, limit=100: make_inited_client().get_chat_history(chat_id)
    adapter = make_enabled_adapter(fake)

    dtos = adapter.get_messages()
    assert len(dtos) == 1  # только непрочитанный чат, только входящие (не системные/свои)
    dto = dtos[0]
    assert dto.external_id == "users-555-777:2"
    assert dto.counterpart_username == "Buyer1"
    assert dto.text.startswith("!статус")


# ---------------------------------------------------- сервис: поллинг+автоответы
def enable_funpay(ctx, monkeypatch, fake_client) -> None:
    ctx.config.funpay.mode = "golden_key"
    ctx.config.funpay.unofficial_allowed = True
    ctx.secrets.set(GOLDEN_KEY_SECRET, "GOLDEN")
    monkeypatch.setattr("app.integrations.funpay.golden_key_adapter.GoldenKeyClient",
                        lambda key, user_agent=None, timeout=15.0: fake_client)


def make_fake_client_for_service():
    from app.integrations.funpay.golden_key_client import ChatInfo

    fake = FakeChatClient()
    fake.get_chats = lambda max_chats=50: [
        ChatInfo("users-555-777", "Buyer1", "!статус?", True, 10, 9),
    ]
    fake.get_chat_history = lambda chat_id, limit=100: make_inited_client().get_chat_history(chat_id)
    return fake


def test_poll_messages_saves_and_deduplicates(ctx, monkeypatch):
    fake = make_fake_client_for_service()
    enable_funpay(ctx, monkeypatch, fake)

    result = ctx.funpay.poll_messages()
    assert result["received"] == 1
    with ctx.db.session() as session:
        from app.database import models
        from sqlalchemy import select
        incoming = session.scalars(select(models.Message).where(models.Message.direction == "in")).all()
        assert len(incoming) == 1
        assert incoming[0].external_id == "users-555-777:2"
        assert incoming[0].status == "received"
    # повторный поллинг — дедупликация по внешнему ID
    assert ctx.funpay.poll_messages()["received"] == 0


def test_auto_reply_draft_in_manual_mode(ctx, monkeypatch):
    fake = make_fake_client_for_service()
    enable_funpay(ctx, monkeypatch, fake)
    ctx.config.funpay.message_mode = "manual_approval"
    ctx.config.funpay.auto_replies = {"!статус": "Заказ в работе."}

    result = ctx.funpay.poll_messages()
    assert result == {"received": 1, "replies_sent": 0, "reply_drafts": 1}
    assert fake.sent == []  # ничего не ушло наружу
    drafts = ctx.funpay.drafted()
    assert any(d.body == "Заказ в работе." and d.template_key == "auto_reply" for d in drafts)


def test_auto_reply_sends_in_auto_safe(ctx, monkeypatch):
    fake = make_fake_client_for_service()
    enable_funpay(ctx, monkeypatch, fake)
    ctx.config.funpay.message_mode = "auto_safe"
    ctx.config.dry_run = False
    ctx.config.funpay.auto_replies = {"!статус": "Заказ в работе."}

    result = ctx.funpay.poll_messages()
    assert result == {"received": 1, "replies_sent": 1, "reply_drafts": 0}
    assert fake.sent == [("users-555-777", "Заказ в работе.")]


def test_auto_reply_dry_run_makes_draft(ctx, monkeypatch):
    fake = make_fake_client_for_service()
    enable_funpay(ctx, monkeypatch, fake)
    ctx.config.funpay.message_mode = "auto_safe"
    ctx.config.dry_run = True
    ctx.config.funpay.auto_replies = {"!статус": "Заказ в работе."}

    result = ctx.funpay.poll_messages()
    assert result["replies_sent"] == 0 and result["reply_drafts"] == 1
    assert fake.sent == []  # DRY RUN: наружу ничего


def test_no_auto_reply_without_matching_command(ctx, monkeypatch):
    fake = make_fake_client_for_service()
    enable_funpay(ctx, monkeypatch, fake)
    ctx.config.funpay.auto_replies = {"!другая": "Ответ"}

    result = ctx.funpay.poll_messages()
    assert result == {"received": 1, "replies_sent": 0, "reply_drafts": 0}
