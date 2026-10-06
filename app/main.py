"""Точка входа приложения (ТЗ: отдельное desktop-приложение на PySide6)."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger(__name__)


def _data_dir() -> Path:
    override = os.environ.get("STEAMRENT_DATA_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "data"


def _register_scheduler(ctx, window) -> None:
    """(Пере)создаёт планировщик с интервалами из настроек."""
    from app.core.scheduler import BackgroundScheduler
    from app.workers.funpay_worker import build_funpay_poll_job
    from app.workers.steam_sync_worker import build_steam_sync_job

    if getattr(ctx, "scheduler", None) is not None:
        ctx.scheduler.stop_all()

    config = ctx.config
    scheduler = BackgroundScheduler(
        ctx.events,
        backoff_base_sec=config.scheduler.backoff_base_sec,
        backoff_cap_sec=config.scheduler.backoff_cap_sec,
    )
    scheduler.add_task(
        "steam_sync", build_steam_sync_job(ctx),
        max(600, config.steam.sync_interval_min * 60),
    )
    scheduler.add_task(
        "lease_check", ctx.access.process_expirations,
        max(60, config.scheduler.lease_check_interval_min * 60),
    )
    scheduler.add_task(
        "funpay_poll", build_funpay_poll_job(ctx),
        max(300, config.funpay.poll_interval_min * 60),
    )
    scheduler.add_task(
        "backup", ctx.backup_database,
        max(3600, config.scheduler.backup_interval_hours * 3600),
    )
    scheduler.add_task(
        "demand_recalc", ctx.demand.recalculate_all,
        max(3600, config.scheduler.demand_recalc_interval_hours * 3600),
    )
    scheduler.add_task(
        "drops_tick", _build_drops_tick_job(ctx),
        max(120, config.scheduler.lease_check_interval_min * 60),
    )
    scheduler.add_task(
        "stock_replenishment", _build_replenishment_job(ctx),
        max(3600, config.scheduler.backup_interval_hours * 1800),
    )
    scheduler.add_task(
        "rules_tick", _build_rules_job(ctx),
        max(300, config.scheduler.lease_check_interval_min * 120),
    )
    ctx.scheduler = scheduler
    log.info("Планировщик: задач зарегистрировано — %d", len(scheduler.tasks))


def _build_drops_tick_job(ctx):
    """Проверка сессий дропов: ожидаемое время вышло → напомнить о верификации."""

    def job() -> None:
        result = ctx.drops.tick_sessions()
        if result["verify"]:
            ctx.notifications.notify(
                "action_required", "Drops: проверьте прогресс",
                f"Сессий на верификацию: {len(result['verify'])}. "
                "Сверьтесь с инвентарём дропов и подтвердите фактические минуты.",
            )
        if result["ending_campaigns"]:
            ctx.notifications.notify(
                "drops", "Кампании заканчиваются",
                f"Кампаний, завершающихся в течение 24 ч: {len(result['ending_campaigns'])}.",
            )

    return job


def _build_replenishment_job(ctx):
    """Дефицит цифрового склада → задачи пополнения."""

    def job() -> None:
        tasks = ctx.stock.check_replenishment(ctx.products.all(active_only=True))
        if tasks:
            ctx.notifications.notify(
                "stock", "Требуется пополнение склада",
                f"Продуктов с дефицитом: {len(tasks)}.",
            )

    return job


def _build_rules_job(ctx):
    """Движок правил: дефицит склада, дедлайны, долгие подтверждения."""

    def job() -> None:
        fired = ctx.rules.evaluate()
        if fired:
            ctx.rules.apply(fired)

    return job


def _maybe_seed_demo_accounts(ctx, window) -> None:
    """Первый запуск: предложим добавить демо-аккаунты в режиме симуляции."""
    from PySide6.QtWidgets import QMessageBox

    if ctx.library.accounts():
        return
    if ctx.config.steam.mode != "simulation":
        return
    answer = QMessageBox.question(
        window, "Первый запуск",
        "База пуста. Добавить 4 демонстрационных Steam-аккаунта (режим симуляции) "
        "и синхронизировать библиотеки прямо сейчас?",
    )
    if answer != QMessageBox.StandardButton.Yes:
        return
    from app.integrations.steam.simulator import SIM_ACCOUNTS

    for steam_id in SIM_ACCOUNTS:
        ctx.library.add_account(steam_id)
    report = ctx.library.sync_all()
    ctx.library.enrich_games(only_missing=True)
    ctx.eligibility.evaluate_all(force=True)
    ctx.demand.recalculate_all()
    ctx.notifications.notify(
        "sync", "Демо-данные загружены",
        f"Игр: {report.games_imported}, лицензий-копий: {report.licenses_upserted}.",
    )
    window._refresh_pages("dashboard", "library", "steam")


def run() -> int:
    from PySide6.QtWidgets import QApplication

    from app.app_context import build_context
    from app.core.applog import setup_logging
    from app.ui.main_window import MainWindow
    from app.ui.theme import DARK_QSS

    data_dir = _data_dir()
    setup_logging(data_dir)

    app = QApplication(sys.argv)
    app.setApplicationName("Steam Rent Manager")
    app.setOrganizationName("SteamRentLibrarySoft")
    app.setStyleSheet(DARK_QSS)

    ctx = build_context(data_dir)

    window = MainWindow(ctx)
    ctx.on_settings_applied = window.apply_settings  # type: ignore[attr-defined]
    _register_scheduler(ctx, window)
    ctx.reschedule_all = lambda: _register_scheduler(ctx, window)  # type: ignore[attr-defined]

    ctx.events.subscribe(
        "scheduler.failed",
        lambda name="", error="": ctx.notifications.notify(
            "error", f"Фоновая задача «{name}» завершилась с ошибкой", error
        ),
    )

    window.show()
    _maybe_seed_demo_accounts(ctx, window)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
