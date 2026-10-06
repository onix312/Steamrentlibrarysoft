"""Registry of fulfillment engines used by WorkflowEngine."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.engines import config_factory, mod_doctor, server_doctor
from app.engines.game_doctor import diagnose_game
from app.engines.save_doctor import diagnose_save


@dataclass
class DiagnosticCase:
    kind: str
    game: str = ""
    inputs: dict[str, Any] = field(default_factory=dict)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_inputs(cls, kind: str, payload: dict[str, Any]) -> "DiagnosticCase":
        return cls(
            kind=kind,
            game=str(payload.get("game") or ""),
            inputs=dict(payload),
            attachments=list(payload.get("attachments") or []),
            metadata=dict(payload.get("metadata") or {}),
        )


class FulfillmentEngine(ABC):
    name = "base"

    @abstractmethod
    def analyze(self, case: DiagnosticCase) -> dict:
        raise NotImplementedError

    def prepare(self, case: DiagnosticCase, analysis: dict) -> dict:
        return {
            "summary": analysis.get("problem") or analysis.get("summary") or "Решение подготовлено",
            "confidence": float(analysis.get("confidence", 0.0)),
            "evidence": list(analysis.get("evidence") or []),
            "recommendations": list(analysis.get("recommendations") or []),
            "fix": analysis.get("fix") or "",
            "rollback": analysis.get("rollback") or "Сделайте резервную копию перед изменениями.",
            "risk": analysis.get("risk", "low"),
        }

    def validate(self, solution: dict) -> tuple[bool, str]:
        if not solution:
            return False, "solution is empty"
        if not (solution.get("summary") or solution.get("fix") or solution.get("files")):
            return False, "solution has no actionable content"
        return True, "ok"

    def build_delivery(self, case: DiagnosticCase, solution: dict) -> dict:
        return {"solution": solution}


class ServerDoctorEngine(FulfillmentEngine):
    name = "server_doctor"

    def analyze(self, case: DiagnosticCase) -> dict:
        case = enrich_case(case)
        result = server_doctor.diagnose(case.inputs)
        recommendations = list(result.get("recommendations") or [])
        recommendations.extend(plugin_recommendations(case))
        if not recommendations and result.get("fix"):
            recommendations.append(result["fix"])
        result["recommendations"] = recommendations
        return result


class ModDoctorEngine(FulfillmentEngine):
    name = "mod_doctor"

    def analyze(self, case: DiagnosticCase) -> dict:
        case = enrich_case(case)
        raw = mod_doctor.analyze(case.inputs)
        issues = (
            raw.get("missing_dependencies", [])
            + raw.get("outdated", [])
            + raw.get("duplicates", [])
            + raw.get("order_issues", [])
        )
        confidence = 0.9 if issues else 0.75
        return {
            "problem": "Обнаружены проблемы модов" if issues else "Явных конфликтов модов не обнаружено",
            "category": "mods",
            "confidence": confidence,
            "evidence": issues[:20],
            "recommendations": [
                "Исправьте отсутствующие зависимости и версии.",
                "Примените рекомендованный порядок загрузки.",
            ] if issues else ["Проверьте полный лог игры, если проблема сохраняется."],
            "recommended_order": raw.get("recommended_order", []),
            "fix": "\n".join(issues) if issues else "Конфигурация модов выглядит согласованной.",
            "rollback": "Сохраните исходный список модов и load order перед изменениями.",
            "risk": "low",
        }


class GameDoctorEngine(FulfillmentEngine):
    name = "game_doctor"

    def analyze(self, case: DiagnosticCase) -> dict:
        case = enrich_case(case)
        result = diagnose_game(case.inputs)
        result["recommendations"] = list(result.get("recommendations") or []) + plugin_recommendations(case)
        return result


class SaveDoctorEngine(FulfillmentEngine):
    name = "save_doctor"

    def analyze(self, case: DiagnosticCase) -> dict:
        case = enrich_case(case)
        return diagnose_save(case.inputs)


class ConfigFactoryEngine(FulfillmentEngine):
    name = "config_factory"

    def analyze(self, case: DiagnosticCase) -> dict:
        generated = config_factory.generate(case.inputs)
        return {
            "problem": "Конфигурация подготовлена",
            "category": "config",
            "confidence": 1.0,
            "evidence": [],
            "recommendations": [generated.get("instructions", "")],
            "generated": generated,
            "fix": generated.get("instructions", ""),
            "rollback": generated.get("rollback", ""),
            "risk": "low",
        }

    def prepare(self, case: DiagnosticCase, analysis: dict) -> dict:
        return dict(analysis.get("generated") or {})


class DigitalDeliveryEngine(FulfillmentEngine):
    name = "digital_delivery"

    def analyze(self, case: DiagnosticCase) -> dict:
        return {
            "problem": "Цифровой товар готов",
            "category": "delivery",
            "confidence": 1.0,
            "evidence": [],
            "recommendations": [],
            "fix": "",
            "rollback": "—",
            "risk": "low",
        }

    def prepare(self, case: DiagnosticCase, analysis: dict) -> dict:
        return {"payload_ref": case.inputs.get("payload_ref"), "instructions": case.inputs.get("instructions", "")}


class EngineRegistry:
    def __init__(self) -> None:
        self._engines: dict[str, FulfillmentEngine] = {}

    def register(self, engine: FulfillmentEngine) -> None:
        self._engines[engine.name] = engine

    def resolve(self, name: str | None) -> FulfillmentEngine:
        key = name or "server_doctor"
        if key not in self._engines:
            raise KeyError(f"Unknown fulfillment engine: {key}")
        return self._engines[key]

    def names(self) -> list[str]:
        return sorted(self._engines)


def build_default_registry() -> EngineRegistry:
    registry = EngineRegistry()
    for engine in (
        ServerDoctorEngine(),
        ModDoctorEngine(),
        GameDoctorEngine(),
        ConfigFactoryEngine(),
        SaveDoctorEngine(),
        DigitalDeliveryEngine(),
    ):
        registry.register(engine)
    return registry
