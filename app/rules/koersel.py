"""Kør reglerne for én kunde og gem fundene i findings.

Fund skrives med upsert på (client_id, rule_code, fingerprint): findes fundet i
forvejen, opdateres kun last_seen_at, detail, severity og updated_at. Status
røres ALDRIG af en kørsel – kun et menneske ændrer status (app.rules.status).
Fund slettes aldrig: forsvinder problemet, bliver last_seen_at bare stående.

Hver kørsel af en regel noteres i rule_runs (samme tidspunkt som last_seen_at, da det
sker i samme transaktion). Et fund er AKTUELT, når last_seen_at >= seneste kørsel.
"""

import logging
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.rules.base import FindingDraft, Rule, alle_regler
from app.rules.models import Finding, RuleRun

log = logging.getLogger(__name__)

BLOK = 1000


@dataclass
class RegelResultat:
    rule_code: str
    fundet: int = 0     # fund i denne kørsel
    nye: int = 0        # heraf nye
    set_igen: int = 0   # heraf set før (status bevaret)


@dataclass
class KoerselResultat:
    client_id: int
    regler: list[RegelResultat] = field(default_factory=list)


def gem_fund(session: Session, client_id: int, regel: Rule, udkast: list[FindingDraft]) -> RegelResultat:
    resultat = RegelResultat(regel.code)
    unikke = {u.fingerprint: u for u in udkast}  # samme fund to gange i én kørsel = ét fund
    raekker = [{
        "client_id": client_id, "rule_code": regel.code, "rule_version": regel.version,
        "fingerprint": u.fingerprint, "severity": u.severity, "title": u.title,
        "detail": u.detail, "entry_ids": u.entry_ids,
        "period_start": u.period_start, "period_end": u.period_end,
    } for u in unikke.values()]
    for start in range(0, len(raekker), BLOK):
        stmt = insert(Finding).values(raekker[start:start + BLOK])
        stmt = stmt.on_conflict_do_update(
            constraint="uq_findings_kunde_regel_fingerprint",
            set_={
                "last_seen_at": func.now(),
                "detail": stmt.excluded.detail,
                "severity": stmt.excluded.severity,
                "updated_at": func.now(),
            },
        ).returning(literal_column("xmax = 0"))
        for (ny,) in session.execute(stmt):
            resultat.nye += ny
            resultat.set_igen += not ny
    resultat.fundet = len(raekker)
    return resultat


def koer_regler(session: Session, client_id: int, since: date | None = None) -> KoerselResultat:
    """Kør alle aktive regler for én kunde. Gemmes ved session.commit() hos den, der kalder."""
    resultat = KoerselResultat(client_id)
    for regel in alle_regler():
        if not getattr(regel, "aktiv", True):
            continue
        r = gem_fund(session, client_id, regel, regel.run(session, client_id, since))
        session.add(RuleRun(client_id=client_id, rule_code=regel.code, rule_version=regel.version,
                            fund=r.fundet))
        session.flush()
        log.info("Kunde %s, regel %s: %s fund (%s nye, %s set før)",
                 client_id, regel.code, r.fundet, r.nye, r.set_igen)
        resultat.regler.append(r)
    return resultat
