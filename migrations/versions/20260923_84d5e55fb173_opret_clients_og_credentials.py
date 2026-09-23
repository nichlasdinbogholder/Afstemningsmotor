"""opret clients og credentials

Revision ID: 84d5e55fb173
Revises: 
Create Date: 2026-09-23 18:30:21.336657

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '84d5e55fb173'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Fernet-krypterede værdier starter altid med 'gAAAAA'.
TOKEN_TRIGGER_FUNKTION = """
CREATE FUNCTION credentials_kraev_krypteret_token() RETURNS trigger AS $$
BEGIN
    IF NEW.token_krypteret IS NOT NULL
       AND left(NEW.token_krypteret, 6) <> 'gAAAAA' THEN
        RAISE EXCEPTION USING
            ERRCODE = 'check_violation',
            MESSAGE = 'credentials.token_krypteret skal være krypteret';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    """Opretter kundekartoteket (clients) og adgange (credentials)."""
    op.create_table('clients',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('navn', sa.String(length=255), nullable=False),
    sa.Column('cvr', sa.String(length=8), nullable=True),
    sa.Column('kundenummer', sa.String(length=50), nullable=False),
    sa.Column('system', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='aktiv', nullable=False),
    sa.Column('ansvarlig_medarbejder', sa.String(length=255), nullable=True),
    sa.Column('aftaletype', sa.String(length=100), nullable=True),
    sa.Column('startdato', sa.Date(), nullable=True),
    sa.Column('kassekladdenavn', sa.String(length=255), nullable=True),
    sa.Column('oprettet', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("cvr ~ '^[0-9]{8}$'", name=op.f('ck_clients_cvr_8_cifre')),
    sa.CheckConstraint("status IN ('aktiv', 'pause', 'ophoert')", name=op.f('ck_clients_status_gyldig')),
    sa.CheckConstraint("system IN ('economic', 'dinero')", name=op.f('ck_clients_system_gyldig')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_clients')),
    sa.UniqueConstraint('cvr', name=op.f('uq_clients_cvr')),
    sa.UniqueConstraint('kundenummer', name=op.f('uq_clients_kundenummer'))
    )
    op.create_table('credentials',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('systemnavn', sa.String(length=20), nullable=False),
    sa.Column('token_krypteret', sa.Text(), nullable=False),
    sa.Column('organisation_id', sa.String(length=64), nullable=True),
    sa.Column('sidst_fornyet', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='aktiv', nullable=False),
    sa.CheckConstraint("status IN ('aktiv', 'udloebet', 'tilbagekaldt')", name=op.f('ck_credentials_status_gyldig')),
    sa.CheckConstraint("systemnavn IN ('economic', 'dinero')", name=op.f('ck_credentials_systemnavn_gyldig')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_credentials_client_id_clients'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_credentials')),
    sa.UniqueConstraint('client_id', 'systemnavn', name='uq_credentials_client_system')
    )
    op.create_index(op.f('ix_credentials_client_id'), 'credentials', ['client_id'], unique=False)

    # Sikkerhedsnet: databasen afviser tokens, der ikke er krypteret.
    # Fejlbeskeden indeholder bevidst ikke værdien, så intet token havner i logs.
    op.execute(TOKEN_TRIGGER_FUNKTION)
    op.execute(
        "CREATE TRIGGER credentials_kraev_krypteret_token "
        "BEFORE INSERT OR UPDATE OF token_krypteret ON credentials "
        "FOR EACH ROW EXECUTE FUNCTION credentials_kraev_krypteret_token()"
    )


def downgrade() -> None:
    """Fjerner tabellerne igen."""
    op.execute("DROP TRIGGER IF EXISTS credentials_kraev_krypteret_token ON credentials")
    op.execute("DROP FUNCTION IF EXISTS credentials_kraev_krypteret_token()")
    op.drop_index(op.f('ix_credentials_client_id'), table_name='credentials')
    op.drop_table('credentials')
    op.drop_table('clients')
