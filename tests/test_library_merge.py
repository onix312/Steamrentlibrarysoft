"""Дубликаты AppID: одна игра, несколько лицензий-копий (ТЗ §30)."""
from __future__ import annotations

from sqlalchemy import select

from app.database import models, repositories


def test_duplicate_app_id_merges_into_one_game(seeded):
    ctx = seeded
    with ctx.db.session() as session:
        # Project Zomboid (108600) есть у аккаунтов 1, 2 и 3 в демо-данных
        games = session.scalars(
            select(models.Game).where(models.Game.app_id == 108600)
        ).all()
        assert len(games) == 1, "Одинаковый AppID с нескольких аккаунтов = одна запись игры"
        game = games[0]
        licenses = repositories.LicenseRepo(session).for_game(game.id)
        assert len(licenses) == 3, "Три аккаунта-владельца = три лицензии-копии"
        account_ids = {lic.account_id for lic in licenses}
        assert len(account_ids) == 3

        # account_games: по записи на каждый аккаунт-источник
        account_games = session.scalars(
            select(models.AccountGame).where(models.AccountGame.app_id == 108600)
        ).all()
        assert len(account_games) == 3


def test_sync_is_idempotent(seeded):
    """Повторная синхронизация не создаёт дублей."""
    ctx = seeded
    with ctx.db.session() as session:
        games_before = len(session.scalars(select(models.Game)).all())
        licenses_before = len(session.scalars(select(models.GameLicense)).all())
    ctx.library.sync_all()
    with ctx.db.session() as session:
        games_after = len(session.scalars(select(models.Game)).all())
        licenses_after = len(session.scalars(select(models.GameLicense)).all())
    assert games_before == games_after
    assert licenses_before == licenses_after


def test_availability_counts_all_copies(seeded):
    ctx = seeded
    from app.services.access_service import LicenseAllocator

    with ctx.db.session() as session:
        game = session.scalar(select(models.Game).where(models.Game.app_id == 108600))
        availability = LicenseAllocator(session, ctx.clock).availability(game.id)
    assert availability.total == 3
    assert availability.free == 3
