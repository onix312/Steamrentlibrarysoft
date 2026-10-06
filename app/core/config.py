"""Конфигурация приложения: типизированные значения + сохранение в JSON.

Секретов здесь нет — только ссылки на credentials вида ``keyring://...``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path

log = logging.getLogger(__name__)

CONFIG_FILE_NAME = "config.json"


@dataclass
class SteamSettings:
    mode: str = "simulation"            # simulation | live
    api_key_ref: str = ""               # keyring://steam/api_key
    sync_interval_min: int = 60
    metadata_rate_limit_sec: float = 1.2
    max_accounts: int = 16


@dataclass
class FunPaySettings:
    mode: str = "manual"                # manual | golden_key
    golden_key_ref: str = ""            # keyring://funpay/golden_key
    poll_interval_min: int = 15
    message_mode: str = "manual_approval"  # manual_approval | auto_safe
    unofficial_allowed: bool = False    # явное согласие на неофициальный доступ
    user_agent: str = ""                # UA браузера, где выполнен вход (для golden_key)
    auto_replies: dict = field(default_factory=dict)
    # Автоответы: {"команда": "текст ответа"}. Пусто = автоответы выключены.
    # Пример: {"!статус": "Заказ в работе, напишу, как будет готово."}
    lot_nodes: dict = field(default_factory=dict)
    # Подкатегории лотов по играм: {"Имя игры": <id подкатегории из /lots/<id>/trade>}.
    lot_auto_sync: bool = False            # поллер сам синхронизирует лоты (только АВТО без DRY RUN)
    lot_close_when_empty: bool = True      # закрывать лот, если нет готового товара
    lot_payment_message: str = ""          # сообщение покупателю после оплаты в новых лотах
    lot_price_tolerance_pct: float = 0.0   # допуск расхождения цены лота, % (0 = любая копейка)


@dataclass
class DemandWeights:
    """Коэффициенты Demand Score (по ТЗ вынесены в настройки)."""
    popularity: float = 0.25
    reviews: float = 0.20
    price: float = 0.15
    recency: float = 0.10
    multiplayer: float = 0.10
    trend: float = 0.10
    marketplace: float = 0.10           # конкуренция/спрос на FunPay


@dataclass
class CommercialWeights:
    demand: float = 1.0
    price: float = 0.8
    competition: float = -0.5           # чем больше конкурентов, тем ниже
    free_copies: float = 0.6


@dataclass
class GradeThresholds:
    s: float = 80.0
    a: float = 65.0
    b: float = 45.0
    c: float = 25.0


@dataclass
class TwitchSettings:
    mode: str = "manual"                # manual | live (Helix с своим приложением)
    client_id_ref: str = ""             # keyring://twitch/client_id
    client_secret_ref: str = ""         # keyring://twitch/client_secret
    preferred_language: str = ""


@dataclass
class SchedulerSettings:
    lease_check_interval_min: int = 5
    lease_expiry_warn_hours: int = 24
    demand_recalc_interval_hours: int = 24
    backup_interval_hours: int = 24
    backoff_base_sec: int = 30
    backoff_cap_sec: int = 1800


@dataclass
class AppConfig:
    dry_run: bool = True                     # по умолчанию безопасно
    automation_mode: str = "assisted"        # off | assisted | auto_safe (ТЗ §57)
    language: str = "ru"
    data_dir: str = "data"
    steam: SteamSettings = field(default_factory=SteamSettings)
    funpay: FunPaySettings = field(default_factory=FunPaySettings)
    twitch: TwitchSettings = field(default_factory=TwitchSettings)
    demand_weights: DemandWeights = field(default_factory=DemandWeights)
    commercial_weights: CommercialWeights = field(default_factory=CommercialWeights)
    grade_thresholds: GradeThresholds = field(default_factory=GradeThresholds)
    scheduler: SchedulerSettings = field(default_factory=SchedulerSettings)

    # ------------------------------------------------------------------ I/O
    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)

    @property
    def db_url(self) -> str:
        self.data_path.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.data_path / 'steamrent.db'}"

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        if not path.exists():
            cfg = cls()
            cfg.save(path)
            return cfg
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.error("Не удалось прочитать конфиг %s: %s — используем значения по умолчанию", path, exc)
            return cls()
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> "AppConfig":
        cfg = cls()
        for f in fields(cfg):
            if f.name not in raw:
                continue
            value = raw[f.name]
            if is_dataclass(getattr(cfg, f.name)) and isinstance(value, dict):
                nested = getattr(cfg, f.name)
                known = {nf.name for nf in fields(nested)}
                for key, val in value.items():
                    if key in known:
                        setattr(nested, key, val)
            else:
                setattr(cfg, f.name, value)
        return cfg
