"""Fund lukkes automatisk, når reglens betingelse ikke længere er opfyldt

Hver regel deles i to:
  regel_N_kandidater(...)  – de poster, der opfylder reglen LIGE NU (ren læsning)
  regel_N_...(...)         – skriver fund for kandidaterne (som før)
luk_loeste_fund(...) lukker åbne fund, der ikke længere er kandidater, og bruger
derfor præcis samme betingelser. Et løst fund, der igen opfylder reglen, genåbnes.
Afviste fund (status afvist) røres aldrig.

Revision ID: c0b2dec070b2
Revises: 709567d983c2
Create Date: 2026-10-04 18:06:30.180291

"""
from typing import Sequence, Union

import importlib.util
from pathlib import Path

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c0b2dec070b2'
down_revision: Union[str, Sequence[str], None] = '709567d983c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


FAELLES = """
    client_id, regel, kilde_id, type, partnummer, partnavn, fakturanummer,
    dato, forfaldsdato, beloeb, restbeloeb, valuta, beskrivelse
"""

# Fælles start på alle kandidat-forespørgsler: kun aktive kunder, evt. én kunde.
AKTIVE = """
    FROM open_entries o
    JOIN clients c ON c.id = o.client_id
    WHERE c.status = 'aktiv'
      AND (p_client_id IS NULL OR o.client_id = p_client_id)
"""

KANDIDATER = {
    "regel_1_kandidater(p_client_id integer DEFAULT NULL)": f"""
    SELECT o.client_id, o.bogfoert_id
    {AKTIVE}
      AND o.valuta = 'DKK'
      AND abs(o.restbeloeb) BETWEEN 0.01 AND 99.99
      AND abs(o.restbeloeb) < abs(o.beloeb)
    """,
    "regel_2_kandidater(p_client_id integer DEFAULT NULL)": f"""
    SELECT o.client_id, o.bogfoert_id
    {AKTIVE}
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
    """,
    "regel_3_kandidater(p_client_id integer DEFAULT NULL, p_dato date DEFAULT current_date)": f"""
    SELECT o.client_id, o.bogfoert_id
    {AKTIVE}
      AND o.forfaldsdato < (p_dato - interval '6 months')::date
    """,
}


def _kandidat_funktion(signatur: str, sql: str) -> str:
    return (f"CREATE FUNCTION {signatur}\n"
            f"RETURNS TABLE (k_client_id integer, k_bogfoert_id bigint)\n"
            f"LANGUAGE sql STABLE AS $$ {sql} $$;")


def _regel_funktion(navn: str, parametre: str, kald: str, regel: str, beskrivelse: str) -> str:
    return f"""
CREATE OR REPLACE FUNCTION {navn}({parametre})
RETURNS integer LANGUAGE sql AS $$
    WITH nye AS (
        INSERT INTO findings ({FAELLES})
        SELECT o.client_id, '{regel}', o.bogfoert_id, o.type, o.partnummer,
               o.partnavn, o.fakturanummer, o.dato, o.forfaldsdato, o.beloeb,
               o.restbeloeb, o.valuta, {beskrivelse}
        FROM {kald} k
        JOIN open_entries o ON o.client_id = k.k_client_id AND o.bogfoert_id = k.k_bogfoert_id
        ON CONFLICT (client_id, regel, kilde_id) DO UPDATE
            SET status = 'aaben', loest_tidspunkt = NULL, loest_aarsag = NULL,
                restbeloeb = EXCLUDED.restbeloeb, beskrivelse = EXCLUDED.beskrivelse
            WHERE findings.status = 'loest'
        RETURNING 1
    )
    SELECT count(*)::integer FROM nye;
$$;
"""


REGLER = [
    _regel_funktion("regel_1_smaa_restbeloeb", "p_client_id integer DEFAULT NULL",
                    "regel_1_kandidater(p_client_id)", "smaa_restbeloeb",
                    "'Delvist udlignet post med restbeløb på ' || abs(o.restbeloeb) || ' kr.'"),
    _regel_funktion("regel_2_betaling_uden_faktura", "p_client_id integer DEFAULT NULL",
                    "regel_2_kandidater(p_client_id)", "betaling_uden_faktura",
                    "'Åben betaling uden en åben faktura hos samme ' || o.type || ' at udligne mod'"),
    _regel_funktion("regel_3_forfalden_over_6_mdr",
                    "p_client_id integer DEFAULT NULL, p_dato date DEFAULT current_date",
                    "regel_3_kandidater(p_client_id, p_dato)", "forfalden_over_6_mdr",
                    "'Åben post forfaldet ' || o.forfaldsdato || ' – mere end 6 måneder før ' || p_dato"),
]

LUK = """
CREATE FUNCTION luk_loeste_fund(p_client_id integer DEFAULT NULL,
                                p_dato date DEFAULT current_date)
RETURNS integer LANGUAGE sql AS $$
    WITH kandidater AS (
        SELECT 'smaa_restbeloeb' AS regel, k_client_id, k_bogfoert_id
        FROM regel_1_kandidater(p_client_id)
        UNION ALL
        SELECT 'betaling_uden_faktura', k_client_id, k_bogfoert_id
        FROM regel_2_kandidater(p_client_id)
        UNION ALL
        SELECT 'forfalden_over_6_mdr', k_client_id, k_bogfoert_id
        FROM regel_3_kandidater(p_client_id, p_dato)
    ),
    lukket AS (
        UPDATE findings f
        SET status = 'loest',
            loest_tidspunkt = now(),
            loest_aarsag = CASE
                WHEN EXISTS (SELECT 1 FROM open_entries o
                             WHERE o.client_id = f.client_id AND o.bogfoert_id = f.kilde_id)
                THEN 'Reglens betingelse er ikke længere opfyldt'
                ELSE 'Posten er ikke længere åben (udlignet eller betalt)'
            END
        FROM clients c
        WHERE c.id = f.client_id
          AND c.status = 'aktiv'
          AND f.status = 'aaben'
          AND (p_client_id IS NULL OR f.client_id = p_client_id)
          AND NOT EXISTS (SELECT 1 FROM kandidater k
                          WHERE k.regel = f.regel
                            AND k.k_client_id = f.client_id
                            AND k.k_bogfoert_id = f.kilde_id)
        RETURNING 1
    )
    SELECT count(*)::integer FROM lukket;
$$;
"""


def _forrige_migrering():
    """Funktionerne fra migreringen 'findings og afstemningsregler' (til nedgradering)."""
    fil = next(Path(__file__).parent.glob("*_709567d983c2_*.py"))
    spec = importlib.util.spec_from_file_location("forrige", fil)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def upgrade() -> None:
    """Opgrader databasen."""
    op.add_column('findings', sa.Column('loest_tidspunkt', sa.DateTime(timezone=True), nullable=True))
    op.add_column('findings', sa.Column('loest_aarsag', sa.Text(), nullable=True))
    op.create_check_constraint(op.f("ck_findings_loest_har_tidspunkt"), "findings",
                               "(status = 'loest') = (loest_tidspunkt IS NOT NULL)")
    # Fund, der allerede står som løst (manuelt), får tidspunktet nu.
    op.execute("UPDATE findings SET loest_tidspunkt = now() WHERE status = 'loest'")
    for signatur, sql in KANDIDATER.items():
        op.execute(_kandidat_funktion(signatur, sql))
    for sql in REGLER:
        op.execute(sql)
    op.execute(LUK)


def downgrade() -> None:
    """Nedgrader databasen."""
    op.execute("DROP FUNCTION luk_loeste_fund(integer, date)")
    for navn in ("regel_1_smaa_restbeloeb(integer)", "regel_2_betaling_uden_faktura(integer)",
                 "regel_3_forfalden_over_6_mdr(integer, date)"):
        op.execute(f"DROP FUNCTION {navn}")
    for navn in ("regel_1_kandidater(integer)", "regel_2_kandidater(integer)",
                 "regel_3_kandidater(integer, date)"):
        op.execute(f"DROP FUNCTION {navn}")
    forrige = _forrige_migrering()
    for sql in (forrige.REGEL_1, forrige.REGEL_2, forrige.REGEL_3):
        op.execute(sql)
    op.drop_constraint(op.f("ck_findings_loest_har_tidspunkt"), "findings", type_="check")
    op.drop_column('findings', 'loest_aarsag')
    op.drop_column('findings', 'loest_tidspunkt')
