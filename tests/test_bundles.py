"""Пакеты: автопредложения по жанрам и спросу (ТЗ §9)."""
from __future__ import annotations

from app.domain.enums import BundleStatus


def test_suggest_creates_genre_packs(seeded):
    ctx = seeded
    created = ctx.bundles.suggest()
    assert created, "на демо-данных должны быть предложения"
    for bundle in created:
        assert bundle.status == BundleStatus.SUGGESTED.value
        assert len(bundle.games) >= 3
        assert bundle.suggested_reason


def test_suggest_is_idempotent(seeded):
    ctx = seeded
    first = ctx.bundles.suggest()
    second = ctx.bundles.suggest()
    assert first and not second, "повторное предложение не должно плодить дубли"


def test_manual_bundle(seeded):
    ctx = seeded
    from sqlalchemy import select
    from app.database import models

    with ctx.db.session() as session:
        ids = [
            g.id for g in session.scalars(
                select(models.Game).where(models.Game.is_free.is_(False)).limit(3)
            )
        ]
    bundle = ctx.bundles.create("Test Pack", ids, price=499)
    assert bundle.status == BundleStatus.ACTIVE.value
    assert len(bundle.games) == 3

    listing = ctx.listings.generate_for_bundle(bundle.id)
    assert "Test Pack" in listing.title
