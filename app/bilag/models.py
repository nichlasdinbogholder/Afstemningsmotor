"""Bilagsindbakken – tabeller (prefix inbox_ samt supplier_account_map).

Tre indgange, én pipeline: kundens videresendte fakturamail (kunde-<id>@<INBOX_DOMAIN>, én delt postkasse)
og upload fra appen (`source='upload'`, `message_id` NULL) lander i inbox_documents og går gennem samme
klassificering, dublettjek, godkendelsesgate og levering.

Rutning: rykker → Corpay (uden godkendelse); kvittering → bilag i kundens kassekladde (ALDRIG bogført);
faktura/kreditnota/kontoudtog → kundens godkendelse → Corpay.

Databasens regler (kan ikke omgås af koden):
- `inbox_require_approval`: en levering til Corpay af faktura/kreditnota/kontoudtog kræver en godkendelse
  ('approved') af præcis den fil (samme sha256), som er leveret. Kun rykker er undtaget.
- `inbox_approvals` er uforanderlig (ingen UPDATE/DELETE) og har højst én afgørelse pr. dokument.
- Levering og kontering er idempotente (unik idempotency_key, beregnet med `leveringsnoegle()`).
- Samme fil til samme kunde findes kun én gang (delvist unikt indeks).
- Beløb er NUMERIC(15,2). E-mailadresser gemmes med små bogstaver (CHECK, som staff.email).
"""

import hashlib
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
    String,
    Text,
    false,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, kun_vaerdier

BELOEB = Numeric(15, 2)

DESTINATIONSTYPER = ("corpay", "economic_journal")
BESKED_STATUSSER = ("received", "classified", "done", "review", "loop", "failed")
# Hvorfor en besked (eller en del af den) ikke blev til et bilag. Faste koder – ikke fri tekst.
IGNORERINGSAARSAGER = ("loop", "ukendt_modtager", "link_only", "ingen_bilag", "ukendt_filtype", "for_stor")
KILDER = ("email", "upload")
DOKUMENTTYPER = ("faktura", "kreditnota", "kontoudtog", "rykker", "kvittering", "andet")
KRAEVER_GODKENDELSE = ("faktura", "kreditnota", "kontoudtog")
KATEGORIER = ("braendstof", "materialer", "restaurant", "hotel", "bar", "kontorhold", "transport", "andet")
KRAEVER_NOTE = ("restaurant", "hotel", "bar")
FORFALD_KILDER = ("parsed", "estimated")
KLASSIFICERINGER = ("rules", "llm", "human")
DOKUMENT_STATUSSER = ("extracting", "pending_approval", "approved", "rejected", "auto_route", "delivered",
                      "review", "duplicate", "failed")
AFGOERELSER = ("approved", "rejected")
AFGOERELSE_OPRINDELSE = ("app", "upload")
AFVISNINGSGRUNDE = ("ikke_vores", "forkert_beloeb", "ikke_modtaget", "dublet", "andet")
LEVERING_STATUSSER = ("pending", "sent", "failed")
MOMSKODE_KILDER = ("mapped", "guessed")
KONTERING_STATUSSER = ("pending", "created", "failed")
KONTOREGEL_KILDER = ("manual", "learned")
ALARMTILSTANDE = ("ok", "warning", "alarm")

_SMAA_BOGSTAVER = "{kolonne} = lower({kolonne})"
_SHA256 = "{kolonne} ~ '^[0-9a-f]{{64}}$'"


def _oprettet() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def _kunde(nullable: bool = False, index: bool = True):
    return mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), nullable=nullable, index=index)


def leveringsnoegle(document_id: int, destination_id: int | str) -> str:
    """Deterministisk nøgle: et gentaget job rammer den unikke regel i stedet for at sende/kontere igen."""
    return hashlib.sha256(f"{document_id}:{destination_id}".encode()).hexdigest()


class InboxAddress(Base):
    """Ét alias pr. kunde. Adressen er routingnøglen."""

    __tablename__ = "inbox_addresses"
    __table_args__ = (CheckConstraint(_SMAA_BOGSTAVER.format(kolonne="address"), name="adresse_smaa_bogstaver"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = _kunde()
    address: Mapped[str] = mapped_column(String(320), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    created_at: Mapped[datetime] = _oprettet()


class InboxDestination(Base):
    """Corpay-mail (betaling) eller kundens kassekladde i e-conomic (kvitteringer)."""

    __tablename__ = "inbox_destinations"
    __table_args__ = (
        kun_vaerdier("kind", DESTINATIONSTYPER),
        CheckConstraint("(kind = 'corpay' AND address IS NOT NULL) OR "
                        "(kind = 'economic_journal' AND journal_id IS NOT NULL)", name="adresse_eller_kladde"),
        CheckConstraint("address IS NULL OR " + _SMAA_BOGSTAVER.format(kolonne="address"),
                        name="adresse_smaa_bogstaver"),
        # Højst én aktiv destination af hver slags pr. kunde – ellers ville et bilag gå to steder hen.
        Index("uq_inbox_destinations_aktiv_type", "client_id", "kind", unique=True,
              postgresql_where=text("active")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = _kunde()
    kind: Mapped[str] = mapped_column(String(20))
    address: Mapped[str | None] = mapped_column(String(320))
    journal_id: Mapped[str | None] = mapped_column(String(50))
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    created_at: Mapped[datetime] = _oprettet()


class InboxMessage(Base):
    """Én række pr. modtaget mail. client_id NULL = ukendt alias (status 'review', aldrig kasseret)."""

    __tablename__ = "inbox_messages"
    __table_args__ = (
        kun_vaerdier("status", BESKED_STATUSSER),
        kun_vaerdier("ignore_reason", IGNORERINGSAARSAGER),
        CheckConstraint("attachment_count >= 0", name="vedhaeftninger_ikke_negativ"),
        Index("ix_inbox_messages_kunde_modtaget", "client_id", text("received_at DESC")),
        Index("ix_inbox_messages_kraever_handling", "status",
              postgresql_where=text("status IN ('review', 'failed')")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int | None] = _kunde(nullable=True, index=False)
    graph_message_id: Mapped[str] = mapped_column(Text, unique=True)
    internet_message_id: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    from_address: Mapped[str | None] = mapped_column(String(320))
    to_address: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str | None] = mapped_column(Text)
    body_preview: Mapped[str | None] = mapped_column(Text)
    raw_object_key: Mapped[str] = mapped_column(Text)
    attachment_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(12))
    ignore_reason: Mapped[str | None] = mapped_column(String(20))
    # Vedhæftninger, der blev sorteret fra (signaturlogo, .ics …) – logges her, så intet forsvinder sporløst.
    ignored_attachments: Mapped[list | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _oprettet()
    updated_at: Mapped[datetime] = _oprettet()


class InboxDocument(Base):
    """Ét bilag – fra mail (message_id sat) eller upload (message_id NULL). Samme pipeline."""

    __tablename__ = "inbox_documents"
    __table_args__ = (
        kun_vaerdier("source", KILDER),
        kun_vaerdier("doc_type", DOKUMENTTYPER),
        kun_vaerdier("category", KATEGORIER),
        kun_vaerdier("due_date_source", FORFALD_KILDER),
        kun_vaerdier("classifier", KLASSIFICERINGER),
        kun_vaerdier("status", DOKUMENT_STATUSSER),
        CheckConstraint("(source = 'email') = (message_id IS NOT NULL)", name="mail_har_besked"),
        CheckConstraint("source <> 'upload' OR uploaded_by IS NOT NULL", name="upload_har_bruger"),
        CheckConstraint("size_bytes >= 0", name="stoerrelse_ikke_negativ"),
        CheckConstraint(_SHA256.format(kolonne="sha256"), name="sha256_hex"),
        CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="sikkerhed_0_1"),
        CheckConstraint("currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="valuta_3_bogstaver"),
        CheckConstraint("(due_date IS NULL) = (due_date_source IS NULL)", name="forfald_har_kilde"),
        CheckConstraint("(status = 'duplicate') = (duplicate_of IS NOT NULL)", name="dublet_peger_paa_original"),
        CheckConstraint("classifier <> 'human' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
                        name="menneske_er_navngivet"),
        # Dublettjek: samme fil til samme kunde findes kun én gang (dubletterne selv undtaget).
        Index("uq_inbox_documents_dedup", "client_id", "sha256", unique=True,
              postgresql_where=text("status <> 'duplicate'")),
        Index("ix_inbox_documents_kunde_status_forfald", "client_id", "status", "due_date"),
        Index("ix_inbox_documents_bloedt_match", "client_id", "supplier", "amount", "doc_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("inbox_messages.id", ondelete="RESTRICT"),
                                                   index=True)
    client_id: Mapped[int] = _kunde(index=False)       # indekseret via de sammensatte indeks
    source: Mapped[str] = mapped_column(String(6))
    uploaded_by: Mapped[str | None] = mapped_column(String(255))   # kundens bruger i appen
    filename: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(Text)

    doc_type: Mapped[str | None] = mapped_column(String(12))
    supplier: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(12))
    doc_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    due_date_source: Mapped[str | None] = mapped_column(String(10))
    amount: Mapped[Decimal | None] = mapped_column(BELOEB)
    vat_amount: Mapped[Decimal | None] = mapped_column(BELOEB)
    currency: Mapped[str | None] = mapped_column(String(3))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    classifier: Mapped[str | None] = mapped_column(String(5))
    reason: Mapped[str | None] = mapped_column(Text)   # klassificeringens begrundelse – vises i back office

    # Kundens egen dokumentation (restaurationsbilag: deltagere og anledning).
    note: Mapped[str | None] = mapped_column(Text)
    participants: Mapped[str | None] = mapped_column(Text)
    occasion: Mapped[str | None] = mapped_column(Text)
    note_required: Mapped[bool] = mapped_column(Boolean, server_default=false())
    note_complete: Mapped[bool] = mapped_column(Boolean, Computed(
        "NOT note_required OR (participants IS NOT NULL AND occasion IS NOT NULL "
        "AND length(trim(participants)) > 0 AND length(trim(occasion)) > 0)", persisted=True))

    status: Mapped[str] = mapped_column(String(16))
    duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("inbox_documents.id", ondelete="RESTRICT"),
                                                     index=True)
    # Afgjort af en medarbejder i back office (review-køen).
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="RESTRICT"), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _oprettet()


class InboxApproval(Base):
    """Kundens afgørelse. Uforanderlig (trigger) og højst én pr. dokument."""

    __tablename__ = "inbox_approvals"
    __table_args__ = (
        kun_vaerdier("decision", AFGOERELSER),
        kun_vaerdier("origin", AFGOERELSE_OPRINDELSE),
        kun_vaerdier("reason_code", AFVISNINGSGRUNDE),
        CheckConstraint(_SHA256.format(kolonne="document_sha256"), name="sha256_hex"),
        CheckConstraint("decision <> 'rejected' OR reason_code IS NOT NULL", name="afvisning_har_grund"),
        CheckConstraint("length(trim(decided_by)) > 0", name="afgjort_af_er_udfyldt"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("inbox_documents.id", ondelete="RESTRICT"), unique=True)
    client_id: Mapped[int] = _kunde()
    decision: Mapped[str] = mapped_column(String(8))
    origin: Mapped[str] = mapped_column(String(6))
    reason_code: Mapped[str | None] = mapped_column(String(15))
    reason_text: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(String(255))
    decided_at: Mapped[datetime] = _oprettet()
    device: Mapped[str | None] = mapped_column(String(255))
    document_sha256: Mapped[str] = mapped_column(String(64))   # præcis den fil, brugeren så


class InboxDelivery(Base):
    """Levering til Corpay. Indsættes FØR afsendelse; triggeren kræver godkendelse."""

    __tablename__ = "inbox_deliveries"
    __table_args__ = (
        kun_vaerdier("status", LEVERING_STATUSSER),
        CheckConstraint("attempts >= 0", name="forsoeg_ikke_negativ"),
        CheckConstraint("status <> 'sent' OR sent_at IS NOT NULL", name="sendt_har_tidspunkt"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("inbox_documents.id", ondelete="RESTRICT"), index=True)
    destination_id: Mapped[int] = mapped_column(ForeignKey("inbox_destinations.id", ondelete="RESTRICT"),
                                                index=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(8))
    attempts: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)   # kun redigeret tekst – aldrig tokens
    created_at: Mapped[datetime] = _oprettet()


class InboxPosting(Base):
    """Kvittering → bilag i kundens kassekladde. Skrives FØR kaldet (idempotent). Kladden bogføres ALDRIG."""

    __tablename__ = "inbox_postings"
    __table_args__ = (
        kun_vaerdier("vat_code_source", MOMSKODE_KILDER),
        kun_vaerdier("status", KONTERING_STATUSSER),
        CheckConstraint("status <> 'created' OR external_voucher_id IS NOT NULL", name="oprettet_har_bilag"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("inbox_documents.id", ondelete="RESTRICT"), unique=True)
    client_id: Mapped[int] = _kunde()
    journal_id: Mapped[str] = mapped_column(String(50))
    account: Mapped[str | None] = mapped_column(String(20))
    vat_code: Mapped[str | None] = mapped_column(String(20))
    vat_code_source: Mapped[str | None] = mapped_column(String(7))
    voucher_text: Mapped[str] = mapped_column(Text)   # indeholder noten – den skal med til revisionen
    amount: Mapped[Decimal] = mapped_column(BELOEB)
    posting_date: Mapped[date] = mapped_column(Date)
    supplier: Mapped[str | None] = mapped_column(Text)
    external_voucher_id: Mapped[str | None] = mapped_column(String(50))
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(7))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _oprettet()


class SupplierAccountMap(Base):
    """Leverandør → konto og momskode. client_id NULL = fællesregel for alle kunder."""

    __tablename__ = "supplier_account_map"
    __table_args__ = (
        kun_vaerdier("source", KONTOREGEL_KILDER),
        kun_vaerdier("category", KATEGORIER),
        CheckConstraint("hits >= 0", name="traef_ikke_negativ"),
        Index("uq_supplier_account_map_kunde_moenster", text("COALESCE(client_id, 0)"), "supplier_pattern",
              unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int | None] = _kunde(nullable=True)
    supplier_pattern: Mapped[str] = mapped_column(Text)
    account: Mapped[str] = mapped_column(String(20))
    vat_code: Mapped[str] = mapped_column(String(20))
    category: Mapped[str | None] = mapped_column(String(12))
    requires_note: Mapped[bool] = mapped_column(Boolean, server_default=false())
    source: Mapped[str] = mapped_column(String(7))
    hits: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    updated_at: Mapped[datetime] = _oprettet()


class InboxHealth(Base):
    """Hjerteslag pr. kunde – opdager, når kundens videresendelse stopper (tæller kun mail)."""

    __tablename__ = "inbox_health"
    __table_args__ = (
        kun_vaerdier("alert_state", ALARMTILSTANDE),
        CheckConstraint("rolling_30d_count >= 0", name="antal_ikke_negativ"),
    )

    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), primary_key=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rolling_30d_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    baseline_monthly: Mapped[int | None] = mapped_column(Integer)
    alert_state: Mapped[str] = mapped_column(String(7), server_default="ok")
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
