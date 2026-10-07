"""Referencesatsen hentes automatisk: Nationalbankens udlånsrente, som den er 1. januar og 1. juli.

Kilde: Danmarks Statistiks Statistikbank (offentlig, kun læsning, ingen nøgle), hvor Nationalbankens
officielle renter ligger dag for dag (tabel DNRENTD). Vi henter tabellens beskrivelse, finder serien med
udlånsrenten og den seneste dag på eller før 1.1/1.7 – og læser satsen.

Gætter aldrig: findes serien ikke entydigt, er dagen mere end 7 dage gammel, eller ser tallet forkert ud,
gemmes intet og der rejses `HentFejl` (jobbet prøves igen og meldes i Sentry). Satsen kan altid sættes i
hånden: `python -m app.opkraevning.referencesats saet ...`. En gemt sats overskrives aldrig.
"""

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.config import get_settings
from app.opkraevning.lov import halvaar_start
from app.opkraevning.models import ReferenceRate

MAKS_ALDER_DAGE = 7
FORNUFTIG = (Decimal("-5"), Decimal("25"))   # procent – alt udenfor er en fejl, ikke en rente


class HentFejl(Exception):
    pass


@dataclass(frozen=True)
class Sats:
    gaelder_fra: date
    dato_i_kilden: date
    sats: Decimal
    serie: str          # fx "INSTRUMENT=ODKNAA (Udlånsrente)"

    @property
    def kilde(self) -> str:
        s = get_settings()
        return (f"Nationalbanken via Danmarks Statistik {s.referencesats_tabel}, {self.serie}, "
                f"værdi pr. {self.dato_i_kilden:%d.%m.%Y} – hentet automatisk")


def _tid(vaerdi: str) -> date | None:
    m = re.fullmatch(r"(\d{4})M(\d{2})D(\d{2})", vaerdi)
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


def _valg_fra_indstilling() -> dict[str, str]:
    raa = get_settings().referencesats_valg or ""
    return dict(par.split("=", 1) for par in re.split(r"[;,]\s*", raa.strip()) if "=" in par)


def beskriv_tabel(klient: httpx.Client) -> dict:
    s = get_settings()
    svar = klient.get(f"{s.referencesats_api_url}/tableinfo/{s.referencesats_tabel}",
                      params={"lang": "da", "format": "JSON"})
    svar.raise_for_status()
    return svar.json()


def vaelg_serie(info: dict) -> tuple[dict[str, str], str, list[date]]:
    """(valg pr. variabel uden tid, beskrivelse af serien, tabellens datoer). Rejser HentFejl, hvis ikke entydigt."""
    variabler = info.get("variables") or []
    tid = [v for v in variabler if v.get("time") or str(v.get("id", "")).lower() == "tid"]
    if len(tid) != 1:
        raise HentFejl("Tabellen har ingen entydig tidsvariabel")
    datoer = sorted(d for d in (_tid(x["id"]) for x in tid[0].get("values", [])) if d)
    valg = _valg_fra_indstilling()
    beskrivelse = []
    for v in variabler:
        if v is tid[0]:
            continue
        vaerdier = {x["id"]: x.get("text", "") for x in v.get("values", [])}
        if v["id"] in valg:
            if valg[v["id"]] not in vaerdier:
                raise HentFejl(f"{v['id']}={valg[v['id']]} findes ikke i tabellen")
        else:
            udlaan = [k for k, t in vaerdier.items() if "udlån" in t.casefold()]
            if len(vaerdier) == 1:
                valg[v["id"]] = next(iter(vaerdier))
            elif len(udlaan) == 1:
                valg[v["id"]] = udlaan[0]
            else:
                muligheder = "; ".join(f"{k}={t}" for k, t in vaerdier.items())
                raise HentFejl(f"Kan ikke vælge entydigt for {v['id']} – sæt REFERENCESATS_VALG. Muligheder: "
                               f"{muligheder}")
        beskrivelse.append(f"{v['id']}={valg[v['id']]} ({vaerdier[valg[v['id']]]})")
    if not any("udlån" in b.casefold() for b in beskrivelse) and not _valg_fra_indstilling():
        raise HentFejl("Fandt ingen serie med udlånsrenten i tabellen")
    return valg, ", ".join(beskrivelse), datoer


def hent_sats(gaelder_fra: date, klient: httpx.Client | None = None) -> Sats:
    egen = klient is None
    klient = klient or httpx.Client(timeout=20)
    try:
        valg, serie, datoer = vaelg_serie(beskriv_tabel(klient))
        foer = [d for d in datoer if d <= gaelder_fra]
        if not foer or (gaelder_fra - foer[-1]).days > MAKS_ALDER_DAGE:
            raise HentFejl(f"Kilden har endnu ingen værdi for {gaelder_fra:%d.%m.%Y}")
        dag = foer[-1]
        s = get_settings()
        svar = klient.post(f"{s.referencesats_api_url}/data", json={
            "table": s.referencesats_tabel, "format": "CSV", "lang": "en", "delimiter": "Semicolon",
            "variables": [{"code": k, "values": [v]} for k, v in valg.items()]
                         + [{"code": "Tid", "values": [f"{dag:%Y}M{dag:%m}D{dag:%d}"]}],
        })
        svar.raise_for_status()
    except httpx.HTTPError as fejl:
        raise HentFejl(f"Kunne ikke hente fra Statistikbanken ({type(fejl).__name__})") from None
    finally:
        if egen:
            klient.close()
    raekker = [r for r in csv.reader(io.StringIO(svar.text.lstrip("﻿")), delimiter=";") if r]
    if len(raekker) != 2:
        raise HentFejl(f"Forventede én række fra kilden, fik {len(raekker) - 1}")
    tekst = raekker[1][-1].strip().replace(",", ".")
    try:
        sats = Decimal(tekst)
    except InvalidOperation:
        raise HentFejl(f"Kilden svarede ikke med et tal ('{tekst[:20]}')") from None
    if not FORNUFTIG[0] <= sats <= FORNUFTIG[1]:
        raise HentFejl(f"Satsen {sats} % ser ikke rigtig ud – gemmes ikke")
    return Sats(gaelder_fra=gaelder_fra, dato_i_kilden=dag, sats=sats, serie=serie)


def opdater_referencesats(session: Session, dag: date, klient: httpx.Client | None = None) -> ReferenceRate:
    """Sørg for, at halvåret for `dag` har en sats. En gemt sats røres aldrig."""
    fra = halvaar_start(dag)
    findes = session.scalar(select(ReferenceRate).where(ReferenceRate.valid_from == fra))
    if findes is not None:
        return findes
    s = hent_sats(fra, klient)
    r = ReferenceRate(valid_from=fra, rate=s.sats, source=s.kilde)
    session.add(r)
    session.add(AuditLog(handling="referencesats_hentet",
                         detaljer={"fra": fra.isoformat(), "sats": str(s.sats), "kilde": s.kilde,
                                   "hentet": datetime.now(timezone.utc).isoformat()}))
    session.flush()
    return r
