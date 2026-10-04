"""Regel: duplicate_entries – samme bilag bogført to gange.

Et par af posteringslinjer er et muligt dobbeltbogført beløb, når:
- de hører til samme kunde,
- de står på SAMME KONTO (en dobbeltbogføring gentager sig på den samme konto),
- beløbet er ens MED SAMME FORTEGN (+5.000 og −5.000 er en tilbageførsel, ikke en dublet),
- samme valuta,
- datoerne ligger højst VINDUE_DAGE fra hinanden (faste månedlige betalinger falder udenfor),
- de er to forskellige posteringer (forskelligt posteringsnummer),
- de er IKKE fra samme bilag (et bilags egne linjer er ikke dubletter af hinanden),
- SAMME TEKST (uden forskel på store/små bogstaver og ekstra mellemrum) (D),
- bilagenes kunde/leverandør er den samme, når begge bilag har en (A),
- ingen af dem er tilbageført: der findes ingen postering med MODSAT beløb på samme
  konto inden for vinduet (B).
Linjepar mellem de samme to bilag samles til ÉT fund (C) – så salgslinje, momslinje
og banklinje fra samme dobbeltbogføring ikke giver tre fund.

Alvorlighed:
  high   – samme dato (på mindst ét linjepar)
  medium – inden for vinduet

Historik:
- Version 2 (04.10.2026): demo-aftalen gav 59 fund på 138 posteringer, heraf 56 med
  ens runde beløb på forskellige konti. Vindue 7 -> 3 dage, og samme konto kræves.
- Version 3 (04.10.2026): Din Bogholder ApS (43.463 posteringer) gav 12.053 fund.
  Målt: 10.504 var fakturaer til FORSKELLIGE kunder med samme pris (A), 2.332 var
  tilbageført (B); efter A og B 589 linjepar = 306 bilagspar (C).
- Version 4 (04.10.2026): 5 tilfældige fund fra version 3 kontrolleret – alle 5 falske:
  to forskellige fakturaer/kunder, hvor kunden kun står i teksten (fakturaer fra et
  eksternt faktureringssystem uden debitor i regnskabssystemet). Samme tekst kræves nu (D).
  Pris: et bilag bogført to gange med FORSKELLIG tekst fanges ikke.

Sammenligningen er ÉN SQL-forespørgsel (entries sammenlignet med sig selv). Python
samler kun de fundne par pr. bilagspar – ingen løkke over alle posteringer.
Reglen læser kun vores egen database.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.rules.base import FindingDraft, fingerprint, registrer_regel

# Hvor mange dage der højst må være mellem to posteringer, før de ikke længere regnes
# som en mulig dublet. Kort vindue, så husleje, leasing og abonnementer ikke rammes.
# Version 1 brugte 7 dage; sænket til 3 efter kontrollen på demo-aftalen.
VINDUE_DAGE = 3

PAR_SQL = text(r"""
WITH bilag AS (
    -- Kunde/leverandør pr. bilag: står på debitor-/kreditorlinjen, ikke på salgs-/udgiftslinjen.
    SELECT bilagsnummer, dato, min(modpart) AS modpart
    FROM entries
    WHERE client_id = :client_id AND modpart IS NOT NULL AND bilagsnummer IS NOT NULL
    GROUP BY bilagsnummer, dato
)
SELECT a.id            AS a_id,           b.id            AS b_id,
       a.bogfoert_id   AS a_nr,           b.bogfoert_id   AS b_nr,
       a.dato          AS a_dato,         b.dato          AS b_dato,
       a.kontonummer   AS a_konto,        b.kontonummer   AS b_konto,
       a.tekst         AS a_tekst,        b.tekst         AS b_tekst,
       a.bilagsnummer  AS a_bilag,        b.bilagsnummer  AS b_bilag,
       a.modpart       AS a_modpart,      b.modpart       AS b_modpart,
       ba.modpart      AS a_bilag_modpart, bb.modpart     AS b_bilag_modpart,
       a.beloeb        AS beloeb,         a.valuta        AS valuta,
       CASE WHEN a.dato = b.dato THEN 'high' ELSE 'medium' END AS severity
FROM entries a
JOIN entries b
  ON  b.client_id   = a.client_id
  AND b.kontonummer = a.kontonummer                 -- samme konto
  AND b.beloeb      = a.beloeb                      -- samme beløb OG samme fortegn
  AND b.valuta      IS NOT DISTINCT FROM a.valuta
  AND b.bogfoert_id > a.bogfoert_id                 -- forskellige poster, hvert par kun én gang
  AND b.dato BETWEEN a.dato - :vindue AND a.dato + :vindue
LEFT JOIN bilag ba ON ba.bilagsnummer = a.bilagsnummer AND ba.dato = a.dato
LEFT JOIN bilag bb ON bb.bilagsnummer = b.bilagsnummer AND bb.dato = b.dato
-- B: er beløbet tilbageført på samme konto inden for vinduet, er sagen udlignet.
-- (LATERAL + LIMIT 1 = ét direkte opslag i indekset pr. par, også når samme beløb går igen
-- tusindvis af gange.)
LEFT JOIN LATERAL (
    SELECT 1 AS fundet FROM entries c
    WHERE c.client_id   = a.client_id
      AND c.kontonummer = a.kontonummer
      AND c.beloeb      = -a.beloeb
      AND c.dato BETWEEN least(a.dato, b.dato) - :vindue AND greatest(a.dato, b.dato) + :vindue
    LIMIT 1
) tilbagefoert ON TRUE
WHERE a.client_id = :client_id
  AND a.beloeb <> 0
  AND a.dato IS NOT NULL
  AND (a.bilagsnummer IS NULL OR b.bilagsnummer IS NULL OR a.bilagsnummer <> b.bilagsnummer)
  AND (a.modpart IS NULL OR b.modpart IS NULL OR a.modpart = b.modpart)
  -- D: samme tekst. Kunden/fakturanummeret står ofte kun i teksten.
  AND lower(regexp_replace(trim(a.tekst), '\s+', ' ', 'g'))
      IS NOT DISTINCT FROM lower(regexp_replace(trim(b.tekst), '\s+', ' ', 'g'))
  -- A: to bilag med hver sin kunde/leverandør er ikke samme bilag bogført to gange.
  AND (ba.modpart IS NULL OR bb.modpart IS NULL OR ba.modpart = bb.modpart)
  AND tilbagefoert.fundet IS NULL                   -- B (se LATERAL ovenfor)
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


def _bilag_noegle(r, side: str) -> tuple:
    """Bilaget, linjen hører til. Uden bilagsnummer står linjen alene."""
    bilag = getattr(r, f"{side}_bilag")
    if bilag is None:
        return ("post", getattr(r, f"{side}_nr"))
    return ("bilag", bilag, getattr(r, f"{side}_dato"))


@registrer_regel
class DuplicateEntries:
    code = "duplicate_entries"
    version = 4
    name_da = "Muligt dobbeltbogført beløb"

    def run(self, session: Session, client_id: int, since: date | None) -> list[FindingDraft]:
        par = session.execute(PAR_SQL, {"client_id": client_id, "vindue": VINDUE_DAGE, "since": since})

        # C: saml linjepar mellem de samme to bilag til ét fund.
        grupper: dict[tuple, list] = defaultdict(list)
        for r in par:
            grupper[tuple(sorted([_bilag_noegle(r, "a"), _bilag_noegle(r, "b")]))].append(r)

        fund = []
        for linjepar in grupper.values():
            hoved = max(linjepar, key=lambda r: (abs(r.beloeb), r.severity == "high"))
            linjer: dict[int, tuple] = {}
            for r in linjepar:
                linjer[r.a_id] = (r.a_nr, _post(r, "a"))
                linjer[r.b_id] = (r.b_nr, _post(r, "b"))
            posteringer = [p for _, p in sorted(linjer.values(), key=lambda x: (x[1]["dato"], x[0]))]
            datoer = [r.a_dato for r in linjepar] + [r.b_dato for r in linjepar]
            titel = (f"Muligt dobbeltbogført beløb: {_kr(hoved.beloeb, hoved.valuta)} på konto "
                     f"{hoved.a_konto} {_datoer(hoved.a_dato, hoved.b_dato)}")
            if len(linjepar) > 1:
                titel += f" (bilag {hoved.a_bilag} og {hoved.b_bilag}, {len(linjer)} linjer)"
            fund.append(FindingDraft(
                fingerprint=fingerprint([str(nr) for nr, _ in linjer.values()]),
                severity="high" if any(r.severity == "high" for r in linjepar) else "medium",
                title=titel,
                detail={
                    "beloeb": str(hoved.beloeb),
                    "valuta": hoved.valuta,
                    "vindue_dage": VINDUE_DAGE,
                    "regel_version": self.version,
                    "dage_imellem": abs((hoved.b_dato - hoved.a_dato).days),
                    "bilag": sorted({str(p["bilagsnummer"]) for p in posteringer}),
                    "kunde_leverandoer": hoved.a_bilag_modpart or hoved.b_bilag_modpart,
                    "linjepar": len(linjepar),
                    "posteringer": posteringer,
                },
                entry_ids=sorted(linjer),
                period_start=min(datoer),
                period_end=max(datoer),
            ))
        return fund
