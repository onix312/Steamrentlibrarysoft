"""Формализованные правила Steam Families (проверено 2026-10, см. docs/RESEARCH.md).

Приложение НИКОГДА не управляет составом семьи программно — только
учитывает ограничения и показывает их оператору. Добавление/удаление
участника — отдельная ограниченная РУЧНАЯ операция.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

MAX_FAMILY_MEMBERS = 6
MIN_ACCOUNT_AGE_DAYS = 30

#: Вступить в новую семью можно через год после вступления в предыдущую.
JOIN_COOLDOWN_DAYS = 365
#: Слот, освобождённый вышедшим участником, закрыт для НОВЫХ людей на год.
SLOT_FREEZE_DAYS = 365


@dataclass(frozen=True)
class SlotState:
    total: int
    taken: int
    frozen_until: datetime | None = None  # если есть замороженный слот

    @property
    def free_now(self) -> int:
        return max(0, self.total - self.taken)


@dataclass(frozen=True)
class MemberChangeVerdict:
    """Результат проверки: можно ли добавить человека в семью СЕЙЧАС."""
    allowed: bool
    reasons: tuple[str, ...]


def evaluate_member_add(
    taken_slots: int,
    frozen_slot_until: datetime | None,
    candidate_joined_previous_family_at: datetime | None,
    now: datetime,
) -> MemberChangeVerdict:
    reasons: list[str] = []
    if taken_slots >= MAX_FAMILY_MEMBERS:
        if frozen_slot_until and frozen_slot_until > now:
            reasons.append(
                f"Все {MAX_FAMILY_MEMBERS} слотов заняты, и освободившийся слот "
                f"заморожен до {frozen_slot_until.date()}. Добавить нового человека нельзя."
            )
        else:
            reasons.append(f"Все {MAX_FAMILY_MEMBERS} слотов семьи заняты.")
    elif frozen_slot_until and frozen_slot_until > now and taken_slots == MAX_FAMILY_MEMBERS - 0:
        reasons.append("Слот заморожен после выхода предыдущего участника.")

    if candidate_joined_previous_family_at is not None:
        available_from = candidate_joined_previous_family_at + timedelta(days=JOIN_COOLDOWN_DAYS)
        if available_from > now:
            reasons.append(
                f"Кандидат вступил в предыдущую семью {candidate_joined_previous_family_at.date()}: "
                f"новая семья доступна только с {available_from.date()} (правило 1 года)."
            )

    return MemberChangeVerdict(allowed=not reasons, reasons=tuple(reasons))


def family_rules_summary() -> str:
    """Текст ограничений для отображения в UI (экран Steam)."""
    return (
        "Ограничения Steam Families (актуально на 2026):\n"
        f"• Максимум {MAX_FAMILY_MEMBERS} участников в одной семье (включая организатора).\n"
        "• Все участники — из одного региона Steam.\n"
        "• Одна копия игры = один одновременный игрок; для параллельной игры нужны копии.\n"
        "• Игры со сторонними лаунчерами/аккаунтами/ключами/подписками не шарятся.\n"
        "• Бесплатные (F2P) игры не раздаются через семью.\n"
        f"• Кулдаун: в новую семью — через {JOIN_COOLDOWN_DAYS} дн. после вступления в прошлую.\n"
        f"• Освободившийся слот закрыт для новых людей на {SLOT_FREEZE_DAYS} дн.\n"
        "• VAC-бан за читы в общей игре может затронуть владельца копии.\n"
        "• Приложение не управляет составом семьи автоматически: только учёт и напоминания."
    )
