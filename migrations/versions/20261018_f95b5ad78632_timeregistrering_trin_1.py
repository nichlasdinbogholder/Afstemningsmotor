"""Timeregistrering, fase 1 trin 1: tabellerne time_*, reglerne i databasen og stamdata

- Den gamle, ubrugte timetabel (time_entries fra CRM-starten) fjernes – migreringen stopper, hvis den har rækker.
- staff_cost_rates afløses af time_employee_rates (kostpris + normtid + produktive timer pr. år). De kostpriser, der
  allerede er tastet, kan ikke flyttes automatisk (ansættelsesform og produktive timer kendes ikke) – de gemmes
  derfor i audit_log ('kostpris_flyttet') med beløb og dato og skal tastes ind igen.
- Triggere: godkendt uge er låst; kun godkendte uger til løn; ferie blokeret for timelønnede; 'ikke på arbejde'
  tvinger 0 timer; bemærkninger/afspadseringsbevægelser/revisionsspor kan kun tilføjes; satser kan ikke slettes;
  alle ændringer logges i time_audit.
- Stamdata: opgavetyperne fra spec'en og salgsprisen 699,00 kr. ekskl. moms fra 18.10.2026.

Revision ID: f95b5ad78632
Revises: 8614a678abec
Create Date: 2026-10-18 11:30:18.241187

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f95b5ad78632'
down_revision: Union[str, Sequence[str], None] = '8614a678abec'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (code, navn, art, ude af effektivitet, må gå til løn, nedsætter afspadsering, tvinger 0 timer,
#  blokeret for timelønnede, aktiv). 'salg' kræver et pipeline-emne og er ikke aktiv, før pipelinen findes.
OPGAVETYPER = [
    ("aarsregnskab", "Årsregnskab", "kunde", False, True, False, False, False, True),
    ("loen", "Løn", "kunde", False, True, False, False, False, True),
    ("bogfoering", "Bogføring", "kunde", False, True, False, False, False, True),
    ("moms", "Moms", "kunde", False, True, False, False, False, True),
    ("afstemning", "Afstemning", "kunde", False, True, False, False, False, True),
    ("raadgivning", "Rådgivning og møder", "kunde", False, True, False, False, False, True),
    ("oprydning", "Oprydning", "kunde", False, True, False, False, False, True),
    ("materialerykning", "Rykker for materiale", "kunde", False, True, False, False, False, True),
    ("oevrigt_kunde", "Øvrigt kundearbejde", "kunde", False, True, False, False, False, True),
    ("oplaering", "Oplæring", "intern", True, True, False, False, False, True),
    ("interne_moeder", "Interne møder", "intern", False, True, False, False, False, True),
    ("administration", "Administration og øvrigt", "intern", False, True, False, False, False, True),
    ("salg", "Salg og tilbud", "intern", False, True, False, False, False, False),
    ("sygdom", "Sygdom", "fravaer", False, True, False, False, False, True),
    ("ferie", "Ferie", "fravaer", False, True, False, False, True, True),
    ("barn_syg", "Barns første sygedag", "fravaer", False, True, False, False, False, True),
    ("afspadsering", "Afspadsering afholdt", "fravaer", False, True, True, False, False, True),
    ("barsel", "Barsel", "fravaer", False, True, False, False, False, True),
    ("skoleophold", "Skoleophold", "fravaer", False, True, False, False, False, True),
    ("ikke_paa_arbejde", "Ikke på arbejde", "fravaer", False, False, False, True, False, True),
]

# Tabeller, hvor hver ændring skrives i time_audit.
LOGGES = ("time_employees", "time_employee_rates", "time_client_terms", "time_sales_rates", "time_task_types",
          "time_weeks", "time_entries", "time_entry_notes", "time_toil_settlements", "time_toil_movements",
          "time_payroll_batches", "time_payroll_lines")
KUN_TILFOEJELSER = ("time_entry_notes", "time_toil_movements", "time_audit")
SATSER = ("time_employee_rates", "time_client_terms", "time_sales_rates")

FOER = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM time_entries) THEN
        RAISE EXCEPTION 'Den gamle timetabel (time_entries) har rækker – de skal flyttes, før timeregistreringen kan oprettes';
    END IF;
END $$;
DROP TABLE time_entries;

INSERT INTO audit_log (staff_id, handling, detaljer)
SELECT r.staff_id, 'kostpris_flyttet',
       jsonb_build_object('kostpris_pr_time', r.hourly_cost::text, 'gyldig_fra', r.valid_from::text,
                          'oprettet_af', r.created_by, 'besked', 'Skal tastes ind igen under Medarbejdere (timeregistrering)')
FROM staff_cost_rates r;
DROP TRIGGER kostpris_slettes_ikke ON staff_cost_rates;
DROP FUNCTION kostpris_slettes_ikke();
DROP TABLE staff_cost_rates;

CREATE EXTENSION IF NOT EXISTS btree_gist;
"""

TRIGGERE = """
-- 1. En godkendt uge er låst – også for en linje, der flyttes ind i eller ud af den.
CREATE FUNCTION time_week_locked() RETURNS trigger AS $$
DECLARE s text;
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT status INTO s FROM time_weeks WHERE id = OLD.week_id;
        IF s = 'godkendt' THEN
            RAISE EXCEPTION 'Uge % er godkendt og kan ikke ændres. Opret en korrektionslinje i en senere periode.', OLD.week_id;
        END IF;
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        SELECT status INTO s FROM time_weeks WHERE id = NEW.week_id;
        IF s = 'godkendt' THEN
            RAISE EXCEPTION 'Uge % er godkendt og kan ikke ændres. Opret en korrektionslinje i en senere periode.', NEW.week_id;
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER time_entries_locked BEFORE INSERT OR UPDATE OR DELETE ON time_entries
    FOR EACH ROW EXECUTE FUNCTION time_week_locked();

-- 2. Kun godkendte uger må gå til løn.
CREATE FUNCTION time_payroll_requires_approval() RETURNS trigger AS $$
DECLARE s text;
BEGIN
    IF NEW.week_id IS NOT NULL THEN
        SELECT status INTO s FROM time_weeks WHERE id = NEW.week_id;
        IF s IS DISTINCT FROM 'godkendt' THEN
            RAISE EXCEPTION 'Uge % har status % og må ikke sendes til løn', NEW.week_id, s;
        END IF;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER time_payroll_lines_approved BEFORE INSERT ON time_payroll_lines
    FOR EACH ROW EXECUTE FUNCTION time_payroll_requires_approval();

-- 3. Opgavetypen bestemmer arten; ferie er blokeret for timelønnede; 'ikke på arbejde' er altid 0 timer;
--    linjen skal høre til medarbejderens egen uge og ligge inden for den.
CREATE FUNCTION time_entry_employment_rules() RETURNS trigger AS $$
DECLARE
    emp text;
    t record;
    u record;
BEGIN
    SELECT employment INTO emp FROM time_employees WHERE id = NEW.employee_id;
    SELECT kind, blocked_for_hourly, zero_hours, active INTO t FROM time_task_types WHERE id = NEW.task_type_id;
    NEW.task_kind := t.kind;
    IF emp = 'timeloennet' AND t.blocked_for_hourly THEN
        RAISE EXCEPTION 'Denne fraværsart kan ikke registreres for timelønnede';
    END IF;
    IF t.zero_hours AND NEW.hours <> 0 THEN
        RAISE EXCEPTION 'Denne art registreres altid med 0 timer';
    END IF;
    IF TG_OP = 'INSERT' AND NOT t.active THEN
        RAISE EXCEPTION 'Opgavetypen er ikke i brug';
    END IF;
    SELECT employee_id, week_start INTO u FROM time_weeks WHERE id = NEW.week_id;
    IF u.employee_id IS DISTINCT FROM NEW.employee_id THEN
        RAISE EXCEPTION 'Linjen skal høre til medarbejderens egen uge';
    END IF;
    IF NEW.work_date < u.week_start OR NEW.work_date > u.week_start + 6 THEN
        RAISE EXCEPTION 'Datoen ligger ikke i ugen';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER time_entries_employment BEFORE INSERT OR UPDATE ON time_entries
    FOR EACH ROW EXECUTE FUNCTION time_entry_employment_rules();

-- Bemærkninger, afspadseringsbevægelser og revisionssporet kan kun tilføjes.
CREATE FUNCTION time_kun_tilfoejelser() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Rækker i % kan ikke ændres eller slettes – de er historik', TG_TABLE_NAME;
END $$ LANGUAGE plpgsql;

-- Satser er historik (rapporter slår op på registreringsdatoen) – de kan ikke slettes.
CREATE FUNCTION time_satser_slettes_ikke() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Satser i % kan ikke slettes – afslut perioden med en slutdato i stedet', TG_TABLE_NAME;
END $$ LANGUAGE plpgsql;

-- Revisionsspor: hvem (app.actor_id, sat pr. transaktion), hvornår, fra hvad og til hvad.
CREATE FUNCTION time_skriv_audit() RETURNS trigger AS $$
DECLARE aktoer integer := NULLIF(current_setting('app.actor_id', true), '')::integer;
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO time_audit (table_name, row_id, action, actor_id, before, after)
        VALUES (TG_TABLE_NAME, NEW.id, TG_OP, aktoer, NULL, to_jsonb(NEW));
        RETURN NEW;
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO time_audit (table_name, row_id, action, actor_id, before, after)
        VALUES (TG_TABLE_NAME, NEW.id, TG_OP, aktoer, to_jsonb(OLD), to_jsonb(NEW));
        RETURN NEW;
    END IF;
    INSERT INTO time_audit (table_name, row_id, action, actor_id, before, after)
    VALUES (TG_TABLE_NAME, OLD.id, TG_OP, aktoer, to_jsonb(OLD), NULL);
    RETURN OLD;
END $$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    """Opgrader databasen."""
    op.execute(FOER)
    op.create_table('time_payroll_batches',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('period_start', sa.Date(), nullable=False),
    sa.Column('period_end', sa.Date(), nullable=False),
    sa.Column('idempotency_key', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default='kladde', nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('response_ref', sa.Text(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('kladde', 'sendt', 'fejl', 'bekraeftet')", name=op.f('ck_time_payroll_batches_status_gyldig')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_time_payroll_batches')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_time_payroll_batches_idempotency_key'))
    )
    op.create_table('time_sales_rates',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('rate_per_hour', sa.Numeric(precision=15, scale=2), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('period', postgresql.DATERANGE(), sa.Computed("daterange(valid_from, valid_to, '[)')", persisted=True), nullable=True),
    postgresql.ExcludeConstraint((sa.column('period'), '&&'), using='gist', name='time_sales_rates_ingen_overlap'),
    sa.CheckConstraint('rate_per_hour >= 0', name=op.f('ck_time_sales_rates_pris_ikke_negativ')),
    sa.CheckConstraint('valid_to IS NULL OR valid_to > valid_from', name=op.f('ck_time_sales_rates_periode_gyldig')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_time_sales_rates'))
    )
    op.create_table('time_task_types',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('code', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('excluded_from_efficiency', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('payable', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('reduces_toil', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('zero_hours', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('blocked_for_hourly', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.CheckConstraint("kind IN ('kunde', 'intern', 'fravaer')", name=op.f('ck_time_task_types_kind_gyldig')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_time_task_types')),
    sa.UniqueConstraint('code', name=op.f('uq_time_task_types_code'))
    )
    op.create_table('time_audit',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('table_name', sa.Text(), nullable=False),
    sa.Column('row_id', sa.BigInteger(), nullable=False),
    sa.Column('action', sa.Text(), nullable=False),
    sa.Column('actor_id', sa.Integer(), nullable=True),
    sa.Column('before', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('after', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("action IN ('INSERT', 'UPDATE', 'DELETE')", name=op.f('ck_time_audit_action_gyldig')),
    sa.ForeignKeyConstraint(['actor_id'], ['staff.id'], name=op.f('fk_time_audit_actor_id_staff'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_time_audit'))
    )
    op.create_index(op.f('ix_time_audit_actor_id'), 'time_audit', ['actor_id'], unique=False)
    op.create_table('time_employees',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('staff_id', sa.Integer(), nullable=False),
    sa.Column('employment', sa.Text(), nullable=False),
    sa.Column('flexjob', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('accrues_toil', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('payroll_ref', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("NOT (employment = 'timeloennet' AND accrues_toil)", name=op.f('ck_time_employees_timeloennet_uden_afspadsering')),
    sa.CheckConstraint("employment IN ('fastloennet', 'timeloennet')", name=op.f('ck_time_employees_employment_gyldig')),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], name=op.f('fk_time_employees_staff_id_staff'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_time_employees')),
    sa.UniqueConstraint('staff_id', name=op.f('uq_time_employees_staff_id'))
    )
    op.create_table('time_client_terms',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('billing_model', sa.Text(), nullable=False),
    sa.Column('subscription_price', sa.Numeric(precision=15, scale=2), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('period', postgresql.DATERANGE(), sa.Computed("daterange(valid_from, valid_to, '[)')", persisted=True), nullable=True),
    postgresql.ExcludeConstraint((sa.column('client_id'), '='), (sa.column('period'), '&&'), using='gist', name='time_client_terms_ingen_overlap'),
    sa.CheckConstraint("billing_model <> 'abonnement' OR subscription_price IS NOT NULL", name=op.f('ck_time_client_terms_abonnement_kraever_pris')),
    sa.CheckConstraint("billing_model IN ('abonnement', 'timeafregning')", name=op.f('ck_time_client_terms_billing_model_gyldig')),
    sa.CheckConstraint('subscription_price IS NULL OR subscription_price >= 0', name=op.f('ck_time_client_terms_pris_ikke_negativ')),
    sa.CheckConstraint('valid_to IS NULL OR valid_to > valid_from', name=op.f('ck_time_client_terms_periode_gyldig')),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_time_client_terms_client_id_clients'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by'], ['staff.id'], name=op.f('fk_time_client_terms_created_by_staff'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_time_client_terms'))
    )
    op.create_index(op.f('ix_time_client_terms_client_id'), 'time_client_terms', ['client_id'], unique=False)
    op.create_index(op.f('ix_time_client_terms_created_by'), 'time_client_terms', ['created_by'], unique=False)
    op.create_table('time_employee_rates',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('employee_id', sa.BigInteger(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('weekly_norm', sa.Numeric(precision=5, scale=2), nullable=True),
    sa.Column('cost_per_hour', sa.Numeric(precision=15, scale=2), nullable=False),
    sa.Column('productive_hours_year', sa.Numeric(precision=7, scale=2), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('period', postgresql.DATERANGE(), sa.Computed("daterange(valid_from, valid_to, '[)')", persisted=True), nullable=True),
    postgresql.ExcludeConstraint((sa.column('employee_id'), '='), (sa.column('period'), '&&'), using='gist', name='time_employee_rates_ingen_overlap'),
    sa.CheckConstraint('cost_per_hour >= 0', name=op.f('ck_time_employee_rates_kostpris_ikke_negativ')),
    sa.CheckConstraint('productive_hours_year > 0', name=op.f('ck_time_employee_rates_produktive_timer_positive')),
    sa.CheckConstraint('valid_to IS NULL OR valid_to > valid_from', name=op.f('ck_time_employee_rates_periode_gyldig')),
    sa.CheckConstraint('weekly_norm IS NULL OR (weekly_norm >= 0 AND weekly_norm <= 168)', name=op.f('ck_time_employee_rates_normtid_gyldig')),
    sa.ForeignKeyConstraint(['created_by'], ['staff.id'], name=op.f('fk_time_employee_rates_created_by_staff'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['employee_id'], ['time_employees.id'], name=op.f('fk_time_employee_rates_employee_id_time_employees'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_time_employee_rates'))
    )
    op.create_index(op.f('ix_time_employee_rates_created_by'), 'time_employee_rates', ['created_by'], unique=False)
    op.create_index(op.f('ix_time_employee_rates_employee_id'), 'time_employee_rates', ['employee_id'], unique=False)
    op.create_table('time_toil_settlements',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('employee_id', sa.BigInteger(), nullable=False),
    sa.Column('year', sa.Integer(), nullable=False),
    sa.Column('balance_hours', sa.Numeric(precision=7, scale=2), nullable=False),
    sa.Column('payout_hours', sa.Numeric(precision=7, scale=2), nullable=False),
    sa.Column('settled_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('settled_by', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['employee_id'], ['time_employees.id'], name=op.f('fk_time_toil_settlements_employee_id_time_employees'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['settled_by'], ['staff.id'], name=op.f('fk_time_toil_settlements_settled_by_staff'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_time_toil_settlements')),
    sa.UniqueConstraint('employee_id', 'year', name='uq_time_toil_settlements_medarbejder_aar')
    )
    op.create_index(op.f('ix_time_toil_settlements_settled_by'), 'time_toil_settlements', ['settled_by'], unique=False)
    op.create_table('time_weeks',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('employee_id', sa.BigInteger(), nullable=False),
    sa.Column('iso_year', sa.Integer(), nullable=False),
    sa.Column('iso_week', sa.Integer(), nullable=False),
    sa.Column('week_start', sa.Date(), nullable=False),
    sa.Column('status', sa.Text(), server_default='kladde', nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('rejected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('rejected_by', sa.Integer(), nullable=True),
    sa.Column('rejection_note', sa.Text(), nullable=True),
    sa.Column('resubmit_due', sa.Date(), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('approved_by', sa.Integer(), nullable=True),
    sa.Column('self_approved', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.CheckConstraint("status IN ('kladde', 'afleveret', 'afvist', 'godkendt')", name=op.f('ck_time_weeks_status_gyldig')),
    sa.CheckConstraint('extract(isodow FROM week_start) = 1', name=op.f('ck_time_weeks_uge_starter_mandag')),
    sa.CheckConstraint('iso_week BETWEEN 1 AND 53', name=op.f('ck_time_weeks_ugenummer_gyldigt')),
    sa.ForeignKeyConstraint(['approved_by'], ['staff.id'], name=op.f('fk_time_weeks_approved_by_staff'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['employee_id'], ['time_employees.id'], name=op.f('fk_time_weeks_employee_id_time_employees'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['rejected_by'], ['staff.id'], name=op.f('fk_time_weeks_rejected_by_staff'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_time_weeks')),
    sa.UniqueConstraint('employee_id', 'iso_year', 'iso_week', name='uq_time_weeks_medarbejder_uge')
    )
    op.create_index(op.f('ix_time_weeks_approved_by'), 'time_weeks', ['approved_by'], unique=False)
    op.create_index(op.f('ix_time_weeks_rejected_by'), 'time_weeks', ['rejected_by'], unique=False)
    op.create_table('time_payroll_lines',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('batch_id', sa.BigInteger(), nullable=False),
    sa.Column('employee_id', sa.BigInteger(), nullable=False),
    sa.Column('week_id', sa.BigInteger(), nullable=True),
    sa.Column('settlement_id', sa.BigInteger(), nullable=True),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('hours', sa.Numeric(precision=7, scale=2), nullable=False),
    sa.Column('idempotency_key', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('timeloen', 'afspadsering_udbetalt')", name=op.f('ck_time_payroll_lines_kind_gyldig')),
    sa.ForeignKeyConstraint(['batch_id'], ['time_payroll_batches.id'], name=op.f('fk_time_payroll_lines_batch_id_time_payroll_batches'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['employee_id'], ['time_employees.id'], name=op.f('fk_time_payroll_lines_employee_id_time_employees'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['settlement_id'], ['time_toil_settlements.id'], name=op.f('fk_time_payroll_lines_settlement_id_time_toil_settlements'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['week_id'], ['time_weeks.id'], name=op.f('fk_time_payroll_lines_week_id_time_weeks'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_time_payroll_lines')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_time_payroll_lines_idempotency_key'))
    )
    op.create_index(op.f('ix_time_payroll_lines_batch_id'), 'time_payroll_lines', ['batch_id'], unique=False)
    op.create_index(op.f('ix_time_payroll_lines_employee_id'), 'time_payroll_lines', ['employee_id'], unique=False)
    op.create_index(op.f('ix_time_payroll_lines_settlement_id'), 'time_payroll_lines', ['settlement_id'], unique=False)
    op.create_index(op.f('ix_time_payroll_lines_week_id'), 'time_payroll_lines', ['week_id'], unique=False)
    op.create_table('time_toil_movements',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('employee_id', sa.BigInteger(), nullable=False),
    sa.Column('movement_date', sa.Date(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('hours', sa.Numeric(precision=7, scale=2), nullable=False),
    sa.Column('week_id', sa.BigInteger(), nullable=True),
    sa.Column('settlement_id', sa.BigInteger(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('optjent', 'afholdt', 'udbetalt', 'korrektion')", name=op.f('ck_time_toil_movements_kind_gyldig')),
    sa.ForeignKeyConstraint(['employee_id'], ['time_employees.id'], name=op.f('fk_time_toil_movements_employee_id_time_employees'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['settlement_id'], ['time_toil_settlements.id'], name=op.f('fk_time_toil_movements_settlement_id_time_toil_settlements'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['week_id'], ['time_weeks.id'], name=op.f('fk_time_toil_movements_week_id_time_weeks'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_time_toil_movements'))
    )
    op.create_index(op.f('ix_time_toil_movements_employee_id'), 'time_toil_movements', ['employee_id'], unique=False)
    op.create_index(op.f('ix_time_toil_movements_settlement_id'), 'time_toil_movements', ['settlement_id'], unique=False)
    op.create_index(op.f('ix_time_toil_movements_week_id'), 'time_toil_movements', ['week_id'], unique=False)
    op.create_table('time_entries',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('week_id', sa.BigInteger(), nullable=False),
    sa.Column('employee_id', sa.BigInteger(), nullable=False),
    sa.Column('work_date', sa.Date(), nullable=False),
    sa.Column('task_type_id', sa.BigInteger(), nullable=False),
    sa.Column('task_kind', sa.Text(), nullable=False),
    sa.Column('client_id', sa.Integer(), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('hours', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('billing_model', sa.Text(), nullable=True),
    sa.Column('billable_separately', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('invoice_ref', sa.Text(), nullable=True),
    sa.Column('invoiced_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_correction', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('corrects_entry_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("task_kind IN ('kunde', 'intern', 'fravaer')", name=op.f('ck_time_entries_task_kind_gyldig')),
    sa.CheckConstraint("billing_model IN ('abonnement', 'timeafregning')", name=op.f('ck_time_entries_billing_model_gyldig')),
    sa.CheckConstraint('hours >= 0 AND hours <= 24', name=op.f('ck_time_entries_timer_0_til_24')),
    sa.CheckConstraint('hours = round(hours * 4) / 4', name=op.f('ck_time_entries_kvarte_timer')),
    sa.CheckConstraint("(task_kind = 'kunde' AND client_id IS NOT NULL) OR (task_kind <> 'kunde' AND client_id IS NULL)", name=op.f('ck_time_entries_kunde_kun_paa_kundetid')),
    sa.CheckConstraint("NOT billable_separately OR (description IS NOT NULL AND btrim(description) <> '')", name=op.f('ck_time_entries_saerskilt_kraever_beskrivelse')),
    sa.CheckConstraint('corrects_entry_id IS NULL OR is_correction', name=op.f('ck_time_entries_korrektion_markeret')),
    sa.ForeignKeyConstraint(['week_id'], ['time_weeks.id'], name=op.f('fk_time_entries_week_id_time_weeks'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['employee_id'], ['time_employees.id'], name=op.f('fk_time_entries_employee_id_time_employees'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['task_type_id'], ['time_task_types.id'], name=op.f('fk_time_entries_task_type_id_time_task_types'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name=op.f('fk_time_entries_client_id_clients'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['corrects_entry_id'], ['time_entries.id'], name=op.f('fk_time_entries_corrects_entry_id_time_entries'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by'], ['staff.id'], name=op.f('fk_time_entries_created_by_staff'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_time_entries'))
    )
    op.create_index(op.f('ix_time_entries_week_id'), 'time_entries', ['week_id'], unique=False)
    op.create_index(op.f('ix_time_entries_task_type_id'), 'time_entries', ['task_type_id'], unique=False)
    op.create_index(op.f('ix_time_entries_corrects_entry_id'), 'time_entries', ['corrects_entry_id'], unique=False)
    op.create_index(op.f('ix_time_entries_created_by'), 'time_entries', ['created_by'], unique=False)
    op.create_index('ix_time_entries_medarbejder_dato', 'time_entries', ['employee_id', 'work_date'], unique=False)
    op.create_index('ix_time_entries_kunde_dato', 'time_entries', ['client_id', 'work_date'], unique=False)
    op.create_index('ix_time_entries_ikke_faktureret', 'time_entries', ['billable_separately'], unique=False, postgresql_where=sa.text('billable_separately AND invoiced_at IS NULL'))
    op.create_table('time_entry_notes',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('entry_id', sa.BigInteger(), nullable=False),
    sa.Column('week_id', sa.BigInteger(), nullable=False),
    sa.Column('author_id', sa.Integer(), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['staff.id'], name=op.f('fk_time_entry_notes_author_id_staff'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['entry_id'], ['time_entries.id'], name=op.f('fk_time_entry_notes_entry_id_time_entries'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['week_id'], ['time_weeks.id'], name=op.f('fk_time_entry_notes_week_id_time_weeks'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_time_entry_notes'))
    )
    op.create_index(op.f('ix_time_entry_notes_author_id'), 'time_entry_notes', ['author_id'], unique=False)
    op.create_index(op.f('ix_time_entry_notes_entry_id'), 'time_entry_notes', ['entry_id'], unique=False)
    op.create_index(op.f('ix_time_entry_notes_week_id'), 'time_entry_notes', ['week_id'], unique=False)

    op.execute(TRIGGERE)
    for tabel in KUN_TILFOEJELSER:
        op.execute(f"CREATE TRIGGER {tabel}_kun_tilfoejelser BEFORE UPDATE OR DELETE ON {tabel} "
                   "FOR EACH ROW EXECUTE FUNCTION time_kun_tilfoejelser()")
    for tabel in SATSER:
        op.execute(f"CREATE TRIGGER {tabel}_slettes_ikke BEFORE DELETE ON {tabel} "
                   "FOR EACH ROW EXECUTE FUNCTION time_satser_slettes_ikke()")
    for tabel in LOGGES:
        op.execute(f"CREATE TRIGGER {tabel}_audit AFTER INSERT OR UPDATE OR DELETE ON {tabel} "
                   "FOR EACH ROW EXECUTE FUNCTION time_skriv_audit()")

    opgavetyper = sa.table(
        "time_task_types", sa.column("code"), sa.column("name"), sa.column("kind"),
        sa.column("excluded_from_efficiency"), sa.column("payable"), sa.column("reduces_toil"),
        sa.column("zero_hours"), sa.column("blocked_for_hourly"), sa.column("sort_order"), sa.column("active"))
    op.bulk_insert(opgavetyper, [
        {"code": c, "name": n, "kind": k, "excluded_from_efficiency": e, "payable": p, "reduces_toil": r,
         "zero_hours": z, "blocked_for_hourly": b, "sort_order": 10 * (i + 1), "active": a}
        for i, (c, n, k, e, p, r, z, b, a) in enumerate(OPGAVETYPER)])
    # Salgsprisen ekskl. moms fra dags dato (bygherren 18.10.2026). Fast dato, så migreringen altid giver det samme.
    op.execute("INSERT INTO time_sales_rates (valid_from, rate_per_hour) VALUES ('2026-10-18', 699.00)")


def downgrade() -> None:
    """Nedgrader databasen (timeregistreringens data forsvinder – kun til udvikling)."""
    for tabel in ("time_payroll_lines", "time_payroll_batches", "time_toil_movements", "time_toil_settlements",
                  "time_entry_notes", "time_entries", "time_weeks", "time_task_types", "time_sales_rates",
                  "time_client_terms", "time_employee_rates", "time_employees", "time_audit"):
        op.drop_table(tabel)
    op.execute("DROP FUNCTION time_week_locked(); DROP FUNCTION time_payroll_requires_approval(); "
               "DROP FUNCTION time_entry_employment_rules(); DROP FUNCTION time_kun_tilfoejelser(); "
               "DROP FUNCTION time_satser_slettes_ikke(); DROP FUNCTION time_skriv_audit();")
    # De gamle tabeller genskabes tomme (som de så ud før).
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
    op.create_table('staff_cost_rates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('staff_id', sa.Integer(), nullable=False),
    sa.Column('hourly_cost', sa.Numeric(precision=15, scale=2), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('hourly_cost >= 0', name=op.f('ck_staff_cost_rates_kostpris_ikke_negativ')),
    sa.ForeignKeyConstraint(['created_by'], ['staff.id'], name=op.f('fk_staff_cost_rates_created_by_staff'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['staff_id'], ['staff.id'], name=op.f('fk_staff_cost_rates_staff_id_staff'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_staff_cost_rates')),
    sa.UniqueConstraint('staff_id', 'valid_from', name='uq_staff_cost_rates_medarbejder_dato')
    )
    op.create_index(op.f('ix_staff_cost_rates_created_by'), 'staff_cost_rates', ['created_by'], unique=False)
    op.execute("""
CREATE FUNCTION kostpris_slettes_ikke() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Kostpriser kan ikke slettes (de er historik) – læg en ny kostpris ind med en ny dato';
END $$ LANGUAGE plpgsql;
CREATE TRIGGER kostpris_slettes_ikke BEFORE DELETE ON staff_cost_rates
    FOR EACH ROW EXECUTE FUNCTION kostpris_slettes_ikke();
""")
