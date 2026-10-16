"""Kommandoer til at køre ting i hånden.

    python -m app.cli sync-entries <client_id>
    python -m app.cli run-rules <client_id>
    python -m app.cli findings <client_id> [--status open] [--severity high] [--alle]
    python -m app.cli set-status <finding_id> <accepted|resolved|ignored|open> --note "..."

run-rules kører alle aktive regler for kunden og gemmer fundene (findings).
findings viser fundene som en tabel. set-status ændrer status på ét fund og
logger det i finding_events med dit brugernavn som actor.

sync-entries opretter et `synk_entries`-job i jobkøen og kører netop det job
med det samme (synkront), så outputtet ses i terminalen. Til sidst vises antal
hentede, nye og opdaterede posteringer og det nye bogmærke (cursor).
"""

import argparse
import getpass
import logging
import os
import sys
from datetime import date, datetime

from sqlalchemy import text

import app.models  # noqa: F401
from app.db import ny_session
from app.jobs.koe import laeg_i_koe
from app.jobs.worker import Worker
from app.kunder.models import Client
from app.rules.koersel import koer_regler
from app.rules.models import ALVORLIGHED, STATUSSER, Finding
from app.rules.status import UgyldigStatus, saet_status
from app.rules.visning import fund_med_aktuel, sorteringsnoegle


def sync_entries(client_id: int, worker: Worker | None = None) -> int:
    with ny_session() as session:
        kunde = session.get(Client, client_id)
        if kunde is None:
            print(f"Fejl: Kunde {client_id} findes ikke", file=sys.stderr)
            return 2
        tidspunkt = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")
        job = laeg_i_koe(session, "synk_entries", client_id=client_id, prioritet=0,
                         idempotens_noegle=f"entries:{client_id}:cli-{tidspunkt}")
        session.commit()
    print(f"Kunde {client_id} ({kunde.navn}): job #{job.job_id} oprettet – kører nu …")

    (worker or Worker(navn=f"cli:{job.job_id}")).koer_et_job(bestemt_id=job.job_id)

    with ny_session() as session:
        raekke = session.execute(
            text("SELECT status, forsoeg, sidste_fejl, payload -> 'resultat' AS resultat "
                 "FROM jobs WHERE id = :id"), {"id": job.job_id}).one()
    if raekke.status != "faerdig":
        print(f"Job #{job.job_id} blev IKKE færdigt (status: {raekke.status}, forsøg {raekke.forsoeg}).")
        print(f"Fejl: {raekke.sidste_fejl}")
        if raekke.status == "koe":
            print("Jobkøen prøver igen automatisk, når workeren kører (python -m app.jobs.worker).")
        return 1
    r = raekke.resultat or {}
    print(f"Hentet:      {r.get('hentet', 0)}")
    print(f"Nye:         {r.get('nye', 0)}")
    print(f"Opdaterede:  {r.get('opdaterede', 0)}")
    print(f"Ny cursor:   {r.get('cursor')}")
    return 0


def run_rules(client_id: int) -> int:
    with ny_session() as session:
        kunde = session.get(Client, client_id)
        if kunde is None:
            print(f"Fejl: Kunde {client_id} findes ikke", file=sys.stderr)
            return 2
        resultat = koer_regler(session, client_id)
        session.commit()
    print(f"Kunde {client_id} ({kunde.navn}): regler kørt")
    for r in resultat.regler:
        print(f"  {r.rule_code:<22} fund: {r.fundet:>5}   nye: {r.nye:>5}   set før: {r.set_igen:>5}")
    return 0


def _klip(tekst: str, bredde: int) -> str:
    return tekst if len(tekst) <= bredde else tekst[:bredde - 1] + "…"


def vis_findings(client_id: int, status: str | None = None, severity: str | None = None,
                 alle: bool = False) -> int:
    """Som standard kun AKTUELLE fund: dem, seneste kørsel af reglen stadig fandt.
    Fund, der ikke længere optræder, slettes aldrig – de vises med --alle."""
    with ny_session() as session:
        stmt = fund_med_aktuel(client_id)
        if status:
            stmt = stmt.where(Finding.status == status)
        if severity:
            stmt = stmt.where(Finding.severity == severity)
        raekker = session.execute(stmt).all()
    skjult = sum(1 for _, er_aktuel in raekker if not er_aktuel)
    fund = sorted(((f, er_aktuel) for f, er_aktuel in raekker if alle or er_aktuel),
                  key=lambda x: sorteringsnoegle(x[0]))
    if not fund:
        print("Ingen fund.")
    else:
        aktuel_kol = f"  {'aktuel':<6}" if alle else ""
        print(f"{'id':>6}  {'dato':<10}  {'alvor':<6}  {'status':<8}{aktuel_kol}  {'poster':>6}  titel")
        print("-" * (135 + len(aktuel_kol)))
        for f, er_aktuel in fund:
            dato = (f.period_start or f.first_seen_at.date()).isoformat()
            kol = f"  {'ja' if er_aktuel else 'nej':<6}" if alle else ""
            print(f"{f.id:>6}  {dato:<10}  {f.severity:<6}  {f.status:<8}{kol}  {len(f.entry_ids):>6}  "
                  f"{_klip(f.title, 95)}")
        print(f"\n{len(fund)} fund")
    if skjult and not alle:
        print(f"({skjult} ældre fund optræder ikke længere i seneste kørsel – vis dem med --alle)")
    return 0


def set_status(finding_id: int, status: str, note: str | None) -> int:
    actor = os.environ.get("USER") or getpass.getuser()
    with ny_session() as session:
        try:
            skift = saet_status(session, finding_id, status, actor=actor, note=note)
        except UgyldigStatus as fejl:
            print(f"Fejl: {fejl}", file=sys.stderr)
            return 2
        session.commit()
    if skift.fra == skift.til:
        print(f"Fund {finding_id} har allerede status {skift.til} – intet ændret.")
    else:
        print(f"Fund {finding_id}: {skift.fra} -> {skift.til} (af {actor}). Logget i finding_events.")
    return 0


SPAERRE_TEKST = {
    "betalt": "betalt", "krediteret": "krediteret", "afskrevet": "afskrevet", "kreditnota": "kreditnota",
    "ikke_forfalden": "ikke forfalden", "indbetaling_seneste_2_bankdage": "indbetaling de seneste 2 bankdage",
    "indbetaling_i_kassekladde": "indbetaling ligger i kassekladden", "debitor_blokeret": "debitor blokeret",
    "afbetalingsordning": "aktiv afbetalingsordning", "under_minimumsbeloeb": "restbeløb under minimum",
    "under_10_dage": "for tidligt (10-dagesreglen/kundens plan)", "max_3_rykkere": "har allerede 3 rykkere",
    "aktiv_inkassosag": "aktiv inkassosag", "fjernet_af_medarbejder": "fjernet af medarbejder",
    "rykker_i_koe": "en påmindelse eller rykker ligger allerede i kø",
}


def _kr(b) -> str:
    return f"{b:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def dunning_preview(client_id: int, dag: date | None) -> int:
    """Hvad ville rykkermotoren lægge i kø? Intet gemmes og intet sendes."""
    from app.opkraevning.lov import ManglerReferencesats
    from app.opkraevning.rykkerkoersel import forhaandsvis
    from app.tid import TIDSZONE

    dag = dag or datetime.now(TIDSZONE).date()
    with ny_session() as session:
        kunde = session.get(Client, client_id)
        if kunde is None:
            print("Fejl: kunden findes ikke", file=sys.stderr)
            return 2
        try:
            r = forhaandsvis(session, client_id, dag)
        except ManglerReferencesats as fejl:
            print(f"Fejl: {fejl}", file=sys.stderr)
            return 2
        print(f"Rykkere for {kunde.navn} pr. {dag:%d.%m.%Y}  (forhåndsvisning – intet gemmes, intet sendes;"
              f" rykkere: {kunde.dunning_mode})\n")
        print(f"VILLE BLIVE SENDT ({len(r.lagt_i_koe)})")
        print(f"  {'faktura':>8}  {'debitor':<28} {'forfald':<10} {'restbeløb':>12}  nr  {'gebyr':>7} {'rente':>8} "
              f"{'komp.':>7}")
        for v in r.lagt_i_koe:
            print(f"  {v.faktura.invoice_no:>8}  {v.debitor.name[:28]:<28} {v.faktura.due_date:%d.%m.%Y} "
                  f"{_kr(v.faktura.amount_outstanding):>12}  {'P' if v.rykker.step_no == 0 else v.rykker.step_no + v.faktura.prior_dunning_count:>2}  "
                  f"{_kr(v.rykker.fee_amount):>7} {_kr(v.rykker.interest_amount):>8} {_kr(v.rykker.compensation_amount):>7}")
        print("  (nr P = venlig påmindelse uden gebyr)")
        print(f"\nSPRINGES OVER ({len(r.sprunget_over)})")
        for v in r.sprunget_over:
            grunde = ", ".join(SPAERRE_TEKST.get(k, k) for k, _ in v.grunde)
            print(f"  {v.faktura.invoice_no:>8}  {v.debitor.name[:28]:<28} {_kr(v.faktura.amount_outstanding):>12}  {grunde}")
    return 0


def dunning_remove(rykker_id: int, note: str) -> int:
    from app.opkraevning.rykkerkoersel import KanIkkeFjernes, fjern_rykker

    af = os.environ.get("USER") or getpass.getuser()
    with ny_session() as session:
        try:
            r = fjern_rykker(session, rykker_id, af, note)
        except KanIkkeFjernes as fejl:
            print(f"Fejl: {fejl}", file=sys.stderr)
            return 2
        session.commit()
    print(f"Rykker {r.id} er fjernet (af {af}). Grunden står i dunning_skips.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Kør ting i hånden.")
    under = parser.add_subparsers(dest="kommando", required=True)
    se = under.add_parser("sync-entries", help="hent posteringer for én kunde nu (via jobkøen)")
    se.add_argument("client_id", type=int, help="kundens id i clients")
    rr = under.add_parser("run-rules", help="kør alle aktive regler for én kunde")
    rr.add_argument("client_id", type=int, help="kundens id i clients")
    fi = under.add_parser("findings", help="vis en kundes fund som en tabel")
    fi.add_argument("client_id", type=int, help="kundens id i clients")
    fi.add_argument("--status", choices=STATUSSER)
    fi.add_argument("--severity", choices=ALVORLIGHED)
    fi.add_argument("--alle", action="store_true", help="vis også fund, der ikke længere optræder")
    ss = under.add_parser("set-status", help="skift status på ét fund (logges)")
    ss.add_argument("finding_id", type=int)
    ss.add_argument("status", choices=[s for s in STATUSSER if s != "open"] + ["open"])
    ss.add_argument("--note", help="hvorfor (gemmes i finding_events)")
    dp = under.add_parser("dunning-preview", help="vis hvilke rykkere der ville blive sendt (gemmer intet)")
    dp.add_argument("client_id", type=int)
    dp.add_argument("--dag", type=date.fromisoformat, help="standard: i dag")
    dr = under.add_parser("dunning-remove", help="fjern en rykker i kø, før den sendes")
    dr.add_argument("rykker_id", type=int)
    dr.add_argument("--note", required=True, help="hvorfor (fx 'kunden ringede, betaler fredag')")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%H:%M:%S")
    if args.kommando == "sync-entries":
        return sync_entries(args.client_id)
    if args.kommando == "run-rules":
        return run_rules(args.client_id)
    if args.kommando == "findings":
        return vis_findings(args.client_id, args.status, args.severity, args.alle)
    if args.kommando == "set-status":
        return set_status(args.finding_id, args.status, args.note)
    if args.kommando == "dunning-preview":
        return dunning_preview(args.client_id, args.dag)
    if args.kommando == "dunning-remove":
        return dunning_remove(args.rykker_id, args.note)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # Output sendt videre til fx `head`, som stoppede tidligt – ikke en fejl.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
