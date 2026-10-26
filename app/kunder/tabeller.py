"""Kundeoversigten (spec 26.10.2026): de nye tabeller med prefix kunde_. Alle peger på kundens id (clients.id) –
aldrig på et kundenavn som tekst.

Bygger oven på det, der findes (beslutninger 26.10.2026):
- clients (kundekartoteket) er udvidet – ingen ny kundetabel. Livscyklus = clients.status (emne/aktiv/pause/opsagt).
- contacts (kontaktpersoner) og credentials (tokens) er udvidet – ingen kunde_contacts/kunde_credentials.
- Afregningsform og pris ligger i time_client_terms (med perioder); kunde_agreements har resten af aftalen.
- Noter og dokumenter ligger i notes/documents (app/crm).
- Salgsfakturaer (Din Bogholders fakturaer TIL kunden) kommer i trin 10.

Databasen håndhæver (triggere i migreringen): kunde_log kan kun tilføjes til; kunder kan ikke slettes, og en
ophørt kunde kan kun genåbnes bevidst; skift af ansvarlig skrives i kunde_owner_history; kun en administrator
kan sætte et token, og et gemt token kan ikke ændres.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, kun_vaerdier

ALIAS_KILDER = ("cvr", "manuel")
CVR_ALVOR = ("info", "advarsel", "kritisk")
LEDGER_STATUSSER = ("ikke_tilsluttet", "aktiv", "afvist", "fejlet")
FULDMAGT_TYPER = ("tastselv", "eindkomst", "bank_psd2", "nemkonto", "andet")
FULDMAGT_STATUSSER = ("aktiv", "udloebet", "tilbagekaldt")
# Moms: Skats perioder (bygherren 26.10.2026 – ingen årlig moms hos Skat). 'ingen' = ikke tilmeldt.
MOMSPERIODER = ("maaned", "kvartal", "halvaar", "ingen")
# Lønsumsafgift: Skats metode 1-4 (bygherren 26.10.2026). Perioder og frister pr. metode står i Skats fristkalender.
LOENSUM_METODER = ("metode_1", "metode_2", "metode_3", "metode_4", "ingen")
LOEN_FREKVENSER = ("maaned", "14_dage", "ingen")
RISIKONIVEAUER = ("lav", "mellem", "hoej")
TJEKLISTER = ("onboarding", "offboarding")


def _kunde(**kw):
    return mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True, **kw)


def _medarbejder(nullable: bool = True):
    return mapped_column(ForeignKey("staff.id", ondelete="RESTRICT"), index=True, nullable=nullable)


def _oprettet():
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class KundeGroup(Base):
    """Kundegruppe: holding, driftsselskab og personlig virksomhed, der hænger sammen (clients.group_id)."""

    __tablename__ = "kunde_groups"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _oprettet()


class KundeAlias(Base):
    """Tidligere navne, så søgningen finder dem (fra CVR eller lagt ind af en medarbejder)."""

    __tablename__ = "kunde_aliases"
    __table_args__ = (kun_vaerdier("source", ALIAS_KILDER), UniqueConstraint("client_id", "alias"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = _kunde()
    alias: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _oprettet()


class KundeOwnerHistory(Base):
    """Hvem der var ansvarlig for kunden hvornår. Skrives KUN af triggeren kunde_lifecycle_guard, når
    clients.ansvarlig_medarbejder_id ændres. Perioderne kan ikke overlappe (EXCLUDE i migreringen); valid_to er
    eksklusiv (dagen, den næste overtog). changed_by er tom, når ændringen ikke kom fra en medarbejder i hub'en."""

    __tablename__ = "kunde_owner_history"
    __table_args__ = (CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="periode_gyldig"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = _kunde()
    owner_staff_id: Mapped[int] = _medarbejder(nullable=False)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    changed_by: Mapped[int | None] = _medarbejder()
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _oprettet()


class KundeCvrSnapshot(Base):
    """Seneste stamdata fra CVR-registret (trin 2). CVR er kilden – afvigelser skrives i kunde_cvr_changes."""

    __tablename__ = "kunde_cvr_snapshot"

    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    company_form: Mapped[str | None] = mapped_column(Text)
    industry_code: Mapped[str | None] = mapped_column(Text)
    fiscal_year_end: Mapped[str | None] = mapped_column(String(5))  # 'dd-mm', som kunde_obligations
    status: Mapped[str | None] = mapped_column(Text)  # CVR's egen tekst: normal, under konkurs ...
    beneficial_owners: Mapped[list | None] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = _oprettet()


class KundeCvrChange(Base):
    __tablename__ = "kunde_cvr_changes"
    __table_args__ = (kun_vaerdier("severity", CVR_ALVOR),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = _kunde()
    field: Mapped[str] = mapped_column(Text)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text)
    detected_at: Mapped[datetime] = _oprettet()
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handled_by: Mapped[int | None] = _medarbejder()


class KundeAgreement(Base):
    """Aftalen med kunden. Afregningsform og pris ligger IKKE her, men i time_client_terms (med gyldighedsperioder),
    så der kun er ét sted – timeregistreringen bruger dem."""

    __tablename__ = "kunde_agreements"
    __table_args__ = (
        CheckConstraint("notice_months IS NULL OR notice_months >= 0", name="varsel_ikke_negativt"),
        CheckConstraint("ended_on IS NULL OR ended_on >= started_on", name="slut_efter_start"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = _kunde()
    package: Mapped[str | None] = mapped_column(Text)
    notice_months: Mapped[int | None] = mapped_column(Integer)
    last_price_change: Mapped[date | None] = mapped_column(Date)
    started_on: Mapped[date] = mapped_column(Date)
    ended_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = _oprettet()


class KundeAftaleDokument(Base):
    """Det underskrevne aftalegrundlag med kunden som PDF (bygherren 26.10.2026). Gemmes i databasen (kommer med i
    de krypterede sikkerhedskopier). Slettes aldrig: en ny version arkiverer den gamle, så historikken bevares.
    Højst én gældende (ikke arkiveret) pr. kunde."""

    __tablename__ = "kunde_aftaledokumenter"
    __table_args__ = (
        Index("uq_kunde_aftaledokumenter_en_gaeldende", "client_id", unique=True,
              postgresql_where=text("arkiveret IS NULL")),
        CheckConstraint("substring(indhold from 1 for 5) = '\\x255044462d'::bytea", name="kun_pdf"),
        CheckConstraint("stoerrelse > 0 AND stoerrelse = octet_length(indhold)", name="stoerrelse_passer"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = _kunde()
    filnavn: Mapped[str] = mapped_column(String(255))
    indhold: Mapped[bytes] = mapped_column(LargeBinary)
    stoerrelse: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    uploadet_af: Mapped[int] = _medarbejder(nullable=False)
    uploadet: Mapped[datetime] = _oprettet()
    arkiveret: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KundeLedger(Base):
    """En regnskabsaftale. Én kunde kan have flere (fx to selskaber). Tokenet ligger i credentials.ledger_id.
    last_error indeholder kun fejltypen – aldrig tokenet eller systemets svar."""

    __tablename__ = "kunde_ledgers"
    __table_args__ = (
        kun_vaerdier("system", ("economic", "dinero")),
        kun_vaerdier("status", LEDGER_STATUSSER),
        CheckConstraint("agreement_cvr IS NULL OR agreement_cvr ~ '^[0-9]{8}$'", name="aftale_cvr_8_cifre"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = _kunde()
    system: Mapped[str] = mapped_column(Text)
    label: Mapped[str] = mapped_column(Text)
    agreement_cvr: Mapped[str | None] = mapped_column(Text)
    agreement_name: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="ikke_tilsluttet")
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _oprettet()
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KundeAuthority(Base):
    """Fuldmagter og samtykker med udløb (trin 9). PSD2-samtykker oprettes som 'bank_psd2'."""

    __tablename__ = "kunde_authorities"
    __table_args__ = (kun_vaerdier("kind", FULDMAGT_TYPER), kun_vaerdier("status", FULDMAGT_STATUSSER))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = _kunde()
    kind: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    granted_on: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, server_default="aktiv")
    created_at: Mapped[datetime] = _oprettet()


class KundeObligations(Base):
    """Kundens parametre for frister – datoerne BEREGNES (trin 11), de tastes aldrig. Momsperiode, regnskabsårets
    slutning og lønkørselsdag er flyttet hertil fra clients, så de kun findes ét sted. Fanen Frister (bygherren
    26.10.2026): moms indberettes månedligt, kvartalsvis eller halvårligt; lønsumsafgift efter Skats metode 1-4 – eller
    kunden er ikke tilmeldt. NULL = ikke valgt endnu. Fristdatoerne slås op i Skats fristkalender (app/frister)."""

    __tablename__ = "kunde_obligations"
    __table_args__ = (
        kun_vaerdier("vat_period", MOMSPERIODER),
        kun_vaerdier("payroll_frequency", LOEN_FREKVENSER),
        kun_vaerdier("loensumsafgift_metode", LOENSUM_METODER),
        # Regnskabsårets slutning som dag-måned, fx '31-12' eller '30-06' (gentager sig hvert år).
        CheckConstraint("fiscal_year_end ~ '^(0[1-9]|[12][0-9]|3[01])-(0[1-9]|1[0-2])$'",
                        name="regnskabsaar_slut_dd_mm"),
        CheckConstraint("payroll_day BETWEEN 1 AND 31", name="loenkoersel_dag_1_31"),
    )

    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), primary_key=True)
    vat_period: Mapped[str | None] = mapped_column(Text)
    fiscal_year_end: Mapped[str | None] = mapped_column(String(5))
    payroll_frequency: Mapped[str | None] = mapped_column(Text)
    payroll_day: Mapped[int | None] = mapped_column(SmallInteger)
    has_a_skat: Mapped[bool] = mapped_column(Boolean, server_default=false())
    loensumsafgift_metode: Mapped[str | None] = mapped_column(Text)


class KundeTaskType(Base):
    """Firmaets månedsrytme på hverdagsnummer (hverdag 1 = første bankdag i måneden). Seedes i trin 11."""

    __tablename__ = "kunde_task_types"
    __table_args__ = (CheckConstraint("workday_number BETWEEN 1 AND 23", name="hverdag_1_23"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    workday_number: Mapped[int] = mapped_column(Integer)
    applies_when: Mapped[str | None] = mapped_column(Text)  # fx 'has_a_skat' – NULL = alle kunder


class KundeTask(Base):
    """Én opgave pr. kunde pr. opgavetype pr. måned. Genkørsel kan aldrig give dubletter (unik nøgle)."""

    __tablename__ = "kunde_tasks"
    __table_args__ = (
        UniqueConstraint("client_id", "task_type_id", "period"),
        CheckConstraint("extract(day FROM period) = 1", name="periode_er_den_foerste"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = _kunde()
    task_type_id: Mapped[int] = mapped_column(ForeignKey("kunde_task_types.id", ondelete="RESTRICT"), index=True)
    period: Mapped[date] = mapped_column(Date)
    due_date: Mapped[date] = mapped_column(Date)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[int | None] = _medarbejder()


class KundeAml(Base):
    """Hvidvask: status og datoer – ikke et vedhæftet dokument (trin 15)."""

    __tablename__ = "kunde_aml"
    __table_args__ = (kun_vaerdier("risk_level", RISIKONIVEAUER),)

    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), primary_key=True)
    risk_level: Mapped[str | None] = mapped_column(Text)
    assessed_on: Mapped[date | None] = mapped_column(Date)
    assessed_by: Mapped[int | None] = _medarbejder()
    id_verified_on: Mapped[date | None] = mapped_column(Date)
    beneficial_owners_checked_on: Mapped[date | None] = mapped_column(Date)
    next_review_on: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)


class KundeChecklist(Base):
    """Onboarding- og offboarding-tjeklister (trin 16-17)."""

    __tablename__ = "kunde_checklists"
    __table_args__ = (kun_vaerdier("kind", TJEKLISTER), UniqueConstraint("client_id", "kind", "item_code"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = _kunde()
    kind: Mapped[str] = mapped_column(Text)
    item_code: Mapped[str] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[int | None] = _medarbejder()


class KundeLog(Base):
    """Kundens log. Kan KUN tilføjes til – triggeren kunde_log_immutable afviser UPDATE og DELETE betingelsesløst.
    actor_staff_id tom = systemet. Må aldrig indeholde tokens eller andre hemmeligheder."""

    __tablename__ = "kunde_log"
    __table_args__ = (Index("ix_kunde_log_kunde_tid", "client_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = _kunde()
    event: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    actor_staff_id: Mapped[int | None] = _medarbejder()
    occurred_at: Mapped[datetime] = _oprettet()
