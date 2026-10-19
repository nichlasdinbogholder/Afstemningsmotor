"""Timeregistrering, fase 1 trin 2: periodeopslag – satserne, der gælder på en dato

Ét sted i databasen, som alle rapporter (og Python via app/timeregistrering/satser.py) slår op i. Funktionerne
tager altid datoen som argument – de kigger aldrig på dags dato (now()), så en rapport for marts giver samme tal,
uanset hvornår den køres. Findes der ingen sats på datoen, er svaret NULL (aldrig 0) – Python melder en tydelig fejl.

Revision ID: fb5a83b87ff1
Revises: f95b5ad78632
Create Date: 2026-10-19 07:07:11.517083

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fb5a83b87ff1'
down_revision: Union[str, Sequence[str], None] = 'f95b5ad78632'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


FUNKTIONER = """
CREATE FUNCTION time_salgspris(d date) RETURNS numeric LANGUAGE sql STABLE AS $$
    SELECT rate_per_hour FROM time_sales_rates WHERE period @> d
$$;
CREATE FUNCTION time_kostpris(medarbejder bigint, d date) RETURNS numeric LANGUAGE sql STABLE AS $$
    SELECT cost_per_hour FROM time_employee_rates WHERE employee_id = medarbejder AND period @> d
$$;
CREATE FUNCTION time_normtid(medarbejder bigint, d date) RETURNS numeric LANGUAGE sql STABLE AS $$
    SELECT weekly_norm FROM time_employee_rates WHERE employee_id = medarbejder AND period @> d
$$;
CREATE FUNCTION time_afregning(kunde integer, d date) RETURNS text LANGUAGE sql STABLE AS $$
    SELECT billing_model FROM time_client_terms WHERE client_id = kunde AND period @> d
$$;
CREATE FUNCTION time_abonnementspris(kunde integer, d date) RETURNS numeric LANGUAGE sql STABLE AS $$
    SELECT subscription_price FROM time_client_terms WHERE client_id = kunde AND period @> d
$$;
"""


def upgrade() -> None:
    """Opgrader databasen."""
    op.execute(FUNKTIONER)


def downgrade() -> None:
    """Nedgrader databasen."""
    op.execute("DROP FUNCTION time_salgspris(date); DROP FUNCTION time_kostpris(bigint, date); "
               "DROP FUNCTION time_normtid(bigint, date); DROP FUNCTION time_afregning(integer, date); "
               "DROP FUNCTION time_abonnementspris(integer, date);")
