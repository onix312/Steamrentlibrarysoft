"""Истечение аренды автоматически освобождает лицензию (ТЗ §30)."""
from __future__ import annotations

from datetime import timedelta

from app.domain.enums import LeaseStatus, OrderStatus
from app.services.access_service import LicenseAllocator
from tests.conftest import get_client_id, make_game_with_copies


def test_expired_lease_frees_license(ctx, clock):
    game_id = make_game_with_copies(ctx, 910001, "Timed Game", copies=1)
    client_id = get_client_id(ctx, "renter")
    lease = ctx.access.create_lease(client_id=client_id, game_id=game_id, days=3)

    # до истечения — копия занята
    with ctx.db.session() as session:
        assert LicenseAllocator(session, clock).availability(game_id).free == 0

    clock.advance(timedelta(days=3, minutes=1))
    result = ctx.access.process_expirations()
    assert lease.id in result["expired"]

    updated = ctx.access.get(lease.id)
    assert updated.status == LeaseStatus.EXPIRED.value
    assert updated.terminated_at is not None

    # лицензия снова свободна
    with ctx.db.session() as session:
        assert LicenseAllocator(session, clock).availability(game_id).free == 1

    # новый клиент теперь может получить эту копию
    second = get_client_id(ctx, "next_renter")
    new_lease = ctx.access.create_lease(client_id=second, game_id=game_id, days=3)
    assert new_lease.license_id == lease.license_id


def test_expiration_completes_order(ctx, clock):
    game_id = make_game_with_copies(ctx, 910002, "Order Game", copies=1)
    client_id = get_client_id(ctx, "buyer")
    order = ctx.orders.create_order("buyer", game_id=game_id, price=300, access_days=2)
    assert order.status == OrderStatus.ACTIVE.value

    clock.advance(timedelta(days=2, hours=1))
    ctx.access.process_expirations()

    finished = ctx.orders.get(order.id)
    assert finished.status == OrderStatus.COMPLETED.value


def test_warning_window_marks_expiring(ctx, clock):
    ctx.access.warn_hours = 24
    game_id = make_game_with_copies(ctx, 910003, "Warn Game", copies=1)
    client_id = get_client_id(ctx, "warner")
    lease = ctx.access.create_lease(client_id=client_id, game_id=game_id, days=2)

    clock.advance(timedelta(hours=25))  # осталось 23 часа
    result = ctx.access.process_expirations()
    assert lease.id in result["warning"]
    updated = ctx.access.get(lease.id)
    assert updated.status == LeaseStatus.EXPIRING.value
    # лицензия всё ещё занята до фактического истечения
    with ctx.db.session() as session:
        assert LicenseAllocator(session, clock).availability(game_id).free == 0


def test_manual_extend_moves_expiry(ctx, clock):
    game_id = make_game_with_copies(ctx, 910004, "Extend Game", copies=1)
    client_id = get_client_id(ctx, "extender")
    lease = ctx.access.create_lease(client_id=client_id, game_id=game_id, days=1)
    original_end = lease.expires_at

    extended = ctx.access.extend_lease(lease.id, 5)
    assert extended.expires_at == original_end + timedelta(days=5)

    clock.advance(timedelta(days=2))
    ctx.access.process_expirations()
    assert ctx.access.get(lease.id).status == LeaseStatus.ACTIVE.value
