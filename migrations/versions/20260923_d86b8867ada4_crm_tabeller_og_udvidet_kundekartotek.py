"""CRM-tabeller og udvidet kundekartotek

Opretter staff, contacts, tasks, time_entries, notes, documents, handovers og
audit_log, og udvider clients og credentials. Eksisterende data i clients og
credentials bevares: kolonner omdøbes i stedet for at blive slettet.

Revision ID: d86b8867ada4
Revises: 84d5e55fb173
Create Date: 2026-09-23 19:14:40.589244

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd86b8867ada4'
down_revision: Union[str, Sequence[str], None] = '84d5e55fb173'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CLIENTS_REGLER = {
    "ck_clients_opgave_frekvens_gyldig": "opgave_frekvens IN ('maanedligt', 'kvartalsvis', 'aarligt')",
    "ck_clients_momsperiode_gyldig": "momsperiode IN ('maaned', 'kvartal', 'halvaar')",
    "ck_clients_aftaletype_gyldig": "aftaletype IN ('fast_pris', 'timer')",
    "ck_clients_regnskabsaar_slut_dd_mm": "regnskabsaar_slut ~ '^(0[1-9]|[12][0-9]|3[01])-(0[1-9]|1[0-2])$'",
    "ck_clients_antal_ansatte_ikke_negativ": "antal_ansatte >= 0",
    "ck_clients_loenkoersel_dag_1_31": "loenkoersel_dag BETWEEN 1 AND 31",
    "ck_clients_opsagt_efter_start": "opsagt_dato IS NULL OR startdato IS NULL OR opsagt_dato >= startdato",
}

OPDATERET_FUNKTION = """
CREATE FUNCTION saet_opdateret() RETURNS trigger AS $$
BEGIN
    NEW.opdateret = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

AUDIT_KUN_TILFOEJ_FUNKTION = """
CREATE FUNCTION audit_log_kun_tilfoej() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = 'insufficient_privilege',
        MESSAGE = 'audit_log kan ikke ændres eller slettes';
END;
$$ LANGUAGE plpgsql;
"""


def _stop_hvis_data_gaar_tabt() -> None:
    """Stop migreringen, hvis eksisterende kundedata ikke kan flyttes sikkert."""
    antal = op.get_bind().execute(sa.text(
        "SELECT count(*) FROM clients WHERE ansvarlig_medarbejder IS NOT NULL "
        "OR (aftaletype IS NOT NULL AND aftaletype NOT IN ('fast_pris', 'timer'))"
    )).scalar()
    if antal:
        raise RuntimeError(
            f"{antal} kunde(r) har udfyldt ansvarlig_medarbejder (fri tekst) eller en "
            "aftaletype, der ikke er 'fast_pris'/'timer'. Ret dem først – migreringen "
            "sletter ikke data."
        )


def upgrade() -> None:
    """Opgrader databasen."""
    _stop_hvis_data_gaar_tabt()

    # --- Nye tabeller -------------------------------------------------------
    op.create_table('staff',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('navn', sa.String(length=255), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('rolle', sa.String(length=20), server_default='medarbejder', nullable=False),
    sa.Column('aktiv', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('oprettet', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("rolle IN ('admin', 'medarbejder')", name=op.f('ck_staff_rolle_gyldig')),
    sa.CheckConstraint('email = lower(email)', name=op.f('ck_staff_email_smaa_bogstaver')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_staff')),
    sa.UniqueConstraint('email', name=op.f('uq_staff_email'))
    )
    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=True),
    sa.Column('staff_id', sa.Integer(), nullable=True),
    sa.Column('handling', sa.String(length=100), nullable=False),
    sa.Column('detaljer', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('tidspunkt', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_audit_log_client_id_clients'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], name=op.f('fk_audit_log_staff_id_staff'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_log'))
    )
    op.create_index(op.f('ix_audit_log_client_id'), 'audit_log', ['client_id'], unique=False)
    op.create_index(op.f('ix_audit_log_staff_id'), 'audit_log', ['staff_id'], unique=False)
    op.create_index(op.f('ix_audit_log_tidspunkt'), 'audit_log', ['tidspunkt'], unique=False)
    op.create_table('contacts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('navn', sa.String(length=255), nullable=False),
    sa.Column('rolle', sa.String(length=100), nullable=True),
    sa.Column('telefon', sa.String(length=50), nullable=True),
    sa.Column('email', sa.String(length=255), nullable=True),
    sa.Column('primaer', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_contacts_client_id_clients'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_contacts'))
    )
    op.create_index(op.f('ix_contacts_client_id'), 'contacts', ['client_id'], unique=False)
    op.create_index('uq_contacts_en_primaer_pr_kunde', 'contacts', ['client_id'], unique=True, postgresql_where=sa.text('primaer'))
    op.create_table('documents',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('filnavn', sa.String(length=255), nullable=False),
    sa.Column('filsti', sa.String(length=1024), nullable=False),
    sa.Column('dokumenttype', sa.String(length=100), nullable=True),
    sa.Column('uploadet_af', sa.Integer(), nullable=True),
    sa.Column('uploadet', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_documents_client_id_clients'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['uploadet_af'], ['staff.id'], name=op.f('fk_documents_uploadet_af_staff'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_documents'))
    )
    op.create_index(op.f('ix_documents_client_id'), 'documents', ['client_id'], unique=False)
    op.create_index(op.f('ix_documents_uploadet_af'), 'documents', ['uploadet_af'], unique=False)
    op.create_table('handovers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('fra_staff_id', sa.Integer(), nullable=False),
    sa.Column('til_staff_id', sa.Integer(), nullable=False),
    sa.Column('periode_start', sa.Date(), nullable=False),
    sa.Column('periode_slut', sa.Date(), nullable=False),
    sa.Column('aftalt_ugedag', sa.String(length=10), nullable=True),
    sa.Column('kommentar', sa.Text(), nullable=True),
    sa.Column('vigtig_info', sa.Text(), nullable=True),
    sa.Column('fysisk_overlevering_sket', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('kvitteret_af_staff_id', sa.Integer(), nullable=True),
    sa.Column('kvitteret_tidspunkt', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("aftalt_ugedag IN ('mandag', 'tirsdag', 'onsdag', 'torsdag', 'fredag', 'loerdag', 'soendag')", name=op.f('ck_handovers_aftalt_ugedag_gyldig')),
    sa.CheckConstraint('(kvitteret_af_staff_id IS NULL) = (kvitteret_tidspunkt IS NULL)', name=op.f('ck_handovers_kvittering_komplet')),
    sa.CheckConstraint('fra_staff_id <> til_staff_id', name=op.f('ck_handovers_fra_og_til_forskellige')),
    sa.CheckConstraint('periode_slut >= periode_start', name=op.f('ck_handovers_periode_slut_efter_start')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_handovers_client_id_clients'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['fra_staff_id'], ['staff.id'], name=op.f('fk_handovers_fra_staff_id_staff'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['kvitteret_af_staff_id'], ['staff.id'], name=op.f('fk_handovers_kvitteret_af_staff_id_staff'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['til_staff_id'], ['staff.id'], name=op.f('fk_handovers_til_staff_id_staff'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_handovers'))
    )
    op.create_index(op.f('ix_handovers_client_id'), 'handovers', ['client_id'], unique=False)
    op.create_index(op.f('ix_handovers_fra_staff_id'), 'handovers', ['fra_staff_id'], unique=False)
    op.create_index(op.f('ix_handovers_kvitteret_af_staff_id'), 'handovers', ['kvitteret_af_staff_id'], unique=False)
    op.create_index(op.f('ix_handovers_til_staff_id'), 'handovers', ['til_staff_id'], unique=False)
    op.create_table('notes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('staff_id', sa.Integer(), nullable=True),
    sa.Column('tekst', sa.Text(), nullable=False),
    sa.Column('oprettet', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_notes_client_id_clients'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], name=op.f('fk_notes_staff_id_staff'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notes'))
    )
    op.create_index(op.f('ix_notes_client_id'), 'notes', ['client_id'], unique=False)
    op.create_index(op.f('ix_notes_staff_id'), 'notes', ['staff_id'], unique=False)
    op.create_table('tasks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('type', sa.String(length=100), nullable=True),
    sa.Column('titel', sa.String(length=255), nullable=False),
    sa.Column('beskrivelse', sa.Text(), nullable=True),
    sa.Column('ansvarlig_id', sa.Integer(), nullable=True),
    sa.Column('frist', sa.Date(), nullable=True),
    sa.Column('status', sa.String(length=20), server_default='aaben', nullable=False),
    sa.Column('oprettet_af', sa.String(length=20), server_default='medarbejder', nullable=False),
    sa.Column('oprettet', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('loest', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("oprettet_af IN ('medarbejder', 'system')", name=op.f('ck_tasks_oprettet_af_gyldig')),
    sa.CheckConstraint("status IN ('aaben', 'i_gang', 'loest', 'afvist')", name=op.f('ck_tasks_status_gyldig')),
    sa.ForeignKeyConstraint(['ansvarlig_id'], ['staff.id'], name=op.f('fk_tasks_ansvarlig_id_staff'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_tasks_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tasks'))
    )
    op.create_index(op.f('ix_tasks_ansvarlig_id'), 'tasks', ['ansvarlig_id'], unique=False)
    op.create_index(op.f('ix_tasks_client_id'), 'tasks', ['client_id'], unique=False)
    op.create_table('time_entries',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('staff_id', sa.Integer(), nullable=False),
    sa.Column('dato', sa.Date(), nullable=False),
    sa.Column('timer', sa.Numeric(precision=4, scale=2), nullable=False),
    sa.Column('opgavetype', sa.String(length=100), nullable=True),
    sa.Column('kommentar', sa.Text(), nullable=True),
    sa.Column('faktureret', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('oprettet', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('timer > 0 AND timer <= 24', name=op.f('ck_time_entries_timer_0_til_24')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_time_entries_client_id_clients'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], name=op.f('fk_time_entries_staff_id_staff'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_time_entries'))
    )
    op.create_index(op.f('ix_time_entries_client_id'), 'time_entries', ['client_id'], unique=False)
    op.create_index(op.f('ix_time_entries_staff_id'), 'time_entries', ['staff_id'], unique=False)

    # --- clients: omdøb og udvid (data bevares) -----------------------------
    op.alter_column('clients', 'kassekladdenavn', new_column_name='kassekladde_navn')
    op.drop_constraint(op.f('ck_clients_system_gyldig'), 'clients', type_='check')
    op.alter_column('clients', 'system', new_column_name='regnskabssystem', nullable=True)
    op.create_check_constraint(op.f('ck_clients_regnskabssystem_gyldig'), 'clients',
                               "regnskabssystem IN ('economic', 'dinero')")
    op.drop_constraint(op.f('ck_clients_status_gyldig'), 'clients', type_='check')
    op.execute("UPDATE clients SET status = 'opsagt' WHERE status = 'ophoert'")
    op.create_check_constraint(op.f('ck_clients_status_gyldig'), 'clients',
                               "status IN ('aktiv', 'pause', 'opsagt')")
    op.drop_column('clients', 'ansvarlig_medarbejder')
    op.add_column('clients', sa.Column('adresse', sa.String(length=255), nullable=True))
    op.add_column('clients', sa.Column('postnr', sa.String(length=10), nullable=True))
    op.add_column('clients', sa.Column('by', sa.String(length=100), nullable=True))
    op.add_column('clients', sa.Column('virksomhedsform', sa.String(length=50), nullable=True))
    op.add_column('clients', sa.Column('branche', sa.String(length=255), nullable=True))
    op.add_column('clients', sa.Column('regnskabsaar_slut', sa.String(length=5), nullable=True))
    op.add_column('clients', sa.Column('antal_ansatte', sa.Integer(), nullable=True))
    op.add_column('clients', sa.Column('opgave_frekvens', sa.String(length=20), nullable=True))
    op.add_column('clients', sa.Column('opsagt_dato', sa.Date(), nullable=True))
    op.add_column('clients', sa.Column('ansvarlig_medarbejder_id', sa.Integer(), nullable=True))
    op.add_column('clients', sa.Column('daglig_medarbejder_id', sa.Integer(), nullable=True))
    op.add_column('clients', sa.Column('aftalenummer', sa.String(length=50), nullable=True))
    op.add_column('clients', sa.Column('momsperiode', sa.String(length=20), nullable=True))
    op.add_column('clients', sa.Column('loensystem', sa.String(length=100), nullable=True))
    op.add_column('clients', sa.Column('loenkoersel_dag', sa.SmallInteger(), nullable=True))
    op.add_column('clients', sa.Column('revisor_firma', sa.String(length=255), nullable=True))
    op.add_column('clients', sa.Column('revisor_kontakt', sa.String(length=255), nullable=True))
    op.add_column('clients', sa.Column('revisor_telefon', sa.String(length=50), nullable=True))
    op.add_column('clients', sa.Column('revisor_email', sa.String(length=255), nullable=True))
    op.add_column('clients', sa.Column('sagsstyring', sa.Text(), nullable=True))
    op.add_column('clients', sa.Column('betalinger', sa.Text(), nullable=True))
    op.add_column('clients', sa.Column('bilagshaandtering', sa.Text(), nullable=True))
    op.add_column('clients', sa.Column('debitorstyring', sa.Text(), nullable=True))
    op.add_column('clients', sa.Column('eboks_adgang', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('clients', sa.Column('pleo', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('clients', sa.Column('opdateret', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    op.alter_column('clients', 'aftaletype',
               existing_type=sa.VARCHAR(length=100),
               type_=sa.String(length=20),
               existing_nullable=True)
    for navn, regel in CLIENTS_REGLER.items():
        op.create_check_constraint(op.f(navn), 'clients', regel)
    op.create_index(op.f('ix_clients_ansvarlig_medarbejder_id'), 'clients', ['ansvarlig_medarbejder_id'], unique=False)
    op.create_index(op.f('ix_clients_daglig_medarbejder_id'), 'clients', ['daglig_medarbejder_id'], unique=False)
    op.create_foreign_key(op.f('fk_clients_ansvarlig_medarbejder_id_staff'), 'clients', 'staff', ['ansvarlig_medarbejder_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key(op.f('fk_clients_daglig_medarbejder_id_staff'), 'clients', 'staff', ['daglig_medarbejder_id'], ['id'], ondelete='SET NULL')
    op.execute(OPDATERET_FUNKTION)
    op.execute(
        "CREATE TRIGGER clients_saet_opdateret BEFORE UPDATE ON clients "
        "FOR EACH ROW EXECUTE FUNCTION saet_opdateret()"
    )

    # --- credentials: omdøb og udvid (tokens bevares, stadig krypteret) -----
    op.drop_constraint(op.f('ck_credentials_systemnavn_gyldig'), 'credentials', type_='check')
    op.alter_column('credentials', 'systemnavn', new_column_name='system')
    op.create_check_constraint(op.f('ck_credentials_system_gyldig'), 'credentials',
                               "system IN ('economic', 'dinero')")
    op.add_column('credentials', sa.Column('aftale_id', sa.String(length=64), nullable=True))
    op.add_column('credentials', sa.Column('oprettet', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))

    # --- audit_log: kan kun tilføjes, aldrig ændres eller slettes -----------
    op.execute(AUDIT_KUN_TILFOEJ_FUNKTION)
    op.execute(
        "CREATE TRIGGER audit_log_kun_tilfoej BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_kun_tilfoej()"
    )


def downgrade() -> None:
    """Nedgrader databasen (fjerner de nye tabeller og ruller ændringerne tilbage)."""
    op.execute("DROP TRIGGER audit_log_kun_tilfoej ON audit_log")
    op.execute("DROP FUNCTION audit_log_kun_tilfoej()")

    op.drop_column('credentials', 'oprettet')
    op.drop_column('credentials', 'aftale_id')
    op.drop_constraint(op.f('ck_credentials_system_gyldig'), 'credentials', type_='check')
    op.alter_column('credentials', 'system', new_column_name='systemnavn')
    op.create_check_constraint(op.f('ck_credentials_systemnavn_gyldig'), 'credentials',
                               "systemnavn IN ('economic', 'dinero')")

    op.execute("DROP TRIGGER clients_saet_opdateret ON clients")
    op.execute("DROP FUNCTION saet_opdateret()")
    op.drop_constraint(op.f('fk_clients_daglig_medarbejder_id_staff'), 'clients', type_='foreignkey')
    op.drop_constraint(op.f('fk_clients_ansvarlig_medarbejder_id_staff'), 'clients', type_='foreignkey')
    op.drop_index('ix_clients_daglig_medarbejder_id', table_name='clients')
    op.drop_index('ix_clients_ansvarlig_medarbejder_id', table_name='clients')
    for navn in CLIENTS_REGLER:
        op.drop_constraint(op.f(navn), 'clients', type_='check')
    op.alter_column('clients', 'aftaletype',
               existing_type=sa.String(length=20),
               type_=sa.VARCHAR(length=100),
               existing_nullable=True)
    for kolonne in (
        'opdateret', 'pleo', 'eboks_adgang', 'debitorstyring', 'bilagshaandtering',
        'betalinger', 'sagsstyring', 'revisor_email', 'revisor_telefon',
        'revisor_kontakt', 'revisor_firma', 'loenkoersel_dag', 'loensystem',
        'momsperiode', 'aftalenummer', 'daglig_medarbejder_id',
        'ansvarlig_medarbejder_id', 'opsagt_dato', 'opgave_frekvens', 'antal_ansatte',
        'regnskabsaar_slut', 'branche', 'virksomhedsform', 'by', 'postnr', 'adresse',
    ):
        op.drop_column('clients', kolonne)
    op.add_column('clients', sa.Column('ansvarlig_medarbejder', sa.String(length=255), nullable=True))
    op.drop_constraint(op.f('ck_clients_status_gyldig'), 'clients', type_='check')
    op.execute("UPDATE clients SET status = 'ophoert' WHERE status = 'opsagt'")
    op.create_check_constraint(op.f('ck_clients_status_gyldig'), 'clients',
                               "status IN ('aktiv', 'pause', 'ophoert')")
    op.drop_constraint(op.f('ck_clients_regnskabssystem_gyldig'), 'clients', type_='check')
    # Fejler, hvis en kunde ikke har noget regnskabssystem (det var påkrævet før).
    op.alter_column('clients', 'regnskabssystem', new_column_name='system', nullable=False)
    op.create_check_constraint(op.f('ck_clients_system_gyldig'), 'clients',
                               "system IN ('economic', 'dinero')")
    op.alter_column('clients', 'kassekladde_navn', new_column_name='kassekladdenavn')

    for tabel in ('time_entries', 'tasks', 'notes', 'handovers', 'documents',
                  'contacts', 'audit_log', 'staff'):
        op.drop_table(tabel)
