"""Afregningsformen 'intern' – vores eget firma (bygherren 26.10.2026).

Interne timer faktureres aldrig og indgår ikke i omsætningen, men er arbejdstid (løn, normtid, afspadsering).
Databasen håndhæver:
- 'intern' har aldrig en abonnementspris, og en intern timelinje kan aldrig være "uden for abonnementet".
- 'intern' kan kun lægges på en kunde med kun_admin (trigger på time_client_terms).
- kun_admin kan ikke fjernes, mens kunden har en gældende eller fremtidig intern periode (trigger på clients).

Revision ID: a7c41e9d2b30
Revises: 59f02cd75f17
Create Date: 2026-10-26 15:10:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a7c41e9d2b30'
down_revision: Union[str, Sequence[str], None] = '59f02cd75f17'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TRIGGERE = """
CREATE FUNCTION time_intern_kraever_kun_admin() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.billing_model = 'intern'
       AND NOT COALESCE((SELECT kun_admin FROM clients WHERE id = NEW.client_id), false) THEN
        RAISE EXCEPTION 'Afregningsformen intern kan kun bruges på interne kunder (Kun admin hos Svend)';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER time_intern_kraever_kun_admin BEFORE INSERT OR UPDATE OF billing_model, client_id
    ON time_client_terms FOR EACH ROW EXECUTE FUNCTION time_intern_kraever_kun_admin();

CREATE FUNCTION kunde_intern_forbliver_kun_admin() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.kun_admin AND NOT NEW.kun_admin AND EXISTS (
        SELECT 1 FROM time_client_terms t WHERE t.client_id = NEW.id AND t.billing_model = 'intern'
          AND (t.valid_to IS NULL OR t.valid_to > (now() AT TIME ZONE 'Europe/Copenhagen')::date)) THEN
        RAISE EXCEPTION 'Kunden har afregningsformen intern og skal forblive intern';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER kunde_intern_forbliver_kun_admin BEFORE UPDATE OF kun_admin
    ON clients FOR EACH ROW EXECUTE FUNCTION kunde_intern_forbliver_kun_admin();
"""


def _afregninger(vaerdier: str) -> None:
    for tabel in ("time_client_terms", "time_entries"):
        op.drop_constraint(op.f(f"ck_{tabel}_billing_model_gyldig"), tabel, type_="check")
        op.create_check_constraint("billing_model_gyldig", tabel, f"billing_model IN ({vaerdier})")


def upgrade() -> None:
    """Opgrader databasen."""
    _afregninger("'abonnement', 'timeafregning', 'intern'")
    op.create_check_constraint("intern_uden_pris", "time_client_terms",
                               "billing_model <> 'intern' OR subscription_price IS NULL")
    op.create_check_constraint("intern_aldrig_faktureret", "time_entries",
                               "billing_model IS DISTINCT FROM 'intern' OR NOT billable_separately")
    op.execute(TRIGGERE)


def downgrade() -> None:
    """Nedgrader databasen (kun muligt, hvis ingen bruger 'intern')."""
    op.execute("DROP TRIGGER kunde_intern_forbliver_kun_admin ON clients; "
               "DROP FUNCTION kunde_intern_forbliver_kun_admin(); "
               "DROP TRIGGER time_intern_kraever_kun_admin ON time_client_terms; "
               "DROP FUNCTION time_intern_kraever_kun_admin();")
    op.drop_constraint(op.f("ck_time_entries_intern_aldrig_faktureret"), "time_entries", type_="check")
    op.drop_constraint(op.f("ck_time_client_terms_intern_uden_pris"), "time_client_terms", type_="check")
    _afregninger("'abonnement', 'timeafregning'")
