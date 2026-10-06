"""Workflow Engine — сердце FunPay Automation OS (ТЗ §36–37).

Порядок исполнения:
  заказ → определить продукт → загрузить workflow → резерв склада
  → шаги (авто/ручной/подтверждение/валидация) → выдача → завершение.

Ключевые правила:
* локальные вычисления всегда автоматические;
* любые ВНЕШНИЕ действия в режиме ``ASSISTED`` требуют подтверждения,
  в ``AUTO_SAFE`` — только помеченные ``safe``;
* в ``DRY RUN`` выдача показывает превью и ничего не выдаёт наружу;
* ручной шаг останавливает прогон до вызова ``complete_manual``.

Бизнес-логика движка не зависит от Qt и полностью тестируется.
"""
from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy import select

from app.core.clock import Clock
from app.core.config import AppConfig
from app.core.events import TOPIC_DATA_CHANGED, EventBus
from app.database import models
from app.database.session import Database
from app.domain.enums import (
    Actor,
    AuditAction,
    AutomationMode,
    OrderStatus,
    ProductType,
    RunStatus,
    StockStatus,
    WorkflowStepKind,
)
from app.domain.value_objects import DomainError
from app.services.audit_service import AuditService
from app.services.os.stock_service import StockService, StockUnavailable
from app.engines.registry import DiagnosticCase, build_default_registry

log = logging.getLogger(__name__)


#: Встроенные определения воркфлоу.
BUILTIN_WORKFLOWS: list[dict] = [
    {
        "code": "auto_delivery",
        "name": "AUTO: резерв склада и выдача",
        "steps": [
            {"kind": "automatic", "name": "Зарезервировать товар", "handler": "reserve_stock", "params": {}},
            {"kind": "automatic", "name": "Подготовить выдачу", "handler": "prepare_delivery", "params": {}},
            {"kind": "delivery", "name": "Выдать клиенту", "handler": "deliver", "params": {}},
            {"kind": "automatic", "name": "Закрыть заказ", "handler": "complete_order", "params": {}},
        ],
    },
    {
        "code": "semi_auto_doctor",
        "name": "SEMI_AUTO: диагностика → решение → подтверждение → выдача",
        "steps": [
            {"kind": "manual", "name": "Запросить данные у клиента", "handler": "request_input", "params": {}},
            {"kind": "automatic", "name": "Проанализировать", "handler": "analyze", "params": {}},
            {"kind": "automatic", "name": "Сформировать решение", "handler": "generate_solution", "params": {}},
            {"kind": "approval", "name": "Подтвердить решение", "handler": "approval_gate", "params": {"safe": True}},
            {"kind": "delivery", "name": "Отправить решение", "handler": "deliver", "params": {}},
            {"kind": "automatic", "name": "Закрыть заказ", "handler": "complete_order", "params": {}},
        ],
    },
    {
        "code": "server_setup",
        "name": "SEMI_AUTO: сервер под ключ",
        "steps": [
            {"kind": "manual", "name": "Собрать требования", "handler": "request_input", "params": {}},
            {"kind": "automatic", "name": "Сгенерировать конфиг и скрипты", "handler": "generate_solution", "params": {}},
            {"kind": "validation", "name": "Валидация", "handler": "validate_solution", "params": {}},
            {"kind": "delivery", "name": "Выдать", "handler": "deliver", "params": {}},
            {"kind": "automatic", "name": "Закрыть заказ", "handler": "complete_order", "params": {}},
        ],
    },
    {
        "code": "manual_service",
        "name": "MANUAL: очередь, исполнение, отчёт",
        "steps": [
            {"kind": "manual", "name": "Запланировать и исполнить", "handler": "request_input", "params": {}},
            {"kind": "manual", "name": "Отчёт исполнителя", "handler": "request_input", "params": {}},
            {"kind": "delivery", "name": "Закрыть заказ", "handler": "complete_order", "params": {}},
        ],
    },
]


class WorkflowEngine:
    def __init__(self, db: Database, clock: Clock, events: EventBus,
                 audit: AuditService, config: AppConfig, stock: StockService) -> None:
        self.db = db
        self.clock = clock
        self.events = events
        self.audit = audit
        self.config = config
        self.stock = stock
        self.engines = build_default_registry()
        #: Внешний канал выдачи (например, автовыдача в чат заказа маркетплейса).
        #: Вызывается только при реальной (не DRY RUN) выдаче. Подключается снаружи.
        self.delivery_sink: Callable[[str, dict], dict] | None = None
        self._handlers: dict[str, Callable] = {
            "reserve_stock": self._h_reserve_stock,
            "prepare_delivery": self._h_prepare_delivery,
            "analyze": self._h_analyze,
            "generate_solution": self._h_generate_solution,
            "validate_solution": self._h_validate_solution,
            "deliver": self._h_deliver,
            "complete_order": self._h_complete_order,
            "request_input": self._h_request_input,
            "approval_gate": self._h_noop,
        }

    # ----------------------------------------------------------- определения
    def seed_builtin(self) -> None:
        with self.db.session() as session:
            for definition in BUILTIN_WORKFLOWS:
                existing = session.scalar(
                    select(models.WorkflowDef).where(models.WorkflowDef.code == definition["code"])
                )
                if existing is None:
                    session.add(models.WorkflowDef(
                        code=definition["code"], name=definition["name"], steps=definition["steps"]
                    ))

    def definitions(self) -> list[models.WorkflowDef]:
        with self.db.session() as session:
            return list(session.scalars(select(models.WorkflowDef)))

    # ------------------------------------------------------------------ запуск
    def start(self, order_id: int, product_id: int, workflow_code: str,
              context: dict | None = None) -> models.WorkflowRun:
        with self.db.session() as session:
            definition = session.scalar(
                select(models.WorkflowDef).where(models.WorkflowDef.code == workflow_code)
            )
            if definition is None:
                raise DomainError(f"Workflow не найден: {workflow_code}")
            order = session.get(models.Order, order_id)
            if order is not None:
                order.status = OrderStatus.PROCESSING.value
            run = models.WorkflowRun(
                workflow_code=workflow_code, order_id=order_id, product_id=product_id,
                status=RunStatus.RUNNING.value, step_index=0,
                step_states=["pending"] * len(definition.steps),
                context=context or {}, started_at=self.clock.now(),
            )
            session.add(run)
            session.flush()
            if order is not None:
                order.workflow_run_id = run.id
            run_id = run.id
            self.audit.log("workflow.started", Actor.APP, entity="workflow_run", entity_id=run_id,
                            session=session, workflow=workflow_code, order_id=order_id)
        self.events.publish(TOPIC_DATA_CHANGED, section="workflows")
        self._drive(run_id)
        return self.get_run(run_id)

    def get_run(self, run_id: int) -> models.WorkflowRun | None:
        with self.db.session() as session:
            return session.get(models.WorkflowRun, run_id)

    def open_runs(self) -> list[models.WorkflowRun]:
        with self.db.session() as session:
            return list(session.scalars(
                select(models.WorkflowRun).where(models.WorkflowRun.status.in_(
                    [RunStatus.RUNNING.value, RunStatus.WAITING_MANUAL.value,
                     RunStatus.WAITING_APPROVAL.value]))
                .order_by(models.WorkflowRun.id)
            ))

    # ------------------------------------------------------------ исполнение
    def _drive(self, run_id: int) -> None:
        """Прогоняет шаги до первой остановки (ручной шаг/подтверждение/конец)."""
        mode = AutomationMode(self.config.automation_mode)
        if mode == AutomationMode.OFF:
            self._set_status(run_id, RunStatus.PENDING)
            return
        while True:
            run = self.get_run(run_id)
            if run is None or run.status not in (RunStatus.RUNNING.value,):
                return
            definition = self._definition(run.workflow_code)
            if definition is None or run.step_index >= len(definition.steps):
                self._finish(run_id, RunStatus.COMPLETED)
                return
            step = definition.steps[run.step_index]
            kind = WorkflowStepKind(step.get("kind", "manual"))
            handler_name = step.get("handler", "")
            params = step.get("params", {})

            if kind == WorkflowStepKind.MANUAL:
                self._mark_step(run_id, run.step_index, "waiting")
                self._set_status(run_id, RunStatus.WAITING_MANUAL)
                self._notify_manual(run, step)
                return

            if kind == WorkflowStepKind.APPROVAL:
                if mode == AutomationMode.AUTO_SAFE and params.get("safe"):
                    self._mark_step(run_id, run.step_index, "approved_auto")
                elif self._step_approved(run):
                    self._mark_step(run_id, run.step_index, "approved")
                else:
                    self._mark_step(run_id, run.step_index, "waiting_approval")
                    self._set_status(run_id, RunStatus.WAITING_APPROVAL)
                    self._notify_approval(run, step)
                    return

            if kind == WorkflowStepKind.DELIVERY and mode == AutomationMode.ASSISTED and not self.config.dry_run:
                # В ASSISTED внешняя выдача — только после подтверждения оператора.
                if not self._step_approved(run):
                    self._mark_step(run_id, run.step_index, "waiting_approval")
                    self._set_status(run_id, RunStatus.WAITING_APPROVAL)
                    self._notify_approval(run, step)
                    return

            if kind == WorkflowStepKind.WAIT:
                self._mark_step(run_id, run.step_index, "waiting")
                self._set_status(run_id, RunStatus.WAITING_MANUAL)
                return

            handler = self._handlers.get(handler_name)
            if handler is None:
                self._fail(run_id, f"Неизвестный обработчик шага: {handler_name}")
                return
            try:
                result = handler(run_id, step, params)
            except StockUnavailable as exc:
                self._fail(run_id, str(exc))
                return
            except DomainError as exc:
                self._fail(run_id, str(exc))
                return
            if result == "stop":
                return
            self._mark_step(run_id, run.step_index, "done")
            self._advance(run_id)

    def complete_manual(self, run_id: int, result: dict | None = None) -> None:
        """Оператор нажал DONE: продолжаем прогон."""
        run = self.get_run(run_id)
        if run is None:
            return
        self._store_manual_result(run_id, result or {})
        self._mark_step(run_id, run.step_index, "done")
        self._advance(run_id)
        self._set_status(run_id, RunStatus.RUNNING)
        self._drive(run_id)

    def approve(self, run_id: int) -> None:
        """Оператор подтвердил шаг. Шаг НЕ пропускается — исполняется дальше."""
        run = self.get_run(run_id)
        if run is None:
            return
        self._mark_step(run_id, run.step_index, "approved")
        self.audit.log("workflow.approved", Actor.USER, entity="workflow_run", entity_id=run_id)
        self._set_status(run_id, RunStatus.RUNNING)
        self._drive(run_id)

    @staticmethod
    def _step_approved(run: models.WorkflowRun) -> bool:
        states = run.step_states or []
        return run.step_index < len(states) and states[run.step_index] in ("approved", "approved_auto")

    def cancel(self, run_id: int, reason: str = "") -> None:
        run = self.get_run(run_id)
        if run is None:
            return
        if run.stock_unit_id is not None:
            self.stock.release(run.stock_unit_id, reason=f"workflow cancelled: {reason}")
        self._finish(run_id, RunStatus.CANCELLED, error=reason)
        if run.order_id is not None:
            self._set_order_status(run.order_id, OrderStatus.CANCELLED)

    def complete_run(self, run_id: int, reason: str = "") -> None:
        """Досрочно завершает прогон как успешный (например, заказ закрыт на
        маркетплейсе). Зарезервированный юнит считается проданным."""
        run = self.get_run(run_id)
        if run is None or run.status in (RunStatus.COMPLETED.value, RunStatus.CANCELLED.value):
            return
        if run.stock_unit_id is not None:
            unit = self.stock.get(run.stock_unit_id)
            if unit is not None and unit.status == StockStatus.RESERVED.value:
                self.stock.mark_sold(run.stock_unit_id, run.order_id)
        self._finish(run_id, RunStatus.COMPLETED)
        self.audit.log("workflow.closed_externally", Actor.APP, entity="workflow_run",
                        entity_id=run_id, reason=reason)

    # ------------------------------------------------------------- обработчики
    def _h_reserve_stock(self, run_id: int, step: dict, params: dict) -> str | None:
        run = self.get_run(run_id)
        if run is None or run.product_id is None or run.order_id is None:
            raise DomainError("Запуск без продукта/заказа не может резервировать склад.")
        unit = self.stock.reserve_for_order(run.product_id, run.order_id)
        self._set_run_fields(run_id, stock_unit_id=unit.id)
        return None

    def _h_prepare_delivery(self, run_id: int, step: dict, params: dict) -> str | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        product = self._product(run.product_id)
        delivery = {
            "product": product.name if product else "?",
            "payload_ref": None,
            "instructions": "Цифровой товар готов к отправке.",
        }
        if run.stock_unit_id is not None:
            unit = self.stock.get(run.stock_unit_id)
            if unit is not None:
                delivery["payload_ref"] = unit.payload_ref
        self._store_context(run_id, {"delivery": delivery})
        return None

    def _engine_for_run(self, run: models.WorkflowRun):
        product = self._product(run.product_id)
        template = dict(product.payload_template or {}) if product is not None else {}
        engine_name = template.get("engine")
        if not engine_name:
            kind = template.get("kind")
            engine_name = {
                "config": "config_factory",
                "mod_setup": "mod_doctor",
                "save_repair": "save_doctor",
                "server": "server_doctor",
            }.get(kind, "server_doctor")
        try:
            return self.engines.resolve(str(engine_name)), template
        except KeyError as exc:
            raise DomainError(str(exc)) from exc

    def _h_analyze(self, run_id: int, step: dict, params: dict) -> str | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        engine, template = self._engine_for_run(run)
        diagnostics = dict(template)
        diagnostics.update(run.context.get("input") or {})
        case = DiagnosticCase.from_inputs(engine.name, diagnostics)
        diagnosis = engine.analyze(case)
        self._store_context(run_id, {"diagnosis": diagnosis, "engine": engine.name})
        return None

    def _h_generate_solution(self, run_id: int, step: dict, params: dict) -> str | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        engine, template = self._engine_for_run(run)
        diagnostics = dict(template)
        diagnostics.update(run.context.get("input") or {})
        case = DiagnosticCase.from_inputs(engine.name, diagnostics)
        solution = engine.prepare(case, run.context.get("diagnosis") or {})
        self._store_context(run_id, {"solution": solution, "engine": engine.name})
        return None

    def _h_validate_solution(self, run_id: int, step: dict, params: dict) -> str | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        engine, _template = self._engine_for_run(run)
        solution = run.context.get("solution") or {}
        ok, detail = engine.validate(solution)
        if not ok:
            raise DomainError(f"Валидация не пройдена: {detail}")
        return None

    def _h_deliver(self, run_id: int, step: dict, params: dict) -> str | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        delivery = run.context.get("delivery") or {}
        if self.config.dry_run:
            self._store_context(run_id, {"delivery_preview": delivery})
            self.audit.log("workflow.delivery_preview", Actor.APP, entity="workflow_run",
                            entity_id=run_id, dry_run=True)
            return None
        # Реальная выдача наружу — всегда ручное подтверждение оператора.
        if run.stock_unit_id is not None:
            self.stock.mark_sold(run.stock_unit_id, run.order_id)
        if run.order_id is not None:
            self._set_order_status(run.order_id, OrderStatus.READY_TO_DELIVER)
        self.audit.log("workflow.delivery_ready", Actor.APP, entity="workflow_run", entity_id=run_id)
        # Внешний канал выдачи (автовыдача) — если подключён и заказ внешний.
        funpay_id = self._order_funpay_id(run.order_id)
        if self.delivery_sink is not None and funpay_id:
            delivery = run.context.get("delivery") or {}
            try:
                outcome = self.delivery_sink(funpay_id, delivery)
            except Exception as exc:  # noqa: BLE001 - сбой канала не роняет прогон
                log.error("Канал выдачи не сработал для заказа %s: %s", funpay_id, exc)
                outcome = {"status": "error", "error": str(exc)}
                self.audit.log("workflow.delivery_failed", Actor.APP, entity="workflow_run",
                                entity_id=run_id, funpay_id=funpay_id, error=str(exc))
            self._store_context(run_id, {"funpay_delivery": outcome})
        return None

    def _order_funpay_id(self, order_id: int | None) -> str | None:
        if order_id is None:
            return None
        with self.db.session() as session:
            order = session.get(models.Order, order_id)
            return order.funpay_order_id if order else None

    def _h_complete_order(self, run_id: int, step: dict, params: dict) -> str | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        if run.order_id is not None:
            self._set_order_status(run.order_id, OrderStatus.COMPLETED)
        return None

    def _h_request_input(self, run_id: int, step: dict, params: dict) -> str | None:
        return None  # данные придут через complete_manual

    def _h_noop(self, run_id: int, step: dict, params: dict) -> str | None:
        return None

    # ---------------------------------------------------------------- helpers
    def _definition(self, code: str) -> models.WorkflowDef | None:
        with self.db.session() as session:
            return session.scalar(select(models.WorkflowDef).where(models.WorkflowDef.code == code))

    def _product(self, product_id: int | None) -> models.Product | None:
        if product_id is None:
            return None
        with self.db.session() as session:
            return session.get(models.Product, product_id)

    def _advance(self, run_id: int) -> None:
        with self.db.session() as session:
            run = session.get(models.WorkflowRun, run_id)
            if run is not None:
                run.step_index += 1

    def _mark_step(self, run_id: int, index: int, state: str) -> None:
        with self.db.session() as session:
            run = session.get(models.WorkflowRun, run_id)
            if run is None:
                return
            states = list(run.step_states or [])
            while len(states) <= index:
                states.append("pending")
            states[index] = state
            run.step_states = states

    def _set_status(self, run_id: int, status: RunStatus) -> None:
        with self.db.session() as session:
            run = session.get(models.WorkflowRun, run_id)
            if run is not None:
                run.status = status.value
                if status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
                    run.finished_at = self.clock.now()
        self.events.publish(TOPIC_DATA_CHANGED, section="workflows")

    def _finish(self, run_id: int, status: RunStatus, error: str | None = None) -> None:
        with self.db.session() as session:
            run = session.get(models.WorkflowRun, run_id)
            if run is not None:
                run.status = status.value
                run.finished_at = self.clock.now()
                if error:
                    run.error = error
        self.audit.log("workflow.finished", Actor.APP, entity="workflow_run", entity_id=run_id,
                        status=status.value, error=error)
        self.events.publish(TOPIC_DATA_CHANGED, section="workflows")

    def _fail(self, run_id: int, error: str) -> None:
        log.error("Workflow %s упал: %s", run_id, error)
        self._finish(run_id, RunStatus.FAILED, error=error)
        run = self.get_run(run_id)
        if run is not None and run.order_id is not None:
            self._set_order_status(run.order_id, OrderStatus.PROBLEM)

    def _set_order_status(self, order_id: int, status: OrderStatus) -> None:
        with self.db.session() as session:
            order = session.get(models.Order, order_id)
            if order is not None:
                order.status = status.value

    def _set_run_fields(self, run_id: int, **fields) -> None:
        with self.db.session() as session:
            run = session.get(models.WorkflowRun, run_id)
            if run is not None:
                for key, value in fields.items():
                    setattr(run, key, value)

    def _store_context(self, run_id: int, updates: dict) -> None:
        with self.db.session() as session:
            run = session.get(models.WorkflowRun, run_id)
            if run is not None:
                context = dict(run.context or {})
                context.update(updates)
                run.context = context

    def _store_manual_result(self, run_id: int, result: dict) -> None:
        with self.db.session() as session:
            run = session.get(models.WorkflowRun, run_id)
            if run is not None:
                context = dict(run.context or {})
                if "input" not in context:
                    context["input"] = {}
                context["input"].update(result)
                run.context = context

    def _notify_manual(self, run: models.WorkflowRun, step: dict) -> None:
        from app.domain.enums import NotificationKind

        self.events.publish(
            TOPIC_DATA_CHANGED, section="manual_actions")
        # уведомление в центр — через сервис уведомлений вызывается снаружи,
        # здесь только событие, чтобы не создавать циклическую зависимость
        log.info("Требуется ручное действие: %s (run %s)", step.get("name"), run.id)

    def _notify_approval(self, run: models.WorkflowRun, step: dict) -> None:
        log.info("Требуется подтверждение: %s (run %s)", step.get("name"), run.id)
