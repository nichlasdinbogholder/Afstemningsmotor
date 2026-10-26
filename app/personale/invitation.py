"""Velkomstmail ("invitation") til en ny medarbejder – bygherren 26.10.2026.

- Sendes automatisk, når en administrator opretter en medarbejder på siden Medarbejdere, og kan sendes igen derfra
  ("Send invitation igen"), hvis mailen ikke kan findes i Outlook.
- Fra MAIL_AFSENDER (info@dinbogholder.dk) til medarbejderens egen adresse, med et link til hub'en.
- Invitationen udløber aldrig: der er ingen kode i mailen – man logger ind med sin Microsoft-konto (samme som
  Outlook), og adgangen styres af listen over medarbejdere.
- Er udgående mail ikke slået til endnu, sendes intet (status 'ikke_slaaet_til'). Hvert forsøg logges i audit_log
  (`medarbejder_invitation`) med status sendt / fejl / ikke_slaaet_til – aldrig nøgler.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditLog
from app.config import get_settings
from app.mail import MailFejl, mail_slaaet_til, send_mail
from app.personale.models import Staff

HANDLING = "medarbejder_invitation"


def tekst(navn: str, email: str) -> tuple[str, str]:
    link = get_settings().public_url.rstrip("/") + "/"
    emne = "Du har fået adgang til Din Bogholders hub"
    krop = (f"Hej {navn}\n\n"
            "Du har fået adgang til Din Bogholders hub.\n\n"
            f"Log ind her: {link}\n\n"
            f"Tryk på \"Log ind med Microsoft\", og brug din Microsoft-konto ({email}) – den samme som i Outlook.\n"
            "Invitationen udløber ikke. Du skal ikke oprette en adgangskode.\n\n"
            "Venlig hilsen\nDin Bogholder\n\n"
            "(Mailen er sendt automatisk fra hub'en.)")
    return emne, krop


def send_invitation(session: Session, medarbejder: Staff, af: Staff) -> dict:
    """Send velkomstmailen og log forsøget. Rejser aldrig en fejl – resultatet står i 'status'."""
    if not medarbejder.aktiv:
        status, fejl = "fejl", "Adgangen er lukket – genåbn den først"
    elif not mail_slaaet_til():
        status, fejl = "ikke_slaaet_til", "Udgående mail er ikke slået til endnu"
    else:
        emne, krop = tekst(medarbejder.navn, medarbejder.email)
        try:
            send_mail(medarbejder.email, emne, krop)
            status, fejl = "sendt", None
        except MailFejl as f:
            status, fejl = "fejl", str(f)
    session.add(AuditLog(staff_id=af.id, handling=HANDLING, detaljer={
        "medarbejder_id": medarbejder.id, "email": medarbejder.email, "status": status, "fejl": fejl,
        "fra": get_settings().mail_afsender, "udfoert_af": af.email}))
    session.flush()
    return {"status": status, "fejl": fejl}


def seneste(session: Session, medarbejder_id: int) -> dict | None:
    """Seneste invitation til medarbejderen (til visning på siden)."""
    log = session.scalars(select(AuditLog).where(
        AuditLog.handling == HANDLING, AuditLog.detaljer["medarbejder_id"].as_integer() == medarbejder_id)
        .order_by(AuditLog.id.desc())).first()
    if log is None:
        return None
    return {"tidspunkt": log.tidspunkt.isoformat() if log.tidspunkt else None,
            "status": log.detaljer.get("status"), "fejl": log.detaljer.get("fejl")}
