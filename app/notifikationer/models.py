"""Notifikationer til medarbejderne – klokken i øverste højre hjørne (bygherren 26.10.2026).

Én række pr. modtager. Notifikationer slettes ikke; de markeres som læst. Må aldrig indeholde tokens eller andre
hemmeligheder – kun en kort tekst og et link ind i hub'en.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, kun_vaerdier

ALVOR = ("info", "advarsel", "kritisk")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        kun_vaerdier("alvor", ALVOR),
        # Klokken slår de ulæste op pr. medarbejder.
        Index("ix_notifications_ulaeste", "staff_id", postgresql_where=text("laest IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    staff_id: Mapped[int] = mapped_column(ForeignKey("staff.id", ondelete="RESTRICT"), index=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), index=True)
    alvor: Mapped[str] = mapped_column(String(10))
    titel: Mapped[str] = mapped_column(String(255))
    tekst: Mapped[str | None] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(500))      # sti i hub'en, fx /kunder/
    oprettet: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    laest: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
