"""Fanen Stamdata på kundekortet (bygherren 26.10.2026): navn, CVR-nummer, virksomhedsform og regnskabsperiode.

- Navn og virksomhedsform kommer fra CVR (kunde_cvr_snapshot, hentet automatisk af app/kunder/cvr.py – via
  Datafordeleren, indtil Erhvervsstyrelsens adgang er på plads). Tastes aldrig manuelt.
- REGNSKABSPERIODEN kommer fra kundens eget regnskabssystem (accounting_years, hentet hver nat): det regnskabsår,
  der omfatter dagen. Når CVR også kender regnskabsårets slutning (kræver Erhvervsstyrelsens adgang), sammenlignes de,
  og en forskel vises som en advarsel – CVR bruges til kontrol.
- Kun læsning.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.kunder.models import Client
from app.kunder.tabeller import KundeCvrSnapshot
from app.regnskab.models import AccountingYearCache


def regnskabsaar(session: Session, client_id: int, dag: date) -> AccountingYearCache | None:
    """Regnskabsåret fra kundens regnskabssystem, der omfatter dagen. Flere, der overlapper = intet svar (gætter aldrig)."""
    aar = list(session.scalars(select(AccountingYearCache).where(
        AccountingYearCache.client_id == client_id, AccountingYearCache.fra <= dag, AccountingYearCache.til >= dag)))
    return aar[0] if len(aar) == 1 else None


def json(session: Session, kunde: Client, dag: date) -> dict:
    snap = session.get(KundeCvrSnapshot, kunde.id)
    aar = regnskabsaar(session, kunde.id, dag)
    advarsel = None
    if aar is not None and snap is not None and snap.fiscal_year_end and snap.fiscal_year_end != f"{aar.til:%d-%m}":
        dag_, maaned = snap.fiscal_year_end.split("-")
        advarsel = (f"CVR-registret siger, at regnskabsåret slutter {dag_}.{maaned}, men i regnskabssystemet slutter "
                    f"det {aar.til:%d.%m}. Tjek, hvilket der er rigtigt.")
    return {
        "navn": snap.name if snap else None,
        "cvr": kunde.cvr,
        "virksomhedsform": snap.company_form if snap else None,
        "status": snap.status if snap else None,
        "hentet": snap.fetched_at.isoformat() if snap else None,
        "regnskabsperiode": {"fra": aar.fra.isoformat(), "til": aar.til.isoformat(), "navn": aar.navn} if aar else None,
        "regnskabsperiode_advarsel": advarsel,
    }
