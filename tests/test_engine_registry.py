from app.engines.registry import DiagnosticCase, build_default_registry


def test_default_registry_contains_required_engines():
    registry = build_default_registry()
    assert {
        "server_doctor", "mod_doctor", "game_doctor",
        "config_factory", "save_doctor", "digital_delivery",
    }.issubset(set(registry.names()))


def test_game_doctor_returns_normalized_diagnosis():
    engine = build_default_registry().resolve("game_doctor")
    result = engine.analyze(DiagnosticCase.from_inputs("game_doctor", {
        "game": "Ready or Not",
        "error": "DXGI device removed after driver crash",
    }))
    assert result["confidence"] > 0.5
    assert result["evidence"]
    assert result["rollback"]
