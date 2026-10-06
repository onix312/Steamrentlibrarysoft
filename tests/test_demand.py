"""Demand Score: детерминированность, грейды, настройка весов (ТЗ §5–6)."""
from __future__ import annotations

from sqlalchemy import select

from app.database import models
from app.domain.enums import DemandGrade


def test_demand_score_in_range_and_deterministic(seeded):
    ctx = seeded
    with ctx.db.session() as session:
        games = session.scalars(select(models.Game)).all()
        for game in games:
            assert 0.0 <= game.demand_score <= 100.0
            assert game.demand_grade in {g.value for g in DemandGrade}
            assert game.demand_breakdown, "расчёт должен быть прозрачным"
        scores = {g.id: g.demand_score for g in games}
    ctx.demand.recalculate_all()
    with ctx.db.session() as session:
        again = {g.id: g.demand_score for g in session.scalars(select(models.Game)).all()}
    assert scores == again, "повторный расчёт без изменений данных должен давать тот же результат"


def test_hot_coop_game_beats_obscure_singleplayer(seeded):
    ctx = seeded
    with ctx.db.session() as session:
        pz = session.scalar(select(models.Game).where(models.Game.app_id == 108600))
        odd = session.scalar(select(models.Game).where(models.Game.app_id == 215))  # старый нишевый шутер
    assert pz.demand_score > odd.demand_score


def test_weights_affect_score(ctx, clock):
    from tests.conftest import make_game_with_copies

    game_id = make_game_with_copies(ctx, 930001, "Weight Probe", copies=1)
    ctx.demand.recalculate_all()
    with ctx.db.session() as session:
        base = session.get(models.Game, game_id).demand_score

    ctx.config.demand_weights.popularity = 0.0
    ctx.config.demand_weights.reviews = 0.0
    ctx.config.demand_weights.trend = 0.0
    ctx.config.demand_weights.marketplace = 0.0
    ctx.config.demand_weights.price = 1.0
    ctx.config.demand_weights.recency = 0.0
    ctx.config.demand_weights.multiplayer = 0.0
    ctx.demand.recalculate_all()
    with ctx.db.session() as session:
        only_price = session.get(models.Game, game_id).demand_score
    assert abs(only_price - base) > 0.001 or base == 0.0, "изменение весов должно менять оценку"


def test_free_game_grade_is_free(seeded):
    ctx = seeded
    with ctx.db.session() as session:
        cs2 = session.scalar(select(models.Game).where(models.Game.app_id == 730))
        assert cs2.demand_grade == DemandGrade.FREE.value


def test_grade_thresholds_respected(seeded):
    ctx = seeded
    thresholds = ctx.config.grade_thresholds
    with ctx.db.session() as session:
        for game in session.scalars(select(models.Game)).all():
            if game.demand_grade == DemandGrade.FREE.value:
                continue
            score = game.demand_score
            if game.demand_grade == "S":
                assert score >= thresholds.s
            elif game.demand_grade == "A":
                assert thresholds.a <= score < thresholds.s
            elif game.demand_grade == "D":
                assert score < thresholds.c
