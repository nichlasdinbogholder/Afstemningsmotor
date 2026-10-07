"""Regel: mangler der et kontoudtog fra en grossist?

Grossisterne ligger i leverandørgruppe GROSSIST_GRUPPE (20000) hos alle kunder. For hver af
de seneste MAANEDER_TILBAGE afsluttede måneder gælder: har kunden handlet med grossisten
(mindst én postering på leverandøren i måneden), skal der være indlæst et kontoudtog, hvis
periode dækker måneden. Ellers et fund – så alle grossister bliver afstemt.

Grossisterne sender udtogene op til 10. hverdag i måneden efter. En måned tjekkes derfor
først fra FRIST_HVERDAG (10.) hverdag i den følgende måned (weekender og danske
helligdage tæller ikke). Reglen køres hver nat, men giver først fund, når fristen er nået.

Læser kun vores egen database (suppliers, entries, statements) – med SQL, så adapter-laget
ikke trækkes med.
"""

import hashlib
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.rules.base import FindingDraft, registrer_regel

GROSSIST_GRUPPE = 20000
MAANEDER_TILBAGE = 3
FRIST_HVERDAG = 10
MAANEDER = ("januar", "februar", "marts", "april", "maj", "juni", "juli", "august", "september",
            "oktober", "november", "december")

MANGLER_SQL = text("""
    SELECT s.leverandoernummer, s.navn, count(e.id) AS posteringer
    FROM suppliers s
    JOIN entries e ON e.client_id = s.client_id
                  AND e.modpart = 'kreditor:' || s.leverandoernummer
                  AND e.dato BETWEEN :fra AND :til
    WHERE s.client_id = :client_id
      AND s.gruppe = :gruppe
      AND NOT EXISTS (
          SELECT 1 FROM statements st
          WHERE st.client_id = s.client_id
            AND st.modpart = 'kreditor:' || s.leverandoernummer
            AND st.periode_fra <= :til AND st.periode_til >= :fra)
    GROUP BY s.leverandoernummer, s.navn
    ORDER BY s.leverandoernummer
""")


def i_dag() -> date:
    return date.today()


def paaskedag(aar: int) -> date:
    """Påskedag (gregoriansk, "anonym" algoritme)."""
    a, b, c = aar % 19, aar // 100, aar % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    maaned = (h + l - 7 * m + 114) // 31
    return date(aar, maaned, (h + l - 7 * m + 114) % 31 + 1)


def helligdage(aar: int) -> set[date]:
    """Danske helligdage og banklukkedage (store bededag er afskaffet fra 2024)."""
    p = paaskedag(aar)
    dage = {date(aar, 1, 1), p - timedelta(days=3), p - timedelta(days=2), p + timedelta(days=1),
            p + timedelta(days=39), p + timedelta(days=50), date(aar, 6, 5),
            date(aar, 12, 24), date(aar, 12, 25), date(aar, 12, 26), date(aar, 12, 31)}
    if aar < 2024:
        dage.add(p + timedelta(days=26))
    return dage


def nte_hverdag(aar: int, maaned: int, n: int) -> date:
    """Den n'te hverdag (mandag–fredag, ikke helligdag) i måneden."""
    dag, fundet, fri = date(aar, maaned, 1), 0, helligdage(aar)
    while True:
        if dag.weekday() < 5 and dag not in fri:
            fundet += 1
            if fundet == n:
                return dag
        dag += timedelta(days=1)


def frist(maaned_start: date) -> date:
    """Hvornår udtoget for måneden senest skal være kommet: FRIST_HVERDAG. hverdag måneden efter."""
    naeste = (maaned_start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return nte_hverdag(naeste.year, naeste.month, FRIST_HVERDAG)


def afsluttede_maaneder(dag: date, antal: int) -> list[tuple[date, date]]:
    """De `antal` seneste hele måneder før `dag` – nyeste først."""
    maaneder, slut = [], dag.replace(day=1) - timedelta(days=1)
    for _ in range(antal):
        start = slut.replace(day=1)
        maaneder.append((start, slut))
        slut = start - timedelta(days=1)
    return maaneder


@registrer_regel
class ManglendeKontoudtog:
    code = "mangler_kontoudtog"
    version = 1
    name_da = "Mangler kontoudtog fra grossist"

    def run(self, session: Session, client_id: int, since: date | None) -> list[FindingDraft]:
        udkast = []
        dag = i_dag()
        for fra, til in afsluttede_maaneder(dag, MAANEDER_TILBAGE + 1):
            if dag < frist(fra):
                continue  # grossisterne har endnu tid til at sende udtoget
            for r in session.execute(MANGLER_SQL, {"client_id": client_id, "gruppe": GROSSIST_GRUPPE,
                                                   "fra": fra, "til": til}):
                maaned = f"{MAANEDER[fra.month - 1]} {fra.year}"
                udkast.append(FindingDraft(
                    fingerprint=hashlib.sha256(
                        f"mangler_kontoudtog|{client_id}|{r.leverandoernummer}|{fra:%Y-%m}".encode()
                    ).hexdigest()[:32],
                    severity="medium",
                    title=(f"Mangler kontoudtog: {r.navn} (leverandør {r.leverandoernummer}) for {maaned} – "
                           f"{r.posteringer} posteringer i måneden"),
                    detail={"leverandoernummer": r.leverandoernummer, "leverandoer": r.navn,
                            "maaned": f"{fra:%Y-%m}", "posteringer": r.posteringer,
                            "frist": frist(fra).isoformat()},
                    entry_ids=[], period_start=fra, period_end=til,
                ))
        return udkast
