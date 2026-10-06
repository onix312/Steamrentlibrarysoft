"""Общие фикстуры: изолированный AppContext на временной БД + управляемые часы."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.app_context import build_context
from app.core.clock import FixedClock
from app.database import models, repositories

BASE_TIME = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(BASE_TIME)


@pytest.fixture
def ctx(tmp_path, clock):
    """Свежий контекст приложения на временной директории."""
    context = build_context(tmp_path / "data", clock=clock)
    try:
        yield context
    finally:
        context.shutdown()


@pytest.fixture
def seeded(ctx):
    """Контекст с 4 демо-аккаунтами, синхронизацией, eligibility и demand."""
    from app.integrations.steam.simulator import SIM_ACCOUNTS

    for steam_id in SIM_ACCOUNTS:
        ctx.library.add_account(steam_id)
    report = ctx.library.sync_all()
    assert report.ok, report.errors
    ctx.library.enrich_games(only_missing=True)
    ctx.eligibility.evaluate_all(force=True)
    ctx.demand.recalculate_all()
    return ctx


def make_game_with_copies(ctx, app_id: int, name: str, copies: int) -> int:
    """Создаёт игру и `copies` лицензий на разных аккаунтах. Возвращает game.id."""
    with ctx.db.session() as session:
        game, _ = repositories.GameRepo(session).upsert_by_app_id(app_id, name=name)
        game.eligibility_status = "available"
        game.eligibility_source = "manual"
        for i in range(copies):
            steam_id = f"7656999900000{app_id:03d}{i:02d}"
            account = models.SteamAccount(
                steam_id64=steam_id, display_name=f"Owner-{app_id}-{i}", status="active"
            )
            session.add(account)
            session.flush()
            repositories.LicenseRepo(session).upsert(game.id, account.id, "owned")
        return game.id


def get_client_id(ctx, username: str) -> int:
    with ctx.db.session() as session:
        client = repositories.ClientRepo(session).get_or_create(username)
        return client.id
