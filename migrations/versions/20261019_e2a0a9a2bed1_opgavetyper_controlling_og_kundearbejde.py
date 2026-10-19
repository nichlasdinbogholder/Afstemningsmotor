"""Opgavetyper (bygherren 19.10.2026): 'Afstemning' hedder nu 'Controlling', 'Bogføring' hedder nu 'Kundearbejde', og
'Øvrigt kundearbejde' er taget ud af brug (ikke slettet – registrerede timer beholder deres opgavetype).

Revision ID: e2a0a9a2bed1
Revises: fb5a83b87ff1
Create Date: 2026-10-19 08:01:08.991377

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2a0a9a2bed1'
down_revision: Union[str, Sequence[str], None] = 'fb5a83b87ff1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Opgrader databasen."""
    op.execute("UPDATE time_task_types SET code = 'controlling', name = 'Controlling' WHERE code = 'afstemning'")
    op.execute("UPDATE time_task_types SET code = 'kundearbejde', name = 'Kundearbejde' WHERE code = 'bogfoering'")
    op.execute("UPDATE time_task_types SET active = false WHERE code = 'oevrigt_kunde'")


def downgrade() -> None:
    """Nedgrader databasen."""
    op.execute("UPDATE time_task_types SET code = 'afstemning', name = 'Afstemning' WHERE code = 'controlling'")
    op.execute("UPDATE time_task_types SET code = 'bogfoering', name = 'Bogføring' WHERE code = 'kundearbejde'")
    op.execute("UPDATE time_task_types SET active = true WHERE code = 'oevrigt_kunde'")
