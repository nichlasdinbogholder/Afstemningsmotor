"""Samler alle tabeller, så Alembic og testene kender dem alle."""

from app.afstemning.models import AabenPostFund
from app.audit.models import AuditLog
from app.crm.models import Document, Handover, Note, Task, TimeEntry
from app.jobs.models import Job
from app.kontoudtog.models import Statement, StatementLine
from app.kunder.models import Client, Contact, Credential
from app.opkraevning.models import (
    BillingPeriod,
    CollectionCase,
    Debtor,
    Delivery,
    DunningSkip,
    DunningStep,
    FeeRevenue,
    InstallmentLine,
    InstallmentPlan,
    Invoice,
    InvoicePayment,
    PaymentAllocation,
    ReferenceRate,
)
from app.personale.models import Staff
from app.rules.models import Finding, FindingEvent, RuleRun
from app.regnskab.models import Account, CustomerCache, EntryCache, OpenEntryCache, SupplierCache
from app.synk.models import SyncState

__all__ = [
    "BillingPeriod",
    "CollectionCase",
    "Debtor",
    "Delivery",
    "DunningSkip",
    "DunningStep",
    "FeeRevenue",
    "InstallmentLine",
    "InstallmentPlan",
    "Invoice",
    "InvoicePayment",
    "PaymentAllocation",
    "ReferenceRate",
    "AabenPostFund",
    "Account",
    "AuditLog",
    "Client",
    "Contact",
    "Credential",
    "CustomerCache",
    "Document",
    "EntryCache",
    "Finding",
    "FindingEvent",
    "RuleRun",
    "Handover",
    "Job",
    "Note",
    "OpenEntryCache",
    "Staff",
    "Statement",
    "StatementLine",
    "SupplierCache",
    "SyncState",
    "Task",
    "TimeEntry",
]
