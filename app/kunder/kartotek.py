"""Kundekartoteket i hub'en (siden Kunde → Kundeoversigt / Arkiverede kunder), bygherren 23.10.2026.

- Oprettelse (KUN administratorer): navn, kundenummer, CVR, regnskabssystem, ansvarlig medarbejder, STARTDATO for
  samarbejdet og afregningsform (timeafregning, abonnement med pris eller intern = vores eget firma) – afregningsformen gælder fra startdatoen.
  Kundens adgang (token) lægges stadig ind med `python -m app.kunder.gem_token` (skjult indtastning).
- Arkivering (KUN administratorer): årsag (pris for høj, engangsopgave, uoverensstemmelse, har ansat egen bogholder)
  og dato for samarbejdets OPHØR. Kunden får status 'opsagt' og stoppes helt – ingen hentning fra regnskabssystemet,
  ingen rykkere, kan ikke vælges i timeregistreringen eller hos Svend. Intet slettes; historikken bevares.
- Ansvarlig medarbejder (KUN administratorer) kan skiftes i oversigten. Ansvarlig og assistent afgør, hvilke kunder
  en medarbejder kan spørge Svend om.
- Genaktivering (KUN administratorer): kunden bliver aktiv igen. Arkiveringen står stadig i loggen.
- "Kun admin hos Svend" (KUN administratorer): en intern kunde som Din Bogholder selv kan kun administratorer spørge
  Svend om (bygherren 26.10.2026).
- Alt logges i audit_log (kunde_oprettet, kunde_ansvarlig, kunde_kun_admin, kunde_arkiveret, kunde_genaktiveret) med hvem og fra/til.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.db import saet_aktoer
from app.kunder.cvr import laeg_opslag_i_koe
from app.kunder.tabeller import KundeLog
from app.tid import TIDSZONE
from app.kunder.models import ARKIV_AARSAGER, I_OVERSIGTEN, SYSTEMER, Client
from app.personale.models import Staff
from app.timeregistrering.satser import ManglerSats, nye_vilkaar

AARSAG_TEKST = {
    "pris_for_hoej": "Prisen for høj",
    "engangsopgave": "Engangsopgave",
    "uoverensstemmelse": "Uoverensstemmelse",
    "egen_bogholder": "Har ansat egen bogholder",
}
SYSTEM_TEKST = {"economic": "e-conomic", "dinero": "Dinero"}


class KartotekFejl(Exception):
    """Fejl ved indtastning. Beskeden er dansk og kan vises direkte."""


def liste(session: Session, arkiverede: bool, dag: date) -> list[dict]:
    """Kundeoversigten (aktive og på pause) eller de arkiverede (opsagte) kunder, sorteret efter navn.

    Afregningsformen vises på dagen – eller på startdatoen, hvis samarbejdet ikke er begyndt endnu. For en arkiveret
    kunde vises den, der gjaldt ved ophøret."""
    # Kundeoversigten viser kunder (aktiv og pause) – aldrig emner, der endnu ikke er blevet kunde.
    betingelse = Client.status == "opsagt" if arkiverede else Client.status.in_(I_OVERSIGTEN)
    ud = []
    for k in session.scalars(select(Client).where(betingelse).order_by(Client.navn)):
        ansvarlig = session.get(Staff, k.ansvarlig_medarbejder_id) if k.ansvarlig_medarbejder_id else None
        af = session.get(Staff, k.arkiveret_af) if k.arkiveret_af else None
        paa = dag
        if k.startdato and k.startdato > paa:
            paa = k.startdato
        if arkiverede and k.opsagt_dato:
            paa = k.opsagt_dato
        afregning = session.execute(text("SELECT time_afregning(:k, :d), time_abonnementspris(:k, :d)"),
                                    {"k": k.id, "d": paa}).one()
        ud.append({
            "id": k.id, "navn": k.navn, "kundenummer": k.kundenummer, "cvr": k.cvr, "status": k.status,
            "system": SYSTEM_TEKST.get(k.regnskabssystem, k.regnskabssystem),
            "ansvarlig": ansvarlig.navn if ansvarlig else None, "ansvarlig_id": k.ansvarlig_medarbejder_id,
            "startdato": k.startdato.isoformat() if k.startdato else None,
            "afregning": afregning[0], "abonnementspris": None if afregning[1] is None else str(afregning[1]),
            "ophoersdato": k.opsagt_dato.isoformat() if k.opsagt_dato else None,
            "aarsag": AARSAG_TEKST.get(k.arkiv_aarsag) if k.arkiv_aarsag else None,
            "arkiveret_af": af.navn if af else None, "kun_admin": k.kun_admin,
        })
    return ud


def opret(session: Session, af: Staff, navn: str, kundenummer: str, cvr: str | None, system: str,
          ansvarlig_id: int | None, startdato: date, afregning: str, abonnementspris: Decimal | None,
          status: str = "aktiv") -> Client:
    navn, kundenummer = (navn or "").strip(), (kundenummer or "").strip()
    cvr = (cvr or "").replace(" ", "") or None
    if not navn or not kundenummer:
        raise KartotekFejl("Skriv kundens navn og kundenummer")
    if cvr and not (cvr.isdigit() and len(cvr) == 8):
        raise KartotekFejl("CVR skal være 8 cifre")
    if system not in SYSTEMER:
        raise KartotekFejl("Vælg kundens regnskabssystem")
    if startdato is None:
        raise KartotekFejl("Vælg startdatoen for samarbejdet")
    if status not in ("onboarding", "aktiv"):
        raise KartotekFejl("En ny kunde er enten Onboarding eller Aktiv")
    findes = session.scalars(select(Client).where(or_(
        Client.kundenummer == kundenummer, Client.cvr == cvr if cvr else False))).first()
    if findes is not None:
        raise KartotekFejl(f"Kunden findes allerede ({findes.navn}, kundenr. {findes.kundenummer})"
                           + (" – den er arkiveret og kan genaktiveres" if findes.status == "opsagt" else ""))
    if ansvarlig_id is not None:
        m = session.get(Staff, ansvarlig_id)
        if m is None or not m.aktiv:
            raise KartotekFejl("Den ansvarlige skal være en aktiv medarbejder")
    saet_aktoer(session, af.id)
    # Intern afregning (vores eget firma) gør altid kunden intern hos Svend – Intern kræver kun_admin.
    kunde = Client(navn=navn, kundenummer=kundenummer, cvr=cvr, regnskabssystem=system, status=status,
                   startdato=startdato, ansvarlig_medarbejder_id=ansvarlig_id, kun_admin=afregning == "intern")
    session.add(kunde)
    session.flush()
    try:
        nye_vilkaar(session, kunde.id, startdato, afregning, abonnementspris, af.id)
    except ManglerSats as fejl:  # fx abonnement uden pris
        raise KartotekFejl(str(fejl)) from None
    session.add(AuditLog(client_id=kunde.id, staff_id=af.id, handling="kunde_oprettet", detaljer={
        "udfoert_af": af.email, "system": system, "startdato": startdato.isoformat(), "afregning": afregning,
        "abonnementspris": None if abonnementspris is None else str(abonnementspris)}))
    session.flush()
    # Stamdata fra CVR-registret hentes med det samme (job i kø – opslaget forsinker ikke oprettelsen).
    laeg_opslag_i_koe(session, kunde, datetime.now(TIDSZONE).date(), prioritet=10)
    return kunde


def saet_ansvarlig(session: Session, af: Staff, client_id: int, ansvarlig_id: int | None) -> Client:
    kunde = session.get(Client, client_id)
    if kunde is None:
        raise KartotekFejl("Kunden findes ikke")
    if kunde.status == "opsagt":
        raise KartotekFejl("Kunden er arkiveret – genaktivér den først")
    if ansvarlig_id is not None:
        m = session.get(Staff, ansvarlig_id)
        if m is None or not m.aktiv:
            raise KartotekFejl("Den ansvarlige skal være en aktiv medarbejder")
    fra = kunde.ansvarlig_medarbejder_id
    if fra == ansvarlig_id:
        return kunde
    # Skiftet er en hændelse: triggeren skriver det i kunde_owner_history og kunde_log med aktøren.
    saet_aktoer(session, af.id)
    kunde.ansvarlig_medarbejder_id = ansvarlig_id
    session.add(AuditLog(client_id=kunde.id, staff_id=af.id, handling="kunde_ansvarlig", detaljer={
        "udfoert_af": af.email, "fra_staff_id": fra, "til_staff_id": ansvarlig_id}))
    session.flush()
    return kunde


def saet_assistent(session: Session, af: Staff, client_id: int, assistent_id: int | None) -> Client:
    """Assistenten på kunden (det daglige arbejde – samme adgang som den ansvarlige). Skrives i kundens log."""
    kunde = session.get(Client, client_id)
    if kunde is None:
        raise KartotekFejl("Kunden findes ikke")
    if kunde.status == "opsagt":
        raise KartotekFejl("Kunden er arkiveret – genaktivér den først")
    ny = session.get(Staff, assistent_id) if assistent_id is not None else None
    if assistent_id is not None and (ny is None or not ny.aktiv):
        raise KartotekFejl("Assistenten skal være en aktiv medarbejder")
    if kunde.assistent_id == assistent_id:
        return kunde
    gammel = session.get(Staff, kunde.assistent_id) if kunde.assistent_id else None
    kunde.assistent_id = assistent_id
    session.add(KundeLog(client_id=kunde.id, event="assistent_skiftet", actor_staff_id=af.id,
                         detail=f"{gammel.navn if gammel else 'ingen'} → {ny.navn if ny else 'ingen'}"))
    session.flush()
    return kunde


# Status, der kan vælges på kundekortet. Arkivering ('opsagt') sker kun med årsag og dato (arkiver/genaktiver).
VALGBARE_STATUSSER = ("onboarding", "aktiv", "pause")
STATUS_TEKST = {"emne": "Emne", "onboarding": "Onboarding", "aktiv": "Aktiv", "pause": "Pause", "opsagt": "Arkiveret"}


def saet_status(session: Session, af: Staff, client_id: int, status: str) -> Client:
    kunde = session.get(Client, client_id)
    if kunde is None:
        raise KartotekFejl("Kunden findes ikke")
    if status not in VALGBARE_STATUSSER:
        raise KartotekFejl("Vælg Onboarding, Aktiv eller Pause")
    if kunde.status == "opsagt":
        raise KartotekFejl("Kunden er arkiveret – genaktivér den under Arkiverede kunder")
    if kunde.status == status:
        return kunde
    fra = kunde.status
    kunde.status = status
    session.add(KundeLog(client_id=kunde.id, event="status_skiftet", actor_staff_id=af.id,
                         detail=f"{STATUS_TEKST[fra]} → {STATUS_TEKST[status]}"))
    session.add(AuditLog(client_id=kunde.id, staff_id=af.id, handling="kunde_status", detaljer={
        "udfoert_af": af.email, "fra": fra, "til": status}))
    session.flush()
    return kunde


def saet_kun_admin(session: Session, af: Staff, client_id: int, kun_admin: bool) -> Client:
    """Intern kunde: kun administratorer kan spørge Svend om den og se samtalerne om den."""
    kunde = session.get(Client, client_id)
    if kunde is None:
        raise KartotekFejl("Kunden findes ikke")
    if kunde.kun_admin == kun_admin:
        return kunde
    if not kun_admin and session.execute(text(
            "SELECT 1 FROM time_client_terms WHERE client_id = :k AND billing_model = 'intern' "
            "AND (valid_to IS NULL OR valid_to > CURRENT_DATE)"), {"k": kunde.id}).first():
        raise KartotekFejl("Kunden har afregningsformen Intern og skal forblive intern – skift afregningsform først")
    kunde.kun_admin = kun_admin
    session.add(AuditLog(client_id=kunde.id, staff_id=af.id, handling="kunde_kun_admin", detaljer={
        "udfoert_af": af.email, "kun_admin": kun_admin}))
    session.flush()
    return kunde


def arkiver(session: Session, af: Staff, client_id: int, aarsag: str, ophoersdato: date) -> Client:
    kunde = session.get(Client, client_id)
    if kunde is None:
        raise KartotekFejl("Kunden findes ikke")
    if kunde.status == "opsagt":
        raise KartotekFejl("Kunden er allerede arkiveret")
    if aarsag not in ARKIV_AARSAGER:
        raise KartotekFejl("Vælg årsagen til, at kunden arkiveres")
    if ophoersdato is None:
        raise KartotekFejl("Vælg datoen for samarbejdets ophør")
    if kunde.startdato and ophoersdato < kunde.startdato:
        raise KartotekFejl(f"Ophørsdatoen ligger før startdatoen ({kunde.startdato:%d.%m.%Y})")
    fra = kunde.status
    kunde.status, kunde.opsagt_dato, kunde.arkiv_aarsag, kunde.arkiveret_af = "opsagt", ophoersdato, aarsag, af.id
    session.add(AuditLog(client_id=kunde.id, staff_id=af.id, handling="kunde_arkiveret", detaljer={
        "udfoert_af": af.email, "fra_status": fra, "aarsag": aarsag, "ophoersdato": ophoersdato.isoformat()}))
    session.flush()
    return kunde


def genaktiver(session: Session, af: Staff, client_id: int) -> Client:
    kunde = session.get(Client, client_id)
    if kunde is None:
        raise KartotekFejl("Kunden findes ikke")
    if kunde.status != "opsagt":
        raise KartotekFejl("Kunden er ikke arkiveret")
    detaljer = {"udfoert_af": af.email, "tidligere_aarsag": kunde.arkiv_aarsag,
                "tidligere_ophoersdato": kunde.opsagt_dato.isoformat() if kunde.opsagt_dato else None}
    saet_aktoer(session, af.id)
    # Den bevidste genåbning – uden den afviser databasen overgangen fra ophørt (triggeren kunde_lifecycle_guard).
    session.execute(text("SELECT set_config('app.genaabning', 'ja', true)"))
    kunde.status, kunde.opsagt_dato, kunde.arkiv_aarsag, kunde.arkiveret_af = "aktiv", None, None, None
    session.add(AuditLog(client_id=kunde.id, staff_id=af.id, handling="kunde_genaktiveret", detaljer=detaljer))
    session.flush()
    return kunde
