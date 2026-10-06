from app.database import models


def test_operations_prioritizes_problem_orders(ctx):
    with ctx.db.session() as session:
        client = models.Client(funpay_username="ops-user")
        session.add(client)
        session.flush()
        session.add(models.Order(
            client_id=client.id,
            price=100,
            currency="RUB",
            status="problem",
            purchased_at=ctx.clock.now(),
        ))
    actions = ctx.operations.actions()
    assert actions
    assert actions[0].priority >= 95
    assert any(item.kind == "order" for item in actions)
