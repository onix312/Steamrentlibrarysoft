"""Versioned JSON product packs with validation and non-destructive updates."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from app.database import models
from app.database.session import Database

REQUIRED_PRODUCT_FIELDS = {
    "code", "name", "type", "automation_level", "workflow_code", "engine"
}
VALID_TYPES = {"auto", "semi_auto", "manual"}
VALID_LEVELS = {"A0", "A1", "A2", "A3", "A4", "A5"}


class ProductPackError(ValueError):
    pass


@dataclass(frozen=True)
class ProductPack:
    name: str
    version: int
    game: str
    products: list[dict]
    enabled: bool = True


def load_pack(path: Path) -> ProductPack:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductPackError(f"Cannot read product pack {path}: {exc}") from exc
    for key in ("name", "version", "game", "products"):
        if key not in raw:
            raise ProductPackError(f"{path}: missing field {key}")
    if not isinstance(raw["version"], int) or raw["version"] < 1:
        raise ProductPackError(f"{path}: version must be positive integer")
    if not isinstance(raw["products"], list):
        raise ProductPackError(f"{path}: products must be a list")
    for item in raw["products"]:
        missing = REQUIRED_PRODUCT_FIELDS - set(item)
        if missing:
            raise ProductPackError(f"{path}: product missing {sorted(missing)}")
        if item["type"] not in VALID_TYPES:
            raise ProductPackError(f"{path}: invalid product type {item['type']}")
        if item["automation_level"] not in VALID_LEVELS:
            raise ProductPackError(f"{path}: invalid automation level {item['automation_level']}")
    return ProductPack(
        name=str(raw["name"]),
        version=int(raw["version"]),
        game=str(raw["game"]),
        products=raw["products"],
        enabled=bool(raw.get("enabled", True)),
    )


class ProductPackService:
    def __init__(self, db: Database, packs_dir: Path | None = None) -> None:
        self.db = db
        self.packs_dir = packs_dir or (Path(__file__).resolve().parents[2] / "product_packs")

    def available(self) -> list[ProductPack]:
        if not self.packs_dir.exists():
            return []
        return [load_pack(path) for path in sorted(self.packs_dir.glob("*.json"))]

    def import_all(self) -> dict:
        result = {"created": 0, "updated": 0, "disabled": 0, "packs": 0}
        for pack in self.available():
            report = self.import_pack(pack)
            result["packs"] += 1
            for key in ("created", "updated", "disabled"):
                result[key] += report[key]
        return result

    def import_pack(self, pack: ProductPack) -> dict:
        report = {"created": 0, "updated": 0, "disabled": 0}
        codes = {str(item["code"]) for item in pack.products}
        with self.db.session() as session:
            for item in pack.products:
                code = str(item["code"])
                product = session.scalar(select(models.Product).where(models.Product.code == code))
                payload = dict(item.get("payload_template") or {})
                payload["engine"] = item["engine"]
                payload["_pack"] = {"name": pack.name, "version": pack.version}
                enabled = bool(item.get("enabled", True) and pack.enabled)
                if product is None:
                    product = models.Product(
                        code=code,
                        name=str(item["name"]),
                        game=str(item.get("game") or pack.game),
                        category=item.get("category"),
                        type=str(item["type"]),
                        automation_level=str(item["automation_level"]),
                        cost=float(item.get("cost", 0.0)),
                        price=float(item.get("price", 0.0)),
                        minimum_price=float(item.get("minimum_price", 0.0)),
                        estimated_manual_minutes=int(item.get("estimated_manual_minutes", 0)),
                        workflow_code=str(item["workflow_code"]),
                        stock_mode=str(item.get("stock_mode", "none")),
                        target_stock=int(item.get("target_stock", 0)),
                        active=enabled,
                        payload_template=payload,
                    )
                    session.add(product)
                    report["created"] += 1
                else:
                    # Preserve operator-controlled commercial values.
                    product.name = str(item["name"])
                    product.game = str(item.get("game") or pack.game)
                    product.category = item.get("category")
                    product.type = str(item["type"])
                    product.automation_level = str(item["automation_level"])
                    product.estimated_manual_minutes = int(item.get("estimated_manual_minutes", product.estimated_manual_minutes or 0))
                    product.workflow_code = str(item["workflow_code"])
                    product.stock_mode = str(item.get("stock_mode", product.stock_mode or "none"))
                    product.target_stock = int(item.get("target_stock", product.target_stock or 0))
                    product.payload_template = payload
                    product.active = enabled
                    report["updated"] += 1

            existing = session.scalars(select(models.Product).where(models.Product.game == pack.game)).all()
            for product in existing:
                meta = dict(product.payload_template or {}).get("_pack") or {}
                if meta.get("name") == pack.name and product.code not in codes:
                    product.active = False
                    report["disabled"] += 1
        return report
