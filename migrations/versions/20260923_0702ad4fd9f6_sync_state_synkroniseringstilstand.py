"""Tabellen sync_state: hvor langt synkroniseringen er nået pr. kunde og ressource

Opretter også visningen synk_kraever_handling (kunder, der er bagud eller fejler)
og en trigger, der opdaterer kolonnen opdateret.

Revision ID: 0702ad4fd9f6
Revises: e99fc7cf3f27
Create Date: 2026-09-23 20:20:23.173041

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0702ad4fd9f6'
down_revision: Union[str, Sequence[str], None] = 'e99fc7cf3f27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


KRAEVER_HANDLING = """
CREATE VIEW synk_kraever_handling AS
SELECT c.id AS client_id, c.kundenummer, c.navn, s.ressource, s.status,
       CASE
           WHEN s.status = 'fejlet' THEN 'Fejlet ' || s.antal_fejl_i_traek || ' gange i træk – kræver handling'
           WHEN s.status = 'forsinket' THEN 'Fejler – prøver igen automatisk'
           WHEN s.sidste_ok IS NULL THEN 'Aldrig hentet'
           ELSE 'Ikke hentet i over 48 timer'
       END AS aarsag,
       s.antal_fejl_i_traek, s.sidste_ok, s.sidste_fejl_tidspunkt, s.sidste_fejl_besked,
       s.naeste_koersel, s.cursor, s.cursor_type
FROM sync_state s
JOIN clients c ON c.id = s.client_id
WHERE c.status = 'aktiv'
  AND s.status <> 'deaktiveret'
  AND (s.status IN ('fejlet', 'forsinket')
       OR s.sidste_ok IS NULL
       OR s.sidste_ok < now() - interval '48 hours')
ORDER BY (s.status = 'fejlet') DESC, s.antal_fejl_i_traek DESC, c.kundenummer, s.ressource
"""


def upgrade() -> None:
    """Opgrader databasen."""
    op.create_table('sync_state',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('ressource', sa.String(length=30), nullable=False),
    sa.Column('cursor', sa.Text(), nullable=True),
    sa.Column('cursor_type', sa.String(length=10), nullable=True),
    sa.Column('sidste_koersel_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sidste_ok', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sidste_fejl_tidspunkt', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sidste_fejl_besked', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=15), server_default='ok', nullable=False),
    sa.Column('antal_fejl_i_traek', sa.Integer(), server_default='0', nullable=False),
    sa.Column('naeste_koersel', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('antal_hentet_sidst', sa.Integer(), nullable=True),
    sa.Column('oprettet', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('opdateret', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("cursor_type IN ('dato', 'id', 'token')", name=op.f('ck_sync_state_cursor_type_gyldig')),
    sa.CheckConstraint("ressource IN ('accounts', 'entries', 'invoices', 'journals', 'suppliers')", name=op.f('ck_sync_state_ressource_gyldig')),
    sa.CheckConstraint("status IN ('ok', 'forsinket', 'fejlet', 'deaktiveret')", name=op.f('ck_sync_state_status_gyldig')),
    sa.CheckConstraint('antal_fejl_i_traek >= 0', name=op.f('ck_sync_state_antal_fejl_ikke_negativ')),
    sa.CheckConstraint('cursor IS NULL OR cursor_type IS NOT NULL', name=op.f('ck_sync_state_cursor_har_type')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_sync_state_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sync_state')),
    sa.UniqueConstraint('client_id', 'ressource', name='uq_sync_state_kunde_ressource')
    )
    op.create_index(op.f('ix_sync_state_naeste_koersel'), 'sync_state', ['naeste_koersel'], unique=False)
    op.create_index(op.f('ix_sync_state_status'), 'sync_state', ['status'], unique=False)
    # Genbruger funktionen saet_opdateret() fra migreringen, der oprettede clients.opdateret.
    op.execute(
        "CREATE TRIGGER sync_state_saet_opdateret BEFORE UPDATE ON sync_state "
        "FOR EACH ROW EXECUTE FUNCTION saet_opdateret()"
    )
    op.execute(KRAEVER_HANDLING)


def downgrade() -> None:
    """Nedgrader databasen."""
    op.execute("DROP VIEW synk_kraever_handling")
    op.execute("DROP TRIGGER sync_state_saet_opdateret ON sync_state")
    op.drop_index(op.f('ix_sync_state_status'), table_name='sync_state')
    op.drop_index(op.f('ix_sync_state_naeste_koersel'), table_name='sync_state')
    op.drop_table('sync_state')
