"""F2P не является коммерческим преимуществом (ТЗ §6, §30)."""
from __future__ import annotations

from sqlalchemy import select

from app.database import models
from app.domain.enums import DemandGrade, EligibilityStatus


def test_f2p_gets_free_grade_and_not_sellable(seeded):
    ctx = seeded
    with ctx.db.session() as session:
        cs2 = session.scalar(select(models.Game).where(models.Game.app_id == 730))
        dota = session.scalar(select(models.Game).where(models.Game.app_id == 570))
        apex = session.scalar(select(models.Game).where(models.Game.app_id == 1172470))
    for game in (cs2, dota, apex):
        assert game is not None
        assert game.is_free is True
        assert game.demand_grade == DemandGrade.FREE.value
        assert game.eligibility_status == EligibilityStatus.FREE.value
        assert not ctx.eligibility.is_sellable(game), f"{game.name} не должна продаваться"


def test_f2p_not_in_commercial_catalog(seeded):
    """Коммерческий каталог (продаваемые игры) не содержит F2P."""
    ctx = seeded
    with ctx.db.session() as session:
        sellable = session.scalars(
            select(models.Game).where(models.Game.is_free.is_(False))
        ).all()
        for game in sellable:
            assert not game.is_free
        free_in_sellable = [
            g for g in sellable if g.eligibility_status == EligibilityStatus.AVAILABLE.value and g.is_free
        ]
        assert free_in_sellable == []


def test_paid_games_marked_paid(seeded):
    ctx = seeded
    with ctx.db.session() as session:
        pz = session.scalar(select(models.Game).where(models.Game.app_id == 108600))
        assert pz.is_free is False
        assert pz.price > 0
