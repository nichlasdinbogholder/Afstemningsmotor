"""Tabellen jobs: jobkø til baggrundsopgaver

Revision ID: e99fc7cf3f27
Revises: 5d9297692965
Create Date: 2026-09-23 20:12:49.612315

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e99fc7cf3f27'
down_revision: Union[str, Sequence[str], None] = '5d9297692965'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Opgrader databasen."""
    op.create_table('jobs',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('type', sa.String(length=100), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=True),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('status', sa.String(length=10), server_default='koe', nullable=False),
    sa.Column('prioritet', sa.SmallInteger(), server_default='100', nullable=False),
    sa.Column('planlagt_til', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('paabegyndt', sa.DateTime(timezone=True), nullable=True),
    sa.Column('afsluttet', sa.DateTime(timezone=True), nullable=True),
    sa.Column('forsoeg', sa.Integer(), server_default='0', nullable=False),
    sa.Column('max_forsoeg', sa.Integer(), server_default='5', nullable=False),
    sa.Column('sidste_fejl', sa.Text(), nullable=True),
    sa.Column('laast_af', sa.String(length=255), nullable=True),
    sa.Column('laast_tidspunkt', sa.DateTime(timezone=True), nullable=True),
    sa.Column('idempotens_noegle', sa.String(length=255), nullable=False),
    sa.Column('oprettet', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('koe', 'i_gang', 'faerdig', 'fejlet')", name=op.f('ck_jobs_status_gyldig')),
    sa.CheckConstraint('(laast_af IS NULL) = (laast_tidspunkt IS NULL)', name=op.f('ck_jobs_laas_komplet')),
    sa.CheckConstraint('forsoeg >= 0', name=op.f('ck_jobs_forsoeg_ikke_negativ')),
    sa.CheckConstraint('max_forsoeg >= 1', name=op.f('ck_jobs_max_forsoeg_mindst_1')),
    sa.CheckConstraint('prioritet >= 0', name=op.f('ck_jobs_prioritet_ikke_negativ')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_jobs_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs')),
    sa.UniqueConstraint('idempotens_noegle', name=op.f('uq_jobs_idempotens_noegle'))
    )
    op.create_index(op.f('ix_jobs_client_id'), 'jobs', ['client_id'], unique=False)
    op.create_index('ix_jobs_klar', 'jobs', ['prioritet', 'planlagt_til'], unique=False, postgresql_where=sa.text("status = 'koe'"))
    op.create_index(op.f('ix_jobs_planlagt_til'), 'jobs', ['planlagt_til'], unique=False)
    op.create_index(op.f('ix_jobs_status'), 'jobs', ['status'], unique=False)


def downgrade() -> None:
    """Nedgrader databasen."""
    op.drop_index(op.f('ix_jobs_status'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_planlagt_til'), table_name='jobs')
    op.drop_index('ix_jobs_klar', table_name='jobs', postgresql_where=sa.text("status = 'koe'"))
    op.drop_index(op.f('ix_jobs_client_id'), table_name='jobs')
    op.drop_table('jobs')
