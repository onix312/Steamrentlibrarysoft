"""Функция фоновой синхронизации Steam (полный конвейер):

  библиотеки → метаданные → Steam Families eligibility → Demand Score.

Выполняется воркером в пуле потоков; результат — отчёт-словарь.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def build_steam_sync_job(context) -> callable:
    """Возвращает callable для планировщика/кнопки «Синхронизировать Steam»."""

    def job() -> dict:
        report = context.library.sync_all()
        result: dict = {
            "accounts": report.accounts_processed,
            "games": report.games_imported,
            "licenses": report.licenses_upserted,
            "errors": list(report.errors),
        }
        try:
            result["enriched"] = context.library.enrich_games(only_missing=True)
        except Exception as exc:  # noqa: BLE001 - метаданные не критичны
            log.warning("Обогащение метаданных не выполнено: %s", exc)
            result["enriched_error"] = str(exc)
        try:
            result["eligibility"] = context.eligibility.evaluate_all(force=False)
        except Exception as exc:  # noqa: BLE001
            log.exception("Пересчёт eligibility не выполнен")
            result["eligibility_error"] = str(exc)
        try:
            result["demand_recalculated"] = context.demand.recalculate_all()
        except Exception as exc:  # noqa: BLE001
            log.exception("Пересчёт Demand Score не выполнен")
            result["demand_error"] = str(exc)
        return result

    return job
