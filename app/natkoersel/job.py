"""Natkørslen for ÉN kunde (jobtypen `natkoersel_kunde`).

Trin, i denne rækkefølge:
1. synkronisering: kontoplan, kunder, leverandører, posteringer, åbne poster
2. regelmotoren (app.rules – fx dubletter)
3. kassekladdekontrollen: linjer på fejlkontoen (standard 9900) i kassekladden.
   Fundene gemmes i findings med rule_code `fejlkonto_kassekladde`.
4. resultatet skrives i audit_log (handling `natkoersel_kunde`).

Et trin, der fejler, stopper IKKE de næste trin – reglerne kører på de data, der er.
Fejlede et trin, rejses `NatkoerselFejl` til sidst, så jobkøen prøver igen senere, og
fejlen havner i Sentry med kundens tags. Bliver regnskabssystemet ramt af "for mange
kald", udskydes hele jobbet (alle trin tåler at blive kørt igen).
Én kundes fejl påvirker aldrig de andre – hver kunde er sit eget job.
"""

import hashlib
import logging
from decimal import Decimal

from sqlalchemy.orm import Session

from app.adaptere.regnskab.base import ForMangeKald, hent_adapter
from app.afstemning.fejlkonto import STANDARD_FEJLKONTO, tjek_kassekladde
from app.audit.models import AuditLog
from app.jobs.register import JobKontekst, UdskydJob, jobtype
from app.kunder.models import Client
from app.rules.base import FindingDraft
from app.rules.koersel import gem_fund, koer_regler
from app.rules.models import RuleRun
from app.sikkerhed.hemmeligheder import rediger
from app.synk.kontoplan import hent_og_gem_kontoplan
from app.synk.ressourcer import SYNK_FUNKTIONER
from app.synk.tilstand import KundeIkkeAktiv, SynkDeaktiveret, SynkIGang

log = logging.getLogger(__name__)

JOBTYPE = "natkoersel_kunde"
OPDATER_JOBTYPE = "opdater_kunde"
OPDATER_PRIORITET = 10  # foran natkørslen (100)


class NatkoerselFejl(Exception):
    """Et eller flere trin fejlede for kunden (detaljer i audit_log og Sentry)."""


class _KassekladdeRegel:
    """Beskrivelse af kassekladdekontrollen, så fundene kan gemmes som andre regler."""

    code = "fejlkonto_kassekladde"
    version = 1
    name_da = "Postering på fejlkonto i kassekladde"


def _kr(beloeb: Decimal | None) -> str:
    if beloeb is None:
        return "–"
    return f"{beloeb:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " kr."


def _kassekladde_udkast(resultat) -> list[FindingDraft]:
    udkast = []
    navne = {k.nummer: k.navn for k in resultat.kladder}
    for p in resultat.fund:
        noegle = (f"{p.kladde_nummer}|linje:{p.linje_id}" if p.linje_id is not None else
                  f"{p.kladde_nummer}|{p.bilagsnummer}|{p.dato}|{p.konto}|{p.modkonto}|{p.beloeb}|{p.tekst}")
        udkast.append(FindingDraft(
            fingerprint=hashlib.sha256(noegle.encode()).hexdigest()[:32],
            severity="medium",
            title=(f"Postering på fejlkonto {resultat.kontonummer} i kassekladde "
                   f"'{navne.get(p.kladde_nummer) or p.kladde_nummer}': {_kr(p.beloeb)} "
                   f"(bilag {p.bilagsnummer or '–'}, {p.dato or 'uden dato'})"),
            detail={
                "kladde": p.kladde_nummer, "kladde_navn": navne.get(p.kladde_nummer),
                "linje": p.linje_id, "bilagsnummer": p.bilagsnummer,
                "dato": p.dato.isoformat() if p.dato else None,
                "konto": p.konto, "modkonto": p.modkonto, "tekst": p.tekst,
                "beloeb": str(p.beloeb) if p.beloeb is not None else None,
                "fejlkonto": resultat.kontonummer,
            },
            entry_ids=[],
            period_start=p.dato, period_end=p.dato,
        ))
    return udkast


def _fejltekst(fejl: BaseException) -> str:
    return rediger(f"{type(fejl).__name__}: {fejl}").splitlines()[0][:300]


def koer_natkoersel(session: Session, client_id: int, adapter_fabrik=hent_adapter,
                    handling: str = "natkoersel_kunde") -> dict:
    """Kør alle trin for én kunde. Returnerer resultatet (det samme, der skrives i audit_log)."""
    trin: dict[str, str] = {}
    tal: dict[str, int] = {}

    # 1. Synkronisering – hver ressource for sig; én fejl stopper ikke de næste.
    synk_trin = [("kontoplan", lambda: hent_og_gem_kontoplan(session, client_id))]
    synk_trin += [(navn, (lambda f=f: f(session, client_id))) for navn, f in SYNK_FUNKTIONER.items()]
    for navn, kald in synk_trin:
        try:
            kald()
            trin[f"synk_{navn}"] = "ok"
        except ForMangeKald:
            raise
        except (KundeIkkeAktiv, SynkDeaktiveret) as fejl:
            trin[f"synk_{navn}"] = f"sprunget over: {_fejltekst(fejl)}"
        except SynkIGang:
            trin[f"synk_{navn}"] = "sprunget over: synkroniseres allerede"
        except Exception as fejl:  # noqa: BLE001 – næste trin skal stadig køre
            session.rollback()
            trin[f"synk_{navn}"] = f"fejl: {_fejltekst(fejl)}"

    # 2. Regelmotoren.
    try:
        regler = koer_regler(session, client_id)
        session.commit()
        tal["regelfund"] = sum(r.fundet for r in regler.regler)
        trin["regler"] = "ok"
    except Exception as fejl:  # noqa: BLE001
        session.rollback()
        trin["regler"] = f"fejl: {_fejltekst(fejl)}"

    # 3. Kassekladdekontrollen (læser kun fra regnskabssystemet).
    try:
        kunde = session.get(Client, client_id)
        with adapter_fabrik(session, client_id) as adapter:
            resultat = tjek_kassekladde(adapter, STANDARD_FEJLKONTO, None, kunde.kassekladde_navn)
        r = gem_fund(session, client_id, _KassekladdeRegel, _kassekladde_udkast(resultat))
        session.add(RuleRun(client_id=client_id, rule_code=_KassekladdeRegel.code,
                            rule_version=_KassekladdeRegel.version, fund=r.fundet))
        session.commit()
        tal["kassekladde_fund"] = r.fundet
        trin["kassekladde"] = "ok"
    except ForMangeKald:
        raise
    except Exception as fejl:  # noqa: BLE001
        session.rollback()
        trin["kassekladde"] = f"fejl: {_fejltekst(fejl)}"

    fejlede = [navn for navn, status in trin.items() if status.startswith("fejl")]
    resultat = {"status": "fejl" if fejlede else "ok", "trin": trin, **tal}

    # 4. Log kørslen – også når noget fejlede.
    session.add(AuditLog(client_id=client_id, handling=handling, detaljer=resultat))
    session.commit()
    if fejlede:
        log.warning("Natkørsel for kunde %s: %s fejlede", client_id, ", ".join(fejlede))
    else:
        log.info("Natkørsel for kunde %s: ok (%s)", client_id, tal)
    return resultat


@jobtype(JOBTYPE)
def natkoersel_kunde(job: JobKontekst) -> None:
    _koer_som_job(job, "natkoersel_kunde")


@jobtype(OPDATER_JOBTYPE)
def opdater_kunde(job: JobKontekst) -> None:
    """"Opdater nu" fra webdelen: det samme som natkørslen, for én kunde, forrest i køen."""
    _koer_som_job(job, "opdater_kunde")


def _koer_som_job(job: JobKontekst, handling: str) -> None:
    if job.client_id is None:
        raise ValueError(f"Job {job.job_id} mangler client_id")
    try:
        resultat = koer_natkoersel(job.session, job.client_id, handling=handling)
    except ForMangeKald as fejl:
        raise UdskydJob(fejl.vent_sekunder, str(fejl)) from None
    fejlede = [n for n, s in resultat["trin"].items() if s.startswith("fejl")]
    if fejlede:
        raise NatkoerselFejl(f"Kunde {job.client_id}: {', '.join(fejlede)} fejlede – se audit_log")
