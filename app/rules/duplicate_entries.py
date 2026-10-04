"""Regel: duplicate_entries – samme beløb bogført to gange.

Et par af posteringer er et muligt dobbeltbogført beløb, når:
- de hører til samme kunde,
- de står på SAMME KONTO (en dobbeltbogføring gentager sig på den samme konto),
- beløbet er ens MED SAMME FORTEGN (+5.000 og −5.000 er en tilbageførsel, ikke en dublet),
- samme valuta,
- datoerne ligger højst VINDUE_DAGE fra hinanden (faste månedlige betalinger falder udenfor),
- de er to forskellige posteringer (forskelligt posteringsnummer),
- de er IKKE fra samme bilag (et bilags egne linjer er ikke dubletter af hinanden),
- modparten (kunde/leverandør) er den samme, når begge har en.

Alvorlighed:
  high   – samme dato og samme tekst
  medium – inden for vinduet

Version 2 (04.10.2026), strammet efter kontrol på demo-aftalen: 59 fund på 138
posteringer, heraf 56 'low' (ens runde beløb på forskellige konti). Vinduet er
sænket fra 7 til 3 dage, og samme konto er nu et krav ('low' findes ikke længere).

Hele sammenligningen er ÉN SQL-forespørgsel (tabellen entries sammenlignet med sig
selv) – ingen Python-løkke over alle posteringer. Reglen læser kun vores egen database.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.rules.base import FindingDraft, fingerprint, registrer_regel

# Hvor mange dage der højst må være mellem to posteringer, før de ikke længere regnes
# som en mulig dublet. Kort vindue, så husleje, leasing og abonnementer ikke rammes.
# Version 1 brugte 7 dage; sænket til 3 efter kontrollen på demo-aftalen.
VINDUE_DAGE = 3

PAR_SQL = text("""
SELECT a.id            AS a_id,           b.id            AS b_id,
       a.bogfoert_id   AS a_nr,           b.bogfoert_id   AS b_nr,
       a.dato          AS a_dato,         b.dato          AS b_dato,
       a.kontonummer   AS a_konto,        b.kontonummer   AS b_konto,
       a.tekst         AS a_tekst,        b.tekst         AS b_tekst,
       a.bilagsnummer  AS a_bilag,        b.bilagsnummer  AS b_bilag,
       a.modpart       AS a_modpart,      b.modpart       AS b_modpart,
       a.beloeb        AS beloeb,         a.valuta        AS valuta,
       CASE
           WHEN a.dato = b.dato AND a.tekst IS NOT DISTINCT FROM b.tekst THEN 'high'
           ELSE 'medium'
       END AS severity
FROM entries a
JOIN entries b
  ON  b.client_id   = a.client_id
  AND b.kontonummer = a.kontonummer                 -- samme konto
  AND b.beloeb      = a.beloeb                      -- samme beløb OG samme fortegn
  AND b.valuta      IS NOT DISTINCT FROM a.valuta
  AND b.bogfoert_id > a.bogfoert_id                 -- forskellige poster, hvert par kun én gang
  AND b.dato BETWEEN a.dato - :vindue AND a.dato + :vindue
WHERE a.client_id = :client_id
  AND a.beloeb <> 0
  AND a.dato IS NOT NULL
  AND (a.bilagsnummer IS NULL OR b.bilagsnummer IS NULL OR a.bilagsnummer <> b.bilagsnummer)
  AND (a.modpart IS NULL OR b.modpart IS NULL OR a.modpart = b.modpart)
  AND (CAST(:since AS date) IS NULL OR greatest(a.dato, b.dato) >= :since)
ORDER BY a.dato, a.bogfoert_id, b.bogfoert_id
""")


def _kr(beloeb: Decimal, valuta: str | None) -> str:
    tal = f"{beloeb:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{tal} kr." if valuta in (None, "DKK") else f"{tal} {valuta}"


def _datoer(a: date, b: date) -> str:
    a, b = min(a, b), max(a, b)  # altid ældste dato først
    if a == b:
        return f"den {a:%d.%m.%Y}"
    if a.year == b.year:
        return f"den {a:%d.%m} og {b:%d.%m}"
    return f"den {a:%d.%m.%Y} og {b:%d.%m.%Y}"


def _post(r, side: str) -> dict:
    g = lambda felt: getattr(r, f"{side}_{felt}")  # noqa: E731
    return {
        "posteringsnummer": g("nr"),
        "dato": g("dato").isoformat(),
        "konto": g("konto"),
        "tekst": g("tekst"),
        "bilagsnummer": g("bilag"),
        "modpart": g("modpart"),
        "beloeb": str(r.beloeb),
    }


@registrer_regel
class DuplicateEntries:
    code = "duplicate_entries"
    version = 2
    name_da = "Muligt dobbeltbogført beløb"

    def run(self, session: Session, client_id: int, since: date | None) -> list[FindingDraft]:
        par = session.execute(PAR_SQL, {"client_id": client_id, "vindue": VINDUE_DAGE, "since": since})
        fund = []
        for r in par:
            fund.append(FindingDraft(
                fingerprint=fingerprint([str(r.a_nr), str(r.b_nr)]),
                severity=r.severity,
                title=f"Muligt dobbeltbogført beløb: {_kr(r.beloeb, r.valuta)} på konto {r.a_konto} "
                      f"{_datoer(r.a_dato, r.b_dato)}",
                detail={
                    "beloeb": str(r.beloeb),
                    "valuta": r.valuta,
                    "vindue_dage": VINDUE_DAGE,
                    "regel_version": self.version,
                    "dage_imellem": abs((r.b_dato - r.a_dato).days),
                    "posteringer": [_post(r, "a"), _post(r, "b")],
                },
                entry_ids=sorted([r.a_id, r.b_id]),
                period_start=min(r.a_dato, r.b_dato),
                period_end=max(r.a_dato, r.b_dato),
            ))
        return fund
