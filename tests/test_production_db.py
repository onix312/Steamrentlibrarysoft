from __future__ import annotations

from pathlib import Path

from app.database.session import Database


def test_database_is_migrated_and_integrity_is_ok(tmp_path):
    path = tmp_path / "prod.db"
    db = Database(f"sqlite:///{path.as_posix()}")
    assert db.schema_version >= 1
    assert db.schema_revision == "0001"
    ok, detail = db.integrity_check()
    assert ok, detail
    db.engine.dispose()


def test_backup_and_restore_roundtrip(tmp_path):
    path = tmp_path / "prod.db"
    db = Database(f"sqlite:///{path.as_posix()}")
    with db.engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE IF NOT EXISTS restore_probe (value TEXT)")
        conn.exec_driver_sql("INSERT INTO restore_probe(value) VALUES ('before')")
    backup = db.backup_database("test")
    assert backup is not None and backup.exists()
    with db.engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM restore_probe")
    db.restore_from_backup(backup)
    with db.engine.begin() as conn:
        value = conn.exec_driver_sql("SELECT value FROM restore_probe").scalar_one()
    assert value == "before"
    db.engine.dispose()
