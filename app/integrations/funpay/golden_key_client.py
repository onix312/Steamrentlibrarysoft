"""Мини-клиент внутреннего веб-интерфейса FunPay (НЕОФИЦИАЛЬНЫЙ доступ).

Чистая реализация, написанная с нуля для этого проекта (код сторонних
проектов не копировался; использованы только наблюдаемые факты протокола,
см. docs/RESEARCH.md §6, первоисточник-референс: FunPayCardinal).

Правила, зашитые в клиент:
* работаем ТОЛЬКО со своим аккаунтом и его ``golden_key`` (ключ хранится
  в системном хранилище, сюда передаётся уже извлечённым);
* уважаем лимиты: при 429/«подождите» — ждём указанное время, НЕ обходим;
* при 403/разлогине — честно сообщаем (не притворяемся и не чиним сессию);
* никаких попыток обойти антибот/капчу.

Возможности (по состоянию протокола):
* инициализация аккаунта (имя, id, csrf, PHPSESSID);
* список своих продаж (заказов) со страницы /orders/trade;
* отправка сообщения в чат через /runner/ (используется для автовыдачи).
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

BASE_URL = "https://funpay.com/"
ORDERS_TRADE_URL = "https://funpay.com/orders/trade"
RUNNER_URL = "https://funpay.com/runner/"
MAX_PAGES = 3          # сколько страниц заказов читаем за один опрос
SEND_RETRIES = 2       # повторные попытки отправки при временных сбоях

MONTHS_RU = {
    "январ": 1, "феврал": 2, "март": 3, "марта": 3, "апрел": 4, "ма": 5,
    "июн": 6, "июл": 7, "август": 8, "сентябр": 9, "октябр": 10,
    "ноябр": 11, "декабр": 12,
}


class GoldenKeyError(Exception):
    """Базовая ошибка клиента."""


class UnauthorizedError(GoldenKeyError):
    """golden_key недействителен или сессия разлогинена."""


class FloodError(GoldenKeyError):
    """FunPay попросил подождать (лимит частоты). Ждём — не обходим."""

    def __init__(self, message: str, wait_seconds: int = 0) -> None:
        super().__init__(message)
        self.wait_seconds = wait_seconds


@dataclass(frozen=True)
class SaleOrder:
    """Заказ из списка продаж (сырые факты со страницы)."""
    order_id: str
    buyer_username: str
    buyer_id: int
    description: str
    price: float
    currency: str
    status: str            # paid | closed | refunded
    purchased_at: datetime
    chat_id: str           # users-<min>-<max>


@dataclass(frozen=True)
class ChatInfo:
    """Чат из списка диалогов."""
    chat_id: str
    name: str              # ник собеседника
    last_message: str
    unread: bool
    node_msg_id: int
    user_msg_id: int


@dataclass(frozen=True)
class ChatMessage:
    """Сообщение из истории чата."""
    message_id: int
    author_id: int
    author_name: str
    text: str
    by_me: bool


@dataclass(frozen=True)
class OwnLot:
    """Свой лот из списка подкатегории (/lots/<node>/trade)."""
    lot_id: str
    description: str
    price: float
    active: bool
    amount: int | None


def parse_funpay_datetime(text: str, now: datetime | None = None) -> datetime:
    """«сегодня 14:33», «вчера 21:10», «12 мая, 14:33», «12 мая 2025, 14:33»."""
    now = now or datetime.now(timezone.utc)
    text = text.strip().lower()
    hm = re.search(r"(\d{1,2}):(\d{2})", text)
    hour, minute = (int(hm.group(1)), int(hm.group(2))) if hm else (0, 0)
    if text.startswith("сегодня") or text.startswith("today"):
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if text.startswith("вчера") or text.startswith("yesterday"):
        return (now - timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    day = re.search(r"(\d{1,2})", text)
    month = 0
    for stem, number in MONTHS_RU.items():
        if stem in text:
            month = number
            break
    en_month = re.search(
        r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", text)
    if month == 0 and en_month:
        month = ("jan feb mar apr may jun jul aug sep oct nov dec".split().index(en_month.group(1)) + 1)
    year = re.search(r"(20\d{2})", text)
    year_value = int(year.group(1)) if year else now.year
    if day and month:
        try:
            return datetime(year_value, month, int(day.group(1)), hour, minute, tzinfo=now.tzinfo)
        except ValueError:
            pass
    return now


class GoldenKeyClient:
    """Сессия к своему аккаунту FunPay по ``golden_key``."""

    def __init__(self, golden_key: str, user_agent: str | None = None,
                 timeout: float = 15.0) -> None:
        if not golden_key:
            raise GoldenKeyError("golden_key не задан.")
        self.golden_key = golden_key
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        )
        self.timeout = timeout
        self.user_id: int | None = None
        self.username: str | None = None
        self.csrf_token: str | None = None
        self.phpsessid: str | None = None
        self.locale: str = "ru"
        self.initiated = False

        self.session = requests.Session()
        retry = Retry(total=3, connect=3, read=3, status=3, backoff_factor=1,
                      status_forcelist=[500, 502, 503, 504],
                      allowed_methods={"GET", "POST"})
        self.session.mount("https://", HTTPAdapter(max_retries=retry))

    # -------------------------------------------------------------- запросы
    def _headers(self, xhr: bool = False) -> dict:
        headers = {
            "user-agent": self.user_agent,
            "accept": "*/*" if xhr else "text/html,application/xhtml+xml",
            "accept-language": "ru,en;q=0.8",
        }
        if xhr:
            headers["x-requested-with"] = "XMLHttpRequest"
            headers["content-type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        return headers

    def _cookies(self) -> dict:
        cookies = {"golden_key": self.golden_key, "cookie_prefs": "1"}
        if self.phpsessid:
            cookies["PHPSESSID"] = self.phpsessid
        return cookies

    def _get_html(self, url: str) -> BeautifulSoup:
        response = self.session.get(url, headers=self._headers(), cookies=self._cookies(),
                                    timeout=self.timeout, allow_redirects=True)
        self._update_session(response)
        if response.status_code in (401, 403) or "account/login" in response.url:
            raise UnauthorizedError("golden_key не принят: проверьте ключ и браузерный user-agent.")
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")

    def _update_session(self, response: requests.Response) -> None:
        cookies = response.cookies.get_dict()
        if cookies.get("PHPSESSID"):
            self.phpsessid = cookies["PHPSESSID"]

    # --------------------------------------------------------- инициализация
    def init(self) -> "GoldenKeyClient":
        """Загружает главную страницу и вытаскивает данные сессии."""
        parser = self._get_html(BASE_URL)
        name_node = parser.find("div", {"class": "user-link-name"})
        if name_node is None:
            raise UnauthorizedError("Страница без профиля: golden_key недействителен.")
        self.username = name_node.get_text(strip=True)
        body = parser.find("body") or {}
        app_data = {}
        raw = body.get("data-app-data") if hasattr(body, "get") else None
        if raw:
            try:
                app_data = json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise GoldenKeyError("Не удалось разобрать данные страницы.") from exc
        self.user_id = app_data.get("userId")
        self.csrf_token = app_data.get("csrf-token")
        self.locale = app_data.get("locale") or "ru"
        if not self.user_id or not self.csrf_token:
            raise GoldenKeyError("Страница не содержит ожидаемых данных сессии.")
        self.initiated = True
        log.info("FunPay: аккаунт %s (id=%s) инициализирован", self.username, self.user_id)
        return self

    def ensure_initiated(self) -> None:
        if not self.initiated:
            self.init()

    # -------------------------------------------------------------- продажи
    def get_sales(self, include_closed: bool = True, include_refunded: bool = True,
                  max_pages: int = MAX_PAGES) -> list[SaleOrder]:
        """Список своих продаж (страница «Мои продажи»)."""
        self.ensure_initiated()
        sales: list[SaleOrder] = []
        continue_from: str | None = None
        for _ in range(max(1, max_pages)):
            page, continue_from = self._fetch_sales_page(include_closed, include_refunded,
                                                         start_from=continue_from)
            sales.extend(page)
            if not continue_from:
                break
        return sales

    def _fetch_sales_page(self, include_closed: bool, include_refunded: bool,
                          start_from: str | None) -> tuple[list[SaleOrder], str | None]:
        now = datetime.now(timezone.utc)
        if start_from:
            response = self.session.post(ORDERS_TRADE_URL, data={"continue": start_from},
                                         headers=self._headers(), cookies=self._cookies(),
                                         timeout=self.timeout, allow_redirects=True)
        else:
            response = self.session.get(ORDERS_TRADE_URL, headers=self._headers(),
                                        cookies=self._cookies(), timeout=self.timeout,
                                        allow_redirects=True)
        self._update_session(response)
        if response.status_code in (401, 403):
            raise UnauthorizedError("Список продаж недоступен: сессия истекла.")
        response.raise_for_status()
        parser = BeautifulSoup(response.text, "html.parser")
        app_data_node = parser.find("body")
        if app_data_node is not None and app_data_node.get("data-app-data"):
            try:
                csrf = json.loads(app_data_node["data-app-data"]).get("csrf-token")
                if csrf:
                    self.csrf_token = csrf
            except (TypeError, ValueError):
                pass

        next_node = parser.find("input", {"type": "hidden", "name": "continue"})
        next_id = next_node.get("value") if next_node else None

        orders: list[SaleOrder] = []
        for node in parser.find_all("a", {"class": "tc-item"}):
            try:
                orders.append(self._parse_order_node(node, include_closed, include_refunded, now))
            except (AttributeError, ValueError, TypeError) as exc:
                log.warning("Не удалось разобрать строку заказа: %s", exc)
                continue
        return [o for o in orders if o is not None], next_id

    def _parse_order_node(self, node, include_closed: bool, include_refunded: bool,
                          now: datetime) -> SaleOrder | None:
        classes = node.get("class") or []
        if "warning" in classes:
            status = "refunded"
            if not include_refunded:
                return None
        elif "info" in classes:
            status = "paid"
        else:
            status = "closed"
            if not include_closed:
                return None

        order_id = node.find("div", {"class": "tc-order"}).get_text(strip=True).lstrip("#")
        desc_node = node.find("div", {"class": "order-desc"})
        description = desc_node.find("div").get_text(strip=True) if desc_node else ""
        price_node = node.find("div", {"class": "tc-price"})
        price_text = price_node.get_text(strip=True) if price_node else "0"
        parts = price_text.rsplit(maxsplit=1)
        price = float(parts[0].replace(" ", "").replace("\u2009", "").replace(",", ".")) \
            if parts and parts[0] else 0.0
        currency = parts[1] if len(parts) == 2 else "RUB"
        buyer_span = node.find("div", {"class": "media-user-name"}).find("span")
        buyer_username = buyer_span.get_text(strip=True)
        href = buyer_span.get("data-href", "")
        buyer_id = int(href.rstrip("/").split("/users/")[-1]) if "/users/" in href else 0
        date_node = node.find("div", {"class": "tc-date-time"})
        purchased_at = parse_funpay_datetime(date_node.get_text(strip=True), now) \
            if date_node else now
        first, second = sorted([int(self.user_id or 0), buyer_id])
        return SaleOrder(
            order_id=order_id, buyer_username=buyer_username, buyer_id=buyer_id,
            description=description, price=price, currency=currency, status=status,
            purchased_at=purchased_at, chat_id=f"users-{first}-{second}",
        )

    # --------------------------------------------------------------- runner
    def runner(self, objects: list[dict], request: dict | None = None) -> dict:
        """POST /runner/ — универсальный канал обновлений и действий."""
        self.ensure_initiated()
        payload = {
            "csrf_token": self.csrf_token,
            "objects": json.dumps(objects),
        }
        if request is not None:
            payload["request"] = json.dumps(request)
        response = self.session.post(RUNNER_URL, data=payload, headers=self._headers(xhr=True),
                                     cookies=self._cookies(), timeout=self.timeout)
        self._update_session(response)
        if response.status_code in (401, 403):
            raise UnauthorizedError("Runner недоступен: сессия истекла.")
        if response.status_code == 429:
            raise FloodError("Слишком много запросов к FunPay.", wait_seconds=15)
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise GoldenKeyError("Runner вернул не-JSON ответ.") from exc

    # ----------------------------------------------------------------- чаты
    def get_chats(self, max_chats: int = 50) -> list[ChatInfo]:
        """Список своих диалогов (канал runner, объект chat_bookmarks)."""
        self.ensure_initiated()
        tag = f"{int(time.time()):08x}"
        objects = [{"type": "chat_bookmarks", "id": self.user_id, "tag": tag, "data": False}]
        result = self.runner(objects)
        html = ""
        for obj in result.get("objects", []):
            if obj.get("type") == "chat_bookmarks" and obj.get("data"):
                html = obj["data"].get("html") or ""
        if not html:
            return []
        parser = BeautifulSoup(html, "html.parser")
        chats: list[ChatInfo] = []
        for node in parser.find_all("a", {"class": "contact-item"})[:max_chats]:
            try:
                last_node = node.find("div", {"class": "contact-item-message"})
                if last_node is None:
                    continue  # чат удалён администрацией
                name_node = node.find("div", {"class": "media-user-name"})
                chats.append(ChatInfo(
                    chat_id=str(node.get("data-id")),
                    name=name_node.get_text(strip=True) if name_node else "?",
                    last_message=last_node.get_text(strip=True),
                    unread="unread" in (node.get("class") or []),
                    node_msg_id=int(node.get("data-node-msg") or 0),
                    user_msg_id=int(node.get("data-user-msg") or 0),
                ))
            except (AttributeError, ValueError, TypeError) as exc:
                log.warning("Не удалось разобрать чат: %s", exc)
        return chats

    def get_chat_history(self, chat_id: str, limit: int = 100) -> list[ChatMessage]:
        """История чата (до 100 сообщений). Возвращает в порядке возрастания ID."""
        self.ensure_initiated()
        response = self.session.get(
            f"https://funpay.com/chat/history?node={chat_id}&last_message=-1",
            headers=self._headers(xhr=True), cookies=self._cookies(), timeout=self.timeout)
        self._update_session(response)
        if response.status_code in (401, 403):
            raise UnauthorizedError("История чата недоступна: сессия истекла.")
        if response.status_code == 429:
            raise FloodError("Слишком много запросов к FunPay.", wait_seconds=15)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise GoldenKeyError("История чата вернула не-JSON ответ.") from exc
        chat = payload.get("chat") or {}
        raw_messages = chat.get("messages") or []
        messages: list[ChatMessage] = []
        for raw in raw_messages[-limit:]:
            try:
                messages.append(self._parse_chat_message(raw))
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("Не удалось разобрать сообщение %s: %s", raw.get("id"), exc)
        return messages

    def _parse_chat_message(self, raw: dict) -> ChatMessage:
        author_id = int(raw.get("author", 0))
        html = str(raw.get("html", "")).replace("<br>", "\n")
        parser = BeautifulSoup(html, "html.parser")
        author_name = ""
        author_node = parser.find("div", {"class": "media-user-name"})
        if author_node is not None:
            link = author_node.find("a")
            author_name = (link or author_node).get_text(strip=True)
        if author_id == 0:  # системное сообщение
            alert = parser.find("div", role="alert")
            text = alert.get_text(strip=True) if alert else ""
        else:
            text_node = parser.find("div", {"class": "chat-msg-text"})
            text = text_node.get_text("\n", strip=True) if text_node else ""
        return ChatMessage(message_id=int(raw.get("id", 0)), author_id=author_id,
                           author_name=author_name or ("FunPay" if author_id == 0 else "?"),
                           text=text, by_me=author_id == self.user_id)

    # -------------------------------------------------------------- отправка
    def send_chat_message(self, chat_id: str, text: str) -> dict:
        """Отправляет сообщение в чат. Уважает лимиты частоты (ждёт и повторяет)."""
        self.ensure_initiated()
        request = {"action": "chat_message",
                   "data": {"node": chat_id, "last_message": -1, "content": text}}
        objects = [{"type": "chat_bookmarks", "id": self.user_id, "tag": "00000000",
                    "data": False}]
        last_error = ""
        for attempt in range(SEND_RETRIES + 1):
            result = self.runner(objects, request=request)
            response_block = result.get("response") or {}
            error = response_block.get("error")
            if error is None:
                return result
            last_error = str(error)
            wait_match = re.search(r"(\d+)", last_error)
            wait = int(wait_match.group(1)) if wait_match else 5
            if "часто" in last_error.lower() or "frequently" in last_error.lower():
                log.warning("FunPay: лимит частоты отправки, ждём %s с (попытка %s)",
                            wait, attempt + 1)
                time.sleep(min(wait, 60))
                continue
            raise FloodError(last_error, wait_seconds=wait)
        raise FloodError(f"Сообщение не доставлено после повторов: {last_error}")

    # ------------------------------------------------------------------ лоты
    def get_own_lots(self, node_id: int | str) -> list[OwnLot]:
        """Свои лоты в подкатегории (страница /lots/<node>/trade)."""
        self.ensure_initiated()
        parser = self._get_html(f"https://funpay.com/lots/{node_id}/trade")
        lots: list[OwnLot] = []
        for node in parser.find_all("a", {"class": "tc-item"}):
            try:
                lot_id = str(node.get("data-offer"))
                if not lot_id:
                    continue
                desc_node = node.find("div", {"class": "tc-desc-text"})
                price_node = node.find("div", {"class": "tc-price"})
                price = float(price_node.get("data-s")) if price_node and price_node.get("data-s") else 0.0
                amount_node = node.find("div", {"class": "tc-amount"})
                amount_match = re.search(r"(\d+)",
                                         amount_node.get_text() if amount_node else "")
                lots.append(OwnLot(
                    lot_id=lot_id,
                    description=desc_node.get_text(strip=True) if desc_node else "",
                    price=price,
                    active="warning" not in (node.get("class") or []),
                    amount=int(amount_match.group(1)) if amount_match else None,
                ))
            except (AttributeError, ValueError, TypeError) as exc:
                log.warning("Не удалось разобрать строку лота: %s", exc)
        return lots

    def get_lot_fields(self, lot_id: int | str | None = None,
                       node_id: int | str | None = None) -> dict:
        """Поля формы лота: редактирование (``offer=``) или новый (``node=``)."""
        self.ensure_initiated()
        tail = []
        if lot_id:
            tail.append(f"offer={lot_id}")
        if node_id:
            tail.append(f"node={node_id}")
        url = "https://funpay.com/lots/offerEdit" + (f"?{'&'.join(tail)}" if tail else "")
        parser = self._get_html(url)
        error_node = parser.find("p", {"class": "lead"})
        if error_node is not None:
            raise GoldenKeyError(f"FunPay не открыл форму лота: {error_node.get_text(strip=True)}")
        form = parser.find("form", {"class": "form-offer-editor"})
        if form is None:
            raise GoldenKeyError("Форма редактирования лота не найдена на странице.")
        fields: dict[str, str] = {}
        for input_node in form.find_all("input"):
            if input_node.get("type") == "checkbox":
                continue  # невыключенные чекбоксы не отправляются; включённые — ниже
            name = input_node.get("name")
            if name:
                fields[name] = input_node.get("value") or ""
        for area in form.find_all("textarea"):
            name = area.get("name")
            if name:
                fields[name] = area.get_text()
        for select_node in form.find_all("select"):
            parent = select_node.find_parent(class_="form-group")
            if parent is not None and "hidden" in (parent.get("class") or []):
                continue
            selected = select_node.find("option", selected=True)
            if selected is not None and select_node.get("name"):
                fields[select_node["name"]] = selected.get("value") or ""
        for checkbox in form.find_all("input", {"type": "checkbox"}, checked=True):
            name = checkbox.get("name")
            if name:
                fields[name] = "on"
        if fields.get("csrf_token"):
            self.csrf_token = fields["csrf_token"]
        return fields

    def save_lot_fields(self, fields: dict) -> dict:
        """POST /lots/offerSave — сохранение лота (создание/изменение/закрытие)."""
        self.ensure_initiated()
        fields = dict(fields)
        fields["csrf_token"] = self.csrf_token or fields.get("csrf_token", "")
        response = self.session.post("https://funpay.com/lots/offerSave", data=fields,
                                     headers=self._headers(xhr=True), cookies=self._cookies(),
                                     timeout=self.timeout)
        self._update_session(response)
        if response.status_code in (401, 403):
            raise UnauthorizedError("Сохранение лота недоступно: сессия истекла.")
        if response.status_code == 429:
            raise FloodError("Слишком много запросов к FunPay.", wait_seconds=15)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as exc:
            raise GoldenKeyError("FunPay вернул не-JSON ответ при сохранении лота.") from exc
        errors = payload.get("errors")
        error = payload.get("error")
        if errors or error:
            details = "; ".join(str(message) for _, message in errors) if errors else str(error)
            raise GoldenKeyError(f"FunPay отклонил сохранение лота: {details}")
        return payload

    def create_lot(self, node_id: int | str, title: str, description: str, price: float,
                   payment_message: str = "", amount: int = 1) -> str | None:
        """Создаёт лот в подкатегории и возвращает его ID (или None, если ID не найден)."""
        fields = self.get_lot_fields(node_id=node_id)
        fields["offer_id"] = "0"
        fields["fields[summary][ru]"] = title
        fields["fields[summary][en]"] = title
        fields["fields[desc][ru]"] = description
        fields["fields[desc][en]"] = description
        if payment_message:
            fields["fields[payment_msg][ru]"] = payment_message
            fields["fields[payment_msg][en]"] = payment_message
        fields["price"] = str(price)
        fields["active"] = "on"
        if "amount" in fields or amount is not None:
            fields["amount"] = str(amount)
        self.save_lot_fields(fields)
        for lot in self.get_own_lots(node_id):
            if lot.description == title:
                return lot.lot_id
        log.warning("Лот «%s» сохранён, но его ID не найден в списке подкатегории %s",
                    title, node_id)
        return None

    def set_lot_active(self, lot_id: int | str, active: bool) -> None:
        """Активирует/деактивирует («закрывает») существующий лот."""
        fields = self.get_lot_fields(lot_id=lot_id)
        fields["offer_id"] = str(lot_id)
        fields["active"] = "on" if active else ""
        self.save_lot_fields(fields)

    def set_lot_price(self, lot_id: int | str, price: float) -> None:
        """Меняет цену лота, сохраняя остальные поля без изменений."""
        if price <= 0:
            raise GoldenKeyError("Цена лота должна быть положительной.")
        fields = self.get_lot_fields(lot_id=lot_id)
        fields["offer_id"] = str(lot_id)
        fields["price"] = str(price)
        self.save_lot_fields(fields)
