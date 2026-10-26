"""Timeregistrering (spec 18.10.2026) – tabellerne `time_*`. Kun Din Bogholder (ingen selskabsdimension).

Bygger på vores egne medarbejdere (`staff`) og kunder (`clients`). Reglerne, der ikke må brydes, håndhæves i
DATABASEN (triggere i migreringen 'timeregistrering trin 1'):
- en godkendt uge er låst – rettelser sker som korrektionslinje i en åben uge,
- kun godkendte uger må gå til løn,
- timelønnede kan ikke registrere ferie og optjener ikke afspadsering,
- bemærkninger, afspadseringsbevægelser og revisionssporet kan aldrig ændres eller slettes,
- satser (kostpris, kundevilkår, salgspris) kan ikke slettes, og deres perioder kan ikke overlappe,
- hver ændring logges i time_audit med hvem, hvornår, fra hvad og til hvad.
Beløb og timer er altid NUMERIC – aldrig kommatal. Alle priser er ekskl. moms.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import DATERANGE, JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, kun_vaerdier

ANSAETTELSER = ("fastloennet", "timeloennet")
AFREGNINGER = ("abonnement", "timeafregning")
OPGAVEARTER = ("kunde", "intern", "fravaer")
UGESTATUS = ("kladde", "afleveret", "afvist", "godkendt")
AFSPADSERING = ("optjent", "afholdt", "udbetalt", "korrektion")
BATCHSTATUS = ("kladde", "sendt", "fejl", "bekraeftet")
LOENARTER = ("timeloen", "afspadsering_udbetalt")
HANDLINGER = ("INSERT", "UPDATE", "DELETE")
PAAMINDELSE = ("sendt", "toerloeb", "fejl")

_PERIODE = "daterange(valid_from, valid_to, '[)')"


def _medarbejder(nullable: bool = True):
    return mapped_column(Integer, ForeignKey("staff.id", ondelete="RESTRICT"), nullable=nullable, index=True)


def _ansat():
    return mapped_column(BigInteger, ForeignKey("time_employees.id", ondelete="RESTRICT"), index=True)


# --- Medarbejdere -------------------------------------------------------------


class TimeEmployee(Base):
    """Timeregistreringens oplysninger om en medarbejder (navn og e-mail står på staff – ikke to steder)."""

    __tablename__ = "time_employees"
    __table_args__ = (
        kun_vaerdier("employment", ANSAETTELSER),
        CheckConstraint("NOT (employment = 'timeloennet' AND accrues_toil)", name="timeloennet_uden_afspadsering"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    staff_id: Mapped[int] = mapped_column(Integer, ForeignKey("staff.id", ondelete="RESTRICT"), unique=True)
    employment: Mapped[str] = mapped_column(Text)
    flexjob: Mapped[bool] = mapped_column(Boolean, server_default=false())
    accrues_toil: Mapped[bool] = mapped_column(Boolean, server_default=true())
    payroll_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TimeEmployeeRate(Base):
    """Normtid og kostpris for en periode – indtastes manuelt. productive_hours_year gemmes, så kostprisen kan
    dokumenteres bagud (der beregnes ingenting automatisk). Bygherren 26.10.2026: TIMELØNNEDE har ingen produktive
    timer (NULL – kostprisen tager udgangspunkt i timelønnen); fastlønnede har standard 1.400 timer om året."""

    __tablename__ = "time_employee_rates"
    __table_args__ = (
        ExcludeConstraint(("employee_id", "="), ("period", "&&"), name="time_employee_rates_ingen_overlap",
                          using="gist"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="periode_gyldig"),
        CheckConstraint("weekly_norm IS NULL OR (weekly_norm >= 0 AND weekly_norm <= 168)", name="normtid_gyldig"),
        CheckConstraint("cost_per_hour >= 0", name="kostpris_ikke_negativ"),
        CheckConstraint("productive_hours_year IS NULL OR productive_hours_year > 0", name="produktive_timer_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    employee_id: Mapped[int] = _ansat()
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    weekly_norm: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    cost_per_hour: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    productive_hours_year: Mapped[Decimal | None] = mapped_column(Numeric(7, 2))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[int | None] = _medarbejder()
    period = mapped_column(DATERANGE, Computed(_PERIODE, persisted=True))


# --- Kunder og priser ---------------------------------------------------------


class TimeClientTerms(Base):
    """Kundens afregningsform (og abonnementspris) for en periode."""

    __tablename__ = "time_client_terms"
    __table_args__ = (
        ExcludeConstraint(("client_id", "="), ("period", "&&"), name="time_client_terms_ingen_overlap",
                          using="gist"),
        kun_vaerdier("billing_model", AFREGNINGER),
        CheckConstraint("billing_model <> 'abonnement' OR subscription_price IS NOT NULL",
                        name="abonnement_kraever_pris"),
        CheckConstraint("subscription_price IS NULL OR subscription_price >= 0", name="pris_ikke_negativ"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="periode_gyldig"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = mapped_column(Integer, ForeignKey("clients.id", ondelete="RESTRICT"), index=True)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    billing_model: Mapped[str] = mapped_column(Text)
    subscription_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[int | None] = _medarbejder()
    period = mapped_column(DATERANGE, Computed(_PERIODE, persisted=True))


class TimeSalesRate(Base):
    """Salgspris pr. time på firmaniveau, ekskl. moms."""

    __tablename__ = "time_sales_rates"
    __table_args__ = (
        ExcludeConstraint(("period", "&&"), name="time_sales_rates_ingen_overlap", using="gist"),
        CheckConstraint("rate_per_hour >= 0", name="pris_ikke_negativ"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="periode_gyldig"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    rate_per_hour: Mapped[Decimal] = mapped_column(Numeric(15, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    period = mapped_column(DATERANGE, Computed(_PERIODE, persisted=True))


# --- Opgavetyper --------------------------------------------------------------


class TimeTaskType(Base):
    __tablename__ = "time_task_types"
    __table_args__ = (kun_vaerdier("kind", OPGAVEARTER),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    excluded_from_efficiency: Mapped[bool] = mapped_column(Boolean, server_default=false())
    payable: Mapped[bool] = mapped_column(Boolean, server_default=true())
    reduces_toil: Mapped[bool] = mapped_column(Boolean, server_default=false())
    zero_hours: Mapped[bool] = mapped_column(Boolean, server_default=false())
    blocked_for_hourly: Mapped[bool] = mapped_column(Boolean, server_default=false())
    # Kun timelønnede kan vælge den (fx "Ikke på arbejde") – databasen afviser den for fastlønnede.
    only_hourly: Mapped[bool] = mapped_column(Boolean, server_default=false())
    # En "død" type (fx "Ikke på arbejde"): tæller ALDRIG med i rapporter og statistikker.
    excluded_from_reports: Mapped[bool] = mapped_column(Boolean, server_default=false())
    sort_order: Mapped[int] = mapped_column(Integer, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())


# --- Uger og tidslinjer -------------------------------------------------------


class TimeWeek(Base):
    __tablename__ = "time_weeks"
    __table_args__ = (
        UniqueConstraint("employee_id", "iso_year", "iso_week", name="uq_time_weeks_medarbejder_uge"),
        kun_vaerdier("status", UGESTATUS),
        CheckConstraint("iso_week BETWEEN 1 AND 53", name="ugenummer_gyldigt"),
        CheckConstraint("extract(isodow FROM week_start) = 1", name="uge_starter_mandag"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("time_employees.id", ondelete="RESTRICT"))
    iso_year: Mapped[int] = mapped_column(Integer)
    iso_week: Mapped[int] = mapped_column(Integer)
    week_start: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, server_default="kladde")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_by: Mapped[int | None] = _medarbejder()
    rejection_note: Mapped[str | None] = mapped_column(Text)
    resubmit_due: Mapped[date | None] = mapped_column(Date)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[int | None] = _medarbejder()
    self_approved: Mapped[bool] = mapped_column(Boolean, server_default=false())


class TimeRegistration(Base):
    """Én linje: timer på én dag. `task_kind` sættes af databasen ud fra opgavetypen. `billing_model` er et
    øjebliksbillede af kundens afregningsform ved indtastning og ændres aldrig siden.
    Pipeline-emner (til opgavetypen 'salg') kommer, når pipelinen bygges – indtil da er 'salg' ikke aktiv."""

    __tablename__ = "time_entries"
    __table_args__ = (
        kun_vaerdier("task_kind", OPGAVEARTER),
        kun_vaerdier("billing_model", AFREGNINGER),
        CheckConstraint("hours >= 0 AND hours <= 24", name="timer_0_til_24"),
        CheckConstraint("hours = round(hours * 4) / 4", name="kvarte_timer"),
        CheckConstraint("(task_kind = 'kunde' AND client_id IS NOT NULL) OR (task_kind <> 'kunde' AND client_id IS NULL)",
                        name="kunde_kun_paa_kundetid"),
        CheckConstraint("NOT billable_separately OR (description IS NOT NULL AND btrim(description) <> '')",
                        name="saerskilt_kraever_beskrivelse"),
        CheckConstraint("corrects_entry_id IS NULL OR is_correction", name="korrektion_markeret"),
        Index("ix_time_entries_medarbejder_dato", "employee_id", "work_date"),
        Index("ix_time_entries_kunde_dato", "client_id", "work_date"),
        Index("ix_time_entries_ikke_faktureret", "billable_separately",
              postgresql_where=text("billable_separately AND invoiced_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    week_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("time_weeks.id", ondelete="RESTRICT"), index=True)
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("time_employees.id", ondelete="RESTRICT"))
    work_date: Mapped[date] = mapped_column(Date)
    task_type_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("time_task_types.id", ondelete="RESTRICT"),
                                              index=True)
    task_kind: Mapped[str] = mapped_column(Text)
    client_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("clients.id", ondelete="RESTRICT"))
    description: Mapped[str | None] = mapped_column(Text)
    hours: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    billing_model: Mapped[str | None] = mapped_column(Text)
    billable_separately: Mapped[bool] = mapped_column(Boolean, server_default=false())
    invoice_ref: Mapped[str | None] = mapped_column(Text)
    invoiced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_correction: Mapped[bool] = mapped_column(Boolean, server_default=false())
    corrects_entry_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("time_entries.id",
                                                                                ondelete="RESTRICT"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[int] = _medarbejder(nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TimeEntryNote(Base):
    """Bemærkning ved afvisning (på linjeniveau). Kan aldrig ændres eller slettes."""

    __tablename__ = "time_entry_notes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entry_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("time_entries.id", ondelete="RESTRICT"), index=True)
    week_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("time_weeks.id", ondelete="RESTRICT"), index=True)
    author_id: Mapped[int] = _medarbejder(nullable=False)
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- Afspadsering -------------------------------------------------------------


class TimeToilSettlement(Base):
    __tablename__ = "time_toil_settlements"
    __table_args__ = (UniqueConstraint("employee_id", "year", name="uq_time_toil_settlements_medarbejder_aar"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("time_employees.id", ondelete="RESTRICT"))
    year: Mapped[int] = mapped_column(Integer)
    balance_hours: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    payout_hours: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    settled_by: Mapped[int] = _medarbejder(nullable=False)


class TimeToilMovement(Base):
    """Kun tilføjelser. Saldo = SUM(hours). + optjent, − afholdt/udbetalt."""

    __tablename__ = "time_toil_movements"
    __table_args__ = (kun_vaerdier("kind", AFSPADSERING),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    employee_id: Mapped[int] = _ansat()
    movement_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(Text)
    hours: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    week_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("time_weeks.id", ondelete="RESTRICT"),
                                                index=True)
    settlement_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("time_toil_settlements.id",
                                                                            ondelete="RESTRICT"), index=True)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- Løn ----------------------------------------------------------------------


class TimePayrollBatch(Base):
    __tablename__ = "time_payroll_batches"
    __table_args__ = (kun_vaerdier("status", BATCHSTATUS),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(Text, server_default="kladde")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_ref: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TimePayrollLine(Base):
    __tablename__ = "time_payroll_lines"
    __table_args__ = (kun_vaerdier("kind", LOENARTER),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("time_payroll_batches.id", ondelete="RESTRICT"),
                                          index=True)
    employee_id: Mapped[int] = _ansat()
    week_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("time_weeks.id", ondelete="RESTRICT"),
                                                index=True)
    settlement_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("time_toil_settlements.id",
                                                                            ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(Text)
    hours: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- Påmindelser ----------------------------------------------------------------


class TimeReminder(Base):
    """Én påmindelse pr. medarbejder pr. dag (kl. 16.05), når dagen ikke er udfyldt. Unik, så ingen får den to gange.
    'toerloeb' = afsendelse ikke slået til endnu – kun registreret. Fejlbeskeden indeholder aldrig nøgler."""

    __tablename__ = "time_reminders"
    __table_args__ = (
        UniqueConstraint("employee_id", "work_date", name="uq_time_reminders_medarbejder_dag"),
        kun_vaerdier("status", PAAMINDELSE),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("time_employees.id", ondelete="RESTRICT"))
    work_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# --- Revisionsspor ------------------------------------------------------------


class TimeAudit(Base):
    """Skrives af databasen selv (trigger) ved hver ændring. Kan aldrig ændres eller slettes.
    Aktøren sættes pr. transaktion med `saet_aktoer()` (app/timeregistrering/aktoer.py)."""

    __tablename__ = "time_audit"
    __table_args__ = (kun_vaerdier("action", HANDLINGER),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    table_name: Mapped[str] = mapped_column(Text)
    row_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[int | None] = _medarbejder()
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
