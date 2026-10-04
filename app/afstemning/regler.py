"""Kør afstemningsreglerne mod open_entries og vis fundene.

    python -m app.afstemning.regler --kundenummer 40850635
    python -m app.afstemning.regler --alle
    python -m app.afstemning.regler --alle --dato 2026-10-04     # regel 3 beregnet fra en bestemt dato

Selve reglerne er SQL-funktioner i databasen (se migreringerne
"findings og afstemningsregler" og "fund lukkes automatisk"; tabellen hedder nu aabne_post_fund). Hver kørsel:
1. lukker åbne fund, hvis reglens betingelse ikke længere er opfyldt
   (fx posten er betalt) – status 'loest' med tidspunkt og årsag,
2. kører de tre regler: nye fund oprettes, og løste fund, der igen opfylder
   reglen, genåbnes. Afviste fund ('afvist') røres aldrig.
Idempotent: kører den igen på samme data, sker der ingenting.
"""

import argparse
import sys
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.afstemning.models import AabenPostFund
from app.db import ny_session
from app.kunder.models import Client

REGLER = {
    "smaa_restbeloeb": "Regel 1: udlignet post med restbeløb 0,01–99,99 kr.",
    "betaling_uden_faktura": "Regel 2: betaling uden faktura at udligne mod",
    "forfalden_over_6_mdr": "Regel 3: forfalden for mere end 6 måneder siden",
}


def koer_regler(session: Session, client_id: int | None = None, dato: date | None = None) -> dict:
    """Luk løste fund og kør alle tre regler. Returnerer antal lukkede og NYE/genåbnede fund
    pr. regel. Gemmes ved session.commit()."""
    p = {"k": client_id, "d": dato or session.scalar(text("SELECT current_date"))}
    return {
        "lukket": session.scalar(text("SELECT luk_loeste_fund(:k, :d)"), p),
        "smaa_restbeloeb": session.scalar(text("SELECT regel_1_smaa_restbeloeb(:k)"), p),
        "betaling_uden_faktura": session.scalar(text("SELECT regel_2_betaling_uden_faktura(:k)"), p),
        "forfalden_over_6_mdr": session.scalar(text("SELECT regel_3_forfalden_over_6_mdr(:k, :d)"), p),
    }


def _kr(beloeb) -> str:
    return "–" if beloeb is None else f"{beloeb:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kør afstemningsreglerne mod åbne poster.")
    hvem = parser.add_mutually_exclusive_group(required=True)
    hvem.add_argument("--kundenummer")
    hvem.add_argument("--alle", action="store_true")
    parser.add_argument("--dato", type=date.fromisoformat, help="beregningsdato for regel 3 (standard: i dag)")
    args = parser.parse_args(argv)

    with ny_session() as session:
        client_id = None
        if args.kundenummer:
            client_id = session.scalar(select(Client.id).where(Client.kundenummer == args.kundenummer))
            if client_id is None:
                print("Fejl: Kunden findes ikke", file=sys.stderr)
                return 2
        nye = koer_regler(session, client_id, args.dato)
        session.commit()

        print(f"Lukket (betingelsen er ikke længere opfyldt): {nye['lukket']}")
        print("Nye eller genåbnede fund i denne kørsel:")
        for regel, tekst in REGLER.items():
            print(f"  {tekst:<52} {nye[regel]:>5}")
        stmt = (select(AabenPostFund, Client.kundenummer).join(Client, Client.id == AabenPostFund.client_id)
                .where(AabenPostFund.status == "aaben").order_by(Client.kundenummer, AabenPostFund.regel, AabenPostFund.forfaldsdato))
        if client_id is not None:
            stmt = stmt.where(AabenPostFund.client_id == client_id)
        aabne = session.execute(stmt).all()

    print(f"\nÅbne fund i alt: {len(aabne)}")
    for f, kundenummer in aabne:
        print(f"  {kundenummer:<10} {f.regel:<22} {f.type or '':<9} part {f.partnummer or '–':<8} "
              f"faktura {f.fakturanummer or '–':<8} forfald {f.forfaldsdato or '–'}  "
              f"rest {_kr(f.restbeloeb):>12} {f.valuta or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
