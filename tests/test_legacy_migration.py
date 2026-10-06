from __future__ import annotations

from sqlalchemy import create_engine

from app.database.base import Base
from app.database import models
from app.database.session import Database


def test_legacy_create_all_database_is_stamped_without_data_loss(tmp_path):
    path = tmp_path / "legacy.db"
    url = f"sqlite:///{path.as_posix()}"
    engine = create_engine(url, future=True)
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            models.Setting.__table__.insert().values(
                key="legacy_probe", value={"preserve": True}
            )
        )
    engine.dispose()

    db = Database(url)
    assert db.migration_report.legacy_stamped is True
    assert db.migration_report.backup_path is not None
    assert db.migration_report.backup_path.exists()
    with db.session() as session:
        row = session.get(models.Setting, "legacy_probe")
        assert row is not None
        assert row.value == {"preserve": True}
    db.engine.dispose()
