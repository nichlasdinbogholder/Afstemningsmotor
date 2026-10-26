"""Kunder, som kun administratorer kan spørge Svend om (fx Din Bogholder selv).

Revision ID: a7c3e19d4b20
Revises: f932b776d5b6
Create Date: 2026-10-26
"""

import sqlalchemy as sa
from alembic import op

revision = "a7c3e19d4b20"
down_revision = "f932b776d5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("kun_admin", sa.Boolean(), server_default=sa.false(), nullable=False))
    # Din Bogholder selv er kun for administratorer (bygherren 26.10.2026). Kan rettes i Kundeoversigten.
    op.execute("UPDATE clients SET kun_admin = true WHERE navn ILIKE '%din bogholder%'")


def downgrade() -> None:
    op.drop_column("clients", "kun_admin")
