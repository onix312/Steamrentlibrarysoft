"""Audit log фиксирует значимые действия (ТЗ §20)."""
from __future__ import annotations

from tests.conftest import get_client_id, make_game_with_copies


def test_order_and_lease_lifecycle_logged(ctx, clock):
    game_id = make_game_with_copies(ctx, 940001, "Audit Game", copies=1)
    order = ctx.orders.create_order("auditclient", game_id=game_id, price=250, access_days=5)
    ctx.access.complete_lease(order.lease.id)

    actions = [entry.action for entry in ctx.audit.latest(200)]
    assert "order.created" in actions
    assert "lease.created" in actions
    assert "license.assigned" in actions
    assert "lease.completed" in actions
    assert "license.released" in actions


def test_account_add_logged(ctx):
    ctx.library.add_account("76561198000000001", "Audit Acc")
    actions = [entry.action for entry in ctx.audit.latest(50)]
    assert "account.added" in actions


def test_eligibility_override_logged(seeded):
    ctx = seeded
    from sqlalchemy import select
    from app.database import models
    from app.domain.enums import EligibilityStatus

    with ctx.db.session() as session:
        game = session.scalar(select(models.Game).where(models.Game.app_id == 108600))
        game_id = game.id
    ctx.eligibility.set_override(game_id, EligibilityStatus.NEEDS_CHECK, reason="тест")
    entries = ctx.audit.latest(50)
    assert any(e.action == "eligibility.overridden" for e in entries)


def test_listing_price_change_logged(ctx, clock):
    game_id = make_game_with_copies(ctx, 940002, "Listing Game", copies=1)
    with ctx.db.session() as session:
        from app.database import models

        game = session.get(models.Game, game_id)
        game.eligibility_status = "available"
        game.price = 1000
    listing = ctx.listings.generate_for_game(game_id)
    ctx.listings.set_price(listing.id, 555)
    actions = [entry.action for entry in ctx.audit.latest(100)]
    assert "listing.created" in actions
    assert "price.changed" in actions
