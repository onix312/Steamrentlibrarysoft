"""Database sessions backed by versioned Alembic migrations."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.database.migrations import MigrationManager, MigrationReport


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # pragma: no cover
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.migrations = MigrationManager(url)
        self.migration_report: MigrationReport = self.migrations.upgrade()
        self.engine = create_engine(url, future=True)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def new_session(self) -> Session:
        return self.session_factory()

    @property
    def schema_version(self) -> int:
        return self.migration_report.schema_version

    @property
    def schema_revision(self) -> str:
        return self.migrations.current_revision() or self.migration_report.revision

    def integrity_check(self) -> tuple[bool, str]:
        return self.migrations.integrity_check()

    def backup_database(self, reason: str = "manual") -> Path | None:
        return self.migrations.backup(reason)

    def restore_from_backup(self, backup_path: Path) -> None:
        """Restore a verified SQLite backup and reconnect the session factory."""
        self.engine.dispose()
        self.migrations.restore(Path(backup_path))
        self.engine = create_engine(self.url, future=True)
        self.session_factory.configure(bind=self.engine)
        self.migration_report = self.migrations.upgrade()
