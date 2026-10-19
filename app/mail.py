"""Udgående mail fra hub'en via Microsoft Graph – fra én fast afsender (MAIL_AFSENDER, fx info@dinbogholder.dk).

- Bruger en egen app-registrering i Din Bogholders Microsoft 365 med KUN tilladelsen Mail.Send, begrænset til netop
  afsenderadressen. Uden MAIL_TENANT_ID/MAIL_CLIENT_ID/MAIL_CLIENT_SECRET er mail slået fra.
- Hemmeligheden registreres i logfilteret og står aldrig i en fejlbesked. Fejl giver kun statuskoden.
- Prøver igen (3 gange med stigende ventetid) ved 429, 5xx og netværksfejl.
"""

import httpx
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.sikkerhed.hemmeligheder import registrer_hemmelighed

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"


class MailFejl(Exception):
    """Mailen kunne ikke sendes. Beskeden er dansk og indeholder aldrig nøgler."""


class _ProevIgen(Exception):
    pass


def mail_slaaet_til() -> bool:
    s = get_settings()
    return bool(s.mail_tenant_id and s.mail_client_id and s.mail_client_secret)


def _kald(fn, vent=None):
    for forsoeg in Retrying(retry=retry_if_exception(lambda f: isinstance(f, _ProevIgen)),
                            wait=vent or wait_exponential(multiplier=1, max=20), stop=stop_after_attempt(3),
                            reraise=True):
        with forsoeg:
            try:
                svar = fn()
            except httpx.TransportError:
                raise _ProevIgen() from None
            if svar.status_code == 429 or svar.status_code >= 500:
                raise _ProevIgen()
            return svar
    return None


def send_mail(til: str, emne: str, tekst: str, transport: httpx.BaseTransport | None = None, vent=None) -> None:
    """Send en almindelig tekstmail. Rejser MailFejl, hvis den ikke kunne sendes."""
    s = get_settings()
    if not mail_slaaet_til():
        raise MailFejl("Udgående mail er ikke slået til (MAIL_TENANT_ID/MAIL_CLIENT_ID/MAIL_CLIENT_SECRET mangler)")
    hemmelighed = s.mail_client_secret.get_secret_value()
    registrer_hemmelighed(hemmelighed)
    try:
        with httpx.Client(timeout=30.0, transport=transport) as http:
            svar = _kald(lambda: http.post(f"{LOGIN}/{s.mail_tenant_id}/oauth2/v2.0/token", data={
                "grant_type": "client_credentials", "client_id": s.mail_client_id, "client_secret": hemmelighed,
                "scope": "https://graph.microsoft.com/.default"}), vent)
            if svar.status_code != 200:
                raise MailFejl(f"Login hos Microsoft mislykkedes (status {svar.status_code})")
            adgang = svar.json().get("access_token")
            if not adgang:
                raise MailFejl("Microsoft sendte ingen adgangsnøgle")
            registrer_hemmelighed(adgang)
            besked = {"message": {"subject": emne, "body": {"contentType": "Text", "content": tekst},
                                  "toRecipients": [{"emailAddress": {"address": til}}]},
                      "saveToSentItems": True}
            svar = _kald(lambda: http.post(f"{GRAPH}/users/{s.mail_afsender}/sendMail", json=besked,
                                           headers={"Authorization": f"Bearer {adgang}"}), vent)
            if svar.status_code != 202:
                raise MailFejl(f"Microsoft afviste mailen (status {svar.status_code})")
    except _ProevIgen:
        raise MailFejl("Microsoft svarede ikke – prøvet 3 gange") from None
    except httpx.HTTPError:
        raise MailFejl("Forbindelsen til Microsoft mislykkedes") from None
