from __future__ import annotations


def test_product_packs_cover_many_games(ctx):
    games = ctx.products.factory_games()
    assert len(games) >= 15
    assert "Project Zomboid" in games
    assert "Minecraft" in games
    assert "Rust" in games


def test_pack_update_preserves_operator_price(ctx):
    created = ctx.products.create_from_factory("Project Zomboid")
    assert created
    product = ctx.products.by_code("PZ_SERVER_CONFIG")
    assert product is not None
    with ctx.db.session() as session:
        from app.database import models
        row = session.get(models.Product, product.id)
        row.price = 777.0
        row.minimum_price = 555.0
    ctx.products.create_from_factory("Project Zomboid")
    product = ctx.products.by_code("PZ_SERVER_CONFIG")
    assert product.price == 777.0
    assert product.minimum_price == 555.0
