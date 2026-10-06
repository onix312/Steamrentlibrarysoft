from __future__ import annotations

from app.database import models
from app.domain.enums import TwitchAccountStatus


def _make_order(ctx, username: str) -> int:
    with ctx.db.session() as session:
        client = models.Client(funpay_username=username)
        session.add(client)
        session.flush()
        order = models.Order(
            client_id=client.id,
            price=100,
            currency="RUB",
            status="new",
            purchased_at=ctx.clock.now(),
        )
        session.add(order)
        session.flush()
        return order.id


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
    ctx.drops.mark_ready(account.id)

    unit = ctx.drops.sync_stock_unit(account.id, product.id)
    assert unit.status == "ready"

    first_order_id = _make_order(ctx, "drops-buyer-1")
    assert ctx.drops.reserve_account(account.id, order_id=first_order_id)
    inventory = {x["account_id"]: x for x in ctx.drops.unified_inventory()}
    assert inventory[account.id]["stock_status"] == "reserved"

    assert ctx.drops.release_account(account.id)
    inventory = {x["account_id"]: x for x in ctx.drops.unified_inventory()}
    assert inventory[account.id]["stock_status"] == "ready"

    second_order_id = _make_order(ctx, "drops-buyer-2")
    assert ctx.drops.reserve_account(account.id, order_id=second_order_id)
    assert ctx.drops.sell_account(account.id, purge_credentials=False)
    inventory = {x["account_id"]: x for x in ctx.drops.unified_inventory()}
    assert inventory[account.id]["stock_status"] == "sold"
