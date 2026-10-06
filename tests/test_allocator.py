"""Критические тесты LicenseAllocator (ТЗ §30)."""
from __future__ import annotations

import pytest

from app.domain.value_objects import AllocationError
from app.services.access_service import LicenseAllocator
from tests.conftest import get_client_id, make_game_with_copies


def test_single_copy_busy_means_zero_availability(ctx, clock):
    """1 копия + 1 активный клиент => доступность 0, новый заказ копию НЕ получает."""
    game_id = make_game_with_copies(ctx, 900001, "Solo Game", copies=1)
    client_a = get_client_id(ctx, "client_a")
    client_b = get_client_id(ctx, "client_b")

    ctx.access.create_lease(client_id=client_a, game_id=game_id, days=30)

    with ctx.db.session() as session:
        availability = LicenseAllocator(session, clock).availability(game_id)
    assert availability.total == 1
    assert availability.busy == 1
    assert availability.free == 0

    with pytest.raises(AllocationError):
        ctx.access.create_lease(client_id=client_b, game_id=game_id, days=30)


def test_multiple_owners_busy_a_assigns_b(ctx, clock):
    """Игра на аккаунтах A и B; A занят — назначается B."""
    game_id = make_game_with_copies(ctx, 900002, "Dual Game", copies=2)
    client_a = get_client_id(ctx, "client_a")
    client_b = get_client_id(ctx, "client_b")

    lease_a = ctx.access.create_lease(client_id=client_a, game_id=game_id, days=30)

    with ctx.db.session() as session:
        accounts = {}
        from app.database import models
        from sqlalchemy import select
        for acc in session.scalars(select(models.SteamAccount)):
            accounts[acc.id] = acc.display_name

    lease_b = ctx.access.create_lease(client_id=client_b, game_id=game_id, days=30)
    assert lease_b.license_id != lease_a.license_id
    assert lease_b.owner_account_id != lease_a.owner_account_id


def test_third_client_blocked_when_two_copies_busy(ctx, clock):
    game_id = make_game_with_copies(ctx, 900003, "Two Copies", copies=2)
    for name in ("c1", "c2"):
        ctx.access.create_lease(client_id=get_client_id(ctx, name), game_id=game_id, days=30)
    with pytest.raises(AllocationError):
        ctx.access.create_lease(client_id=get_client_id(ctx, "c3"), game_id=game_id, days=30)


def test_allocator_prefers_least_used_copy(ctx, clock):
    game_id = make_game_with_copies(ctx, 900004, "Wear Level", copies=2)
    client = get_client_id(ctx, "cl")
    lease1 = ctx.access.create_lease(client_id=client, game_id=game_id, days=7)
    ctx.access.complete_lease(lease1.id)
    lease2 = ctx.access.create_lease(client_id=client, game_id=game_id, days=7)
    assert lease2.license_id != lease1.id, "вторая аренда должна лечь на менее нагруженную копию"
