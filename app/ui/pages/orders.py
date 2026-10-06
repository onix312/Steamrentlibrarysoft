"""Экран «Заказы»: создание, статусы, привязка лицензий."""
from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)
from sqlalchemy import select

from app.database import models
from app.domain.enums import EligibilityStatus, OrderStatus
from app.domain.value_objects import DomainError
from app.services.access_service import LicenseAllocator
from app.ui.dialogs import NewOrderDialog
from app.ui.pages.base import BasePage, page_header, toolbar_row
from app.ui.theme import ORDER_STATUS_META
from app.ui.widgets import add_pill_cell, fill_cell, make_table, set_row_id, selected_row_id


class OrdersPage(BasePage):
    title = "Заказы"

    def __init__(self, ctx, parent=None) -> None:
        super().__init__(ctx, parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(8)
        root.addWidget(page_header(
            "Заказы",
            "Заказы с FunPay (вручную или импорт) и их жизненный цикл. Лицензии назначает LicenseAllocator.",
        ))

        self.new_button = QPushButton("Новый заказ")
        self.new_button.setProperty("accent", True)
        self.new_button.clicked.connect(self._new_order)
        self.status_filter = QComboBox()
        self.status_filter.addItem("Все статусы", "")
        for status in OrderStatus:
            self.status_filter.addItem(ORDER_STATUS_META[status.value][0], status.value)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        self.count_label = QLabel("")
        self.count_label.setProperty("muted", True)
        root.addWidget(toolbar_row(self.new_button, self.status_filter, self.count_label))

        self.table = make_table([
            "ID", "FunPay ID", "Клиент", "Позиция", "Продукт ОС", "Воркфлоу",
            "Цена", "Куплен", "Доступ до", "Статус",
        ])
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(2, 130)
        self.table.setColumnWidth(3, 200)
        self.table.setColumnWidth(4, 160)
        self.table.setColumnWidth(5, 120)
        self.table.setColumnWidth(9, 140)
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.activate_button = QPushButton("Выдать доступ (создать аренду)")
        self.activate_button.clicked.connect(self._create_lease_for_order)
        self.deliver_button = QPushButton("Подтвердить выдачу")
        self.deliver_button.clicked.connect(self._confirm_delivery)
        self.link_button = QPushButton("Привязать к продукту…")
        self.link_button.clicked.connect(self._link_product)
        self.complete_button = QPushButton("Завершить")
        self.complete_button.clicked.connect(lambda: self._set_status(OrderStatus.COMPLETED))
        self.cancel_button = QPushButton("Отменить")
        self.cancel_button.setProperty("danger", True)
        self.cancel_button.clicked.connect(lambda: self._set_status(OrderStatus.CANCELLED))
        self.problem_button = QPushButton("Отметить проблему")
        self.problem_button.clicked.connect(lambda: self._set_status(OrderStatus.PROBLEM))
        actions.addWidget(self.activate_button)
        actions.addWidget(self.deliver_button)
        actions.addWidget(self.link_button)
        actions.addWidget(self.complete_button)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.problem_button)
        actions.addStretch(1)
        root.addLayout(actions)

    def refresh(self) -> None:
        status_filter = self.status_filter.currentData()
        orders = self.ctx.orders.all()
        if status_filter:
            orders = [order for order in orders if order.status == status_filter]
        with self.ctx.db.session() as session:
            self.table.setRowCount(len(orders))
            for row, order in enumerate(orders):
                client = session.get(models.Client, order.client_id)
                game = session.get(models.Game, order.game_id) if order.game_id else None
                bundle = session.get(models.Bundle, order.bundle_id) if order.bundle_id else None
                product = session.get(models.Product, order.product_id) if order.product_id else None
                run = session.get(models.WorkflowRun, order.workflow_run_id) \
                    if order.workflow_run_id else None
                item_label = game.name if game else (f"Пакет: {bundle.name}" if bundle else "—")
                if product is not None:
                    item_label = product.name
                set_row_id(self.table, row, order.id)
                fill_cell(self.table, row, 1, order.funpay_order_id or "ручной")
                fill_cell(self.table, row, 2, client.funpay_username if client else "?")
                fill_cell(self.table, row, 3, item_label)
                fill_cell(self.table, row, 4, product.code if product else "—")
                fill_cell(self.table, row, 5, run.status if run else "—")
                fill_cell(self.table, row, 6, f"{order.price:.0f} ₽", align_right=True)
                fill_cell(self.table, row, 7, order.purchased_at.strftime("%d.%m.%Y %H:%M"))
                ends = order.access_ends_at.strftime("%d.%m.%Y") if order.access_ends_at else "—"
                fill_cell(self.table, row, 8, ends)
                text, color = ORDER_STATUS_META.get(order.status, (order.status, "#8f959e"))
                add_pill_cell(self.table, row, 9, text, color)
        self.count_label.setText(f"{len(orders)} заказов")

    # -------------------------------------------------------------- действия
    def _new_order(self) -> None:
        games_options = self._games_options()
        if not games_options:
            QMessageBox.information(
                self, "Заказы",
                "Нет продаваемых игр со свободными копиями.\nСначала синхронизируйте Steam и проверьте eligibility.",
            )
            return
        clients = [c.funpay_username for c in self._clients()]
        dialog = NewOrderDialog(games_options, clients, self)
        if not dialog.exec():
            return
        values = dialog.values()
        if not values["client"]:
            QMessageBox.warning(self, "Заказы", "Укажите клиента (ник на FunPay).")
            return
        try:
            order = self.ctx.orders.create_order(
                client_username=values["client"],
                game_id=values["game_id"],
                price=values["price"],
                access_days=values["days"],
                funpay_order_id=values["funpay_id"],
                auto_assign=values["auto_assign"],
            )
        except DomainError as exc:
            QMessageBox.warning(self, "Заказы", str(exc))
            return
        self.refresh()
        if order.status == OrderStatus.ACTIVE.value:
            QMessageBox.information(
                self, "Заказ создан",
                f"Заказ #{order.id} создан, лицензия #{order.assigned_license_id} назначена.\n"
                "Следующий шаг вручную: добавьте клиента в семью в Steam и отметьте «Доступ выдан».",
            )
        else:
            QMessageBox.warning(
                self, "Заказ создан без лицензии",
                f"Заказ #{order.id} создан в статусе «{order.status}». "
                "Свободных копий нет — лицензия будет назначена, когда копия освободится (кнопка «Выдать доступ»).",
            )

    def _create_lease_for_order(self) -> None:
        order_id = selected_row_id(self.table)
        if order_id is None:
            return
        order = self.ctx.orders.get(order_id)
        if order is None or order.game_id is None:
            QMessageBox.information(self, "Заказы", "У заказа нет игры (пакеты пока выдаются вручную по каждой игре).")
            return
        if order.assigned_license_id:
            QMessageBox.information(self, "Заказы", "Лицензия уже назначена на этот заказ.")
            return
        try:
            lease = self.ctx.access.create_lease(
                client_id=order.client_id, game_id=order.game_id,
                days=order.access_days or 30, order_id=order.id,
            )
        except DomainError as exc:
            QMessageBox.warning(self, "Заказы", str(exc))
            return
        self.refresh()
        QMessageBox.information(self, "Доступ выдан",
                                f"Аренда #{lease.id} создана, лицензия #{lease.license_id}, "
                                f"аккаунт-владелец #{lease.owner_account_id}.")

    def _set_status(self, status: OrderStatus) -> None:
        order_id = selected_row_id(self.table)
        if order_id is None:
            return
        self.ctx.orders.set_status(order_id, status)
        self.refresh()

    def _confirm_delivery(self) -> None:
        """Оператор фактически выдал товар (для заказов ОС)."""
        order_id = selected_row_id(self.table)
        if order_id is None:
            return
        self.ctx.sales.confirm_delivery(order_id)
        self.refresh()

    def _link_product(self) -> None:
        """Привязка непривязанного заказа к продукту ОС (запуск воркфлоу)."""
        from PySide6.QtWidgets import QInputDialog

        order_id = selected_row_id(self.table)
        if order_id is None:
            return
        codes = [product.code for product in self.ctx.products.all(active_only=True)]
        if not codes:
            QMessageBox.warning(self, "Привязка", "Нет активных продуктов.")
            return
        code, ok = QInputDialog.getItem(self, "Продукт", "Привязать заказ к продукту:", codes, 0, False)
        if not ok:
            return
        try:
            order = self.ctx.importer.link_order_to_product(order_id, code)
        except ValueError as exc:
            QMessageBox.warning(self, "Привязка", str(exc))
            return
        QMessageBox.information(self, "Привязка",
                                f"Создан связанный заказ #{order.id}, воркфлоу запущен (если включено).")
        self.refresh()

    # -------------------------------------------------------------- helpers
    def _games_options(self) -> list[tuple[int, str, int]]:
        with self.ctx.db.session() as session:
            games = session.scalars(
                select(models.Game).where(
                    models.Game.is_free.is_(False),
                    models.Game.eligibility_status == EligibilityStatus.AVAILABLE.value,
                ).order_by(models.Game.name)
            ).all()
            allocator = LicenseAllocator(session, self.ctx.clock)
            options = []
            for game in games:
                availability = allocator.availability(game.id)
                if availability.total:
                    options.append((game.id, game.name, availability.free))
            return options

    def _clients(self):
        from app.database import repositories

        with self.ctx.db.session() as session:
            return list(repositories.ClientRepo(session).all())
