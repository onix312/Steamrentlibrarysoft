"""Drops Control Center: аккаунты, кампании, сессии, верификация,
оценка стоимости, атомарный сток (ТЗ §28–34)."""
from __future__ import annotations

from datetime import timedelta

from app.core.secret_store import SecretStore
from app.domain.enums import (
    DropCampaignStatus,
    SellRecommendation,
    TwitchAccountStatus,
    WatchSessionStatus,
)


def make_campaign(ctx, **overrides):
    defaults = dict(
        game_name="Path of Exile 2", campaign_name="PoE2 Early Drops",
        required_minutes=120, rewards=["Скинка", "Портал"], estimated_value=600.0,
    )
    defaults.update(overrides)
    return ctx.drops.add_campaign(**defaults)


def test_campaign_statuses(ctx):
    now = ctx.clock.now()
    active = make_campaign(ctx, campaign_name="A", starts_at=now - timedelta(hours=5))
    upcoming = make_campaign(ctx, campaign_name="U", starts_at=now + timedelta(days=2),
                             ends_at=now + timedelta(days=9))
    ending = make_campaign(ctx, campaign_name="E", starts_at=now - timedelta(days=1),
                           ends_at=now + timedelta(hours=5))
    ended = make_campaign(ctx, campaign_name="X", starts_at=now - timedelta(days=9),
                          ends_at=now - timedelta(hours=1))

    assert ctx.drops.campaign_status(active) == DropCampaignStatus.ACTIVE
    assert ctx.drops.campaign_status(upcoming) == DropCampaignStatus.UPCOMING
    assert ctx.drops.campaign_status(ending) == DropCampaignStatus.ENDING_SOON
    assert ctx.drops.campaign_status(ended) == DropCampaignStatus.ENDED
    assert [c.id for c in ctx.drops.ending_soon()] == [ending.id]


def test_watch_session_lifecycle_and_tick(ctx):
    account = ctx.drops.add_account("acc-1")
    campaign = make_campaign(ctx, required_minutes=60)

    watch_session = ctx.drops.plan_session(account.id, campaign.id, channel="streamer_ru")
    assert watch_session.status == WatchSessionStatus.PLANNED.value
    assert watch_session.planned_minutes == 60

    ctx.drops.start_session(watch_session.id, browser_pid=4242)
    assert ctx.drops.get_account(account.id).status == TwitchAccountStatus.WATCHING.value

    # время ещё не вышло — тик ничего не требует
    assert ctx.drops.tick_sessions()["verify"] == []

    # сдвиг времени за горизонт — сессия уходит на верификацию
    ctx.clock.advance(timedelta(minutes=61))
    result = ctx.drops.tick_sessions()
    assert result["verify"] == [watch_session.id]
    assert ctx.drops.sessions(open_only=True)[0].status == WatchSessionStatus.VERIFY_PROGRESS.value

    # оператор подтвердил прогресс по инвентарю
    done = ctx.drops.verify_session(watch_session.id, verified_minutes=58)
    assert done.status == WatchSessionStatus.DONE.value
    assert done.actual_progress == 58
    progress = ctx.drops.progress(account.id, campaign.id)
    assert progress.expected_minutes == 60  # оценка локального таймера


def test_verified_progress_and_claim_flag(ctx):
    account = ctx.drops.add_account("acc-2")
    campaign = make_campaign(ctx, required_minutes=120, rewards=["R1", "R2"])

    ctx.drops.verify_progress(account.id, campaign.id, verified_minutes=60,
                              claimed_count=1, claim_states=["claimed", "ready_to_claim"])
    assert ctx.drops.get_account(account.id).status == TwitchAccountStatus.CLAIM_REQUIRED.value

    ctx.drops.verify_progress(account.id, campaign.id, verified_minutes=120,
                              claimed_count=2, claim_states=["claimed", "claimed"])
    assert ctx.drops.get_account(account.id).status == TwitchAccountStatus.COMPLETE.value


def test_valuation_and_recommendation(ctx):
    account = ctx.drops.add_account("acc-3")
    done_campaign = make_campaign(ctx, campaign_name="Done", required_minutes=60,
                                  rewards=["R"], estimated_value=300.0)
    next_campaign = make_campaign(ctx, campaign_name="Next", required_minutes=60,
                                  rewards=["R"], estimated_value=500.0)
    ctx.drops.verify_progress(account.id, done_campaign.id, verified_minutes=60,
                              claimed_count=1, claim_states=["claimed"])

    report = ctx.drops.valuation_report(account.id)
    assert report["current_value"] == 300.0
    assert report["next_campaign"] == "Next"
    assert report["incremental_value_per_hour"] == 500.0  # 500 ₽ / 1 ч
    assert report["recommendation"] == SellRecommendation.ADD_NEXT_CAMPAIGN.value


def test_valuation_sell_now_when_no_campaigns(ctx):
    account = ctx.drops.add_account("acc-4")
    campaign = make_campaign(ctx, required_minutes=60, rewards=["R"], estimated_value=300.0)
    ctx.drops.verify_progress(account.id, campaign.id, verified_minutes=60,
                              claimed_count=1, claim_states=["claimed"])
    report = ctx.drops.valuation_report(account.id)
    assert report["recommendation"] == SellRecommendation.SELL_NOW.value


def test_campaign_priority_deadline_first(ctx):
    now = ctx.clock.now()
    cheap_long = make_campaign(ctx, campaign_name="CheapLong", required_minutes=600,
                               estimated_value=600.0)  # 60 ₽/ч, без дедлайна
    urgent = make_campaign(ctx, campaign_name="Urgent", required_minutes=60,
                           estimated_value=300.0,      # 300 ₽/ч + горящий дедлайн
                           starts_at=now - timedelta(days=1), ends_at=now + timedelta(hours=3))
    ranked = dict((c.campaign_name, score) for c, score in ctx.drops.campaign_priorities())
    assert ranked["Urgent"] > ranked["CheapLong"]


def test_reserve_account_race(ctx):
    """Два заказа претендуют на один аккаунт-сток — побеждает один."""
    account = ctx.drops.add_account("acc-5")
    ctx.drops.mark_ready(account.id)
    assert ctx.drops.get_account(account.id).status == TwitchAccountStatus.READY.value

    assert ctx.drops.reserve_account(account.id, order_id=1) is True
    assert ctx.drops.reserve_account(account.id, order_id=2) is False
    assert ctx.drops.get_account(account.id).reserved_order_id == 1


def test_release_and_sell_account(ctx):
    account = ctx.drops.add_account("acc-6", twitch_login="login6")
    ctx.secrets.set("twitch/acc6", "super-secret")
    with ctx.db.session() as session:
        from app.database import models
        db_account = session.get(models.TwitchAccount, account.id)
        db_account.login_ref = SecretStore.ref("twitch/acc6")

    ctx.drops.mark_ready(account.id)
    assert ctx.drops.reserve_account(account.id, order_id=7)
    # отмена заказа → аккаунт снова в продаже
    assert ctx.drops.release_account(account.id)
    assert ctx.drops.get_account(account.id).status == TwitchAccountStatus.READY.value

    # повторный резерв и продажа
    assert ctx.drops.reserve_account(account.id, order_id=8)
    assert ctx.drops.sell_account(account.id)
    sold = ctx.drops.get_account(account.id)
    assert sold.status == TwitchAccountStatus.SOLD.value
    assert sold.sold_at is not None
    # после продажи: ссылка на секрет удалена, секрет стёрт из хранилища
    assert sold.login_ref is None
    assert ctx.secrets.get("twitch/acc6") is None


def test_delivery_package_has_refs_not_secrets(ctx):
    account = ctx.drops.add_account("acc-7", twitch_login="login7")
    ctx.secrets.set("twitch/acc7", "super-secret")
    with ctx.db.session() as session:
        from app.database import models
        db_account = session.get(models.TwitchAccount, account.id)
        db_account.login_ref = SecretStore.ref("twitch/acc7")

    campaign = make_campaign(ctx, rewards=["R1"])
    ctx.drops.verify_progress(account.id, campaign.id, verified_minutes=120,
                              claimed_count=1, claim_states=["claimed"])
    package = ctx.drops.delivery_package(account.id)
    assert package["login"] == "login7"
    assert package["login_ref"] == "keyring://twitch/acc7"
    assert "super-secret" not in str(package)  # сам секрет наружу не отдаём
    assert package["included_drops"]


def test_browser_profile_dry_run_guard(ctx):
    profile = ctx.drops.add_profile("main", browser="chrome", profile_dir="Profile 1",
                                    executable="/usr/bin/true")
    assert profile.browser == "chrome"
    from app.browser.profile_manager import TWITCH_INVENTORY_URL
    command = ctx.browser.launch_command(profile, TWITCH_INVENTORY_URL)
    assert "--profile-directory=Profile 1" in command
    assert TWITCH_INVENTORY_URL in command
    # DRY RUN по умолчанию: браузер не запускается (возврат None, не pid)
    assert ctx.config.dry_run is True
    assert ctx.browser.open(profile, TWITCH_INVENTORY_URL) is None
