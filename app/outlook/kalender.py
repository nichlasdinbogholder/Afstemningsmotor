"""Outlook-kalenderen via Microsoft Graph (bygherren 26.10.2026) – læser optaget tid og opretter aftaler i ÉN kalender
(KALENDER_POSTKASSE) til masterafstemningerne.

- Egen app-registrering med KUN Calendars.ReadWrite, begrænset til postkassen (DEPLOY.md "Outlook-kalender"). Uden
  KALENDER_TENANT_ID/KALENDER_CLIENT_ID/KALENDER_CLIENT_SECRET/KALENDER_POSTKASSE er den slået fra.
- Hemmeligheden og adgangsnøglen registreres i logfilteret og står aldrig i en fejlbesked – kun statuskoden.
- Tider udveksles i dansk tid ("Romance Standard Time"). Prøver igen (3 gange) ved 429, 5xx og netværksfejl.
"""

from dataclasses import dataclass
from datetime import datetime

import httpx
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import get_settings
from app.sikkerhed.hemmeligheder import registrer_hemmelighed

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
TIDSZONE = "Romance Standard Time"            # Microsofts navn for dansk tid
# Aftaler med disse markeringer blokerer IKKE tiden ("Ledig" og "Arbejder et andet sted").
LEDIG = ("free", "workingElsewhere")


class KalenderFejl(Exception):
    """Kalenderen kunne ikke læses eller ændres. Beskeden er dansk og indeholder aldrig nøgler."""


class _ProevIgen(Exception):
    pass


@dataclass
class Optaget:
    start: datetime          # dansk tid, uden tidszone
    slut: datetime


def slaaet_til() -> bool:
    s = get_settings()
    return bool(s.kalender_tenant_id and s.kalender_client_id and s.kalender_client_secret and s.kalender_postkasse)


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


class Kalender:
    """Én forbindelse til kalenderen. Brug: `with Kalender() as k: k.optaget(...)`."""

    def __init__(self, transport: httpx.BaseTransport | None = None, vent=None):
        if not slaaet_til():
            raise KalenderFejl("Outlook-kalenderen er ikke slået til (KALENDER_TENANT_ID/KALENDER_CLIENT_ID/"
                               "KALENDER_CLIENT_SECRET/KALENDER_POSTKASSE mangler)")
        self.s = get_settings()
        self.vent = vent
        self.http = httpx.Client(timeout=30.0, transport=transport)
        self.adgang: str | None = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.http.close()

    def _login(self) -> str:
        if self.adgang:
            return self.adgang
        hemmelighed = self.s.kalender_client_secret.get_secret_value()
        registrer_hemmelighed(hemmelighed)
        svar = self._sikkert(lambda: self.http.post(
            f"{LOGIN}/{self.s.kalender_tenant_id}/oauth2/v2.0/token",
            data={"grant_type": "client_credentials", "client_id": self.s.kalender_client_id,
                  "client_secret": hemmelighed, "scope": "https://graph.microsoft.com/.default"}))
        if svar.status_code != 200 or not svar.json().get("access_token"):
            raise KalenderFejl(f"Login hos Microsoft mislykkedes (status {svar.status_code})")
        self.adgang = svar.json()["access_token"]
        registrer_hemmelighed(self.adgang)
        return self.adgang

    def _sikkert(self, fn):
        try:
            return _kald(fn, self.vent)
        except _ProevIgen:
            raise KalenderFejl("Microsoft svarede ikke – prøvet 3 gange") from None
        except httpx.HTTPError:
            raise KalenderFejl("Forbindelsen til Microsoft mislykkedes") from None

    def _hoved(self) -> dict:
        return {"Authorization": f"Bearer {self._login()}", "Prefer": f'outlook.timezone="{TIDSZONE}"'}

    def optaget(self, fra: datetime, til: datetime) -> list[Optaget]:
        """Alle aftaler i perioden, der blokerer tiden (ikke "Ledig")."""
        url = f"{GRAPH}/users/{self.s.kalender_postkasse}/calendarView"
        params = {"startDateTime": fra.isoformat(), "endDateTime": til.isoformat(),
                  "$select": "start,end,showAs,isCancelled", "$top": "200"}
        ud = []
        while url:
            svar = self._sikkert(lambda u=url, p=params: self.http.get(u, params=p, headers=self._hoved()))
            if svar.status_code != 200:
                raise KalenderFejl(f"Kalenderen kunne ikke læses (status {svar.status_code})")
            data = svar.json()
            for a in data.get("value", []):
                if a.get("isCancelled") or a.get("showAs") in LEDIG:
                    continue
                ud.append(Optaget(_tid(a["start"]["dateTime"]), _tid(a["end"]["dateTime"])))
            url, params = data.get("@odata.nextLink"), None
        return ud

    def opret(self, start: datetime, slut: datetime, emne: str, tekst: str) -> str:
        """Opretter en aftale (vist som optaget) og returnerer Outlooks id for den."""
        aftale = {"subject": emne, "body": {"contentType": "Text", "content": tekst}, "showAs": "busy",
                  "start": {"dateTime": start.isoformat(), "timeZone": TIDSZONE},
                  "end": {"dateTime": slut.isoformat(), "timeZone": TIDSZONE},
                  "isReminderOn": True, "reminderMinutesBeforeStart": 15}
        svar = self._sikkert(lambda: self.http.post(f"{GRAPH}/users/{self.s.kalender_postkasse}/events", json=aftale,
                                                    headers=self._hoved()))
        if svar.status_code != 201 or not svar.json().get("id"):
            raise KalenderFejl(f"Aftalen kunne ikke oprettes i Outlook (status {svar.status_code})")
        return svar.json()["id"]


def _tid(tekst: str) -> datetime:
    """Graph sender fx '2026-11-03T08:00:00.0000000' – sekundbrøker skæres fra."""
    return datetime.fromisoformat(tekst.split(".")[0])
