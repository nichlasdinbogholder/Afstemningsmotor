"""Kundeoversigten, trin 1 (spec 26.10.2026): kunde_-tabellerne, udvidelse af clients/contacts/credentials og de
tre triggere, der bærer reglerne (kunde_log kan ikke ændres; kunder kan ikke slettes, ophørte genåbnes kun bevidst,
skift af ansvarlig skrives i historikken; kun administrator kan sætte et token, og et gemt token kan ikke ændres).

Revision ID: ce3cd96fe13e
Revises: c41d8e2f9a63
Create Date: 2026-10-26 09:10:03.139099

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ce3cd96fe13e'
down_revision: Union[str, Sequence[str], None] = 'c41d8e2f9a63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# De triggere, der bærer reglerne. Fejlbeskederne gentager ALDRIG værdier fra rækken (heller ikke krypterede tokens).
TRIGGERE = """
-- 1) Kundens log kan kun tilføjes til.
CREATE FUNCTION kunde_log_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Kundens log kan ikke ændres eller slettes – den kan kun tilføjes til'
        USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER kunde_log_immutable BEFORE UPDATE OR DELETE ON kunde_log
    FOR EACH ROW EXECUTE FUNCTION kunde_log_immutable();
CREATE TRIGGER kunde_log_ingen_truncate BEFORE TRUNCATE ON kunde_log
    FOR EACH STATEMENT EXECUTE FUNCTION kunde_log_immutable();

-- 2) Tokens: kun en aktiv administrator (app.actor_id) må indsætte eller ændre; det krypterede felt kan aldrig
--    ændres (udskift = tilbagekald + ny række); et tilbagekaldt token kan ikke genoplives; intet slettes.
CREATE FUNCTION kunde_credential_guard() RETURNS trigger AS $$
DECLARE aktoer integer := NULLIF(current_setting('app.actor_id', true), '')::integer;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Et token slettes ikke – det tilbagekaldes' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF aktoer IS NULL OR NOT EXISTS (SELECT 1 FROM staff WHERE id = aktoer AND rolle = 'admin' AND aktiv) THEN
        RAISE EXCEPTION 'Kun en administrator kan sætte eller ændre et token'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.token_krypteret IS DISTINCT FROM OLD.token_krypteret THEN
            RAISE EXCEPTION 'Et gemt token kan ikke ændres – tilbagekald det og indsæt et nyt'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
            RAISE EXCEPTION 'Et tilbagekaldt token kan ikke tages i brug igen – indsæt et nyt'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
    ELSE
        NEW.set_by := aktoer;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER kunde_credential_guard BEFORE INSERT OR UPDATE OR DELETE ON credentials
    FOR EACH ROW EXECUTE FUNCTION kunde_credential_guard();

-- ... og hver indsættelse og tilbagekaldelse skrives i kundens log – med fingeraftrykket, ALDRIG værdien.
CREATE FUNCTION kunde_credential_log() RETURNS trigger AS $$
DECLARE aftale text := (SELECT label FROM kunde_ledgers WHERE id = NEW.ledger_id);
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO kunde_log (client_id, event, detail, actor_staff_id)
        VALUES (NEW.client_id, 'token_indsat',
                aftale || ' · ••••' || COALESCE(NEW.fingerprint, '????'), NEW.set_by);
    ELSIF OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL THEN
        INSERT INTO kunde_log (client_id, event, detail, actor_staff_id)
        VALUES (NEW.client_id, 'token_tilbagekaldt', aftale || ' · ••••' || COALESCE(NEW.fingerprint, '????'),
                NULLIF(current_setting('app.actor_id', true), '')::integer);
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER kunde_credential_log AFTER INSERT OR UPDATE ON credentials
    FOR EACH ROW EXECUTE FUNCTION kunde_credential_log();

-- 3) Kunder slettes aldrig (opbevaringspligt: 5 år efter ophør), og en ophørt kunde ('opsagt') kan kun genåbnes
--    bevidst (app.genaabning = 'ja', sat af kartotek.genaktiver).
CREATE FUNCTION kunde_lifecycle_guard() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Kunder slettes aldrig – arkivér kunden i stedet' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF OLD.status = 'opsagt' AND NEW.status <> 'opsagt'
       AND COALESCE(current_setting('app.genaabning', true), '') <> 'ja' THEN
        RAISE EXCEPTION 'En ophørt kunde kan kun genåbnes bevidst (Genaktivér under Arkiverede kunder)'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER kunde_lifecycle_guard BEFORE UPDATE OR DELETE ON clients
    FOR EACH ROW EXECUTE FUNCTION kunde_lifecycle_guard();

-- ... og skift af ansvarlig er en hændelse: den åbne historikrække lukkes, og en ny åbnes fra i dag (dansk tid).
-- Skiftes der igen samme dag, rettes dagens række (ingen tom periode). Aktør og årsag kommer fra app.actor_id og
-- app.ansvarlig_aarsag.
CREATE FUNCTION kunde_owner_history_skriv() RETURNS trigger AS $$
DECLARE dag date := (now() AT TIME ZONE 'Europe/Copenhagen')::date;
        aktoer integer := NULLIF(current_setting('app.actor_id', true), '')::integer;
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.ansvarlig_medarbejder_id IS NOT DISTINCT FROM OLD.ansvarlig_medarbejder_id THEN
        RETURN NULL;
    END IF;
    DELETE FROM kunde_owner_history WHERE client_id = NEW.id AND valid_to IS NULL AND valid_from >= dag;
    UPDATE kunde_owner_history SET valid_to = dag WHERE client_id = NEW.id AND valid_to IS NULL;
    IF NEW.ansvarlig_medarbejder_id IS NOT NULL THEN
        INSERT INTO kunde_owner_history (client_id, owner_staff_id, valid_from, changed_by, reason)
        VALUES (NEW.id, NEW.ansvarlig_medarbejder_id, dag, aktoer,
                NULLIF(current_setting('app.ansvarlig_aarsag', true), ''));
    END IF;
    IF TG_OP = 'UPDATE' THEN
        INSERT INTO kunde_log (client_id, event, detail, actor_staff_id)
        VALUES (NEW.id, 'ansvarlig_skiftet',
                COALESCE((SELECT navn FROM staff WHERE id = OLD.ansvarlig_medarbejder_id), 'ingen') || ' → ' ||
                COALESCE((SELECT navn FROM staff WHERE id = NEW.ansvarlig_medarbejder_id), 'ingen'), aktoer);
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER kunde_owner_history_skriv AFTER INSERT OR UPDATE OF ansvarlig_medarbejder_id ON clients
    FOR EACH ROW EXECUTE FUNCTION kunde_owner_history_skriv();
"""

FJERN_TRIGGERE = """
DROP TRIGGER IF EXISTS kunde_owner_history_skriv ON clients;
DROP FUNCTION IF EXISTS kunde_owner_history_skriv();
DROP TRIGGER IF EXISTS kunde_lifecycle_guard ON clients;
DROP FUNCTION IF EXISTS kunde_lifecycle_guard();
DROP TRIGGER IF EXISTS kunde_credential_log ON credentials;
DROP FUNCTION IF EXISTS kunde_credential_log();
DROP TRIGGER IF EXISTS kunde_credential_guard ON credentials;
DROP FUNCTION IF EXISTS kunde_credential_guard();
DROP TRIGGER IF EXISTS kunde_log_ingen_truncate ON kunde_log;
DROP TRIGGER IF EXISTS kunde_log_immutable ON kunde_log;
DROP FUNCTION IF EXISTS kunde_log_immutable();
"""


def upgrade() -> None:
    """Opgrader databasen."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('kunde_groups',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_kunde_groups'))
    )
    op.create_table('kunde_task_types',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('code', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('workday_number', sa.Integer(), nullable=False),
    sa.Column('applies_when', sa.Text(), nullable=True),
    sa.CheckConstraint('workday_number BETWEEN 1 AND 23', name=op.f('ck_kunde_task_types_hverdag_1_23')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_kunde_task_types')),
    sa.UniqueConstraint('code', name=op.f('uq_kunde_task_types_code'))
    )
    op.create_table('kunde_agreements',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('package', sa.Text(), nullable=True),
    sa.Column('notice_months', sa.Integer(), nullable=True),
    sa.Column('last_price_change', sa.Date(), nullable=True),
    sa.Column('started_on', sa.Date(), nullable=False),
    sa.Column('ended_on', sa.Date(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('ended_on IS NULL OR ended_on >= started_on', name=op.f('ck_kunde_agreements_slut_efter_start')),
    sa.CheckConstraint('notice_months IS NULL OR notice_months >= 0', name=op.f('ck_kunde_agreements_varsel_ikke_negativt')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_kunde_agreements_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_kunde_agreements'))
    )
    op.create_index(op.f('ix_kunde_agreements_client_id'), 'kunde_agreements', ['client_id'], unique=False)
    op.create_table('kunde_aliases',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('alias', sa.Text(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("source IN ('cvr', 'manuel')", name=op.f('ck_kunde_aliases_source_gyldig')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_kunde_aliases_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_kunde_aliases')),
    sa.UniqueConstraint('client_id', 'alias', name=op.f('uq_kunde_aliases_client_id'))
    )
    op.create_index(op.f('ix_kunde_aliases_client_id'), 'kunde_aliases', ['client_id'], unique=False)
    op.create_table('kunde_aml',
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('risk_level', sa.Text(), nullable=True),
    sa.Column('assessed_on', sa.Date(), nullable=True),
    sa.Column('assessed_by', sa.Integer(), nullable=True),
    sa.Column('id_verified_on', sa.Date(), nullable=True),
    sa.Column('beneficial_owners_checked_on', sa.Date(), nullable=True),
    sa.Column('next_review_on', sa.Date(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.CheckConstraint("risk_level IN ('lav', 'mellem', 'hoej')", name=op.f('ck_kunde_aml_risk_level_gyldig')),
    sa.ForeignKeyConstraint(['assessed_by'], ['staff.id'], name=op.f('fk_kunde_aml_assessed_by_staff'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_kunde_aml_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('client_id', name=op.f('pk_kunde_aml'))
    )
    op.create_index(op.f('ix_kunde_aml_assessed_by'), 'kunde_aml', ['assessed_by'], unique=False)
    op.create_table('kunde_authorities',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('granted_on', sa.Date(), nullable=True),
    sa.Column('expires_on', sa.Date(), nullable=True),
    sa.Column('status', sa.Text(), server_default='aktiv', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('tastselv', 'eindkomst', 'bank_psd2', 'nemkonto', 'andet')", name=op.f('ck_kunde_authorities_kind_gyldig')),
    sa.CheckConstraint("status IN ('aktiv', 'udloebet', 'tilbagekaldt')", name=op.f('ck_kunde_authorities_status_gyldig')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_kunde_authorities_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_kunde_authorities'))
    )
    op.create_index(op.f('ix_kunde_authorities_client_id'), 'kunde_authorities', ['client_id'], unique=False)
    op.create_table('kunde_checklists',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('item_code', sa.Text(), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_by', sa.Integer(), nullable=True),
    sa.CheckConstraint("kind IN ('onboarding', 'offboarding')", name=op.f('ck_kunde_checklists_kind_gyldig')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_kunde_checklists_client_id_clients'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['completed_by'], ['staff.id'], name=op.f('fk_kunde_checklists_completed_by_staff'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_kunde_checklists')),
    sa.UniqueConstraint('client_id', 'kind', 'item_code', name=op.f('uq_kunde_checklists_client_id'))
    )
    op.create_index(op.f('ix_kunde_checklists_client_id'), 'kunde_checklists', ['client_id'], unique=False)
    op.create_index(op.f('ix_kunde_checklists_completed_by'), 'kunde_checklists', ['completed_by'], unique=False)
    op.create_table('kunde_cvr_changes',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('field', sa.Text(), nullable=False),
    sa.Column('old_value', sa.Text(), nullable=True),
    sa.Column('new_value', sa.Text(), nullable=True),
    sa.Column('severity', sa.Text(), nullable=False),
    sa.Column('detected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('handled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('handled_by', sa.Integer(), nullable=True),
    sa.CheckConstraint("severity IN ('info', 'advarsel', 'kritisk')", name=op.f('ck_kunde_cvr_changes_severity_gyldig')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_kunde_cvr_changes_client_id_clients'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['handled_by'], ['staff.id'], name=op.f('fk_kunde_cvr_changes_handled_by_staff'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_kunde_cvr_changes'))
    )
    op.create_index(op.f('ix_kunde_cvr_changes_client_id'), 'kunde_cvr_changes', ['client_id'], unique=False)
    op.create_index(op.f('ix_kunde_cvr_changes_handled_by'), 'kunde_cvr_changes', ['handled_by'], unique=False)
    op.create_table('kunde_cvr_snapshot',
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.Text(), nullable=True),
    sa.Column('address', sa.Text(), nullable=True),
    sa.Column('company_form', sa.Text(), nullable=True),
    sa.Column('industry_code', sa.Text(), nullable=True),
    sa.Column('fiscal_year_end', sa.String(length=5), nullable=True),
    sa.Column('status', sa.Text(), nullable=True),
    sa.Column('beneficial_owners', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('fetched_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_kunde_cvr_snapshot_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('client_id', name=op.f('pk_kunde_cvr_snapshot'))
    )
    op.create_table('kunde_ledgers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('system', sa.Text(), nullable=False),
    sa.Column('label', sa.Text(), nullable=False),
    sa.Column('agreement_cvr', sa.Text(), nullable=True),
    sa.Column('agreement_name', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), server_default='ikke_tilsluttet', nullable=False),
    sa.Column('last_ok_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("agreement_cvr IS NULL OR agreement_cvr ~ '^[0-9]{8}$'", name=op.f('ck_kunde_ledgers_aftale_cvr_8_cifre')),
    sa.CheckConstraint("status IN ('ikke_tilsluttet', 'aktiv', 'afvist', 'fejlet')", name=op.f('ck_kunde_ledgers_status_gyldig')),
    sa.CheckConstraint("system IN ('economic', 'dinero')", name=op.f('ck_kunde_ledgers_system_gyldig')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_kunde_ledgers_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_kunde_ledgers'))
    )
    op.create_index(op.f('ix_kunde_ledgers_client_id'), 'kunde_ledgers', ['client_id'], unique=False)
    op.create_table('kunde_log',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('event', sa.Text(), nullable=False),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.Column('actor_staff_id', sa.Integer(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['actor_staff_id'], ['staff.id'], name=op.f('fk_kunde_log_actor_staff_id_staff'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_kunde_log_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_kunde_log'))
    )
    op.create_index(op.f('ix_kunde_log_actor_staff_id'), 'kunde_log', ['actor_staff_id'], unique=False)
    op.create_index(op.f('ix_kunde_log_client_id'), 'kunde_log', ['client_id'], unique=False)
    op.create_index('ix_kunde_log_kunde_tid', 'kunde_log', ['client_id', 'occurred_at'], unique=False)
    op.create_table('kunde_obligations',
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('vat_period', sa.Text(), nullable=True),
    sa.Column('fiscal_year_end', sa.String(length=5), nullable=True),
    sa.Column('payroll_frequency', sa.Text(), nullable=True),
    sa.Column('payroll_day', sa.SmallInteger(), nullable=True),
    sa.Column('has_a_skat', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('has_loensumsafgift', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.CheckConstraint("fiscal_year_end ~ '^(0[1-9]|[12][0-9]|3[01])-(0[1-9]|1[0-2])$'", name=op.f('ck_kunde_obligations_regnskabsaar_slut_dd_mm')),
    sa.CheckConstraint("payroll_frequency IN ('maaned', '14_dage', 'ingen')", name=op.f('ck_kunde_obligations_payroll_frequency_gyldig')),
    sa.CheckConstraint("vat_period IN ('maaned', 'kvartal', 'halvaar', 'ingen')", name=op.f('ck_kunde_obligations_vat_period_gyldig')),
    sa.CheckConstraint('payroll_day BETWEEN 1 AND 31', name=op.f('ck_kunde_obligations_loenkoersel_dag_1_31')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_kunde_obligations_client_id_clients'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('client_id', name=op.f('pk_kunde_obligations'))
    )
    op.create_table('kunde_owner_history',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('owner_staff_id', sa.Integer(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('changed_by', sa.Integer(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('valid_to IS NULL OR valid_to > valid_from', name=op.f('ck_kunde_owner_history_periode_gyldig')),
    sa.ForeignKeyConstraint(['changed_by'], ['staff.id'], name=op.f('fk_kunde_owner_history_changed_by_staff'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_kunde_owner_history_client_id_clients'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['owner_staff_id'], ['staff.id'], name=op.f('fk_kunde_owner_history_owner_staff_id_staff'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_kunde_owner_history'))
    )
    op.create_index(op.f('ix_kunde_owner_history_changed_by'), 'kunde_owner_history', ['changed_by'], unique=False)
    op.create_index(op.f('ix_kunde_owner_history_client_id'), 'kunde_owner_history', ['client_id'], unique=False)
    op.create_index(op.f('ix_kunde_owner_history_owner_staff_id'), 'kunde_owner_history', ['owner_staff_id'], unique=False)
    op.create_table('kunde_tasks',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('task_type_id', sa.BigInteger(), nullable=False),
    sa.Column('period', sa.Date(), nullable=False),
    sa.Column('due_date', sa.Date(), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_by', sa.Integer(), nullable=True),
    sa.CheckConstraint('extract(day FROM period) = 1', name=op.f('ck_kunde_tasks_periode_er_den_foerste')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_kunde_tasks_client_id_clients'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['completed_by'], ['staff.id'], name=op.f('fk_kunde_tasks_completed_by_staff'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['task_type_id'], ['kunde_task_types.id'], name=op.f('fk_kunde_tasks_task_type_id_kunde_task_types'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_kunde_tasks')),
    sa.UniqueConstraint('client_id', 'task_type_id', 'period', name=op.f('uq_kunde_tasks_client_id'))
    )
    op.create_index(op.f('ix_kunde_tasks_client_id'), 'kunde_tasks', ['client_id'], unique=False)
    op.create_index(op.f('ix_kunde_tasks_completed_by'), 'kunde_tasks', ['completed_by'], unique=False)
    op.create_index(op.f('ix_kunde_tasks_task_type_id'), 'kunde_tasks', ['task_type_id'], unique=False)
    # --- clients: "daglig medarbejder" hedder nu assistent (bygherren 26.10.2026) – omdøbes, så data bevares ---
    op.alter_column("clients", "daglig_medarbejder_id", new_column_name="assistent_id")
    op.execute("ALTER INDEX ix_clients_daglig_medarbejder_id RENAME TO ix_clients_assistent_id")
    op.execute("ALTER TABLE clients RENAME CONSTRAINT fk_clients_daglig_medarbejder_id_staff "
               "TO fk_clients_assistent_id_staff")
    op.add_column("clients", sa.Column("group_id", sa.BigInteger(), nullable=True))
    op.create_index(op.f("ix_clients_group_id"), "clients", ["group_id"], unique=False)
    op.create_foreign_key(op.f("fk_clients_group_id_kunde_groups"), "clients", "kunde_groups", ["group_id"], ["id"],
                          ondelete="RESTRICT")
    # Livscyklus: 'emne' tilføjes (Alembic ser ikke ændrede CHECK-regler).
    op.drop_constraint("status_gyldig", "clients", type_="check")
    op.create_check_constraint("status_gyldig", "clients", "status IN ('emne', 'aktiv', 'pause', 'opsagt')")

    # Fristparametrene flyttes til kunde_obligations, så de kun findes ét sted.
    op.execute("""
        INSERT INTO kunde_obligations (client_id, vat_period, fiscal_year_end, payroll_day)
        SELECT id, momsperiode, regnskabsaar_slut, loenkoersel_dag FROM clients
        WHERE momsperiode IS NOT NULL OR regnskabsaar_slut IS NOT NULL OR loenkoersel_dag IS NOT NULL
    """)
    for kolonne in ("loenkoersel_dag", "regnskabsaar_slut", "momsperiode", "opgave_frekvens", "aftaletype"):
        op.drop_column("clients", kolonne)

    # Nuværende ansvarlige bliver første række i historikken (fra startdatoen eller oprettelsen).
    op.execute("""
        INSERT INTO kunde_owner_history (client_id, owner_staff_id, valid_from, reason)
        SELECT id, ansvarlig_medarbejder_id, COALESCE(startdato, (oprettet AT TIME ZONE 'Europe/Copenhagen')::date),
               'Ansvarlig ved indførelsen af kundeoversigten'
        FROM clients WHERE ansvarlig_medarbejder_id IS NOT NULL
    """)
    op.execute("""
        ALTER TABLE kunde_owner_history ADD CONSTRAINT kunde_owner_history_ingen_overlap
        EXCLUDE USING gist (client_id WITH =, daterange(valid_from, valid_to, '[)') WITH &&)
    """)

    # --- contacts: underskriver, "må kontaktes af", arkivering; slettes ikke længere med kunden ---
    op.add_column("contacts", sa.Column("underskriver", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("contacts", sa.Column("maa_kontaktes_af", sa.String(length=10), server_default="alle",
                                        nullable=False))
    op.add_column("contacts", sa.Column("oprettet", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                                        nullable=False))
    op.add_column("contacts", sa.Column("arkiveret", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("maa_kontaktes_af_gyldig", "contacts", "maa_kontaktes_af IN ('alle', 'ansvarlig')")
    op.drop_index("uq_contacts_en_primaer_pr_kunde", table_name="contacts")
    op.create_index("uq_contacts_en_primaer_pr_kunde", "contacts", ["client_id"], unique=True,
                    postgresql_where=sa.text("primaer AND arkiveret IS NULL"))
    op.drop_constraint(op.f("fk_contacts_client_id_clients"), "contacts", type_="foreignkey")
    op.create_foreign_key(op.f("fk_contacts_client_id_clients"), "contacts", "clients", ["client_id"], ["id"],
                          ondelete="RESTRICT")

    # --- credentials: knyttes til en regnskabsaftale; udskiftning = tilbagekald + ny række ---
    op.add_column("credentials", sa.Column("ledger_id", sa.Integer(), nullable=True))
    op.add_column("credentials", sa.Column("fingerprint", sa.String(length=4), nullable=True))
    op.add_column("credentials", sa.Column("set_by", sa.Integer(), nullable=True))
    op.add_column("credentials", sa.Column("set_at", sa.DateTime(timezone=True), server_default=sa.text("now()"),
                                           nullable=False))
    op.add_column("credentials", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    # Hver eksisterende adgang bliver kundens første regnskabsaftale. Fingeraftrykket kan ikke laves for gamle tokens
    # (de dekrypteres aldrig her) – det kommer, når tokenet udskiftes.
    op.execute("""
        INSERT INTO kunde_ledgers (client_id, system, label, status, created_at)
        SELECT client_id, system, CASE system WHEN 'economic' THEN 'e-conomic' ELSE 'Dinero' END,
               CASE status WHEN 'aktiv' THEN 'aktiv' ELSE 'ikke_tilsluttet' END, oprettet
        FROM credentials ORDER BY id
    """)
    op.execute("""
        UPDATE credentials c SET ledger_id = l.id, set_at = COALESCE(c.sidst_fornyet, c.oprettet)
        FROM kunde_ledgers l WHERE l.client_id = c.client_id AND l.system = c.system
    """)
    op.execute("UPDATE credentials SET revoked_at = COALESCE(sidst_fornyet, oprettet) WHERE status = 'tilbagekaldt'")
    op.alter_column("credentials", "ledger_id", nullable=False)
    op.drop_constraint(op.f("uq_credentials_client_system"), "credentials", type_="unique")
    op.create_index(op.f("ix_credentials_ledger_id"), "credentials", ["ledger_id"], unique=False)
    op.create_index(op.f("ix_credentials_set_by"), "credentials", ["set_by"], unique=False)
    op.create_index("uq_credentials_et_aktivt_pr_aftale", "credentials", ["ledger_id"], unique=True,
                    postgresql_where=sa.text("revoked_at IS NULL"))
    op.create_check_constraint("tilbagekaldt_har_tidspunkt", "credentials",
                               "(revoked_at IS NULL) = (status <> 'tilbagekaldt')")
    op.drop_constraint(op.f("fk_credentials_client_id_clients"), "credentials", type_="foreignkey")
    op.create_foreign_key(op.f("fk_credentials_ledger_id_kunde_ledgers"), "credentials", "kunde_ledgers",
                          ["ledger_id"], ["id"], ondelete="RESTRICT")
    op.create_foreign_key(op.f("fk_credentials_client_id_clients"), "credentials", "clients", ["client_id"], ["id"],
                          ondelete="RESTRICT")
    op.create_foreign_key(op.f("fk_credentials_set_by_staff"), "credentials", "staff", ["set_by"], ["id"],
                          ondelete="RESTRICT")
    op.add_column('documents', sa.Column('arkiveret', sa.DateTime(timezone=True), nullable=True))
    op.add_column('notes', sa.Column('opdateret', sa.DateTime(timezone=True), nullable=True))
    # ### end Alembic commands ###
    op.execute(TRIGGERE)


def downgrade() -> None:
    """Nedgrader databasen. Afvises, hvis data ikke kan bringes tilbage uden tab."""
    forbindelse = op.get_bind()
    if forbindelse.execute(sa.text("SELECT count(*) FROM credentials GROUP BY client_id, system "
                                   "HAVING count(*) > 1 LIMIT 1")).scalar():
        raise RuntimeError("Kan ikke fortrydes: en kunde har flere tokens til samme system (udskiftede/aftaler)")
    if forbindelse.execute(sa.text("SELECT count(*) FROM clients WHERE status = 'emne'")).scalar():
        raise RuntimeError("Kan ikke fortrydes: der findes emner (status 'emne')")
    op.execute(FJERN_TRIGGERE)
    op.drop_column("notes", "opdateret")
    op.drop_column("documents", "arkiveret")

    op.drop_constraint(op.f("fk_credentials_set_by_staff"), "credentials", type_="foreignkey")
    op.drop_constraint(op.f("fk_credentials_client_id_clients"), "credentials", type_="foreignkey")
    op.drop_constraint(op.f("fk_credentials_ledger_id_kunde_ledgers"), "credentials", type_="foreignkey")
    op.create_foreign_key(op.f("fk_credentials_client_id_clients"), "credentials", "clients", ["client_id"], ["id"],
                          ondelete="CASCADE")
    op.drop_constraint("tilbagekaldt_har_tidspunkt", "credentials", type_="check")
    op.drop_index("uq_credentials_et_aktivt_pr_aftale", table_name="credentials")
    op.drop_index(op.f("ix_credentials_set_by"), table_name="credentials")
    op.drop_index(op.f("ix_credentials_ledger_id"), table_name="credentials")
    op.create_unique_constraint(op.f("uq_credentials_client_system"), "credentials", ["client_id", "system"])
    for kolonne in ("revoked_at", "set_at", "set_by", "fingerprint", "ledger_id"):
        op.drop_column("credentials", kolonne)

    op.drop_constraint(op.f("fk_contacts_client_id_clients"), "contacts", type_="foreignkey")
    op.create_foreign_key(op.f("fk_contacts_client_id_clients"), "contacts", "clients", ["client_id"], ["id"],
                          ondelete="CASCADE")
    op.drop_index("uq_contacts_en_primaer_pr_kunde", table_name="contacts")
    op.create_index("uq_contacts_en_primaer_pr_kunde", "contacts", ["client_id"], unique=True,
                    postgresql_where=sa.text("primaer"))
    op.drop_constraint("maa_kontaktes_af_gyldig", "contacts", type_="check")
    for kolonne in ("arkiveret", "oprettet", "maa_kontaktes_af", "underskriver"):
        op.drop_column("contacts", kolonne)

    op.add_column("clients", sa.Column("aftaletype", sa.VARCHAR(length=20), nullable=True))
    op.add_column("clients", sa.Column("opgave_frekvens", sa.VARCHAR(length=20), nullable=True))
    op.add_column("clients", sa.Column("momsperiode", sa.VARCHAR(length=20), nullable=True))
    op.add_column("clients", sa.Column("regnskabsaar_slut", sa.VARCHAR(length=5), nullable=True))
    op.add_column("clients", sa.Column("loenkoersel_dag", sa.SMALLINT(), nullable=True))
    op.create_check_constraint("opgave_frekvens_gyldig", "clients",
                               "opgave_frekvens IN ('maanedligt', 'kvartalsvis', 'aarligt')")
    op.create_check_constraint("momsperiode_gyldig", "clients", "momsperiode IN ('maaned', 'kvartal', 'halvaar')")
    op.create_check_constraint("aftaletype_gyldig", "clients", "aftaletype IN ('fast_pris', 'timer')")
    op.create_check_constraint("regnskabsaar_slut_dd_mm", "clients",
                               "regnskabsaar_slut ~ '^(0[1-9]|[12][0-9]|3[01])-(0[1-9]|1[0-2])$'")
    op.create_check_constraint("loenkoersel_dag_1_31", "clients", "loenkoersel_dag BETWEEN 1 AND 31")
    op.execute("""
        UPDATE clients c SET momsperiode = NULLIF(o.vat_period, 'ingen'), regnskabsaar_slut = o.fiscal_year_end,
                             loenkoersel_dag = o.payroll_day
        FROM kunde_obligations o WHERE o.client_id = c.id
    """)
    op.drop_constraint("status_gyldig", "clients", type_="check")
    op.create_check_constraint("status_gyldig", "clients", "status IN ('aktiv', 'pause', 'opsagt')")
    op.drop_constraint(op.f("fk_clients_group_id_kunde_groups"), "clients", type_="foreignkey")
    op.drop_index(op.f("ix_clients_group_id"), table_name="clients")
    op.drop_column("clients", "group_id")
    op.execute("ALTER TABLE clients RENAME CONSTRAINT fk_clients_assistent_id_staff "
               "TO fk_clients_daglig_medarbejder_id_staff")
    op.execute("ALTER INDEX ix_clients_assistent_id RENAME TO ix_clients_daglig_medarbejder_id")
    op.alter_column("clients", "assistent_id", new_column_name="daglig_medarbejder_id")

    for tabel in ("kunde_tasks", "kunde_owner_history", "kunde_obligations", "kunde_log", "kunde_ledgers",
                  "kunde_cvr_snapshot", "kunde_cvr_changes", "kunde_checklists", "kunde_authorities", "kunde_aml",
                  "kunde_aliases", "kunde_agreements", "kunde_task_types", "kunde_groups"):
        op.drop_table(tabel)
