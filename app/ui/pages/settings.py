"""Экран «Настройки»: режимы, секреты (только через системное хранилище),
коэффициенты спроса, интервалы планировщика."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.app_context import (
    STEAM_API_SECRET,
    TWITCH_CLIENT_ID_SECRET,
    TWITCH_CLIENT_SECRET_SECRET,
)
from app.integrations.funpay.golden_key_adapter import GOLDEN_KEY_SECRET
from app.ui.pages.base import BasePage, page_header


class SettingsPage(BasePage):
    title = "Настройки"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(8)
        root.addWidget(page_header("Настройки", "Все коэффициенты и режимы. Секреты хранятся в Windows Credential Manager (keyring)."))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        # ---------------- Режим безопасности ----------------
        safe_box = QGroupBox("Режим работы")
        safe_layout = QVBoxLayout(safe_box)
        self.dry_run = QCheckBox("DRY RUN: только чтение, анализ и превью; никаких внешних изменений")
        self.dry_run.setChecked(ctx.config.dry_run)
        safe_layout.addWidget(self.dry_run)
        self.message_mode = QComboBox()
        self.message_mode.addItem("Ручное одобрение сообщений (рекомендуется)", "manual_approval")
        self.message_mode.addItem("Автоотправка безопасных шаблонов (если разрешено адаптером)", "auto_safe")
        index = self.message_mode.findData(ctx.config.funpay.message_mode)
        self.message_mode.setCurrentIndex(max(0, index))
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Сообщения:"))
        mode_row.addWidget(self.message_mode, 1)
        safe_layout.addLayout(mode_row)
        self.automation_mode = QComboBox()
        self.automation_mode.addItem("АВТОМАТИЗАЦИЯ ВЫКЛЮЧЕНА — всё вручную", "off")
        self.automation_mode.addItem("РЕЖИМ ПОМОЩНИКА — внешние действия с подтверждением", "assisted")
        self.automation_mode.addItem("АВТО БЕЗ ОПАСНЫХ ДЕЙСТВИЙ — только разрешённые шаги", "auto_safe")
        auto_index = self.automation_mode.findData(ctx.config.automation_mode)
        self.automation_mode.setCurrentIndex(max(0, auto_index))
        auto_row = QHBoxLayout()
        auto_row.addWidget(QLabel("Режим автоматизации:"))
        auto_row.addWidget(self.automation_mode, 1)
        safe_layout.addLayout(auto_row)
        layout.addWidget(safe_box)

        # ---------------- Steam ----------------
        steam_box = QGroupBox("Steam")
        steam_form = QFormLayout(steam_box)
        self.steam_mode = QComboBox()
        self.steam_mode.addItem("Симуляция (офлайн демо-данные)", "simulation")
        self.steam_mode.addItem("Live: официальный Steam Web API", "live")
        steam_index = self.steam_mode.findData(ctx.config.steam.mode)
        self.steam_mode.setCurrentIndex(max(0, steam_index))
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText("Введите ключ и нажмите «Сохранить» — сохранится в keyring")
        if ctx.secrets.has(STEAM_API_SECRET):
            self.api_key.setPlaceholderText("Ключ сохранён в системном хранилище (••••••)")
        self.sync_interval = QSpinBox()
        self.sync_interval.setRange(10, 24 * 60)
        self.sync_interval.setValue(ctx.config.steam.sync_interval_min)
        self.sync_interval.setSuffix(" мин")
        steam_form.addRow("Режим источника", self.steam_mode)
        steam_form.addRow("Steam Web API ключ", self.api_key)
        steam_form.addRow("Интервал синхронизации", self.sync_interval)
        note = QLabel("Ключ получаете на steamcommunity.com/dev/apikey. В БД хранится только ссылка на keyring.")
        note.setProperty("muted", True)
        note.setWordWrap(True)
        steam_form.addRow(note)
        layout.addWidget(steam_box)

        # ---------------- FunPay ----------------
        funpay_box = QGroupBox("FunPay (неофициальные возможности — под вашу ответственность)")
        funpay_form = QFormLayout(funpay_box)
        self.funpay_mode = QComboBox()
        self.funpay_mode.addItem("Ручной режим (безопасно, по умолчанию)", "manual")
        self.funpay_mode.addItem("Golden key (неофициально, только чтение в будущем)", "golden_key")
        fp_index = self.funpay_mode.findData(ctx.config.funpay.mode)
        self.funpay_mode.setCurrentIndex(max(0, fp_index))
        self.unofficial_allowed = QCheckBox("Разрешить неофициальный доступ к своему аккаунту")
        self.unofficial_allowed.setChecked(ctx.config.funpay.unofficial_allowed)
        self.golden_key = QLineEdit()
        self.golden_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.golden_key.setPlaceholderText("golden_key (никогда не хранится в БД)")
        if ctx.secrets.has(GOLDEN_KEY_SECRET):
            self.golden_key.setPlaceholderText("golden_key сохранён в системном хранилище (••••••)")
        self.poll_interval = QSpinBox()
        self.poll_interval.setRange(5, 24 * 60)
        self.poll_interval.setValue(ctx.config.funpay.poll_interval_min)
        self.poll_interval.setSuffix(" мин")
        self.user_agent = QLineEdit()
        self.user_agent.setPlaceholderText("User-Agent браузера, где выполнен вход на FunPay (для golden_key)")
        self.user_agent.setText(ctx.config.funpay.user_agent)
        funpay_form.addRow("Режим адаптера", self.funpay_mode)
        funpay_form.addRow(self.unofficial_allowed)
        funpay_form.addRow("Golden key", self.golden_key)
        funpay_form.addRow("User-Agent", self.user_agent)
        funpay_form.addRow("Интервал опроса", self.poll_interval)
        ua_note = QLabel(
            "Неофициальный режим: чтение своих заказов и автовыдача в чат. Включается только "
            "вместе с галкой выше; лимиты частоты уважаются; ключ — только в системном хранилище.")
        ua_note.setProperty("muted", True)
        ua_note.setWordWrap(True)
        funpay_form.addRow(ua_note)
        from PySide6.QtWidgets import QPlainTextEdit

        self.auto_replies = QPlainTextEdit()
        self.auto_replies.setPlaceholderText(
            "команда = текст ответа (по строке на правило)\n"
            "Например: !статус = Заказ в работе, напишу, как будет готово.")
        self.auto_replies.setFixedHeight(70)
        lines = [f"{command} = {reply}"
                 for command, reply in (ctx.config.funpay.auto_replies or {}).items()]
        self.auto_replies.setPlainText("\n".join(lines))
        funpay_form.addRow("Автоответы на команды", self.auto_replies)
        replies_note = QLabel(
            "Автоответ уходит сам только в режиме сообщений «авто-безопасный» без DRY RUN; "
            "иначе создаётся черновик на подтверждение.")
        replies_note.setProperty("muted", True)
        replies_note.setWordWrap(True)
        funpay_form.addRow(replies_note)
        self.lot_auto_sync = QCheckBox(
            "Автосинхронизация лотов при опросе (только АВТО-режим без DRY RUN)")
        self.lot_auto_sync.setChecked(ctx.config.funpay.lot_auto_sync)
        self.lot_close_when_empty = QCheckBox(
            "Закрывать лот, если нет готового товара на складе")
        self.lot_close_when_empty.setChecked(ctx.config.funpay.lot_close_when_empty)
        self.lot_payment_message = QLineEdit()
        self.lot_payment_message.setText(ctx.config.funpay.lot_payment_message)
        self.lot_payment_message.setPlaceholderText(
            "Сообщение покупателю после оплаты (для новых лотов)")
        self.lot_nodes = QPlainTextEdit()
        self.lot_nodes.setPlaceholderText(
            "Имя игры = ID подкатегории FunPay (по строке на игру)\n"
            "Например: Project Zomboid = 1701")
        self.lot_nodes.setFixedHeight(70)
        node_lines = [f"{game} = {node}"
                      for game, node in (ctx.config.funpay.lot_nodes or {}).items()]
        self.lot_nodes.setPlainText("\n".join(node_lines))
        self.lot_price_tolerance = QDoubleSpinBox()
        self.lot_price_tolerance.setRange(0.0, 50.0)
        self.lot_price_tolerance.setDecimals(1)
        self.lot_price_tolerance.setSuffix(" %")
        self.lot_price_tolerance.setValue(ctx.config.funpay.lot_price_tolerance_pct)
        self.lot_price_tolerance.setToolTip(
            "Насколько цена лота может отличаться от цены продукта, прежде чем "
            "синхронизация её обновит. 0 % — обновлять при любом изменении.")
        funpay_form.addRow(self.lot_auto_sync)
        funpay_form.addRow(self.lot_close_when_empty)
        funpay_form.addRow("Сообщение после оплаты", self.lot_payment_message)
        funpay_form.addRow("Допуск цены лота", self.lot_price_tolerance)
        funpay_form.addRow("Подкатегории лотов", self.lot_nodes)
        lots_note = QLabel(
            "ID подкатегории — из адресной строки списка лотов: /lots/<ID>/trade. "
            "Создание/закрытие лотов — только в неофициальном режиме и после вашего "
            "подтверждения; лимиты частоты уважаются, лоты не удаляются (только деактивация).")
        lots_note.setProperty("muted", True)
        lots_note.setWordWrap(True)
        funpay_form.addRow(lots_note)
        layout.addWidget(funpay_box)

        # ---------------- Twitch ----------------
        twitch_box = QGroupBox("Twitch (только официальный Helix, свои креды разработчика)")
        twitch_form = QFormLayout(twitch_box)
        self.twitch_client_id = QLineEdit()
        self.twitch_client_id.setEchoMode(QLineEdit.EchoMode.Password)
        self.twitch_client_id.setPlaceholderText("Client ID своего приложения (сохранится в keyring)")
        if ctx.secrets.has(TWITCH_CLIENT_ID_SECRET):
            self.twitch_client_id.setPlaceholderText("Client ID сохранён в системном хранилище (••••••)")
        self.twitch_client_secret = QLineEdit()
        self.twitch_client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.twitch_client_secret.setPlaceholderText("Client Secret (сохранится в keyring)")
        if ctx.secrets.has(TWITCH_CLIENT_SECRET_SECRET):
            self.twitch_client_secret.setPlaceholderText("Client Secret сохранён в системном хранилище (••••••)")
        twitch_form.addRow("Client ID", self.twitch_client_id)
        twitch_form.addRow("Client Secret", self.twitch_client_secret)
        twitch_note = QLabel(
            "Используется для публичных данных: игры и стримы (поиск каналов с дропами). "
            "Прогресс дропов и клэйм официальной API не доступны — только вручную (см. Матрицу возможностей).")
        twitch_note.setProperty("muted", True)
        twitch_note.setWordWrap(True)
        twitch_form.addRow(twitch_note)
        layout.addWidget(twitch_box)

        # ---------------- Коэффициенты ----------------
        weights_box = QGroupBox("Коэффициенты Demand Score")
        weights_form = QFormLayout(weights_box)
        self.weight_editors: dict[str, QDoubleSpinBox] = {}
        weights = ctx.config.demand_weights
        for key, label in [
            ("popularity", "Популярность"), ("reviews", "Отзывы"), ("price", "Цена"),
            ("recency", "Актуальность"), ("multiplayer", "Мультиплеер/кооп"),
            ("trend", "Тренд"), ("marketplace", "История продаж"),
        ]:
            editor = QDoubleSpinBox()
            editor.setRange(0.0, 1.0)
            editor.setSingleStep(0.05)
            editor.setValue(getattr(weights, key))
            self.weight_editors[key] = editor
            weights_form.addRow(label, editor)
        layout.addWidget(weights_box)

        # ---------------- Планировщик ----------------
        sched_box = QGroupBox("Планировщик")
        sched_form = QFormLayout(sched_box)
        sched = ctx.config.scheduler
        self.lease_interval = QSpinBox(); self.lease_interval.setRange(1, 240); self.lease_interval.setValue(sched.lease_check_interval_min); self.lease_interval.setSuffix(" мин")
        self.warn_hours = QSpinBox(); self.warn_hours.setRange(1, 240); self.warn_hours.setValue(sched.lease_expiry_warn_hours); self.warn_hours.setSuffix(" ч")
        self.demand_interval = QSpinBox(); self.demand_interval.setRange(1, 240); self.demand_interval.setValue(sched.demand_recalc_interval_hours); self.demand_interval.setSuffix(" ч")
        self.backup_interval = QSpinBox(); self.backup_interval.setRange(1, 240); self.backup_interval.setValue(sched.backup_interval_hours); self.backup_interval.setSuffix(" ч")
        sched_form.addRow("Проверка аренд", self.lease_interval)
        sched_form.addRow("Предупреждать об окончании за", self.warn_hours)
        sched_form.addRow("Пересчёт Demand", self.demand_interval)
        sched_form.addRow("Резервная копия БД", self.backup_interval)
        layout.addWidget(sched_box)

        # ---------------- Кнопки ----------------
        buttons = QHBoxLayout()
        self.save_button = QPushButton("Сохранить и применить")
        self.save_button.setProperty("accent", True)
        self.save_button.clicked.connect(self._save)
        self.open_data_button = QPushButton("Открыть папку данных")
        self.open_data_button.clicked.connect(self._open_data_dir)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.open_data_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)

        scroll.setWidget(body)
        root.addWidget(scroll)

    # ------------------------------------------------------------------ save
    def _save(self) -> None:
        config = self.ctx.config
        dry_run_was = config.dry_run

        config.dry_run = self.dry_run.isChecked()
        config.automation_mode = self.automation_mode.currentData()
        config.funpay.message_mode = self.message_mode.currentData()
        config.steam.mode = self.steam_mode.currentData()
        config.steam.sync_interval_min = self.sync_interval.value()
        config.funpay.mode = self.funpay_mode.currentData()
        config.funpay.unofficial_allowed = self.unofficial_allowed.isChecked()
        config.funpay.poll_interval_min = self.poll_interval.value()
        config.funpay.user_agent = self.user_agent.text().strip()
        auto_replies: dict = {}
        for line in self.auto_replies.toPlainText().splitlines():
            if "=" not in line:
                continue
            command, _, reply = line.partition("=")
            command, reply = command.strip(), reply.strip()
            if command and reply:
                auto_replies[command] = reply
        config.funpay.auto_replies = auto_replies
        lot_nodes: dict = {}
        for line in self.lot_nodes.toPlainText().splitlines():
            if "=" not in line:
                continue
            game, _, node = line.partition("=")
            game, node = game.strip(), node.strip()
            if game and node.isdigit():
                lot_nodes[game] = int(node)
        config.funpay.lot_nodes = lot_nodes
        config.funpay.lot_auto_sync = self.lot_auto_sync.isChecked()
        config.funpay.lot_close_when_empty = self.lot_close_when_empty.isChecked()
        config.funpay.lot_payment_message = self.lot_payment_message.text().strip()
        config.funpay.lot_price_tolerance_pct = self.lot_price_tolerance.value()

        api_key = self.api_key.text().strip()
        if api_key:
            self.ctx.secrets.set(STEAM_API_SECRET, api_key)
            config.steam.api_key_ref = self.ctx.secrets.ref(STEAM_API_SECRET)
            self.api_key.clear()
        golden_key = self.golden_key.text().strip()
        if golden_key:
            self.ctx.secrets.set(GOLDEN_KEY_SECRET, golden_key)
            config.funpay.golden_key_ref = self.ctx.secrets.ref(GOLDEN_KEY_SECRET)
            self.golden_key.clear()

        twitch_id = self.twitch_client_id.text().strip()
        if twitch_id:
            self.ctx.secrets.set(TWITCH_CLIENT_ID_SECRET, twitch_id)
            config.twitch.client_id_ref = self.ctx.secrets.ref(TWITCH_CLIENT_ID_SECRET)
            self.twitch_client_id.clear()
        twitch_secret = self.twitch_client_secret.text().strip()
        if twitch_secret:
            self.ctx.secrets.set(TWITCH_CLIENT_SECRET_SECRET, twitch_secret)
            config.twitch.client_secret_ref = self.ctx.secrets.ref(TWITCH_CLIENT_SECRET_SECRET)
            self.twitch_client_secret.clear()

        weights = config.demand_weights
        for key, editor in self.weight_editors.items():
            setattr(weights, key, editor.value())

        sched = config.scheduler
        sched.lease_check_interval_min = self.lease_interval.value()
        sched.lease_expiry_warn_hours = self.warn_hours.value()
        sched.demand_recalc_interval_hours = self.demand_interval.value()
        sched.backup_interval_hours = self.backup_interval.value()

        from app.core.config import CONFIG_FILE_NAME

        config.save(self.ctx.data_dir / CONFIG_FILE_NAME)
        self.ctx.audit.log("settings.changed", entity="settings",
                           dry_run=config.dry_run, steam_mode=config.steam.mode,
                           funpay_mode=config.funpay.mode)
        self.ctx.rebuild_steam_source()
        if hasattr(self.ctx, "on_settings_applied"):
            self.ctx.on_settings_applied()
        QMessageBox.information(
            self, "Настройки",
            "Сохранено." + (" Внимание: DRY RUN выключен — изменения во внешних сервисах разрешены."
                            if dry_run_was and not config.dry_run else ""),
        )
        self.refresh()

    def _open_data_dir(self) -> None:
        import os
        import subprocess
        import sys

        path = str(self.ctx.data_dir.resolve())
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError as exc:
            QMessageBox.warning(self, "Настройки", f"Не удалось открыть папку: {exc}")

    def refresh(self) -> None:
        config = self.ctx.config
        self.dry_run.setChecked(config.dry_run)
        if self.ctx.secrets.has(STEAM_API_SECRET):
            self.api_key.setPlaceholderText("Ключ сохранён в системном хранилище (••••••)")
        if self.ctx.secrets.has(GOLDEN_KEY_SECRET):
            self.golden_key.setPlaceholderText("golden_key сохранён в системном хранилище (••••••)")
