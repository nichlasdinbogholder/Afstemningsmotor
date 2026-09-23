"""Overblik over synkronisering og tvungen genindlæsning.

    python -m app.synk.kommando oversigt                              # hvem er bagud / fejler
    python -m app.synk.kommando status --kundenummer 40850635         # alt for én kunde
    python -m app.synk.kommando nulstil --kundenummer 40850635 --ressource accounts
    python -m app.synk.kommando nulstil --kundenummer 40850635 --alle-ressourcer
    python -m app.synk.kommando deaktiver --kundenummer 40850635 --ressource entries
    python -m app.synk.kommando aktiver  --kundenummer 40850635 --ressource entries
"""

import argparse
import sys

from sqlalchemy import select

import app.models  # noqa: F401  (alle tabeller skal være kendt)
from app.db import ny_session
from app.kunder.models import Client
from app.synk.models import RESSOURCER, SyncState
from app.synk.tilstand import SynkFejl, kraever_handling, nulstil_cursor, saet_deaktiveret


def _tid(t) -> str:
    return f"{t:%Y-%m-%d %H:%M}" if t else "–"


def _find_kunde(session, kundenummer: str) -> Client:
    kunde = session.scalars(select(Client).where(Client.kundenummer == kundenummer)).one_or_none()
    if kunde is None:
        raise SynkFejl(f"Kunde {kundenummer} findes ikke")
    return kunde


def _oversigt(session) -> None:
    raekker = kraever_handling(session)
    if not raekker:
        print("Alt er opdateret – ingen kunder er bagud.")
        return
    print(f"{len(raekker)} kræver opmærksomhed:\n")
    for r in raekker:
        print(f"  {r['kundenummer']:<10} {r['navn'][:28]:<28} {r['ressource']:<10} {r['aarsag']}")
        print(f"  {'':<10} sidst OK: {_tid(r['sidste_ok'])}   næste forsøg: {_tid(r['naeste_koersel'])}")
        if r["sidste_fejl_besked"]:
            print(f"  {'':<10} fejl: {r['sidste_fejl_besked'].splitlines()[0][:100]}")
        print()


def _status(session, kunde: Client) -> None:
    print(f"{kunde.kundenummer} {kunde.navn} (kundestatus: {kunde.status})\n")
    tilstande = session.scalars(
        select(SyncState).where(SyncState.client_id == kunde.id).order_by(SyncState.ressource)
    ).all()
    if not tilstande:
        print("  Ingen synkronisering endnu.")
    for t in tilstande:
        print(f"  {t.ressource:<10} {t.status:<11} bogmærke: {t.cursor or '(forfra)'}"
              f"   sidst OK: {_tid(t.sidste_ok)}   næste: {_tid(t.naeste_koersel)}"
              f"   fejl i træk: {t.antal_fejl_i_traek}   hentet sidst: {t.antal_hentet_sidst or '–'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synkroniseringstilstand.")
    under = parser.add_subparsers(dest="handling", required=True)
    under.add_parser("oversigt", help="kunder, der er bagud eller fejler")
    st = under.add_parser("status", help="al synkronisering for én kunde")
    st.add_argument("--kundenummer", required=True)
    for navn, hjaelp in (("nulstil", "tving fuld genindlæsning"),
                         ("deaktiver", "slå synkronisering fra"),
                         ("aktiver", "slå synkronisering til igen")):
        p = under.add_parser(navn, help=hjaelp)
        p.add_argument("--kundenummer", required=True)
        hvad = p.add_mutually_exclusive_group(required=True)
        hvad.add_argument("--ressource", choices=RESSOURCER)
        hvad.add_argument("--alle-ressourcer", action="store_true")
    args = parser.parse_args(argv)

    with ny_session() as session:
        try:
            if args.handling == "oversigt":
                _oversigt(session)
                return 0
            kunde = _find_kunde(session, args.kundenummer)
            if args.handling == "status":
                _status(session, kunde)
                return 0
            ressourcer = list(RESSOURCER) if args.alle_ressourcer else [args.ressource]
            for ressource in ressourcer:
                if args.handling == "nulstil":
                    nulstil_cursor(session, kunde.id, ressource)
                else:
                    saet_deaktiveret(session, kunde.id, ressource, args.handling == "deaktiver")
        except SynkFejl as fejl:
            print(f"Fejl: {fejl}", file=sys.stderr)
            return 1

    tekst = {
        "nulstil": "Bogmærke nulstillet – hentes helt forfra ved næste kørsel",
        "deaktiver": "Synkronisering slået fra",
        "aktiver": "Synkronisering slået til igen",
    }[args.handling]
    print(f"{tekst}: {kunde.kundenummer} {kunde.navn} – {', '.join(ressourcer)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
