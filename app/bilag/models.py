"""Bilagsindbakken – tabeller (prefix inbox_).

Flowet: kundens egen videresendelsesregel → kunde-<id>@<INBOX_DOMAIN> (én delt postkasse, ét alias
pr. kunde) → hentning → klassificering → dublettjek → levering som mail til kundens ERP-indbakke.

Faste regler, som tabellerne understøtter:
- Intet kasseres stiltiende: alt usikkert får status 'review' (også mail til ukendt alias – client_id NULL).
- Levering er idempotent: inbox_deliveries.idempotency_key er unik og beregnes deterministisk
  (`leveringsnoegle(dokument, destination)`), og rækken indsættes FØR afsendelse.
- Samme fil til samme kunde findes kun én gang (delvist unikt indeks på client_id + sha256).
- Beløb er NUMERIC(15,2). E-mailadresser gemmes med små bogstaver (CHECK), som staff.email.
"""

import hashlib
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
    String,
    Text,
    func,
    text,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, kun_vaerdier

BELOEB = Numeric(15, 2)

DESTINATIONSTYPER = ("economic", "dinero", "email")
BESKED_STATUSSER = ("received", "classified", "delivered", "review", "ignored", "loop", "failed")
# Hvorfor en besked (eller en del af den) ikke blev til et bilag. Faste koder – ikke fri tekst.
IGNORERINGSAARSAGER = ("loop", "ukendt_modtager", "link_only", "ingen_bilag", "ukendt_filtype", "for_stor")
DOKUMENTTYPER = ("faktura", "kvittering", "kreditnota", "rykker", "kontoudtog", "andet")
KLASSIFICERINGER = ("rules", "llm", "human")
DOKUMENT_STATUSSER = ("pending", "delivered", "review", "duplicate", "rejected")
LEVERING_STATUSSER = ("pending", "sent", "failed")
ALARMTILSTANDE = ("ok", "warning", "alarm")

_SMAA_BOGSTAVER = "{kolonne} = lower({kolonne})"


def _oprettet() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


def leveringsnoegle(document_id: int, destination_id: int) -> str:
    """Deterministisk nøgle: et gentaget job rammer den unikke regel i stedet for at sende igen."""
    return hashlib.sha256(f"{document_id}:{destination_id}".encode()).hexdigest()


class InboxAddress(Base):
    """Ét alias pr. kunde. Adressen er routingnøglen."""

    __tablename__ = "inbox_addresses"
    __table_args__ = (CheckConstraint(_SMAA_BOGSTAVER.format(kolonne="address"), name="adresse_smaa_bogstaver"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True)
    address: Mapped[str] = mapped_column(String(320), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    created_at: Mapped[datetime] = _oprettet()


class InboxDestination(Base):
    """Hvor bilagene skal hen (kundens scanningsindbakke i e-conomic/Dinero eller en anden mail)."""

    __tablename__ = "inbox_destinations"
    __table_args__ = (
        kun_vaerdier("kind", DESTINATIONSTYPER),
        CheckConstraint(_SMAA_BOGSTAVER.format(kolonne="address"), name="adresse_smaa_bogstaver"),
        # Samme destination kun én gang pr. kunde – ellers ville hvert bilag blive sendt to gange.
        Index("uq_inbox_destinations_kunde_adresse", "client_id", "address", unique=True),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    address: Mapped[str] = mapped_column(String(320))
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
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"))
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
    """Én række pr. vedhæftning, der er vurderet som et muligt bilag."""

    __tablename__ = "inbox_documents"
    __table_args__ = (
        kun_vaerdier("doc_type", DOKUMENTTYPER),
        kun_vaerdier("classifier", KLASSIFICERINGER),
        kun_vaerdier("status", DOKUMENT_STATUSSER),
        CheckConstraint("size_bytes >= 0", name="stoerrelse_ikke_negativ"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256_hex"),
        CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1", name="sikkerhed_0_1"),
        CheckConstraint("currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="valuta_3_bogstaver"),
        CheckConstraint("(status = 'duplicate') = (duplicate_of IS NOT NULL)", name="dublet_peger_paa_original"),
        CheckConstraint("classifier <> 'human' OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)",
                        name="menneske_er_navngivet"),
        # Dublettjek: samme fil til samme kunde findes kun én gang (dubletterne selv undtaget).
        Index("uq_inbox_documents_dedup", "client_id", "sha256", unique=True,
              postgresql_where=text("status <> 'duplicate'")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("inbox_messages.id", ondelete="RESTRICT"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True)
    filename: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(Text)
    doc_type: Mapped[str | None] = mapped_column(String(12))
    supplier: Mapped[str | None] = mapped_column(Text)
    doc_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal | None] = mapped_column(BELOEB)
    currency: Mapped[str | None] = mapped_column(String(3))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    classifier: Mapped[str | None] = mapped_column(String(5))
    # Klassificeringens begrundelse (regel eller LLM) – vises i back office.
    reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10))
    duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("inbox_documents.id", ondelete="RESTRICT"),
                                                     index=True)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="RESTRICT"), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _oprettet()


class InboxDelivery(Base):
    """Leveringslog. Rækken indsættes FØR afsendelse – idempotency_key gør gentagen levering umulig."""

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


class InboxHealth(Base):
    """Hjerteslag pr. kunde – opdager, når kundens videresendelse stopper."""

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
