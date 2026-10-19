"""Fravær (bygherren 19.10.2026): timelønnede kan ikke se eller registrere ferie, barns første sygedag, afspadsering
afholdt, barsel og skoleophold (databasen afviser det). 'Ikke på arbejde' bruges ikke og tages ud af brug for alle
(ikke slettet – historik bevares).

Revision ID: 80cc17832d74
Revises: e2a0a9a2bed1
Create Date: 2026-10-19 08:21:45.092812

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '80cc17832d74'
down_revision: Union[str, Sequence[str], None] = 'e2a0a9a2bed1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None



def upgrade() -> None:
    """Opgrader databasen."""
    op.execute("UPDATE time_task_types SET blocked_for_hourly = true "
               "WHERE code IN ('ferie', 'barn_syg', 'afspadsering', 'barsel', 'skoleophold')")
    op.execute("UPDATE time_task_types SET active = false WHERE code = 'ikke_paa_arbejde'")


def downgrade() -> None:
    """Nedgrader databasen."""
    op.execute("UPDATE time_task_types SET blocked_for_hourly = false "
               "WHERE code IN ('barn_syg', 'afspadsering', 'barsel', 'skoleophold')")
    op.execute("UPDATE time_task_types SET active = true WHERE code = 'ikke_paa_arbejde'")
