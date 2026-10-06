from __future__ import annotations

from app.domain.enums import TwitchAccountStatus


def test_drops_account_stock_lifecycle_is_synchronized(ctx):
    product = ctx.products.create(
        code="DROP_TEST",
        name="Drops test account",
        game="Test Game",
        type="auto",
        automation_level="A4",
        price=100,
        minimum_price=50,
        workflow_code="auto_delivery",
        stock_mode="account",
        target_stock=1,
        payload_template={"engine": "digital_delivery"},
    )
    account = ctx.drops.add_account("drops-01")
    ctx.drops.set_status(account.id, TwitchAccountStatus.COMPLETE)
    assert ctx.drops.mark_ready(account.id) is None

    unit = ctx.drops.sync_stock_unit(account.id, product.id)
    assert unit.status == "ready"

    assert ctx.drops.reserve_account(account.id, order_id=123)
    inventory = {x["account_id"]: x for x in ctx.drops.unified_inventory()}
    assert inventory[account.id]["stock_status"] == "reserved"

    assert ctx.drops.release_account(account.id)
    inventory = {x["account_id"]: x for x in ctx.drops.unified_inventory()}
    assert inventory[account.id]["stock_status"] == "ready"

    assert ctx.drops.reserve_account(account.id, order_id=124)
    assert ctx.drops.sell_account(account.id, purge_credentials=False)
    inventory = {x["account_id"]: x for x in ctx.drops.unified_inventory()}
    assert inventory[account.id]["stock_status"] == "sold"
