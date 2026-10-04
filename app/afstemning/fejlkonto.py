"""Tjek en kassekladde for posteringer på en fejlkonto (standard 9900).

    python -m app.afstemning.fejlkonto --kundenummer 40850635
    python -m app.afstemning.fejlkonto --kundenummer 40850635 --kladde "Kassekladde"
    python -m app.afstemning.fejlkonto --kundenummer 40850635 --alle-kladder --konto 9900

Hvilken kassekladde:
- `--kladde` (navn eller nummer), ellers
- kundens `kassekladde_navn` fra kundekartoteket, ellers
- alle kassekladder hos kunden.

En linje tæller med, når fejlkontoen står som konto ELLER modkonto.
Tjekket LÆSER kun fra regnskabssystemet – intet bogføres eller ændres.
Desuden vises bogførte poster på fejlkontoen fra seneste synkronisering.

Afslutningskode: 0 = ingen posteringer på fejlkontoen, 1 = der er posteringer, 2 = fejl.
"""

import argparse
import sys
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.adaptere.regnskab.base import Kassekladde, KladdePost, RegnskabsAdapter, hent_adapter
from app.db import ny_session
from app.kunder.models import Client
from app.regnskab.models import EntryCache
from app.sikkerhed.hemmeligheder import rediger

STANDARD_FEJLKONTO = 9900


class FejlkontoFejl(Exception):
    pass


@dataclass
class Resultat:
    kontonummer: int
    kladder: list[Kassekladde] = field(default_factory=list)
    fund: list[KladdePost] = field(default_factory=list)

    @property
    def sum(self) -> Decimal:
        """Nettobeløb på fejlkontoen (modkonto tæller med modsat fortegn)."""
        return sum(
            ((p.beloeb or Decimal(0)) * (1 if p.konto == self.kontonummer else -1) for p in self.fund),
            Decimal(0),
        )


def vaelg_kladder(alle: list[Kassekladde], kladde: str | None, standard_navn: str | None,
                  alle_kladder: bool) -> list[Kassekladde]:
    if alle_kladder:
        return alle
    oenske = kladde if kladde is not None else standard_navn
    if oenske is None:
        return alle
    valgt = [k for k in alle
             if (oenske.isdigit() and k.nummer == int(oenske))
             or (k.navn or "").strip().lower() == oenske.strip().lower()]
    if not valgt:
        navne = ", ".join(f"{k.nummer} '{k.navn}'" for k in alle) or "(ingen)"
        raise FejlkontoFejl(f"Kassekladden '{oenske}' findes ikke. Kunden har: {navne}")
    return valgt


def tjek_kassekladde(adapter: RegnskabsAdapter, kontonummer: int = STANDARD_FEJLKONTO,
                     kladde: str | None = None, standard_navn: str | None = None,
                     alle_kladder: bool = False) -> Resultat:
    resultat = Resultat(kontonummer)
    resultat.kladder = vaelg_kladder(adapter.hent_kassekladder(), kladde, standard_navn, alle_kladder)
    for k in resultat.kladder:
        resultat.fund += [p for p in adapter.hent_kassekladde_poster(k.nummer)
                          if kontonummer in (p.konto, p.modkonto)]
    return resultat


def bogfoerte_paa_konto(session: Session, client_id: int, kontonummer: int) -> list[EntryCache]:
    return list(session.scalars(
        select(EntryCache)
        .where(EntryCache.client_id == client_id, EntryCache.kontonummer == kontonummer)
        .order_by(EntryCache.dato.desc(), EntryCache.bogfoert_id.desc())
    ))


def _kr(beloeb: Decimal | None) -> str:
    if beloeb is None:
        return "–"
    return f"{beloeb:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def main(argv: list[str] | None = None, adapter_fabrik=hent_adapter) -> int:
    parser = argparse.ArgumentParser(description="Tjek kassekladde for posteringer på en fejlkonto.")
    parser.add_argument("--kundenummer", required=True)
    parser.add_argument("--konto", type=int, default=STANDARD_FEJLKONTO, help="standard: 9900")
    hvilken = parser.add_mutually_exclusive_group()
    hvilken.add_argument("--kladde", help="kassekladdens navn eller nummer")
    hvilken.add_argument("--alle-kladder", action="store_true")
    args = parser.parse_args(argv)

    with ny_session() as session:
        kunde = session.scalars(select(Client).where(Client.kundenummer == args.kundenummer)).one_or_none()
        if kunde is None:
            print("Fejl: Kunden findes ikke", file=sys.stderr)
            return 2
        try:
            with adapter_fabrik(session, kunde.id) as adapter:
                r = tjek_kassekladde(adapter, args.konto, args.kladde, kunde.kassekladde_navn,
                                     args.alle_kladder)
        except Exception as fejl:  # noqa: BLE001
            print(f"Fejl: {rediger(f'{type(fejl).__name__}: {fejl}').splitlines()[0][:300]}",
                  file=sys.stderr)
            return 2
        bogfoerte = bogfoerte_paa_konto(session, kunde.id, args.konto)

    print(f"{kunde.kundenummer} {kunde.navn} – fejlkonto {args.konto}")
    print("Kassekladde(r) tjekket: " + ", ".join(f"{k.nummer} '{k.navn}'" for k in r.kladder))
    print()
    if not r.fund:
        print(f"✔ INGEN posteringer på konto {args.konto} i kassekladden.")
    else:
        print(f"✘ {len(r.fund)} postering(er) på konto {args.konto} i kassekladden "
              f"(netto {_kr(r.sum)} kr.):")
        print(f"  {'Kladde':<7}{'Bilag':<8}{'Dato':<12}{'Konto':<8}{'Modkonto':<10}{'Beløb':>14}  Tekst")
        for p in r.fund:
            print(f"  {p.kladde_nummer:<7}{p.bilagsnummer or '–':<8}{str(p.dato or '–'):<12}"
                  f"{p.konto or '–':<8}{p.modkonto or '–':<10}{_kr(p.beloeb):>14}  {p.tekst or ''}")
    print()
    if bogfoerte:
        print(f"Bemærk: {len(bogfoerte)} BOGFØRT(E) post(er) på konto {args.konto} "
              f"(netto {_kr(sum((b.beloeb or 0) for b in bogfoerte))} kr., fra seneste synkronisering).")
    else:
        print(f"Ingen bogførte poster på konto {args.konto} (fra seneste synkronisering).")
    return 1 if r.fund else 0


if __name__ == "__main__":
    sys.exit(main())
