"""Server Doctor: диагностика по логам/конфигу/описанию проблемы (ТЗ §12).

Детерминированный классификатор на основе правил: парсит входные данные,
ищет известные паттерны, выдаёт проблему + уверенность + фикс + откат.
Никогда не применяет изменения к системе — только рекомендации.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Diagnosis:
    problem: str
    category: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    fix: str = ""
    risk: str = "low"
    rollback: str = ""

    def to_dict(self) -> dict:
        return {
            "problem": self.problem,
            "category": self.category,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "fix": self.fix,
            "risk": self.risk,
            "rollback": self.rollback,
        }


#: Правила: (категория, паттерн в тексте, проблема, фикс, вес уверенности).
RULES: list[tuple[str, str, str, str]] = [
    ("port", r"\b(port|ports?)\b.*(fail|blocked|closed|refused|timeout)|connection refused",
     "Порт недоступен",
     "Откройте порт в файрволе/роутере (проброс), проверьте, что порт не занят другим процессом."),
    ("firewall", r"firewall|брандмауэр|windows defender network",
     "Файрвол блокирует соединения",
     "Добавьте правило разрешения для порта и исполняемого файла сервера в файрвол."),
    ("memory", r"out of memory|oom|java\.lang\.OutOfMemory|heap space|high ram",
     "Нехватка памяти",
     "Увеличьте выделенную RAM (-Xmx для Java-серверов), закройте фоновые процессы."),
    ("cpu", r"high cpu|cpu (usage|load).*100|tps.*(drop|low)",
     "Высокая нагрузка на CPU",
     "Уменьшите дальность симуляции/прорисовки, ограничьте число сущностей, обновите железо."),
    ("mod", r"mod.*(conflict|error|fail|crash)|missing dependency|incompatible mod",
     "Конфликт или ошибка мода",
     "Отключите последний добавленный мод, проверьте зависимости и порядок загрузки."),
    ("crash", r"crash|segfault|fatal error|unhandled exception",
     "Крэш сервера",
     "Проверьте последние логи перед крэшем, откатите обновление, восстановите бэкап."),
    ("update", r"update.*(fail|break)|version mismatch|broken update",
     "Проблемное обновление",
     "Откатитесь на предыдущую версию из бэкапа, дождитесь фикса."),
    ("save", r"save.*(corrupt|broken|fail)|world.*(corrupt|broken)",
     "Повреждённое сохранение",
     "Восстановите сохранение из последнего бэкапа; не запускайте сервер на битом сейве."),
    ("start", r"(doesn't|does not|won't|not) start|fail(ed)? to start|startup fail",
     "Сервер не запускается",
     "Проверьте наличие зависимостей (Java/VC++/.NET), права на папку и актуальность логов."),
    ("connect", r"(player|client).*(can't|cannot|unable).*(connect|join)|disconnect",
     "Игроки не могут подключиться",
     "Проверьте порт, файрвол, корректность адреса и версию клиента/сервера."),
]


def diagnose(inputs: dict) -> dict:
    """Анализ входных данных. Входы: ``logs``, ``config``, ``error``, ``description``.

    Возвращает словарь-диагноз (см. Diagnosis.to_dict). Если уверенности
    недостаточно — ``confidence`` низкий и требуется ручной разбор.
    """
    text_parts = []
    evidence_sources = []
    for key in ("logs", "config", "error", "description", "server_info"):
        value = inputs.get(key)
        if value:
            text_parts.append(str(value))
            evidence_sources.append(key)
    text = "\n".join(text_parts).lower()

    matches: list[tuple[float, Diagnosis]] = []

    # Structured facts have higher value than keyword matches.
    port_open = inputs.get("port_open")
    if port_open is False:
        matches.append((0.92, Diagnosis(
            problem="Порт сервера недоступен", category="port", confidence=0.92,
            evidence=["port_open=false по результату сетевой проверки"],
            fix="Проверьте bind-address, локальный firewall и проброс порта на роутере.",
            risk="low", rollback="Удалите только добавленные правила firewall/port-forward.",
        )))

    try:
        free_ram_mb = float(inputs.get("available_ram_mb"))
    except (TypeError, ValueError):
        free_ram_mb = -1
    if 0 <= free_ram_mb < 1024:
        matches.append((0.88, Diagnosis(
            problem="Недостаточно свободной RAM для стабильной работы сервера",
            category="memory", confidence=0.88,
            evidence=[f"available_ram_mb={free_ram_mb:.0f}"],
            fix="Освободите RAM или уменьшите memory/tick/view-distance параметры сервера.",
            risk="low", rollback="Верните прежние лимиты памяти из сохранённой конфигурации.",
        )))

    try:
        cpu_pct = float(inputs.get("cpu_percent"))
    except (TypeError, ValueError):
        cpu_pct = -1
    if cpu_pct >= 95:
        matches.append((0.82, Diagnosis(
            problem="CPU практически постоянно насыщен",
            category="cpu", confidence=0.82,
            evidence=[f"cpu_percent={cpu_pct:.0f}%"],
            fix="Снимите профиль нагрузки; уменьшите тяжёлые tick/simulation параметры и проверьте плагины.",
            risk="low", rollback="Верните прежние параметры сервера.",
        )))

    missing_mods = inputs.get("missing_mods") or []
    if isinstance(missing_mods, str):
        missing_mods = [missing_mods]
    if missing_mods:
        matches.append((0.94, Diagnosis(
            problem="Отсутствуют зависимости/моды сервера", category="mod", confidence=0.94,
            evidence=["missing_mods: " + ", ".join(map(str, missing_mods[:10]))],
            fix="Восстановите требуемые моды/версии и проверьте порядок загрузки.",
            risk="low", rollback="Сохраните исходный список модов и конфигурацию.",
        )))

    server_version = inputs.get("server_version")
    client_version = inputs.get("client_version")
    if server_version and client_version and str(server_version) != str(client_version):
        matches.append((0.91, Diagnosis(
            problem="Версия клиента и сервера не совпадает", category="update", confidence=0.91,
            evidence=[f"server_version={server_version}", f"client_version={client_version}"],
            fix="Синхронизируйте версии клиента/сервера и совместимые версии модов.",
            risk="medium", rollback="Перед обновлением сохраните бинарники/конфиги и world backup.",
        )))
    for category, pattern, problem, fix in RULES:
        found = re.findall(pattern, text, flags=re.IGNORECASE)
        if found:
            confidence = min(0.95, 0.55 + 0.13 * len(found))
            evidence = [f"{src}: найдено совпадение по шаблону категории «{category}»"
                        for src in evidence_sources][:3]
            matches.append((confidence, Diagnosis(
                problem=problem, category=category, confidence=round(confidence, 2),
                evidence=evidence, fix=fix, risk="low",
                rollback="Бэкап конфигурации и сохранений до изменений.",
            )))

    if not matches:
        return Diagnosis(
            problem="Не удалось классифицировать проблему автоматически",
            category="unknown", confidence=0.2,
            evidence=["Совпадений с известными паттернами не найдено"],
            fix="Требуется ручной разбор: соберите полные логи и описание шагов.",
            risk="low", rollback="—",
        ).to_dict()

    matches.sort(key=lambda item: -item[0])
    best = matches[0][1]
    # если несколько категорий с близкой уверенностью — понижаем уверенность
    if len(matches) > 1 and matches[1][0] >= best.confidence - 0.1:
        best.confidence = round(max(0.3, best.confidence - 0.15), 2)
        best.evidence.append("Несколько возможных причин — требуется подтверждение.")
    return best.to_dict()
