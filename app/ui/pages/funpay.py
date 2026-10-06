"""Экран «FunPay»: возможности адаптера + исходящие сообщения (ручное подтверждение)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from app.domain.enums import MessageStatus
from app.domain.value_objects import DryRunBlocked
from app.integrations.funpay.templates import MESSAGE_TEMPLATES
from app.ui.pages.base import BasePage, page_header
from app.ui.widgets import Card, add_pill_cell, fill_cell, make_table, set_row_id, selected_row_id

MESSAGE_STATUS_META = {
    "drafted": ("Черновик", "#f0b232"),
    "approved": ("Подтверждено", "#4aa8e0"),
    "sent": ("Отправлено", "#2fbf71"),
    "manual": ("Отправлено вручную", "#2fbf71"),
}


class FunPayPage(BasePage):
    title = "FunPay"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(10)
        root.addWidget(page_header(
            "FunPay",
            "Официального публичного API нет, поэтому приложение работает через адаптер возможностей. "
            "По умолчанию — безопасный ручной режим.",
        ))

        adapter_card = Card()
        adapter_layout = QVBoxLayout(adapter_card)
        adapter_title = QLabel("Адаптер и его возможности")
        adapter_title.setProperty("h2", True)
        adapter_layout.addWidget(adapter_title)
        self.capabilities_label = QLabel()
        self.capabilities_label.setWordWrap(True)
        self.capabilities_label.setTextFormat(Qt.TextFormat.RichText)
        adapter_layout.addWidget(self.capabilities_label)
        root.addWidget(adapter_card)

        warning = Card()
        warning_layout = QVBoxLayout(warning)
        warning_label = QLabel(
            "⚠ Безопасность: приложение не хранит пароли, не обходит антибот-защиту и не выполняет "
            "автоматических действий на FunPay без явного разрешения. Отправка сообщений — только после "
            "вашего подтверждения (режим «Ручное одобрение»)."
        )
        warning_label.setWordWrap(True)
        warning_layout.addWidget(warning_label)
        root.addWidget(warning)

        import_group = QGroupBox("Импорт заказов (автоматический опрос недоступен — ввод вручную)")
        import_layout = QVBoxLayout(import_group)
        import_buttons = QHBoxLayout()
        self.import_button = QPushButton("📥 Внести заказ с FunPay…")
        self.import_button.setProperty("accent", True)
        self.import_button.clicked.connect(self._import_order)
        self.poll_button = QPushButton("🔄 Опросить заказы (адаптер)")
        self.poll_button.clicked.connect(self._poll_now)
        self.check_button = QPushButton("🔌 Проверить соединение")
        self.check_button.clicked.connect(self._check_connection)
        self.link_button = QPushButton("Привязать выбранный к продукту…")
        self.link_button.clicked.connect(self._link_order)
        import_buttons.addWidget(self.import_button)
        import_buttons.addWidget(self.poll_button)
        import_buttons.addWidget(self.check_button)
        import_buttons.addWidget(self.link_button)
        import_buttons.addStretch(1)
        import_layout.addLayout(import_buttons)
        self.import_table = make_table(["ID", "№ FunPay", "Покупатель", "Лот", "Цена", "Статус"])
        self.import_table.setColumnWidth(0, 40)
        self.import_table.setColumnWidth(1, 110)
        self.import_table.setColumnWidth(2, 150)
        self.import_table.setColumnWidth(3, 260)
        self.import_table.setColumnWidth(4, 80)
        self.import_table.setMaximumHeight(150)
        import_layout.addWidget(self.import_table)
        root.addWidget(import_group)

        inbox_group = QGroupBox("Входящие сообщения (неофициальный режим)")
        inbox_layout = QVBoxLayout(inbox_group)
        inbox_buttons = QHBoxLayout()
        self.inbox_button = QPushButton("📨 Проверить сообщения")
        self.inbox_button.clicked.connect(self._poll_messages)
        inbox_buttons.addWidget(self.inbox_button)
        inbox_buttons.addStretch(1)
        inbox_layout.addLayout(inbox_buttons)
        self.inbox_table = make_table(["ID", "Клиент", "Чат", "Текст", "Статус"])
        self.inbox_table.setColumnWidth(0, 40)
        self.inbox_table.setColumnWidth(1, 140)
        self.inbox_table.setColumnWidth(2, 120)
        self.inbox_table.setColumnWidth(4, 140)
        self.inbox_table.setMaximumHeight(170)
        inbox_layout.addWidget(self.inbox_table)
        root.addWidget(inbox_group)

        lots_group = QGroupBox("Лоты (неофициальный режим: автосоздание и закрытие)")
        lots_layout = QVBoxLayout(lots_group)
        lots_buttons = QHBoxLayout()
        self.lots_plan_button = QPushButton("🗂 План синхронизации")
        self.lots_plan_button.clicked.connect(self._lots_plan)
        self.lots_sync_button = QPushButton("🗂 Синхронизировать лоты")
        self.lots_sync_button.setProperty("accent", True)
        self.lots_sync_button.clicked.connect(self._lots_sync)
        lots_buttons.addWidget(self.lots_plan_button)
        lots_buttons.addWidget(self.lots_sync_button)
        lots_buttons.addStretch(1)
        lots_layout.addLayout(lots_buttons)
        self.lots_table = make_table(
            ["ID", "Продукт", "Код", "Игра", "Лот FunPay", "Склад READY",
             "Цена лота", "Состояние"])
        self.lots_table.setColumnWidth(0, 40)
        self.lots_table.setColumnWidth(1, 190)
        self.lots_table.setColumnWidth(2, 160)
        self.lots_table.setColumnWidth(3, 130)
        self.lots_table.setColumnWidth(4, 110)
        self.lots_table.setColumnWidth(5, 100)
        self.lots_table.setColumnWidth(6, 90)
        self.lots_table.setMaximumHeight(170)
        lots_layout.addWidget(self.lots_table)
        root.addWidget(lots_group)

        messages_group = QGroupBox("Исходящие сообщения (черновики)")
        messages_layout = QVBoxLayout(messages_group)
        buttons = QHBoxLayout()
        self.draft_button = QPushButton("Сгенерировать черновик…")
        self.draft_button.setProperty("accent", True)
        self.draft_button.clicked.connect(self._draft)
        buttons.addWidget(self.draft_button)
        buttons.addStretch(1)
        messages_layout.addLayout(buttons)
        self.table = make_table(["ID", "Клиент", "Шаблон", "Текст", "Статус"])
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 160)
        self.table.setColumnWidth(2, 160)
        self.table.setColumnWidth(4, 170)
        messages_layout.addWidget(self.table)
        actions = QHBoxLayout()
        self.approve_button = QPushButton("Подтвердить")
        self.approve_button.clicked.connect(self._approve)
        self.copy_send_button = QPushButton("Отправить (по режиму)")
        self.copy_send_button.clicked.connect(self._send)
        self.mark_manual_button = QPushButton("Отправил вручную")
        self.mark_manual_button.clicked.connect(self._mark_manual)
        actions.addWidget(self.approve_button)
        actions.addWidget(self.copy_send_button)
        actions.addWidget(self.mark_manual_button)
        actions.addStretch(1)
        messages_layout.addLayout(actions)
        root.addWidget(messages_group, 1)

    def refresh(self) -> None:
        report = self.ctx.funpay.capability_report().replace("\n", "<br>")
        dry = " · <b>DRY RUN активен</b>" if self.ctx.config.dry_run else ""
        self.capabilities_label.setText(f"{report}{dry}")

        from sqlalchemy import select
        with self.ctx.db.session() as session:
            from app.database import models

            incoming = list(session.scalars(
                select(models.Message)
                .where(models.Message.direction == "in")
                .order_by(models.Message.id.desc()).limit(20)
            ))
            self.inbox_table.setRowCount(len(incoming))
            for row, message in enumerate(incoming):
                client = session.get(models.Client, message.client_id)
                set_row_id(self.inbox_table, row, message.id)
                fill_cell(self.inbox_table, row, 1, client.funpay_username if client else "?")
                fill_cell(self.inbox_table, row, 2, message.chat_id or "—")
                body_preview = message.body if len(message.body) <= 90 else message.body[:87] + "…"
                fill_cell(self.inbox_table, row, 3, body_preview.replace("\n", " "))
                add_pill_cell(self.inbox_table, row, 4, "Получено", "#4aa8e0")

        from sqlalchemy import select
        with self.ctx.db.session() as session:
            from app.database import models

            unmatched = list(session.scalars(
                select(models.Order)
                .where(models.Order.status == "problem", models.Order.product_id.is_(None))
                .order_by(models.Order.id.desc()).limit(20)
            ))
            self.import_table.setRowCount(len(unmatched))
            for row, order in enumerate(unmatched):
                client = session.get(models.Client, order.client_id)
                set_row_id(self.import_table, row, order.id)
                fill_cell(self.import_table, row, 1, order.funpay_order_id or "—")
                fill_cell(self.import_table, row, 2, client.funpay_username if client else "?")
                fill_cell(self.import_table, row, 3, (order.notes or "").splitlines()[0][:80])
                fill_cell(self.import_table, row, 4, f"{order.price:.0f} ₽", align_right=True)
                add_pill_cell(self.import_table, row, 5, "Нужна привязка", "#ef5b62")

        from sqlalchemy import func
        with self.ctx.db.session() as session:
            from app.database import models

            products = list(session.scalars(
                select(models.Product).order_by(models.Product.id)))
            lot_nodes = self.ctx.config.funpay.lot_nodes or {}
            rows = []
            for product in products:
                if product.funpay_lot_id or (product.game or "") in lot_nodes:
                    ready = session.scalar(select(func.count(models.StockUnit.id)).where(
                        models.StockUnit.product_id == product.id,
                        models.StockUnit.status == "ready")) or 0
                    rows.append((product, ready))
            self.lots_table.setRowCount(len(rows))
            for row, (product, ready) in enumerate(rows):
                set_row_id(self.lots_table, row, product.id)
                fill_cell(self.lots_table, row, 1, product.name)
                fill_cell(self.lots_table, row, 2, product.code)
                fill_cell(self.lots_table, row, 3, product.game or "—")
                fill_cell(self.lots_table, row, 4, product.funpay_lot_id or "—")
                fill_cell(self.lots_table, row, 5, str(ready), align_right=True)
                lot_price = (f"{product.funpay_lot_price:.0f} ₽"
                             if product.funpay_lot_price is not None else "—")
                fill_cell(self.lots_table, row, 6, lot_price, align_right=True)
                if product.funpay_lot_id and product.funpay_lot_active:
                    add_pill_cell(self.lots_table, row, 7, "Лот активен", "#2fbf71")
                elif product.funpay_lot_id:
                    add_pill_cell(self.lots_table, row, 7, "Лот закрыт", "#f0b232")
                elif (product.game or "") in lot_nodes:
                    add_pill_cell(self.lots_table, row, 7, "Лот не создан", "#8f959e")
                else:
                    add_pill_cell(self.lots_table, row, 7, "Нет подкатегории", "#ef5b62")

        messages = self.ctx.funpay.drafted()
        with self.ctx.db.session() as session:
            from app.database import models

            self.table.setRowCount(len(messages))
            for row, message in enumerate(messages):
                client = session.get(models.Client, message.client_id)
                set_row_id(self.table, row, message.id)
                fill_cell(self.table, row, 1, client.funpay_username if client else "?")
                fill_cell(self.table, row, 2, MESSAGE_TEMPLATES.get(message.template_key, ("?", ""))[0]
                          if message.template_key in MESSAGE_TEMPLATES else (message.template_key or "—"))
                body_preview = message.body if len(message.body) <= 90 else message.body[:87] + "…"
                fill_cell(self.table, row, 3, body_preview)
                text, color = MESSAGE_STATUS_META.get(message.status, (message.status, "#8f959e"))
                add_pill_cell(self.table, row, 4, text, color)

    # -------------------------------------------------------------- действия
    def _draft(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        with self.ctx.db.session() as session:
            from app.database import models
            from sqlalchemy import select

            clients = [c.funpay_username for c in session.scalars(select(models.Client))]
        if not clients:
            QMessageBox.information(self, "FunPay", "Сначала добавьте клиента (экран «Клиенты» или новый заказ).")
            return
        username, ok = QInputDialog.getItem(self, "Клиент", "Клиент:", clients, 0, False)
        if not ok:
            return
        template_key, ok2 = QInputDialog.getItem(
            self, "Шаблон", "Шаблон:", [MESSAGE_TEMPLATES[k][0] for k in MESSAGE_TEMPLATES], 0, False
        )
        if not ok2:
            return
        keys = list(MESSAGE_TEMPLATES)
        chosen = keys[[MESSAGE_TEMPLATES[k][0] for k in keys].index(template_key)]
        with self.ctx.db.session() as session:
            from app.database import models

            client = session.scalar(
                select(models.Client).where(models.Client.funpay_username == username)
            )
            client_id = client.id if client else None
        if client_id is None:
            return
        message = self.ctx.funpay.draft_message(
            client_id, chosen,
            client=username, order_ref="—", game="—", expires_at="—",
            eta_minutes=10, problem="—",
        )
        self.refresh()
        self._show_editor(message.id)

    def _approve(self) -> None:
        message_id = selected_row_id(self.table)
        if message_id is None:
            return
        self.ctx.funpay.approve_message(message_id)
        self.refresh()

    def _send(self) -> None:
        message_id = selected_row_id(self.table)
        if message_id is None:
            return
        try:
            result = self.ctx.funpay.send_message(message_id)
        except DryRunBlocked as exc:
            QMessageBox.information(self, "DRY RUN", str(exc))
            self.refresh()
            return
        QMessageBox.information(self, "Отправка", result)
        self.refresh()

    def _mark_manual(self) -> None:
        message_id = selected_row_id(self.table)
        if message_id is None:
            return
        self.ctx.funpay.mark_sent_manually(message_id)
        self.refresh()

    def _show_editor(self, message_id: int) -> None:
        from PySide6.QtWidgets import QDialog, QDialogButtonBox

        with self.ctx.db.session() as session:
            from app.database import models

            message = session.get(models.Message, message_id)
            if message is None:
                return
            body = message.body
        dialog = QDialog(self)
        dialog.setWindowTitle("Текст сообщения (отредактируйте перед отправкой)")
        dialog.resize(560, 320)
        layout = QVBoxLayout(dialog)
        editor = QPlainTextEdit(body)
        layout.addWidget(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            with self.ctx.db.session() as session:
                from app.database import models

                message = session.get(models.Message, message_id)
                message.body = editor.toPlainText()

    # --------------------------------------------------------------- импорт
    def _poll_now(self) -> None:
        from app.domain.enums import Capability

        adapter = self.ctx.funpay.adapter
        if adapter.capability("read_only_polling") != Capability.AVAILABLE:
            QMessageBox.information(
                self, "Опрос FunPay",
                "Автоматический опрос недоступен в текущем режиме.\n"
                "Включите неофициальный доступ и сохраните golden_key в настройках, "
                "или вносите заказы вручную.")
            return
        orders = self.ctx.funpay.poll_orders()
        if not orders:
            QMessageBox.information(self, "Опрос FunPay", "Новых данных нет (или опрос не удался).")
            self.refresh()
            return
        result = self.ctx.importer.import_orders(orders)
        synced = self.ctx.importer.sync_order_statuses(orders)
        QMessageBox.information(
            self, "Опрос FunPay",
            f"Итог: {result.summary}\n"
            f"Статусы: закрыто {synced['completed']}, возвратов {synced['refunded']}.")
        self.refresh()

    def _poll_messages(self) -> None:
        result = self.ctx.funpay.poll_messages()
        if not result.get("received") and not result.get("replies_sent") \
                and not result.get("reply_drafts"):
            QMessageBox.information(
                self, "Сообщения",
                "Ничего нового (или чтение чатов недоступно в текущем режиме).")
        else:
            QMessageBox.information(
                self, "Сообщения",
                f"Получено: {result['received']}. "
                f"Автоответов отправлено: {result['replies_sent']}, "
                f"черновиков на подтверждение: {result['reply_drafts']}.")
        self.refresh()

    # ------------------------------------------------------------------ лоты
    @staticmethod
    def _format_plan(plan: dict) -> str:
        lines = []
        for info in plan.get("create", []):
            lines.append(f"+ Создать: {info['code']} ({info['name']}) → подкатегория "
                         f"{info['node']}, цена {info['price']:.0f}")
        for info in plan.get("open", []):
            lines.append(f"↑ Открыть заново: {info['code']} (лот {info['lot_id']})")
        for info in plan.get("close", []):
            lines.append(f"✕ Закрыть: {info['code']} (лот {info['lot_id']})")
        for info in plan.get("reprice", []):
            old_price = info.get("lot_price")
            old_text = f"{old_price:.0f}" if old_price is not None else "?"
            lines.append(f"₽ Цена: {info['code']}: {old_text} → {info['price']:.0f} "
                         f"(лот {info['lot_id']})")
        for info in plan.get("blocked_below_min", []):
            lines.append(f"⛔ Ниже минимальной: {info['code']} — цена {info['price']:.0f}; "
                         "лот не трогаем, поправьте цену продукта")
        for info in plan.get("skipped_no_node", []):
            lines.append(f"… Пропущено: {info['code']} — не задана подкатегория в настройках")
        return "\n".join(lines)

    def _lots_plan(self) -> None:
        plan = self.ctx.funpay.plan_listing_sync()
        text = self._format_plan(plan) or "Действий не требуется: лоты соответствуют продуктам."
        QMessageBox.information(self, "План синхронизации лотов", text)

    def _lots_sync(self) -> None:
        plan = self.ctx.funpay.plan_listing_sync()
        total = (len(plan["create"]) + len(plan["open"]) + len(plan["close"])
                 + len(plan["reprice"]))
        if total == 0:
            QMessageBox.information(self, "Лоты", "Действий не требуется: лоты соответствуют продуктам.")
            return
        reply = QMessageBox.question(
            self, "Синхронизация лотов",
            "Планируется:\n" + self._format_plan(plan) + "\n\n"
            "Лоты будут созданы/открыты/закрыты на FunPay. Продолжить?")
        if reply != QMessageBox.StandardButton.Yes:
            return
        result = self.ctx.funpay.sync_listings(confirmed=True)
        if result.get("dry_run"):
            QMessageBox.information(
                self, "Лоты", "[DRY RUN] Изменения НЕ применены.\n" + self._format_plan(plan))
        else:
            errors = "\n".join(result.get("errors", [])) or "Без ошибок."
            QMessageBox.information(
                self, "Лоты",
                f"Создано: {result['created']}, открыто: {result['opened']}, "
                f"закрыто: {result['closed']}, цен обновлено: {result['repriced']}.\n{errors}")
        self.refresh()

    def _check_connection(self) -> None:
        result = self.ctx.funpay.check_connection()
        if result.get("ok"):
            QMessageBox.information(
                self, "Соединение с FunPay",
                f"Доступ работает.\nАккаунт: {result.get('username')} "
                f"(ID {result.get('user_id')}).")
        else:
            QMessageBox.warning(self, "Соединение с FunPay", result.get("message", "Ошибка."))

    def _import_order(self) -> None:
        from datetime import datetime, timezone

        from PySide6.QtWidgets import QDialog, QFormLayout, QLineEdit, QDoubleSpinBox

        from app.integrations.funpay.base import FunPayOrderDTO

        dialog = QDialog(self)
        dialog.setWindowTitle("Заказ с FunPay (перенос вручную)")
        dialog.setMinimumWidth(460)
        form = QFormLayout(dialog)
        external_id = QLineEdit()
        external_id.setPlaceholderText("Например, FXX1234567")
        buyer = QLineEdit()
        lot_title = QLineEdit()
        lot_title.setPlaceholderText("Название лота — по нему найдём продукт (можно код: PZ_CONFIG_BEGINNER)")
        price = QDoubleSpinBox()
        price.setRange(0, 10**7)
        form.addRow("№ заказа", external_id)
        form.addRow("Покупатель", buyer)
        form.addRow("Лот", lot_title)
        form.addRow("Цена, ₽", price)
        from PySide6.QtWidgets import QDialogButtonBox

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if not dialog.exec():
            return
        if not external_id.text().strip() or not buyer.text().strip():
            QMessageBox.warning(self, "Импорт", "Нужны номер заказа и покупатель.")
            return
        dto = FunPayOrderDTO(
            external_id=external_id.text().strip(),
            buyer_username=buyer.text().strip(),
            lot_title=lot_title.text().strip(),
            price=price.value(),
            currency="RUB",
            purchased_at=datetime.now(timezone.utc),
        )
        outcome = self.ctx.importer.import_order(dto)
        if outcome == "imported":
            QMessageBox.information(self, "Импорт", "Заказ импортирован и воркфлоу запущен (если включён).")
        elif outcome == "duplicated":
            QMessageBox.information(self, "Импорт", "Этот заказ уже был импортирован ранее.")
        else:
            QMessageBox.warning(self, "Импорт",
                                "Продукт не найден по названию лота. Заказ сохранён — привяжите вручную.")
        self.refresh()

    def _link_order(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        order_id = selected_row_id(self.import_table)
        if order_id is None:
            QMessageBox.information(self, "Привязка", "Выберите непривязанный заказ в таблице выше.")
            return
        codes = [product.code for product in self.ctx.products.all(active_only=True)]
        if not codes:
            QMessageBox.warning(self, "Привязка", "Нет активных продуктов.")
            return
        code, ok = QInputDialog.getItem(self, "Продукт", "Привязать к продукту:", codes, 0, False)
        if not ok:
            return
        try:
            order = self.ctx.importer.link_order_to_product(order_id, code)
        except ValueError as exc:
            QMessageBox.warning(self, "Привязка", str(exc))
            return
        QMessageBox.information(self, "Привязка",
                                f"Заказ заменён новым (ID {order.id}) и передан в воркфлоу.")
        self.refresh()
