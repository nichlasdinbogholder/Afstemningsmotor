"""Samler alle tabeller, så Alembic og testene kender dem alle."""

from app.audit.models import AuditLog
from app.crm.models import Document, Handover, Note, Task, TimeEntry
from app.jobs.models import Job
from app.kunder.models import Client, Contact, Credential
from app.personale.models import Staff
from app.regnskab.models import Account
from app.synk.models import SyncState

__all__ = [
    "Account",
    "AuditLog",
    "Client",
    "Contact",
    "Credential",
    "Document",
    "Handover",
    "Job",
    "Note",
    "Staff",
    "SyncState",
    "Task",
    "TimeEntry",
]
