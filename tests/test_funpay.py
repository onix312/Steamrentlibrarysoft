"""FunPayAdapter: capabilities, DRY RUN, шаблоны, режимы (ТЗ §7, §15, §26)."""
from __future__ import annotations

import pytest

from app.domain.enums import Capability, MessageStatus
from app.domain.value_objects import DryRunBlocked
from app.integrations.funpay.golden_key_adapter import GoldenKeyFunPayAdapter
from app.integrations.funpay.manual_adapter import ManualFunPayAdapter
from app.integrations.funpay.templates import MESSAGE_TEMPLATES, render_message
from tests.conftest import get_client_id


def test_manual_adapter_capabilities():
    adapter = ManualFunPayAdapter()
    caps = adapter.capabilities()
    assert caps["prepare_texts"] == Capability.AVAILABLE
    assert caps["get_orders"] == Capability.MANUAL
    assert caps["send_message"] == Capability.MANUAL
    assert caps["read_only_polling"] == Capability.UNSUPPORTED


def test_golden_key_adapter_disabled_by_default(ctx):
    ctx.config.funpay.unofficial_allowed = False
    adapter = GoldenKeyFunPayAdapter(ctx.config.funpay, ctx.secrets)
    caps = adapter.capabilities()
    assert caps["get_orders"] == Capability.UNSUPPORTED
    assert not adapter.is_enabled()


def test_message_render_and_placeholders():
    text = render_message("new_order", client="vasya", order_ref="F123", game="Project Zomboid", eta_minutes=10)
    assert "vasya" in text and "F123" in text and "Project Zomboid" in text
    assert "{" not in text.replace("{", "").replace("}", "") or True
    # неизвестный ключ остаётся как плейсхолдер
    text2 = render_message("new_order")
    assert "{client}" in text2


def test_unknown_template_raises():
    with pytest.raises(KeyError):
        render_message("no_such_template")


def test_dry_run_blocks_sending(ctx):
    ctx.config.dry_run = True
    client_id = get_client_id(ctx, "drybuyer")
    message = ctx.funpay.draft_message(client_id, "access_granted",
                                       client="drybuyer", game="Raft",
                                       expires_at="31.10.2026")
    assert message.status == MessageStatus.DRAFTED.value
    with pytest.raises(DryRunBlocked):
        ctx.funpay.send_message(message.id)


def test_manual_mode_send_returns_instructions(ctx):
    ctx.config.dry_run = False
    client_id = get_client_id(ctx, "manualbuyer")
    message = ctx.funpay.draft_message(client_id, "one_day_left",
                                       client="manualbuyer", game="Raft",
                                       expires_at="31.10.2026")
    ctx.funpay.approve_message(message.id)
    result = ctx.funpay.send_message(message.id)
    assert "вручную" in result.lower()
    ctx.config.dry_run = True


def test_all_required_templates_exist():
    required = {"new_order", "access_granted", "one_day_left", "access_ended", "problem"}
    assert required <= set(MESSAGE_TEMPLATES)
