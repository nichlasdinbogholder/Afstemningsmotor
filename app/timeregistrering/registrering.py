"""Registrering af timer i ugegitteret (fase 1, trin 3).

En uge er mandag–søndag (syv dage – også weekenden kan registreres). En LINJE i gitteret er en kunde (eller intern
tid/fravær), en opgavetype, en beskrivelse og "faktureres særskilt" – med timer fordelt på dagene. I databasen er
hver dag sin egen række i time_entries; linjen er de rækker i ugen, der har de samme fire oplysninger.

- Linjer kan tilføjes, rettes og slettes, så længe ugen er i kladde eller afvist. En afleveret uge kan ikke rettes af
  medarbejderen, og en godkendt uge kan ikke rettes af nogen (databasen afviser det).
- Kundens afregningsform slås op på arbejdsdagen (periodeopslaget i satser.py) og skrives på rækken, når den oprettes.
  Den ændres aldrig siden – heller ikke hvis kunden senere skifter model. Skiftes kunden på en linje, er det nye
  rækker (med ny afregningsform).
- Timer er altid decimaltal i kvarte timer (1,25 = 1 time og 15 minutter). Højst 24 timer på en dag i alt.
- Hver ændring logges af databasen i time_audit med medarbejderen som aktør.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.kunder.models import I_DRIFT, Client
from app.timeregistrering.models import TimeEmployee, TimeRegistration, TimeTaskType, TimeWeek
from app.timeregistrering.satser import ManglerSats, satser_paa
from app.timeregistrering.stamdata import saet_aktoer

KAN_RETTES = ("kladde", "afvist")
KVARTER = Decimal("0.25")


class RegistreringsFejl(Exception):
    """Fejl ved indtastning. Beskeden er dansk og kan vises direkte for medarbejderen."""


def mandag(dag: date) -> date:
    return dag - timedelta(days=dag.weekday())


def ugens_dage(dag: date) -> list[date]:
    start = mandag(dag)
    return [start + timedelta(days=i) for i in range(7)]


def find_uge(session: Session, e: TimeEmployee, dag: date) -> TimeWeek | None:
    aar, nr, _ = dag.isocalendar()
    return session.scalars(select(TimeWeek).where(TimeWeek.employee_id == e.id, TimeWeek.iso_year == aar,
                                                  TimeWeek.iso_week == nr)).one_or_none()


def hent_eller_opret_uge(session: Session, e: TimeEmployee, dag: date) -> TimeWeek:
    uge = find_uge(session, e, dag)
    if uge is None:
        aar, nr, _ = dag.isocalendar()
        uge = TimeWeek(employee_id=e.id, iso_year=aar, iso_week=nr, week_start=mandag(dag))
        session.add(uge)
        session.flush()
    return uge


def timer(tekst) -> Decimal:
    """Timer som decimaltal i kvarte timer. Komma eller punktum som decimaltegn ('1,25' eller '1.25')."""
    try:
        v = Decimal(str(tekst).strip().replace(",", "."))
    except InvalidOperation:
        raise RegistreringsFejl(f"'{tekst}' er ikke et antal timer – skriv fx 1,25") from None
    if v < 0 or v > 24:
        raise RegistreringsFejl("Timer skal være mellem 0 og 24")
    if v % KVARTER != 0:
        raise RegistreringsFejl(f"{str(v).replace('.', ',')} er ikke hele kvarter – skriv fx 1,25 eller 1,5")
    return v


@dataclass(frozen=True)
class Linje:
    """En linje i gitteret: hvad der arbejdes på. Timerne pr. dag gives særskilt."""

    opgavetype: str               # kode, fx 'kundearbejde'
    client_id: int | None
    beskrivelse: str | None
    saerskilt: bool               # "faktureres særskilt" (uden for abonnementet)


def _opgavetype(session: Session, kode: str) -> TimeTaskType:
    t = session.scalars(select(TimeTaskType).where(TimeTaskType.code == kode)).one_or_none()
    if t is None or not t.active:
        raise RegistreringsFejl("Opgavetypen findes ikke eller er ikke i brug")
    return t


def _kontroller_linje(session: Session, e: TimeEmployee, linje: Linje) -> TimeTaskType:
    t = _opgavetype(session, linje.opgavetype)
    if t.kind == "kunde":
        if linje.client_id is None:
            raise RegistreringsFejl("Vælg kunden – kundearbejde skal altid have en kunde")
        kunde = session.get(Client, linje.client_id)
        if kunde is None or kunde.status not in I_DRIFT:
            raise RegistreringsFejl("Kunden findes ikke eller er ikke aktiv")
    elif linje.client_id is not None:
        raise RegistreringsFejl("Intern tid og fravær registreres uden kunde")
    if t.blocked_for_hourly and e.employment == "timeloennet":
        raise RegistreringsFejl("Denne fraværsart kan ikke registreres for timelønnede")
    if t.only_hourly and e.employment != "timeloennet":
        raise RegistreringsFejl(f"'{t.name}' kan kun registreres af timelønnede")
    if linje.saerskilt and t.kind != "kunde":
        raise RegistreringsFejl("Kun kundearbejde kan afregnes uden for abonnementet")
    if linje.saerskilt and not (linje.beskrivelse or "").strip():
        raise RegistreringsFejl("Skriv en beskrivelse – den skal stå på fakturaen, når arbejdet er uden for "
                                "abonnementet og afregnes som timer")
    return t


def _ugens_raekker(session: Session, uge: TimeWeek, ids: list[int]) -> list[TimeRegistration]:
    if not ids:
        return []
    raekker = list(session.scalars(select(TimeRegistration).where(TimeRegistration.id.in_(ids))))
    if len(raekker) != len(set(ids)) or any(r.week_id != uge.id for r in raekker):
        raise RegistreringsFejl("Linjen hører ikke til ugen")
    return raekker


def _kraev_aaben(uge: TimeWeek) -> None:
    if uge.status not in KAN_RETTES:
        raise RegistreringsFejl("Ugen er afleveret og kan ikke rettes" if uge.status == "afleveret"
                                else "Ugen er godkendt og kan ikke rettes – en fejl rettes med en korrektionslinje")


def gem_linje(session: Session, e: TimeEmployee, staff_id: int, dag_i_ugen: date, ids: list[int], linje: Linje,
              timer_pr_dag: dict[date, Decimal]) -> list[TimeRegistration]:
    """Gem én linje i ugen. `ids` er linjens eksisterende rækker (tom for en ny linje).

    En dag med 0 timer slettes – undtagen for arter, der altid registreres med 0 timer (fx 'ikke på arbejde').
    Ændres kunden, opgavetypen, beskrivelsen eller "faktureres særskilt", gemmes de nye oplysninger på rækkerne;
    skiftes kunden, oprettes rækkerne forfra, så afregningsformen slås op for den nye kunde."""
    saet_aktoer(session, staff_id)
    uge = hent_eller_opret_uge(session, e, dag_i_ugen)
    _kraev_aaben(uge)
    t = _kontroller_linje(session, e, linje)
    dage = set(ugens_dage(uge.week_start))
    if any(d not in dage for d in timer_pr_dag):
        raise RegistreringsFejl("En af datoerne ligger ikke i ugen")
    if t.zero_hours and any(v != 0 for v in timer_pr_dag.values()):
        raise RegistreringsFejl(f"'{t.name}' registreres uden timer – sæt et 0 på dagen")
    gamle = _ugens_raekker(session, uge, ids)
    beskrivelse = (linje.beskrivelse or "").strip() or None
    # To linjer med samme kunde, opgavetype, beskrivelse og "særskilt" ville løbe sammen til én – ret den ene i stedet.
    if any(r.id not in set(ids) for r in linjens_raekker(session, uge, linje)):
        raise RegistreringsFejl("Der er allerede en linje i ugen med samme kunde, opgavetype og beskrivelse – "
                                "skriv timerne på den, eller giv linjen en anden beskrivelse")
    # Skiftes kunden, er det nye rækker: de gamle slettes, så afregningsformen slås op for den nye kunde.
    if any(r.client_id != linje.client_id for r in gamle):
        for r in gamle:
            session.delete(r)
        session.flush()
        gamle = []
    pr_dag = {r.work_date: r for r in gamle}
    nu = datetime.now(timezone.utc)
    for dag in sorted(dage):
        v = timer_pr_dag.get(dag)
        r = pr_dag.get(dag)
        behold = v is not None and (v > 0 or t.zero_hours)
        if not behold:
            if r is not None:
                session.delete(r)
            continue
        if r is None:
            afregning = None
            if t.kind == "kunde":
                try:
                    afregning = satser_paa(session, dag, client_id=linje.client_id).afregning
                except ManglerSats:
                    raise RegistreringsFejl("Kunden har ingen afregningsform (abonnement, timeafregning eller intern) "
                                            f"for {dag:%d.%m.%Y} – en administrator skal sætte den på kunden") from None
            if linje.saerskilt and afregning == "intern":
                raise RegistreringsFejl(f"Kunden er intern {dag:%d.%m.%Y} – interne timer faktureres aldrig")
            if linje.saerskilt and afregning == "timeafregning":
                raise RegistreringsFejl(f"Kunden er på timeafregning {dag:%d.%m.%Y} – alle timer faktureres allerede; "
                                        "valget 'uden for abonnementet' gælder kun abonnementskunder")
            session.add(TimeRegistration(week_id=uge.id, employee_id=e.id, work_date=dag, task_type_id=t.id,
                                         task_kind=t.kind, client_id=linje.client_id, description=beskrivelse,
                                         hours=v, billing_model=afregning, billable_separately=linje.saerskilt,
                                         created_by=staff_id))
        else:
            if linje.saerskilt and r.billing_model == "intern":
                raise RegistreringsFejl(f"Kunden er intern {dag:%d.%m.%Y} – interne timer faktureres aldrig")
            if linje.saerskilt and r.billing_model == "timeafregning":
                raise RegistreringsFejl(f"Kunden er på timeafregning {dag:%d.%m.%Y} – alle timer faktureres allerede; "
                                        "valget 'uden for abonnementet' gælder kun abonnementskunder")
            r.task_type_id, r.task_kind, r.description = t.id, t.kind, beskrivelse
            r.billable_separately, r.hours, r.updated_at = linje.saerskilt, v, nu
    session.flush()
    _kontroller_dagstotal(session, uge)
    return linjens_raekker(session, uge, linje)


def _kontroller_dagstotal(session: Session, uge: TimeWeek) -> None:
    for dag, i_alt in session.execute(
            select(TimeRegistration.work_date, func.sum(TimeRegistration.hours))
            .where(TimeRegistration.week_id == uge.id).group_by(TimeRegistration.work_date)):
        if i_alt > 24:
            raise RegistreringsFejl(f"Der er registreret {str(i_alt).replace('.', ',')} timer {dag:%d.%m.%Y} – "
                                    "højst 24 timer på en dag")


def slet_linje(session: Session, e: TimeEmployee, staff_id: int, dag_i_ugen: date, ids: list[int]) -> None:
    saet_aktoer(session, staff_id)
    uge = find_uge(session, e, dag_i_ugen)
    if uge is None:
        raise RegistreringsFejl("Linjen hører ikke til ugen")
    _kraev_aaben(uge)
    for r in _ugens_raekker(session, uge, ids):
        session.delete(r)
    session.flush()


def linjens_raekker(session: Session, uge: TimeWeek, linje: Linje) -> list[TimeRegistration]:
    t = _opgavetype(session, linje.opgavetype)
    beskrivelse = (linje.beskrivelse or "").strip() or None
    return list(session.scalars(select(TimeRegistration).where(
        TimeRegistration.week_id == uge.id, TimeRegistration.task_type_id == t.id,
        TimeRegistration.client_id.is_(None) if linje.client_id is None else TimeRegistration.client_id == linje.client_id,
        TimeRegistration.description.is_(None) if beskrivelse is None else TimeRegistration.description == beskrivelse,
        TimeRegistration.billable_separately.is_(linje.saerskilt))))


def ugens_linjer(session: Session, uge: TimeWeek) -> list[dict]:
    """Ugens rækker samlet til linjer i gitteret (i den rækkefølge, de blev oprettet)."""
    linjer: dict[tuple, dict] = {}
    raekker = session.scalars(select(TimeRegistration).where(TimeRegistration.week_id == uge.id)
                              .order_by(TimeRegistration.id))
    for r in raekker:
        noegle = (r.client_id, r.task_type_id, r.description, r.billable_separately)
        if noegle not in linjer:
            t = session.get(TimeTaskType, r.task_type_id)
            kunde = session.get(Client, r.client_id) if r.client_id else None
            linjer[noegle] = {"opgavetype": t.code, "opgavetype_navn": t.name, "art": t.kind,
                              "kunde_id": r.client_id, "kunde": kunde.navn if kunde else None,
                              "beskrivelse": r.description, "saerskilt": r.billable_separately,
                              "afregning": [], "timer": {}, "ids": [], "korrektion": False}
        linje = linjer[noegle]
        linje["timer"][r.work_date.isoformat()] = str(r.hours)
        linje["ids"].append(r.id)
        if r.billing_model and r.billing_model not in linje["afregning"]:
            linje["afregning"].append(r.billing_model)
        linje["korrektion"] = linje["korrektion"] or r.is_correction
    for linje in linjer.values():
        linje["i_alt"] = str(sum((Decimal(v) for v in linje["timer"].values()), Decimal("0.00")))
    return list(linjer.values())


def dagstotaler(session: Session, uge: TimeWeek | None, dage: list[date]) -> dict:
    """Timer i alt pr. dag og for hele ugen (vises nederst i gitteret)."""
    pr_dag = {d.isoformat(): Decimal("0.00") for d in dage}
    if uge is not None:
        for dag, i_alt in session.execute(
                select(TimeRegistration.work_date, func.sum(TimeRegistration.hours))
                .where(TimeRegistration.week_id == uge.id).group_by(TimeRegistration.work_date)):
            pr_dag[dag.isoformat()] = i_alt
    return {"dage": {d: str(v) for d, v in pr_dag.items()}, "uge": str(sum(pr_dag.values(), Decimal("0.00")))}
