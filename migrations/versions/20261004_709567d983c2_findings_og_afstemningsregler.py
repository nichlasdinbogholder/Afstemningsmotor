"""Tabellen findings og de tre afstemningsregler som funktioner i databasen

Regel 1 regel_1_smaa_restbeloeb       : udlignede poster med restbeløb 0,01-99,99 kr.
Regel 2 regel_2_betaling_uden_faktura : betalinger uden en åben faktura at udligne mod
Regel 3 regel_3_forfalden_over_6_mdr  : åbne poster forfaldet for mere end 6 måneder siden

Alle tre: SELECT regel_X(p_client_id) – NULL = alle aktive kunder. Returnerer antal NYE fund.
Idempotente via den unikke regel (client_id, regel, kilde_id) + ON CONFLICT DO NOTHING.

Revision ID: 709567d983c2
Revises: 8aa2b07a0699
Create Date: 2026-10-04 18:00:25.450115

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '709567d983c2'
down_revision: Union[str, Sequence[str], None] = '8aa2b07a0699'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


FAELLES_KOLONNER = """
    client_id, regel, kilde_id, type, partnummer, partnavn, fakturanummer,
    dato, forfaldsdato, beloeb, restbeloeb, valuta, beskrivelse
"""

REGEL_1 = f"""
CREATE FUNCTION regel_1_smaa_restbeloeb(p_client_id integer DEFAULT NULL)
RETURNS integer LANGUAGE sql AS $$
    WITH nye AS (
        INSERT INTO findings ({FAELLES_KOLONNER})
        SELECT o.client_id, 'smaa_restbeloeb', o.bogfoert_id, o.type, o.partnummer,
               o.partnavn, o.fakturanummer, o.dato, o.forfaldsdato, o.beloeb,
               o.restbeloeb, o.valuta,
               'Delvist udlignet post med restbeløb på ' || abs(o.restbeloeb) || ' kr.'
        FROM open_entries o
        JOIN clients c ON c.id = o.client_id
        WHERE c.status = 'aktiv'
          AND (p_client_id IS NULL OR o.client_id = p_client_id)
          AND o.valuta = 'DKK'
          AND abs(o.restbeloeb) BETWEEN 0.01 AND 99.99
          AND abs(o.restbeloeb) < abs(o.beloeb)
        ON CONFLICT (client_id, regel, kilde_id) DO NOTHING
        RETURNING 1
    )
    SELECT count(*)::integer FROM nye;
$$;
"""

REGEL_2 = f"""
CREATE FUNCTION regel_2_betaling_uden_faktura(p_client_id integer DEFAULT NULL)
RETURNS integer LANGUAGE sql AS $$
    WITH nye AS (
        INSERT INTO findings ({FAELLES_KOLONNER})
        SELECT o.client_id, 'betaling_uden_faktura', o.bogfoert_id, o.type, o.partnummer,
               o.partnavn, o.fakturanummer, o.dato, o.forfaldsdato, o.beloeb,
               o.restbeloeb, o.valuta,
               'Åben betaling uden en åben faktura hos samme ' || o.type || ' at udligne mod'
        FROM open_entries o
        JOIN clients c ON c.id = o.client_id
        WHERE c.status = 'aktiv'
          AND (p_client_id IS NULL OR o.client_id = p_client_id)
          AND o.entry_type IN ('customerPayment', 'supplierPayment')
          AND NOT EXISTS (
              SELECT 1
              FROM open_entries f
              WHERE f.client_id = o.client_id
                AND f.type = o.type
                AND f.partnummer = o.partnummer
                AND f.entry_type IN ('customerInvoice', 'manualDebtorInvoice', 'supplierInvoice')
                AND sign(f.restbeloeb) = -sign(o.restbeloeb)
          )
        ON CONFLICT (client_id, regel, kilde_id) DO NOTHING
        RETURNING 1
    )
    SELECT count(*)::integer FROM nye;
$$;
"""

REGEL_3 = f"""
CREATE FUNCTION regel_3_forfalden_over_6_mdr(p_client_id integer DEFAULT NULL,
                                             p_dato date DEFAULT current_date)
RETURNS integer LANGUAGE sql AS $$
    WITH nye AS (
        INSERT INTO findings ({FAELLES_KOLONNER})
        SELECT o.client_id, 'forfalden_over_6_mdr', o.bogfoert_id, o.type, o.partnummer,
               o.partnavn, o.fakturanummer, o.dato, o.forfaldsdato, o.beloeb,
               o.restbeloeb, o.valuta,
               'Åben post forfaldet ' || o.forfaldsdato || ' – mere end 6 måneder før ' || p_dato
        FROM open_entries o
        JOIN clients c ON c.id = o.client_id
        WHERE c.status = 'aktiv'
          AND (p_client_id IS NULL OR o.client_id = p_client_id)
          AND o.forfaldsdato < (p_dato - interval '6 months')::date
        ON CONFLICT (client_id, regel, kilde_id) DO NOTHING
        RETURNING 1
    )
    SELECT count(*)::integer FROM nye;
$$;
"""

ENTRY_TYPER = ("customerInvoice", "customerPayment", "supplierInvoice", "supplierPayment",
               "financeVoucher", "reminder", "openingEntry", "transferredOpeningEntry",
               "systemEntry", "manualDebtorInvoice")


def upgrade() -> None:
    """Opgrader databasen."""
    op.create_table('findings',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('regel', sa.String(length=40), nullable=False),
    sa.Column('kilde_id', sa.BigInteger(), nullable=False),
    sa.Column('type', sa.String(length=10), nullable=True),
    sa.Column('partnummer', sa.BigInteger(), nullable=True),
    sa.Column('partnavn', sa.String(length=255), nullable=True),
    sa.Column('fakturanummer', sa.String(length=100), nullable=True),
    sa.Column('dato', sa.Date(), nullable=True),
    sa.Column('forfaldsdato', sa.Date(), nullable=True),
    sa.Column('beloeb', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('restbeloeb', sa.Numeric(precision=18, scale=2), nullable=True),
    sa.Column('valuta', sa.String(length=3), nullable=True),
    sa.Column('beskrivelse', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=10), server_default='aaben', nullable=False),
    sa.Column('fundet', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("regel IN ('smaa_restbeloeb', 'betaling_uden_faktura', 'forfalden_over_6_mdr')", name=op.f('ck_findings_regel_gyldig')),
    sa.CheckConstraint("status IN ('aaben', 'loest', 'afvist')", name=op.f('ck_findings_status_gyldig')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_findings_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_findings')),
    sa.UniqueConstraint('client_id', 'regel', 'kilde_id', name='uq_findings_kunde_regel_post')
    )
    op.create_index(op.f('ix_findings_status'), 'findings', ['status'], unique=False)
    op.add_column('open_entries', sa.Column('entry_type', sa.String(length=30), nullable=True))
    op.create_check_constraint(op.f("ck_open_entries_entry_type_gyldig"), "open_entries",
                               "entry_type IN (" + ", ".join(f"'{t}'" for t in ENTRY_TYPER) + ")")
    for sql in (REGEL_1, REGEL_2, REGEL_3):
        op.execute(sql)


def downgrade() -> None:
    """Nedgrader databasen."""
    op.execute("DROP FUNCTION regel_3_forfalden_over_6_mdr(integer, date)")
    op.execute("DROP FUNCTION regel_2_betaling_uden_faktura(integer)")
    op.execute("DROP FUNCTION regel_1_smaa_restbeloeb(integer)")
    op.drop_constraint(op.f("ck_open_entries_entry_type_gyldig"), "open_entries", type_="check")
    op.drop_column('open_entries', 'entry_type')
    op.drop_index(op.f('ix_findings_status'), table_name='findings')
    op.drop_table('findings')
