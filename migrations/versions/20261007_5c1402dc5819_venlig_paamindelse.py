"""venlig paamindelse

Revision ID: 5c1402dc5819
Revises: 147fe84aee17
Create Date: 2026-10-07 19:45:21.803790

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5c1402dc5819'
down_revision: Union[str, Sequence[str], None] = '147fe84aee17'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


KONTROLLER_RYKKER = r"""
CREATE OR REPLACE FUNCTION kontroller_rykker() RETURNS trigger AS $$
DECLARE
    f            invoices%ROWTYPE;
    forrige_sendt timestamptz;
    forrige_dato date;
    erhverv      boolean;
BEGIN
    -- Annullerede/fejlede rykkere sendes ikke og kontrolleres ikke.
    IF NEW.status NOT IN ('queued', 'sent') THEN
        RETURN NEW;
    END IF;

    SELECT * INTO f FROM invoices WHERE id = NEW.invoice_id;

    IF f.kind <> 'invoice' OR f.amount <= 0 THEN
        RAISE EXCEPTION 'Rykker afvist: faktura % er en kreditnota eller har intet beløb', f.invoice_no
            USING ERRCODE = 'check_violation';
    END IF;

    -- Venlig påmindelse (step_no 0, altid uden beløb – CHECK): kun FØR den første rykker.
    IF NEW.step_no = 0 THEN
        IF f.prior_dunning_count > 0 OR EXISTS (
               SELECT 1 FROM dunning_steps WHERE invoice_id = NEW.invoice_id AND step_no > 0
               AND status IN ('queued', 'sent') AND id IS DISTINCT FROM NEW.id) THEN
            RAISE EXCEPTION 'Påmindelse afvist: faktura % har allerede fået en rykker', f.invoice_no
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    -- Højst 3 rykkere pr. ydelse – også medregnet dem, der blev sendt fra FarPay.
    IF NEW.step_no + f.prior_dunning_count > 3 THEN
        RAISE EXCEPTION 'Rykker afvist: faktura % ville få rykker nr. % (højst 3)',
            f.invoice_no, NEW.step_no + f.prior_dunning_count USING ERRCODE = 'check_violation';
    END IF;

    -- Mindst 10 dage efter forrige rykker (vores egen eller den seneste fra FarPay).
    IF NEW.step_no > 1 THEN
        SELECT sent_at INTO forrige_sendt FROM dunning_steps
        WHERE invoice_id = NEW.invoice_id AND step_no = NEW.step_no - 1 AND status = 'sent';
        IF forrige_sendt IS NULL THEN
            RAISE EXCEPTION 'Rykker afvist: rykker nr. % på faktura % er ikke sendt', NEW.step_no - 1, f.invoice_no
                USING ERRCODE = 'check_violation';
        END IF;
        forrige_dato := (forrige_sendt AT TIME ZONE 'Europe/Copenhagen')::date;
    ELSIF NEW.step_no = 1 THEN
        -- Rykker 1: 10 dage efter seneste rykker i FarPay eller den venlige påmindelse (vores eller FarPays).
        SELECT (sent_at AT TIME ZONE 'Europe/Copenhagen')::date INTO forrige_dato FROM dunning_steps
        WHERE invoice_id = NEW.invoice_id AND step_no = 0 AND status = 'sent';
        forrige_dato := GREATEST(forrige_dato, f.prior_last_dunning_at, f.prior_reminder_at);
    END IF;
    IF forrige_dato IS NOT NULL AND (
         NEW.due_at < forrige_dato + 10
         OR (NEW.sent_at IS NOT NULL AND (NEW.sent_at AT TIME ZONE 'Europe/Copenhagen')::date < forrige_dato + 10)
       ) THEN
        RAISE EXCEPTION 'Rykker afvist: under 10 dage siden forrige rykker på faktura % (%)', f.invoice_no, forrige_dato
            USING ERRCODE = 'check_violation';
    END IF;

    -- Kompensationsbeløb kun i erhvervsforhold.
    IF NEW.compensation_amount > 0 THEN
        SELECT is_business INTO erhverv FROM debtors WHERE id = f.debtor_id;
        IF NOT coalesce(erhverv, false) THEN
            RAISE EXCEPTION 'Rykker afvist: kompensationsbeløb kun i erhvervsforhold (faktura %)', f.invoice_no
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    -- Ingen rykkere på en fordring hos inkasso.
    IF EXISTS (SELECT 1 FROM collection_cases
               WHERE invoice_id = NEW.invoice_id AND status NOT IN ('withdrawn', 'rejected')) THEN
        RAISE EXCEPTION 'Rykker afvist: faktura % har en aktiv inkassosag', f.invoice_no
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

KONTROLLER_RYKKER_GAMMEL = r"""
CREATE OR REPLACE FUNCTION kontroller_rykker() RETURNS trigger AS $$
DECLARE
    f            invoices%ROWTYPE;
    forrige_sendt timestamptz;
    forrige_dato date;
    erhverv      boolean;
BEGIN
    -- Annullerede/fejlede rykkere sendes ikke og kontrolleres ikke.
    IF NEW.status NOT IN ('queued', 'sent') THEN
        RETURN NEW;
    END IF;

    SELECT * INTO f FROM invoices WHERE id = NEW.invoice_id;

    IF f.kind <> 'invoice' OR f.amount <= 0 THEN
        RAISE EXCEPTION 'Rykker afvist: faktura % er en kreditnota eller har intet beløb', f.invoice_no
            USING ERRCODE = 'check_violation';
    END IF;

    -- Højst 3 rykkere pr. ydelse – også medregnet dem, der blev sendt fra FarPay.
    IF NEW.step_no + f.prior_dunning_count > 3 THEN
        RAISE EXCEPTION 'Rykker afvist: faktura % ville få rykker nr. % (højst 3)',
            f.invoice_no, NEW.step_no + f.prior_dunning_count USING ERRCODE = 'check_violation';
    END IF;

    -- Mindst 10 dage efter forrige rykker (vores egen eller den seneste fra FarPay).
    IF NEW.step_no > 1 THEN
        SELECT sent_at INTO forrige_sendt FROM dunning_steps
        WHERE invoice_id = NEW.invoice_id AND step_no = NEW.step_no - 1 AND status = 'sent';
        IF forrige_sendt IS NULL THEN
            RAISE EXCEPTION 'Rykker afvist: rykker nr. % på faktura % er ikke sendt', NEW.step_no - 1, f.invoice_no
                USING ERRCODE = 'check_violation';
        END IF;
        forrige_dato := (forrige_sendt AT TIME ZONE 'Europe/Copenhagen')::date;
    ELSE
        forrige_dato := f.prior_last_dunning_at;
    END IF;
    IF forrige_dato IS NOT NULL AND (
         NEW.due_at < forrige_dato + 10
         OR (NEW.sent_at IS NOT NULL AND (NEW.sent_at AT TIME ZONE 'Europe/Copenhagen')::date < forrige_dato + 10)
       ) THEN
        RAISE EXCEPTION 'Rykker afvist: under 10 dage siden forrige rykker på faktura % (%)', f.invoice_no, forrige_dato
            USING ERRCODE = 'check_violation';
    END IF;

    -- Kompensationsbeløb kun i erhvervsforhold.
    IF NEW.compensation_amount > 0 THEN
        SELECT is_business INTO erhverv FROM debtors WHERE id = f.debtor_id;
        IF NOT coalesce(erhverv, false) THEN
            RAISE EXCEPTION 'Rykker afvist: kompensationsbeløb kun i erhvervsforhold (faktura %)', f.invoice_no
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    -- Ingen rykkere på en fordring hos inkasso.
    IF EXISTS (SELECT 1 FROM collection_cases
               WHERE invoice_id = NEW.invoice_id AND status NOT IN ('withdrawn', 'rejected')) THEN
        RAISE EXCEPTION 'Rykker afvist: faktura % har en aktiv inkassosag', f.invoice_no
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    """Opgrader databasen."""
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('clients', sa.Column('reminder_after_days', sa.SmallInteger(), server_default=sa.text('5'), nullable=True))
    op.drop_constraint(op.f('uq_dunning_steps_faktura_nr'), 'dunning_steps', type_='unique')
    op.create_index('uq_dunning_steps_faktura_nr', 'dunning_steps', ['invoice_id', 'step_no'], unique=True, postgresql_where=sa.text("status IN ('queued', 'sent')"))
    op.create_index(op.f('ix_dunning_steps_invoice_id'), 'dunning_steps', ['invoice_id'], unique=False)
    op.add_column('invoices', sa.Column('prior_reminder_at', sa.Date(), nullable=True))
    # ### end Alembic commands ###
    # CHECK-regler og triggeren opdages ikke af autogenerate.
    op.create_check_constraint("paamindelse_1_60_dage", "clients",
                               "reminder_after_days IS NULL OR reminder_after_days BETWEEN 1 AND 60")
    op.drop_constraint("hoejst_3_rykkere", "dunning_steps", type_="check")
    op.create_check_constraint("hoejst_3_rykkere", "dunning_steps", "step_no BETWEEN 0 AND 3")
    op.create_check_constraint("paamindelse_uden_beloeb", "dunning_steps",
                               "step_no > 0 OR (fee_amount = 0 AND interest_amount = 0 AND compensation_amount = 0)")
    op.execute(KONTROLLER_RYKKER)


def downgrade() -> None:
    """Nedgrader databasen."""
    op.execute(KONTROLLER_RYKKER_GAMMEL)
    op.drop_constraint("paamindelse_uden_beloeb", "dunning_steps", type_="check")
    op.drop_constraint("hoejst_3_rykkere", "dunning_steps", type_="check")
    op.create_check_constraint("hoejst_3_rykkere", "dunning_steps", "step_no BETWEEN 1 AND 3")
    op.drop_constraint("paamindelse_1_60_dage", "clients", type_="check")
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('invoices', 'prior_reminder_at')
    op.drop_index(op.f('ix_dunning_steps_invoice_id'), table_name='dunning_steps')
    op.drop_index('uq_dunning_steps_faktura_nr', table_name='dunning_steps', postgresql_where=sa.text("status IN ('queued', 'sent')"))
    op.create_unique_constraint(op.f('uq_dunning_steps_faktura_nr'), 'dunning_steps', ['invoice_id', 'step_no'], postgresql_nulls_not_distinct=False)
    op.drop_column('clients', 'reminder_after_days')
    # ### end Alembic commands ###
