"""Synkroniseringstilstand: hvor langt vi er nået for hver kunde og ressource."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, kun_vaerdier

RESSOURCER = (
    "accounts", "customers", "suppliers", "entries", "open_entries", "invoices", "journals",
)
CURSOR_TYPER = ("dato", "id", "token")
SYNK_STATUSSER = ("ok", "forsinket", "fejlet", "deaktiveret")


class SyncState(Base):
    """Én række pr. kunde pr. ressource.

    `cursor` er bogmærket: hvor langt vi er nået (en dato, et id eller et
    token fra systemet). Tom cursor = hent alt forfra.
    Må kun ændres via funktionerne i app/synk/tilstand.py.
    """

    __tablename__ = "sync_state"
    __table_args__ = (
        UniqueConstraint("client_id", "ressource", name="uq_sync_state_kunde_ressource"),
        kun_vaerdier("ressource", RESSOURCER),
        kun_vaerdier("cursor_type", CURSOR_TYPER),
        kun_vaerdier("status", SYNK_STATUSSER),
        CheckConstraint("antal_fejl_i_traek >= 0", name="antal_fejl_ikke_negativ"),
        CheckConstraint("cursor IS NULL OR cursor_type IS NOT NULL", name="cursor_har_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Indeks: dækket af den unikke regel (client_id, ressource), som starter med client_id.
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"))
    ressource: Mapped[str] = mapped_column(String(30))
    cursor: Mapped[str | None] = mapped_column(Text)
    cursor_type: Mapped[str | None] = mapped_column(String(10))
    sidste_koersel_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sidste_ok: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sidste_fejl_tidspunkt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sidste_fejl_besked: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(15), server_default="ok", index=True)
    antal_fejl_i_traek: Mapped[int] = mapped_column(Integer, server_default="0")
    naeste_koersel: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    antal_hentet_sidst: Mapped[int | None] = mapped_column(Integer)
    oprettet: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Sættes automatisk af triggeren saet_opdateret ved hver ændring.
    opdateret: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def __repr__(self) -> str:
        return (
            f"<SyncState client_id={self.client_id} ressource={self.ressource!r} "
            f"status={self.status!r} cursor={self.cursor!r}>"
        )
