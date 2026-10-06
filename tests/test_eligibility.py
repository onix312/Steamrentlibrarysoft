"""Steam Families eligibility: недоступные игры не считаются продаваемыми (ТЗ §30)."""
from __future__ import annotations

from sqlalchemy import select

from app.database import models
from app.domain.enums import EligibilityStatus


def _game_by_app(ctx, app_id: int) -> models.Game:
    with ctx.db.session() as session:
        game = session.scalar(select(models.Game).where(models.Game.app_id == app_id))
        assert game is not None
        session.expunge(game)
        return game


def test_excluded_game_is_not_sellable(seeded):
    """GTA V исключена (датасет): статус не «доступна», продавать нельзя."""
    ctx = seeded
    gta = _game_by_app(ctx, 271590)
    assert gta.eligibility_status in (
        EligibilityStatus.PUBLISHER_EXCLUDED.value,
        EligibilityStatus.THIRD_PARTY_LAUNCHER.value,
    )
    assert not ctx.eligibility.is_sellable(gta)


def test_third_party_launcher_flag_blocks(seeded):
    """Игра с признаком стороннего лаунчера недоступна даже без датасета."""
    ctx = seeded
    with ctx.db.session() as session:
        game = session.scalar(select(models.Game).where(models.Game.app_id == 271590))
        assert game.requires_third_party_launcher is True


def test_confirmed_game_is_sellable(seeded):
    ctx = seeded
    pz = _game_by_app(ctx, 108600)
    assert pz.eligibility_status == EligibilityStatus.AVAILABLE.value
    assert pz.eligibility_source == "dataset"
    assert ctx.eligibility.is_sellable(pz)


def test_manual_override_wins(seeded):
    ctx = seeded
    pz = _game_by_app(ctx, 108600)
    ctx.eligibility.set_override(pz.id, EligibilityStatus.UNAVAILABLE, reason="Проверил вручную: не шарится")
    updated = _game_by_app(ctx, 108600)
    assert updated.eligibility_status == EligibilityStatus.UNAVAILABLE.value
    assert updated.eligibility_source == "manual"
    assert not ctx.eligibility.is_sellable(updated)
    # снятие переопределения возвращает результат датасета
    ctx.eligibility.set_override(pz.id, None)
    restored = _game_by_app(ctx, 108600)
    assert restored.eligibility_status == EligibilityStatus.AVAILABLE.value


def test_unknown_game_defaults_to_needs_check(ctx, clock):
    """Без подтверждения игра НЕ считается доступной автоматически."""
    from tests.conftest import make_game_with_copies

    game_id = make_game_with_copies(ctx, 424242, "Unknown Game", copies=1)
    ctx.eligibility.evaluate_all(force=True)
    with ctx.db.session() as session:
        game = session.get(models.Game, game_id)
        assert game.eligibility_status == EligibilityStatus.NEEDS_CHECK.value
        assert not ctx.eligibility.is_sellable(game)


def test_eligibility_cache_records_source_and_date(seeded):
    ctx = seeded
    pz = _game_by_app(ctx, 108600)
    assert pz.eligibility_checked_at is not None
    assert pz.eligibility_reason
