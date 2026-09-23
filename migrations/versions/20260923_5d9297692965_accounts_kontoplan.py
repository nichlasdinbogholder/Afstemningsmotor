"""Tabellen accounts: kundernes kontoplaner hentet fra regnskabssystemet

Revision ID: 5d9297692965
Revises: d16d51239de8
Create Date: 2026-09-23 19:28:32.977958

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '5d9297692965'
down_revision: Union[str, Sequence[str], None] = 'd16d51239de8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Opgrader databasen."""
    op.create_table('accounts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('system', sa.String(length=20), nullable=False),
    sa.Column('kontonummer', sa.Integer(), nullable=False),
    sa.Column('navn', sa.String(length=125), nullable=False),
    sa.Column('kontotype', sa.String(length=20), nullable=True),
    sa.Column('debet_kredit', sa.String(length=10), nullable=True),
    sa.Column('momskode', sa.String(length=5), nullable=True),
    sa.Column('spaerret', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('direkte_posteringer_blokeret', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('saldo', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('kladdesaldo', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('raa_data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('hentet', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("debet_kredit IN ('debit', 'credit')", name=op.f('ck_accounts_debet_kredit_gyldig')),
    sa.CheckConstraint("kontotype IN ('profitAndLoss', 'status', 'totalFrom', 'heading', 'headingStart', 'sumInterval', 'sumAlpha')", name=op.f('ck_accounts_kontotype_gyldig')),
    sa.CheckConstraint("system IN ('economic', 'dinero')", name=op.f('ck_accounts_system_gyldig')),
    sa.ForeignKeyConstraint(['tenant_id'], ['clients.id'], name=op.f('fk_accounts_tenant_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_accounts')),
    sa.UniqueConstraint('tenant_id', 'system', 'kontonummer', name='uq_accounts_tenant_konto')
    )
    op.create_index(op.f('ix_accounts_tenant_id'), 'accounts', ['tenant_id'], unique=False)


def downgrade() -> None:
    """Nedgrader databasen."""
    op.drop_index(op.f('ix_accounts_tenant_id'), table_name='accounts')
    op.drop_table('accounts')
