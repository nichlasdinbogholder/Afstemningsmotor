"""entries: beloeb_dkk (grundvaluta), raa_data (hele svaret) og indeks pr. kunde på dato, konto og beløb

Det gamle indeks kun på dato erstattes af (client_id, dato).

Revision ID: 572d6002a99c
Revises: c0b2dec070b2
Create Date: 2026-10-04 18:44:33.628480

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '572d6002a99c'
down_revision: Union[str, Sequence[str], None] = 'c0b2dec070b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Opgrader databasen."""
    op.add_column('entries', sa.Column('beloeb_dkk', sa.Numeric(precision=18, scale=2), nullable=True))
    op.add_column('entries', sa.Column('raa_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.drop_index(op.f('ix_entries_dato'), table_name='entries')
    op.create_index('ix_entries_kunde_beloeb', 'entries', ['client_id', 'beloeb'], unique=False)
    op.create_index('ix_entries_kunde_dato', 'entries', ['client_id', 'dato'], unique=False)
    op.create_index('ix_entries_kunde_konto', 'entries', ['client_id', 'kontonummer'], unique=False)


def downgrade() -> None:
    """Nedgrader databasen."""
    op.drop_index('ix_entries_kunde_konto', table_name='entries')
    op.drop_index('ix_entries_kunde_dato', table_name='entries')
    op.drop_index('ix_entries_kunde_beloeb', table_name='entries')
    op.create_index(op.f('ix_entries_dato'), 'entries', ['dato'], unique=False)
    op.drop_column('entries', 'raa_data')
    op.drop_column('entries', 'beloeb_dkk')
