"""Status 'onboarding' – samme funktion som 'aktiv' (Kundeoversigten, bygherren 26.10.2026).

Revision ID: 59f02cd75f17
Revises: eb3df98ab8e6
Create Date: 2026-10-26 10:21:33.071026

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '59f02cd75f17'
down_revision: Union[str, Sequence[str], None] = 'eb3df98ab8e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Reglerne for åbne poster vælger "aktive kunder" i databasen. De omskrives ud fra deres nuværende definition, så kun
# netop betingelsen ændres – resten af funktionerne røres ikke. Visningen genskabes ud fra sin oprindelige SQL
# (migrering 0702ad4fd9f6) med den nye betingelse.
FUNKTIONER = ("regel_1_kandidater", "regel_2_kandidater", "regel_3_kandidater", "luk_loeste_fund")
GAMMEL = "c.status = 'aktiv'"
NY = "c.status IN ('aktiv', 'onboarding')"
VISNING = """
CREATE OR REPLACE VIEW synk_kraever_handling AS
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
WHERE {betingelse}
  AND s.status <> 'deaktiveret'
  AND (s.status IN ('fejlet', 'forsinket')
       OR s.sidste_ok IS NULL
       OR s.sidste_ok < now() - interval '48 hours')
ORDER BY (s.status = 'fejlet') DESC, s.antal_fejl_i_traek DESC, c.kundenummer, s.ressource
"""


def _omskriv(fra: str, til: str) -> None:
    forbindelse = op.get_bind()
    for navn in FUNKTIONER:
        definition = forbindelse.execute(sa.text(
            "SELECT pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = 'public' AND p.proname = :navn"), {"navn": navn}).scalar_one()
        if fra not in definition:
            raise RuntimeError(f"{navn}: betingelsen blev ikke fundet – intet ændret")
        forbindelse.execute(sa.text(definition.replace(fra, til)))
    op.execute(VISNING.format(betingelse=til))


def upgrade() -> None:
    """Status 'onboarding' (bygherren 26.10.2026): kunder, der er ved at blive onboardet – samme funktion som 'aktiv'."""
    op.drop_constraint("status_gyldig", "clients", type_="check")
    op.create_check_constraint("status_gyldig", "clients",
                               "status IN ('emne', 'onboarding', 'aktiv', 'pause', 'opsagt')")
    _omskriv(GAMMEL, NY)


def downgrade() -> None:
    if op.get_bind().execute(sa.text("SELECT count(*) FROM clients WHERE status = 'onboarding'")).scalar():
        raise RuntimeError("Kan ikke fortrydes: der findes kunder med status 'onboarding'")
    _omskriv(NY, GAMMEL)
    op.drop_constraint("status_gyldig", "clients", type_="check")
    op.create_check_constraint("status_gyldig", "clients", "status IN ('emne', 'aktiv', 'pause', 'opsagt')")
