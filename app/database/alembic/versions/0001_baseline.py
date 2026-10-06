"""Baseline schema for pre-Alembic installations.

Revision ID: 0001
Revises:
"""
from __future__ import annotations

from alembic import op

from app.database.base import Base
from app.database import models  # noqa: F401

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # For fresh installations create the complete schema from the canonical
    # metadata. Existing pre-Alembic databases are stamped to this revision by
    # MigrationManager only after integrity check + backup.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    # Destructive downgrade of the baseline is intentionally unsupported.
    raise RuntimeError("Baseline downgrade is destructive and is not supported.")
