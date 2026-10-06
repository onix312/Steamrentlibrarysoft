"""Правила Steam Families: слоты и кулдауны только учитываются (ТЗ §32)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.integrations.steam.family_rules import (
    MAX_FAMILY_MEMBERS,
    MemberChangeVerdict,
    evaluate_member_add,
)

NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


def test_free_slot_allows_add():
    verdict = evaluate_member_add(taken_slots=3, frozen_slot_until=None,
                                  candidate_joined_previous_family_at=None, now=NOW)
    assert verdict.allowed


def test_full_family_blocks():
    verdict = evaluate_member_add(taken_slots=MAX_FAMILY_MEMBERS, frozen_slot_until=None,
                                  candidate_joined_previous_family_at=None, now=NOW)
    assert not verdict.allowed
    assert verdict.reasons


def test_frozen_slot_blocks_when_full():
    frozen_until = NOW + timedelta(days=200)
    verdict = evaluate_member_add(taken_slots=MAX_FAMILY_MEMBERS, frozen_slot_until=frozen_until,
                                  candidate_joined_previous_family_at=None, now=NOW)
    assert not verdict.allowed


def test_one_year_join_cooldown():
    joined_prev = NOW - timedelta(days=100)
    verdict = evaluate_member_add(taken_slots=2, frozen_slot_until=None,
                                  candidate_joined_previous_family_at=joined_prev, now=NOW)
    assert not verdict.allowed
    assert any("год" in reason or "1 года" in reason for reason in verdict.reasons)


def test_cooldown_expired_allows():
    joined_prev = NOW - timedelta(days=400)
    verdict = evaluate_member_add(taken_slots=2, frozen_slot_until=None,
                                  candidate_joined_previous_family_at=joined_prev, now=NOW)
    assert verdict.allowed


def test_family_service_reports_state(seeded):
    state = seeded.family.state()
    assert state.max_members == 6
    assert state.members >= 1
    assert "6 участников" in state.rules_text
