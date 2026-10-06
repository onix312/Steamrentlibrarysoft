"""Production SQLite lifecycle: integrity, backup, Alembic migrations and restore."""
from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url

CURRENT_SCHEMA_VERSION = 1
CURRENT_REVISION = "0001"


class DatabaseIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class MigrationReport:
    revision: str
    schema_version: int
    legacy_stamped: bool = False
    backup_path: Path | None = None


def sqlite_path_from_url(url: str) -> Path | None:
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        return None
    database = parsed.database
    if not database or database == ":memory:":
        return None
    return Path(database).expanduser().resolve()


class MigrationManager:
    def __init__(self, url: str) -> None:
        self.url = url
        self.db_path = sqlite_path_from_url(url)
        self.script_location = Path(__file__).with_name("alembic")

    def _config(self) -> Config:
        cfg = Config()
        cfg.set_main_option("script_location", str(self.script_location))
        cfg.set_main_option("sqlalchemy.url", self.url.replace("%", "%%"))
        return cfg

    def integrity_check(self, path: Path | None = None) -> tuple[bool, str]:
        db_path = path or self.db_path
        if db_path is None or not db_path.exists():
            return True, "new database"
        try:
            with sqlite3.connect(str(db_path)) as conn:
                row = conn.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError as exc:
            return False, str(exc)
        result = str(row[0] if row else "unknown")
        return result.lower() == "ok", result

    def backup(self, reason: str = "manual") -> Path | None:
        if self.db_path is None or not self.db_path.exists():
            return None
        ok, detail = self.integrity_check()
        if not ok:
            raise DatabaseIntegrityError(f"SQLite integrity_check failed before backup: {detail}")
        target_dir = self.db_path.parent / "backups"
        target_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        target = target_dir / f"{self.db_path.stem}_{reason}_{stamp}{self.db_path.suffix or '.db'}"
        with sqlite3.connect(str(self.db_path)) as source, sqlite3.connect(str(target)) as dest:
            source.backup(dest)
        return target

    def restore(self, backup_path: Path) -> None:
        if self.db_path is None:
            raise ValueError("Restore is supported only for file-backed SQLite databases.")
        backup_path = Path(backup_path).expanduser().resolve()
        if not backup_path.exists():
            raise FileNotFoundError(backup_path)
        ok, detail = self.integrity_check(backup_path)
        if not ok:
            raise DatabaseIntegrityError(f"Backup integrity_check failed: {detail}")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.db_path.with_suffix(self.db_path.suffix + ".restore.tmp")
        shutil.copy2(backup_path, temp)
        temp.replace(self.db_path)

    def current_revision(self) -> str | None:
        if self.db_path is None or not self.db_path.exists():
            return None
        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                tables = {row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                if "alembic_version" not in tables:
                    return None
                row = conn.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
                return str(row[0]) if row else None
        except sqlite3.DatabaseError:
            return None

    def _has_legacy_schema(self) -> bool:
        if self.db_path is None or not self.db_path.exists():
            return False
        engine = create_engine(self.url, future=True)
        try:
            tables = set(inspect(engine).get_table_names())
            return bool(tables - {"alembic_version"})
        finally:
            engine.dispose()

    def upgrade(self) -> MigrationReport:
        # In-memory SQLite is used only for isolated/dev scenarios.
        if self.db_path is None:
            from app.database.base import Base
            from app.database import models  # noqa: F401
            engine = create_engine(self.url, future=True)
            try:
                Base.metadata.create_all(engine)
            finally:
                engine.dispose()
            return MigrationReport(CURRENT_REVISION, CURRENT_SCHEMA_VERSION)

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        legacy = self._has_legacy_schema() and self.current_revision() is None
        backup_path = None

        if self.db_path.exists():
            ok, detail = self.integrity_check()
            if not ok:
                raise DatabaseIntegrityError(
                    f"SQLite integrity_check failed; migration aborted: {detail}"
                )
            backup_path = self.backup("pre_migration")

        cfg = self._config()
        if legacy:
            # Existing installations were created from the exact SQLAlchemy model
            # metadata before Alembic was introduced. Preserve them and establish
            # the baseline instead of recreating tables.
            command.stamp(cfg, CURRENT_REVISION)
        else:
            command.upgrade(cfg, "head")

        ok, detail = self.integrity_check()
        if not ok:
            if backup_path is not None:
                self.restore(backup_path)
            raise DatabaseIntegrityError(
                f"SQLite integrity_check failed after migration: {detail}"
            )
        return MigrationReport(
            revision=self.current_revision() or CURRENT_REVISION,
            schema_version=CURRENT_SCHEMA_VERSION,
            legacy_stamped=legacy,
            backup_path=backup_path,
        )
