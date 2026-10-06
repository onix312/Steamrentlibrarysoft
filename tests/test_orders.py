"""Заказы: статусы, автораспределение, повторный импорт (ТЗ §10)."""
from __future__ import annotations

import pytest

from app.domain.enums import OrderStatus
from app.domain.value_objects import DomainError
from tests.conftest import make_game_with_copies


def test_order_auto_assigns_license(ctx, clock):
    game_id = make_game_with_copies(ctx, 920001, "Order Target", copies=1)
    order = ctx.orders.create_order("buyer1", game_id=game_id, price=300, access_days=7)
    assert order.status == OrderStatus.ACTIVE.value
    assert order.assigned_license_id is not None
    assert order.access_ends_at is not None


def test_order_without_free_copy_becomes_problem(ctx, clock):
    game_id = make_game_with_copies(ctx, 920002, "Busy Game", copies=1)
    first = ctx.orders.create_order("buyer1", game_id=game_id, price=300, access_days=7)
    assert first.status == OrderStatus.ACTIVE.value
    second = ctx.orders.create_order("buyer2", game_id=game_id, price=300, access_days=7)
    assert second.status == OrderStatus.PROBLEM.value
    assert second.assigned_license_id is None


def test_manual_order_no_auto_assign(ctx, clock):
    game_id = make_game_with_copies(ctx, 920003, "Manual Assign", copies=1)
    order = ctx.orders.create_order("buyer3", game_id=game_id, price=300, access_days=7, auto_assign=False)
    assert order.status == OrderStatus.NEW.value
    assert order.assigned_license_id is None


def test_duplicate_funpay_id_rejected(ctx, clock):
    game_id = make_game_with_copies(ctx, 920004, "Dup", copies=2)
    ctx.orders.create_order("a", game_id=game_id, price=1, access_days=1, funpay_order_id="F1")
    with pytest.raises(DomainError):
        ctx.orders.create_order("b", game_id=game_id, price=1, access_days=1, funpay_order_id="F1")


def test_order_requires_game_or_bundle(ctx, clock):
    with pytest.raises(DomainError):
        ctx.orders.create_order("someone", price=10, access_days=1)
