def test_market_trend_and_opportunity_v2(ctx):
    ctx.market.record_snapshot("Project Zomboid", 8, 100, 150, 300, 12)
    ctx.market.record_snapshot("Project Zomboid", 5, 120, 180, 320, 10)
    trend = ctx.market.trend("Project Zomboid")
    assert trend["median_delta"] == 30
    assert trend["left_competitors"] == 3
    report = ctx.market.opportunity_v2(
        "Project Zomboid", manual_hours=0.5, demand_signal=80, automation_pct=95
    )
    assert 0 <= report["score"] <= 100
    assert report["recommendation"] in {"CREATE", "SCALE", "HOLD", "KILL"}
