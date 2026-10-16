"""Fagassistenten – tabeller (prefix assistant_ og kb_).

- assistant_conversations / assistant_messages / assistant_feedback: samtalerne. Hver samtale tilhører
  ÉN medarbejder (staff_id) og evt. ÉN kunde (client_id; NULL = generelt). En tråd blander aldrig to
  kunders tal – skifter man kunde, startes en ny samtale.
- kb_documents: videnbasen. Kun 'active' dokumenter inden for gyldighedsperioden må nå modellen.
  Satser ('satser') har altid værdi og enhed. Et 'draft' oprettet af satskontrollen peger på det
  dokument, det skal afløse (replaces_id) – det bliver først aktivt, når et menneske godkender.
- kb_rate_checks: log over hver satskontrol, også de uændrede (dokumentation for, at satserne er efterset).

Der står aldrig tokens eller API-nøgler i nogen af tabellerne.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, kun_vaerdier

ROLLER = ("user", "assistant")
# complete = svaret blev færdigt; interrupted = streamen blev afbrudt (det modtagne er gemt);
# failed = kaldet til modellen fejlede.
BESKED_STATUSSER = ("complete", "interrupted", "failed")
VURDERINGER = ("up", "down")
KATEGORIER = ("moms", "skat", "bogfoering", "intern", "kontoplan", "satser")
DOKUMENT_STATUSSER = ("active", "draft", "superseded")
KONTROL_UDFALD = ("unchanged", "changed", "failed")
# Menneskets afgørelse af et fund fra satskontrollen: approved = ny sats godkendt (kun ved 'changed');
# rejected = ny værdi afvist; noted = fejlen er set og håndteret (kun ved 'failed').
AFGOERELSER = ("approved", "rejected", "noted")
METODER = ("regel", "model")  # hvordan værdien blev fundet på kildesiden


def _tidspunkt() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class AssistantConversation(Base):
    __tablename__ = "assistant_conversations"
    __table_args__ = (
        Index("ix_assistant_conversations_staff_opdateret", "staff_id", text("updated_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id", ondelete="RESTRICT"))
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True)
    title: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _tidspunkt()
    updated_at: Mapped[datetime] = _tidspunkt()


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"
    __table_args__ = (
        kun_vaerdier("role", ROLLER),
        kun_vaerdier("status", BESKED_STATUSSER),
        Index("ix_assistant_messages_samtale_tid", "conversation_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("assistant_conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="complete")
    sources: Mapped[list | None] = mapped_column(JSONB)  # [{kb_id, title}]
    lookups: Mapped[list | None] = mapped_column(JSONB)  # [{tool, client_id, args, row_count}]
    model: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _tidspunkt()


class AssistantFeedback(Base):
    __tablename__ = "assistant_feedback"
    __table_args__ = (
        kun_vaerdier("rating", VURDERINGER),
        # Én vurdering pr. medarbejder pr. svar.
        UniqueConstraint("message_id", "staff_id", name="uq_assistant_feedback_besked_medarbejder"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("assistant_messages.id", ondelete="CASCADE"))
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id", ondelete="RESTRICT"), index=True)
    rating: Mapped[str] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _tidspunkt()


class KbDocument(Base):
    __tablename__ = "kb_documents"
    __table_args__ = (
        kun_vaerdier("category", KATEGORIER),
        kun_vaerdier("status", DOKUMENT_STATUSSER),
        # En sats uden tal og enhed giver ingen mening – og må aldrig nå modellen.
        CheckConstraint("category <> 'satser' OR (value IS NOT NULL AND unit IS NOT NULL)",
                        name="sats_har_vaerdi_og_enhed"),
        CheckConstraint("valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
                        name="gyldig_periode"),
        Index("ix_kb_documents_kategori_status", "category", "status"),
        Index("ix_kb_documents_soegning", "search", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    value: Mapped[Decimal | None] = mapped_column(Numeric(15, 4))
    unit: Mapped[str | None] = mapped_column(Text)  # 'kr/km', 'pct', 'kr'
    source_url: Mapped[str | None] = mapped_column(Text)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    # Det aktive dokument, et udkast fra satskontrollen skal afløse, når det godkendes.
    replaces_id: Mapped[int | None] = mapped_column(ForeignKey("kb_documents.id", ondelete="RESTRICT"), index=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verify_source_url: Mapped[str | None] = mapped_column(Text)  # siden mandagsjobbet henter fra
    verify_hint: Mapped[str | None] = mapped_column(Text)  # hvordan værdien findes på siden
    updated_by: Mapped[str | None] = mapped_column(Text)  # medarbejderens e-mail
    updated_at: Mapped[datetime] = _tidspunkt()
    search: Mapped[str] = mapped_column(TSVECTOR, Computed(
        "to_tsvector('danish', coalesce(title, '') || ' ' || coalesce(body, ''))", persisted=True))


class KbRateCheck(Base):
    """Én kontrol af én sats. Udfaldet 'changed' eller 'failed' er et FUND, indtil et menneske har afgjort det.

    Databasens regel (trigger `kb_kraev_godkendelse`): et udkast, der skal afløse en sats (replaces_id), kan
    kun blive 'active', hvis en kontrol med decision='approved' peger på netop det udkast.
    """

    __tablename__ = "kb_rate_checks"
    __table_args__ = (
        kun_vaerdier("outcome", KONTROL_UDFALD),
        kun_vaerdier("decision", AFGOERELSER),
        kun_vaerdier("method", METODER),
        CheckConstraint("(decision IS NULL) = (reviewed_at IS NULL) AND (decision IS NULL) = (reviewed_by IS NULL)",
                        name="afgoerelse_hele"),
        CheckConstraint("decision IS NULL OR (decision IN ('approved', 'rejected') AND outcome = 'changed') "
                        "OR (decision = 'noted' AND outcome = 'failed')", name="afgoerelse_passer_udfald"),
        CheckConstraint("draft_id IS NULL OR outcome = 'changed'", name="udkast_kun_ved_aendring"),
        Index("ix_kb_rate_checks_dokument_tid", "kb_id", text("checked_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("kb_documents.id", ondelete="RESTRICT"))
    checked_at: Mapped[datetime] = _tidspunkt()
    outcome: Mapped[str] = mapped_column(Text)
    previous_value: Mapped[Decimal | None] = mapped_column(Numeric(15, 4))
    fetched_value: Mapped[Decimal | None] = mapped_column(Numeric(15, 4))
    raw_excerpt: Mapped[str | None] = mapped_column(Text)  # tekststykket værdien blev læst ud af
    error: Mapped[str | None] = mapped_column(Text)
    method: Mapped[str | None] = mapped_column(Text)  # 'regel' eller 'model' – hvordan værdien blev fundet
    draft_id: Mapped[int | None] = mapped_column(ForeignKey("kb_documents.id", ondelete="RESTRICT"), index=True)
    decision: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="RESTRICT"), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
