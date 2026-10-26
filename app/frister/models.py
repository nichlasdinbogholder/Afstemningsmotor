"""SKATS FRISTKALENDER (bygherren 26.10.2026): de præcise frister for moms og lønsumsafgift, som Skat udgiver.

Fristerne BEREGNES IKKE – Skat har undtagelser (fx juni-momsen), så hver frist står her som en dato, Skat har
offentliggjort. Kun GODKENDTE frister bruges i AI Planlægning. En administrator lægger dem ind (godkendt med det samme);
senere henter Svend dem fra skat.dk som udkast, som en administrator godkender. Databasen håndhæver: intet slettes; en
godkendt frist kan kun tilbagekaldes (status 'afvist') – aldrig rettes; højst én godkendt pr. afgift/ordning/periode.
"""

from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, kun_vaerdier

AFGIFTER = ("moms", "loensumsafgift")
ORDNINGER = {"moms": ("maaned", "kvartal", "halvaar"),
             "loensumsafgift": ("metode_1", "metode_2", "metode_3", "metode_4")}
FRIST_STATUS = ("udkast", "godkendt", "afvist")


class SkatFrist(Base):
    __tablename__ = "skat_frister"
    __table_args__ = (
        kun_vaerdier("afgift", AFGIFTER),
        kun_vaerdier("status", FRIST_STATUS),
        CheckConstraint("(afgift = 'moms' AND ordning IN ('maaned', 'kvartal', 'halvaar')) OR "
                        "(afgift = 'loensumsafgift' AND ordning IN ('metode_1', 'metode_2', 'metode_3', 'metode_4'))",
                        name="ordning_passer_til_afgift"),
        CheckConstraint("periode_til >= periode_fra", name="periode_gyldig"),
        CheckConstraint("frist > periode_til", name="frist_efter_perioden"),
        CheckConstraint("status <> 'godkendt' OR (godkendt_af IS NOT NULL AND godkendt_at IS NOT NULL)",
                        name="godkendt_kraever_hvem"),
        Index("uq_skat_frister_godkendt", "afgift", "ordning", "periode_fra", unique=True,
              postgresql_where=text("status = 'godkendt'")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    afgift: Mapped[str] = mapped_column(String(20))
    ordning: Mapped[str] = mapped_column(String(20))
    periode_fra: Mapped[date] = mapped_column(Date)
    periode_til: Mapped[date] = mapped_column(Date)
    frist: Mapped[date] = mapped_column(Date)
    kilde: Mapped[str] = mapped_column(Text)                 # adressen på skat.dk, hvor datoen står
    status: Mapped[str] = mapped_column(String(10))
    oprettet: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    oprettet_af: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="RESTRICT"), index=True)
    godkendt_af: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="RESTRICT"), index=True)
    godkendt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    afvist_af: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="RESTRICT"), index=True)
    afvist_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
