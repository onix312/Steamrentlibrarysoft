"""Offline-first и резервные копии (ТЗ §25)."""
from __future__ import annotations


def test_backup_creates_copy(ctx):
    target = ctx.backup_database()
    assert target is not None and target.exists()


def test_backup_rotation_keeps_seven(ctx):
    for _ in range(10):
        ctx.backup_database()
    backups = list((ctx.data_dir / "backups").glob("steamrent_*.db"))
    assert len(backups) <= 7


def test_app_works_without_network(ctx):
    """Источник-симулятор доступен всегда; синхронизация проходит офлайн."""
    from app.integrations.steam.simulator import SIM_ACCOUNTS

    ok, _ = ctx.source.check_available()
    assert ok
    for steam_id in list(SIM_ACCOUNTS)[:1]:
        ctx.library.add_account(steam_id)
    report = ctx.library.sync_all()
    assert report.ok
