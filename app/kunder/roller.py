"""Kundens to roller – defineret ÉT sted og brugt ens overalt (bygherren 26.10.2026).

- ANSVARLIG (clients.ansvarlig_medarbejder_id): har det overordnede ansvar for kunden. Medarbejderne sidder hver
  med deres egne kunder – der er ingen fælles pulje. Skift af ansvarlig er en hændelse med dato (kunde_owner_history,
  skrevet af en trigger).
- ASSISTENT (clients.assistent_id): hjælper med det daglige arbejde. Har SAMME adgang til kunden som den ansvarlige,
  og overtager kundens frister, når den ansvarlige er fraværende (fravær registreres i timeregistreringen).

Login-rollerne er uændrede: administrator og medarbejder (staff.rolle). Kun en administrator kan sætte tokens.
"""

from app.kunder.models import Client
from app.personale.models import Staff


def er_ansvarlig(kunde: Client, medarbejder: Staff) -> bool:
    return kunde.ansvarlig_medarbejder_id is not None and kunde.ansvarlig_medarbejder_id == medarbejder.id


def er_assistent(kunde: Client, medarbejder: Staff) -> bool:
    return kunde.assistent_id is not None and kunde.assistent_id == medarbejder.id


def arbejder_paa(kunde: Client, medarbejder: Staff) -> bool:
    """Ansvarlig eller assistent på kunden – de to har samme adgang."""
    return er_ansvarlig(kunde, medarbejder) or er_assistent(kunde, medarbejder)
