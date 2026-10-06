"""Учёт ограничений Steam Families (ТЗ §32).

Приложение никогда не управляет составом семьи программно — только считает
слоты/кулдауны и подсказывает оператору, возможно ли изменение ВРУЧНУЮ.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.database import models
from app.database.session import Database
from app.integrations.steam import family_rules


@dataclass(frozen=True)
class FamilyState:
    members: int
    max_members: int
    slots_free: int
    rules_text: str

    @property
    def is_full(self) -> bool:
        return self.members >= self.max_members


class FamilyService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def state(self) -> FamilyState:
        with self.db.session() as session:
            members = len(
                session.scalars(
                    select(models.SteamAccount).where(
                        models.SteamAccount.family_role.in_(["organizer", "adult", "child"])
                    )
                ).all()
            )
        return FamilyState(
            members=members,
            max_members=family_rules.MAX_FAMILY_MEMBERS,
            slots_free=max(0, family_rules.MAX_FAMILY_MEMBERS - members),
            rules_text=family_rules.family_rules_summary(),
        )

    def can_add_member(
        self,
        candidate_joined_previous_family_at=None,
        frozen_slot_until=None,
    ) -> family_rules.MemberChangeVerdict:
        """Проверка ДО ручного добавления человека оператором в клиенте Steam."""
        from app.core.clock import Clock

        state = self.state()
        return family_rules.evaluate_member_add(
            taken_slots=state.members,
            frozen_slot_until=frozen_slot_until,
            candidate_joined_previous_family_at=candidate_joined_previous_family_at,
            now=Clock().now(),
        )
