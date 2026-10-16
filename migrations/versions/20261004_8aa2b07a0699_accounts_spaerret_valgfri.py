"""accounts.spaerret og direkte_posteringer_blokeret må være tomme (vi gætter aldrig)

Revision ID: 8aa2b07a0699
Revises: 9ad873ee0ac5
Create Date: 2026-10-04 17:54:03.532217

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8aa2b07a0699'
down_revision: Union[str, Sequence[str], None] = '9ad873ee0ac5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


KOLONNER = ("spaerret", "direkte_posteringer_blokeret")


def upgrade() -> None:
    """Opgrader databasen."""
    for kolonne in KOLONNER:
        op.alter_column("accounts", kolonne, existing_type=sa.BOOLEAN(),
                        nullable=True, server_default=None)


def downgrade() -> None:
    """Nedgrader databasen (tomme værdier bliver til false)."""
    for kolonne in KOLONNER:
        op.execute(f"UPDATE accounts SET {kolonne} = false WHERE {kolonne} IS NULL")
        op.alter_column("accounts", kolonne, existing_type=sa.BOOLEAN(),
                        nullable=False, server_default=sa.text("false"))
