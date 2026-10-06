from __future__ import annotations

import json
from pathlib import Path

from app.integrations.funpay.protocol_health import (
    GLOBAL_FUNPAY_BREAKER,
    CircuitState,
    evaluate_contracts,
    validate_auth_html,
    validate_chat_json,
    validate_lots_html,
    validate_orders_html,
    validate_runner_json,
)

FIXTURES = Path(__file__).parent / "fixtures" / "funpay"


def test_funpay_protocol_fixtures_are_healthy():
    checks = evaluate_contracts(
        validate_auth_html((FIXTURES / "auth.html").read_text(encoding="utf-8")),
        validate_orders_html((FIXTURES / "orders.html").read_text(encoding="utf-8")),
        validate_chat_json(json.loads((FIXTURES / "chat.json").read_text(encoding="utf-8"))),
        validate_runner_json(json.loads((FIXTURES / "runner.json").read_text(encoding="utf-8"))),
        validate_lots_html((FIXTURES / "lots.html").read_text(encoding="utf-8")),
    )
    assert all(check.ok for check in checks)
    assert GLOBAL_FUNPAY_BREAKER.state == CircuitState.CLOSED


def test_protocol_failure_opens_breaker():
    evaluate_contracts(validate_auth_html("<html></html>"))
    assert GLOBAL_FUNPAY_BREAKER.state == CircuitState.OPEN
    GLOBAL_FUNPAY_BREAKER.reset()
