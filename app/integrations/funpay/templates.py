"""Шаблоны текстов: объявления, ответы клиентам, условия.

Все тексты редактируемы человеком перед публикацией/отправкой —
приложение готовит черновик, оператор подтверждает.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class ListingText:
    title: str
    short_description: str
    description: str
    faq: str
    terms: str
    post_purchase: str


def build_listing_text(
    game_names: list[str],
    access_days: int,
    price: float,
    free_copies: int,
    owner_names: list[str],
    bundle_name: str | None = None,
) -> ListingText:
    head = bundle_name or (game_names[0] if len(game_names) == 1 else f"{game_names[0]} + набор")
    title = f"{head} | Steam Family | Доступ {access_days} дн."

    games_list = "\n".join(f"  • {name}" for name in game_names)
    short = (
        f"Доступ к «{head}» через Steam Families на {access_days} дней. "
        f"Активация за 5 минут, поддержка в чате."
    )
    description = (
        f"Игра(ы):\n{games_list}\n\n"
        f"Что вы получаете:\n"
        f"  • Доступ через официальную функцию Steam Families (без передачи паролей).\n"
        f"  • Срок доступа: {access_days} дней с момента настройки.\n"
        f"  • Сохранения и достижения — на ВАШЕМ аккаунте.\n"
        f"  • Свободных копий сейчас: {free_copies}.\n\n"
        "Как это работает:\n"
        "  1. Вы оплачиваете заказ.\n"
        "  2. Я добавляю ваш аккаунт в семейную группу (инструкция в чате).\n"
        "  3. Игра появляется в вашей библиотеке — играете как в своей.\n"
    )
    faq = (
        "FAQ:\n"
        "Q: Это навсегда?\n"
        f"A: Нет, доступ на {access_days} дней. Продление — отдельным заказом.\n"
        "Q: Нужны ли мои данные от аккаунта?\n"
        "A: Нет. Достаточно принять приглашение в семью в Steam.\n"
        "Q: Можно ли играть онлайн/кооп?\n"
        "A: Да, если игра это поддерживает (см. описание игры).\n"
        "Q: Ограничения?\n"
        "A: Регион аккаунта должен совпадать с регионом семьи; одновременно играет 1 человек на копию."
    )
    terms = (
        "Условия:\n"
        f"  • Цена: {price:.0f} руб. за {access_days} дней доступа.\n"
        "  • После окончания срока вы автоматически покидаете семейную группу.\n"
        "  • Запрещено: передача доступа третьим лицам, читы (риск бана у владельца копии).\n"
        "  • Возврат — только если доступ не удалось выдать в течение 24 часов."
    )
    post_purchase = (
        "Спасибо за заказ! Сейчас настрою доступ.\n"
        "Пожалуйста, убедитесь, что:\n"
        "  1. Ваш регион аккаунта совпадает с регионом семьи (обычно Турция/Украина/Россия — уточню).\n"
        "  2. Вы готовы принять приглашение в семью: Steam → Настройки → Семья.\n"
        "Как только отправлю приглашение — напишу сюда. Это займёт до 10 минут."
    )
    return ListingText(title, short, description, faq, terms, post_purchase)


#: Шаблоны автоответов. {placeholders} подставляются при генерации.
MESSAGE_TEMPLATES: dict[str, tuple[str, str]] = {
    # ключ -> (название, шаблон)
    "new_order": (
        "Новый заказ",
        "Здравствуйте, {client}! Заказ {order_ref} на «{game}» получен. "
        "Начинаю настройку доступа, ориентировочно {eta_minutes} минут. Оставайтесь на связи.",
    ),
    "access_granted": (
        "Доступ выдан",
        "{client}, доступ к «{game}» открыт до {expires_at}. "
        "Приглашение в семью отправлено — примите его в Steam (Настройки → Семья). "
        "Если игра не появилась в течение 15 минут, напишите мне.",
    ),
    "one_day_left": (
        "Остался 1 день",
        "{client}, напоминаю: доступ к «{game}» закончится {expires_at}. "
        "Если хотите продлить — оформите заказ на продление, и срок будет добавлен.",
    ),
    "access_ended": (
        "Доступ закончился",
        "{client}, срок доступа к «{game}» истёк. Спасибо, что были со мной! "
        "Буду рад видеть вас снова — на продление действует скидка.",
    ),
    "problem": (
        "Проблема",
        "{client}, по заказу {order_ref} возникла проблема: {problem}. "
        "Уже решаю и напишу, как только будут новости. Приношу извинения за неудобства.",
    ),
}


class _SafeDict(defaultdict):
    """Возвращает сам ключ как значение, чтобы неизвестные плейсхолдеры
    оставались в тексте вместо падения или пустоты."""

    def __missing__(self, key):  # noqa: D105
        return "{" + key + "}"


def render_message(template_key: str, **params) -> str:
    """Подстановка параметров в шаблон. Неизвестные ключи остаются как есть."""
    if template_key not in MESSAGE_TEMPLATES:
        raise KeyError(f"Неизвестный шаблон сообщения: {template_key}")
    _, template = MESSAGE_TEMPLATES[template_key]
    safe = _SafeDict()
    safe.update(params)
    return template.format_map(safe)
