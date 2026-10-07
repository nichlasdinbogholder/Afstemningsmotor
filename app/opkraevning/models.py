"""Opkrævningsmodulet (erstatter FarPay) – tabellerne.

Forretningsmodellen: debitor betaler KUNDENS konto (inkl. rykkergebyr og morarente), kunden
beholder pengene, og Din Bogholder fakturerer kunden månedligt for de indbetalte gebyrer og
renter. Vi modtager aldrig selv en betaling fra en debitor – derfor findes der ingen
betalingsoplysninger i Din Bogholders navn i nogen af tabellerne her.

Feltnavnene i e-conomic er set i rå JSON (scripts/peek_opkraevning.py, Connect El 07.10.2026):
  faktura  /invoices/booked : bookedInvoiceNumber, date, dueDate, grossAmount, remainder,
                              currency, customer.customerNumber, recipient.ean, pdf.download
                              (intet statusfelt – status beregnes af remainder)
  debitor  /customers       : customerNumber, name, email, corporateIdentificationNumber, ean,
                              address, zip, city, country, customerGroup.customerGroupNumber
  indbetaling (entries)     : entryType=customerPayment, entryNumber, date, amount, invoiceNumber

Lovens grænser (renteloven) er låst i databasen – ikke indstillinger:
  rykkergebyr <= 100 kr. pr. skrivelse, højst 3 rykkere pr. faktura, mindst 10 dage mellem
  rykkerne, kompensationsbeløb kun i erhvervsforhold, ingen rykker på kreditnotaer, og ingen
  rykker på en faktura med aktiv inkassosag. Se triggeren `kontroller_rykker` i migreringen.

Alle beløb er NUMERIC(15,2) – aldrig float.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
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

BELOEB = Numeric(15, 2)

KANALER = ("email", "ean", "print", "eboks")
FAKTURA_ARTER = ("invoice", "credit_note")
FAKTURA_STATUSSER = ("open", "paid", "credited", "written_off")
BETALINGSKILDER = ("economic", "kassekladde", "inkasso", "farpay", "manuel")
ALLOKERINGSMAAL = ("fee", "compensation", "interest", "principal")
UDSENDELSE_STATUSSER = ("queued", "sent", "failed", "bounced")
RYKKER_STATUSSER = ("queued", "sent", "cancelled", "failed")
SPAERRE_AARSAGER = (
    "betalt", "krediteret", "afskrevet", "kreditnota", "ikke_forfalden",
    "indbetaling_seneste_2_bankdage", "indbetaling_i_kassekladde", "debitor_blokeret",
    "afbetalingsordning", "under_minimumsbeloeb", "under_10_dage", "max_3_rykkere",
    "aktiv_inkassosag", "fjernet_af_medarbejder",
)
ORDNING_STATUSSER = ("active", "completed", "defaulted", "cancelled")
SAG_STATUSSER = ("requested", "open", "paid", "closed", "withdrawn", "rejected")
SAG_AKTIVE = ("requested", "open", "paid", "closed")  # "withdrawn"/"rejected" spærrer ikke en ny sag
GEBYRTYPER = ("rykkergebyr", "kompensation", "morarente")
AFSKRIVNINGSAARSAGER = ("overdraget_til_inkasso", "eftergivet", "tilbagefoert", "afskrevet")
PERIODE_STATUSSER = ("open", "locked", "draft", "released", "blocked")


def _oprettet() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _kunde_id() -> Mapped[int]:
    return mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True)


class Debtor(Base):
    """Kundens kunde. Persondata – tilhører kundens kunder; ingen eksport uden logning."""

    __tablename__ = "debtors"
    __table_args__ = (
        UniqueConstraint("client_id", "external_id", name="uq_debtors_kunde_ekstern"),
        kun_vaerdier("preferred_channel", KANALER),
        CheckConstraint("cvr IS NULL OR cvr ~ '^[0-9]{8}$'", name="cvr_8_cifre"),
        CheckConstraint("ean IS NULL OR ean ~ '^[0-9]{13}$'", name="ean_13_cifre"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = _kunde_id()
    external_id: Mapped[str] = mapped_column(String(50))        # e-conomic: customerNumber
    name: Mapped[str] = mapped_column(String(255))
    cvr: Mapped[str | None] = mapped_column(String(8))           # corporateIdentificationNumber
    email: Mapped[str | None] = mapped_column(String(255))
    ean: Mapped[str | None] = mapped_column(String(13))
    address: Mapped[str | None] = mapped_column(String(255))
    zip: Mapped[str | None] = mapped_column(String(20))
    city: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(100))
    customer_group: Mapped[int | None] = mapped_column(Integer)  # customerGroup.customerGroupNumber
    preferred_channel: Mapped[str | None] = mapped_column(String(10))
    # Erhverv ud fra kundens debitorgrupper (clients.business_customer_groups). Ukendt = privat,
    # så der aldrig opkræves kompensationsbeløb uden at vi ved, at det er et erhvervsforhold.
    is_business: Mapped[bool] = mapped_column(Boolean, server_default=false())
    blocked_from_dunning: Mapped[bool] = mapped_column(Boolean, server_default=false())
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _oprettet()
    updated_at: Mapped[datetime] = _oprettet()


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("client_id", "external_id", name="uq_invoices_kunde_ekstern"),
        Index("ix_invoices_kunde_status_forfald", "client_id", "status", "due_date"),
        kun_vaerdier("kind", FAKTURA_ARTER),
        kun_vaerdier("status", FAKTURA_STATUSSER),
        # En kreditnota har negativt beløb (e-conomic: grossAmount < 0) – en faktura aldrig.
        CheckConstraint("(kind = 'invoice' AND amount >= 0) OR (kind = 'credit_note' AND amount < 0)",
                        name="art_passer_til_fortegn"),
        CheckConstraint("ean IS NULL OR ean ~ '^[0-9]{13}$'", name="ean_13_cifre"),
        CheckConstraint("prior_dunning_count BETWEEN 0 AND 3", name="tidligere_rykkere_0_3"),
        CheckConstraint("prior_dunning_count = 0 OR prior_last_dunning_at IS NOT NULL",
                        name="tidligere_rykker_har_dato"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = _kunde_id()
    external_id: Mapped[str] = mapped_column(String(50))         # bookedInvoiceNumber
    invoice_no: Mapped[str] = mapped_column(String(50))          # bookedInvoiceNumber (som tekst)
    debtor_id: Mapped[int] = mapped_column(ForeignKey("debtors.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(15), server_default="invoice")
    issue_date: Mapped[date] = mapped_column(Date)               # date
    due_date: Mapped[date] = mapped_column(Date)                 # dueDate
    amount: Mapped[Decimal] = mapped_column(BELOEB)              # grossAmount (inkl. moms)
    amount_outstanding: Mapped[Decimal] = mapped_column(BELOEB)  # remainder
    currency: Mapped[str] = mapped_column(String(3), server_default="DKK")
    ean: Mapped[str | None] = mapped_column(String(13))          # recipient.ean (offentlige modtagere)
    status: Mapped[str] = mapped_column(String(15), server_default="open")
    # Rykkere sendt FØR overgangen fra FarPay (trin 10) – tæller med i "højst 3".
    prior_dunning_count: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    prior_last_dunning_at: Mapped[date | None] = mapped_column(Date)  # seneste rykker i FarPay
    created_at: Mapped[datetime] = _oprettet()
    updated_at: Mapped[datetime] = _oprettet()


class InvoicePayment(Base):
    """En indbetaling på en faktura. Fordeles KUN af allocate_payment (trin 6)."""

    __tablename__ = "invoice_payments"
    __table_args__ = (
        kun_vaerdier("source", BETALINGSKILDER),
        CheckConstraint("amount <> 0", name="beloeb_ikke_nul"),
        # Samme indbetaling (fx e-conomics entryNumber) kan kun registreres én gang pr. faktura.
        UniqueConstraint("invoice_id", "source", "external_id", name="uq_invoice_payments_kilde"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"), index=True)
    payment_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(BELOEB)   # negativt = tilbageført indbetaling
    source: Mapped[str] = mapped_column(String(15))
    external_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = _oprettet()


class DunningStep(Base):
    __tablename__ = "dunning_steps"
    __table_args__ = (
        UniqueConstraint("invoice_id", "step_no", name="uq_dunning_steps_faktura_nr"),
        CheckConstraint("step_no BETWEEN 1 AND 3", name="hoejst_3_rykkere"),
        CheckConstraint("fee_amount >= 0 AND fee_amount <= 100", name="gebyr_hoejst_100"),
        CheckConstraint("interest_amount >= 0", name="rente_ikke_negativ"),
        CheckConstraint("compensation_amount >= 0", name="kompensation_ikke_negativ"),
        kun_vaerdier("status", RYKKER_STATUSSER),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"))
    step_no: Mapped[int] = mapped_column(SmallInteger)
    due_at: Mapped[date] = mapped_column(Date)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fee_amount: Mapped[Decimal] = mapped_column(BELOEB, server_default=text("0"))
    interest_amount: Mapped[Decimal] = mapped_column(BELOEB, server_default=text("0"))
    compensation_amount: Mapped[Decimal] = mapped_column(BELOEB, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(15), server_default="queued")
    document_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _oprettet()


class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"
    __table_args__ = (
        kun_vaerdier("target_type", ALLOKERINGSMAAL),
        CheckConstraint("amount <> 0", name="beloeb_ikke_nul"),
        # Gebyr/kompensation/rente hører altid til en bestemt rykker; hovedstolen gør ikke.
        CheckConstraint("(target_type IN ('fee', 'compensation') AND dunning_step_id IS NOT NULL)"
                        " OR (target_type = 'principal' AND dunning_step_id IS NULL)"
                        " OR target_type = 'interest'", name="rykker_paa_gebyrer"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    payment_id: Mapped[int] = mapped_column(ForeignKey("invoice_payments.id", ondelete="RESTRICT"), index=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"), index=True)
    target_type: Mapped[str] = mapped_column(String(15))
    dunning_step_id: Mapped[int | None] = mapped_column(
        ForeignKey("dunning_steps.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[Decimal] = mapped_column(BELOEB)
    created_at: Mapped[datetime] = _oprettet()


class Delivery(Base):
    """Udsendelse af en faktura eller en rykker. Et bounce bliver 'bounced' – aldrig 'sent'."""

    __tablename__ = "deliveries"
    __table_args__ = (
        kun_vaerdier("channel", KANALER),
        kun_vaerdier("status", UDSENDELSE_STATUSSER),
        CheckConstraint("attempts BETWEEN 0 AND 3", name="hoejst_3_forsoeg"),
        CheckConstraint("status <> 'sent' OR sent_at IS NOT NULL", name="sendt_har_tidspunkt"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"), index=True)
    dunning_step_id: Mapped[int | None] = mapped_column(
        ForeignKey("dunning_steps.id", ondelete="RESTRICT"), index=True)   # tom = selve fakturaen
    channel: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), server_default="queued")
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _oprettet()


class DunningSkip(Base):
    """Hvorfor en rykker IKKE blev sendt – lige så synligt som hvorfor den blev."""

    __tablename__ = "dunning_skips"
    __table_args__ = (kun_vaerdier("reason", SPAERRE_AARSAGER),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"), index=True)
    reason: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict | None] = mapped_column(JSONB)
    checked_at: Mapped[datetime] = _oprettet()


class InstallmentPlan(Base):
    __tablename__ = "installment_plans"
    __table_args__ = (
        kun_vaerdier("status", ORDNING_STATUSSER),
        CheckConstraint("installments > 0", name="mindst_en_rate"),
        CheckConstraint("interval_days > 0", name="interval_positivt"),
        CheckConstraint("total_amount > 0", name="beloeb_positivt"),
        # Højst én aktiv ordning pr. faktura.
        Index("uq_installment_plans_aktiv", "invoice_id", unique=True,
              postgresql_where=text("status = 'active'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("staff.id", ondelete="RESTRICT"), index=True)
    total_amount: Mapped[Decimal] = mapped_column(BELOEB)
    installments: Mapped[int] = mapped_column(SmallInteger)
    first_due_date: Mapped[date] = mapped_column(Date)
    interval_days: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(String(15), server_default="active")
    created_at: Mapped[datetime] = _oprettet()


class InstallmentLine(Base):
    __tablename__ = "installment_lines"
    __table_args__ = (CheckConstraint("amount > 0", name="beloeb_positivt"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("installment_plans.id", ondelete="RESTRICT"), index=True)
    due_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(BELOEB)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _oprettet()


class ReferenceRate(Base):
    """Nationalbankens officielle udlånsrente – gælder fra 1. januar eller 1. juli."""

    __tablename__ = "reference_rates"
    __table_args__ = (
        UniqueConstraint("valid_from", name="uq_reference_rates_fra"),
        CheckConstraint("extract(day FROM valid_from) = 1 AND extract(month FROM valid_from) IN (1, 7)",
                        name="gaelder_fra_1_jan_eller_1_jul"),
        CheckConstraint("rate > -10 AND rate < 50", name="rimelig_sats"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    valid_from: Mapped[date] = mapped_column(Date)
    rate: Mapped[Decimal] = mapped_column(Numeric(6, 4))   # i procent, fx 1.6000
    source: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _oprettet()


class CollectionCase(Base):
    """En fordring overdraget til Inkasso Mægleren. Overdragelsen sker MANUELT (de har ingen API);
    systemet danner listen og registrerer sagen, når en medarbejder har sendt den."""

    __tablename__ = "collection_cases"
    __table_args__ = (
        kun_vaerdier("status", SAG_STATUSSER),
        UniqueConstraint("idempotency_key", name="uq_collection_cases_idempotens"),
        # Aldrig to aktive sager på samme fordring – sidste værn ud over idempotensnøglen.
        Index("uq_collection_cases_aktiv_faktura", "invoice_id", unique=True,
              postgresql_where=text("status NOT IN ('withdrawn', 'rejected')")),
        CheckConstraint("principal > 0", name="hovedstol_positiv"),
        CheckConstraint("status <> 'withdrawn' OR (withdrawn_at IS NOT NULL AND withdraw_reason IS NOT NULL)",
                        name="tilbagekaldt_har_aarsag"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = _kunde_id()
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"), index=True)
    debtor_id: Mapped[int] = mapped_column(ForeignKey("debtors.id", ondelete="RESTRICT"), index=True)
    external_case_id: Mapped[str | None] = mapped_column(String(100))   # sagsnummer hos Inkasso Mægleren
    status: Mapped[str] = mapped_column(String(15), server_default="requested")
    principal: Mapped[Decimal] = mapped_column(BELOEB)
    fees_included: Mapped[Decimal] = mapped_column(BELOEB, server_default=text("0"))
    interest_included: Mapped[Decimal] = mapped_column(BELOEB, server_default=text("0"))
    requested_at: Mapped[datetime] = _oprettet()
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="RESTRICT"), index=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdraw_reason: Mapped[str | None] = mapped_column(Text)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict | None] = mapped_column(JSONB)
    idempotency_key: Mapped[str] = mapped_column(Text)   # sha256(client_id|invoice_id)
    created_at: Mapped[datetime] = _oprettet()
    updated_at: Mapped[datetime] = _oprettet()


class BillingPeriod(Base):
    """Din Bogholders månedsfakturering af én kunde. En låst måned ændres aldrig."""

    __tablename__ = "billing_periods"
    __table_args__ = (
        UniqueConstraint("client_id", "month", name="uq_billing_periods_kunde_maaned"),
        kun_vaerdier("status", PERIODE_STATUSSER),
        CheckConstraint("extract(day FROM month) = 1", name="maaned_er_den_1"),
        CheckConstraint("status <> 'blocked' OR block_reason IS NOT NULL", name="spaerret_har_begrundelse"),
        CheckConstraint("status <> 'released' OR (released_at IS NOT NULL AND released_by IS NOT NULL)",
                        name="frigivet_af_et_menneske"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = _kunde_id()
    month: Mapped[date] = mapped_column(Date)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_by: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="RESTRICT"), index=True)
    total_amount: Mapped[Decimal | None] = mapped_column(BELOEB)
    status: Mapped[str] = mapped_column(String(15), server_default="open")
    block_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _oprettet()


class FeeRevenue(Base):
    """Gebyr/rente pålagt en debitor – og hvor meget der faktisk er indbetalt. Kun indbetalte,
    fuldt dækkede linjer (billable_at sat) kan komme i Din Bogholders fakturagrundlag."""

    __tablename__ = "fee_revenue"
    __table_args__ = (
        kun_vaerdier("fee_type", GEBYRTYPER),
        kun_vaerdier("written_off_reason", AFSKRIVNINGSAARSAGER),
        CheckConstraint("amount_charged >= 0", name="paalagt_ikke_negativ"),
        CheckConstraint("amount_collected >= 0", name="indbetalt_ikke_negativ"),
        CheckConstraint("billing_month IS NULL OR extract(day FROM billing_month) = 1", name="maaned_er_den_1"),
        CheckConstraint("(written_off_at IS NULL) = (written_off_reason IS NULL)", name="afskrivning_har_aarsag"),
        # En linje overdraget til inkasso er afskrevet – den kan aldrig være fakturerbar samtidig.
        CheckConstraint("collection_case_id IS NULL OR written_off_at IS NOT NULL OR billable_at IS NULL",
                        name="inkasso_aldrig_fakturerbar"),
        Index("ix_fee_revenue_kunde_maaned", "client_id", "billing_month"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = _kunde_id()
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"), index=True)
    dunning_step_id: Mapped[int | None] = mapped_column(
        ForeignKey("dunning_steps.id", ondelete="RESTRICT"), index=True)
    fee_type: Mapped[str] = mapped_column(String(15))
    amount_charged: Mapped[Decimal] = mapped_column(BELOEB)
    amount_collected: Mapped[Decimal] = mapped_column(BELOEB, server_default=text("0"))
    accrued_at: Mapped[datetime] = _oprettet()
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    billable_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    billing_month: Mapped[date | None] = mapped_column(Date)
    collection_case_id: Mapped[int | None] = mapped_column(
        ForeignKey("collection_cases.id", ondelete="RESTRICT"), index=True)
    # Linjen på Din Bogholders faktura til kunden – tabellen for fakturalinjer kommer i trin 8.
    invoice_line_ref: Mapped[str | None] = mapped_column(String(100))
    written_off_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    written_off_reason: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = _oprettet()
    updated_at: Mapped[datetime] = _oprettet()
