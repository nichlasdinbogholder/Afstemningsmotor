"""Cache-tabeller: customers, suppliers, entries og open_entries

Udvider også sync_state.ressource med customers og open_entries.

Revision ID: 9ad873ee0ac5
Revises: 0702ad4fd9f6
Create Date: 2026-09-23 20:31:53.699076

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9ad873ee0ac5'
down_revision: Union[str, Sequence[str], None] = '0702ad4fd9f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Opgrader databasen."""
    op.create_table('customers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('kundenummer', sa.BigInteger(), nullable=False),
    sa.Column('navn', sa.String(length=255), nullable=False),
    sa.Column('cvr', sa.String(length=40), nullable=True),
    sa.Column('betalingsbetingelse', sa.Integer(), nullable=True),
    sa.Column('spaerret', sa.Boolean(), nullable=True),
    sa.Column('saldo', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('sidst_set', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_customers_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_customers')),
    sa.UniqueConstraint('client_id', 'kundenummer', name='uq_customers_kunde_nummer')
    )
    op.create_table('entries',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('bogfoert_id', sa.BigInteger(), nullable=False),
    sa.Column('bilagsnummer', sa.BigInteger(), nullable=True),
    sa.Column('dato', sa.Date(), nullable=True),
    sa.Column('kontonummer', sa.Integer(), nullable=True),
    sa.Column('tekst', sa.Text(), nullable=True),
    sa.Column('beloeb', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('modpart', sa.String(length=30), nullable=True),
    sa.Column('valuta', sa.String(length=3), nullable=True),
    sa.Column('entry_type', sa.String(length=30), nullable=True),
    sa.Column('sidst_set', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("entry_type IN ('customerInvoice', 'customerPayment', 'supplierInvoice', 'supplierPayment', 'financeVoucher', 'reminder', 'openingEntry', 'transferredOpeningEntry', 'systemEntry', 'manualDebtorInvoice')", name=op.f('ck_entries_entry_type_gyldig')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_entries_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_entries')),
    sa.UniqueConstraint('client_id', 'bogfoert_id', name='uq_entries_kunde_post')
    )
    op.create_index(op.f('ix_entries_dato'), 'entries', ['dato'], unique=False)
    op.create_table('open_entries',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('bogfoert_id', sa.BigInteger(), nullable=False),
    sa.Column('type', sa.String(length=10), nullable=False),
    sa.Column('partnummer', sa.BigInteger(), nullable=False),
    sa.Column('partnavn', sa.String(length=255), nullable=True),
    sa.Column('bilagsnummer', sa.BigInteger(), nullable=True),
    sa.Column('fakturanummer', sa.String(length=100), nullable=True),
    sa.Column('dato', sa.Date(), nullable=True),
    sa.Column('forfaldsdato', sa.Date(), nullable=True),
    sa.Column('beloeb', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('restbeloeb', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.Column('valuta', sa.String(length=3), nullable=True),
    sa.Column('sidst_set', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("type IN ('debitor', 'kreditor')", name=op.f('ck_open_entries_type_gyldig')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_open_entries_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_open_entries')),
    sa.UniqueConstraint('client_id', 'bogfoert_id', name='uq_open_entries_kunde_post')
    )
    op.create_table('suppliers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('leverandoernummer', sa.BigInteger(), nullable=False),
    sa.Column('navn', sa.String(length=255), nullable=False),
    sa.Column('cvr', sa.String(length=40), nullable=True),
    sa.Column('betalingsbetingelse', sa.Integer(), nullable=True),
    sa.Column('saldo', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('sidst_set', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_suppliers_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_suppliers')),
    sa.UniqueConstraint('client_id', 'leverandoernummer', name='uq_suppliers_kunde_nummer')
    )
    op.drop_constraint(op.f('ck_sync_state_ressource_gyldig'), 'sync_state', type_='check')
    op.create_check_constraint(op.f('ck_sync_state_ressource_gyldig'), 'sync_state',
                               "ressource IN ('accounts', 'customers', 'suppliers', 'entries', 'open_entries', 'invoices', 'journals')")


def downgrade() -> None:
    """Nedgrader databasen."""
    op.execute("DELETE FROM sync_state WHERE ressource IN ('customers', 'open_entries')")
    op.drop_constraint(op.f('ck_sync_state_ressource_gyldig'), 'sync_state', type_='check')
    op.create_check_constraint(op.f('ck_sync_state_ressource_gyldig'), 'sync_state',
                               "ressource IN ('accounts', 'entries', 'invoices', 'journals', 'suppliers')")
    op.drop_table('suppliers')
    op.drop_table('open_entries')
    op.drop_index(op.f('ix_entries_dato'), table_name='entries')
    op.drop_table('entries')
    op.drop_table('customers')
