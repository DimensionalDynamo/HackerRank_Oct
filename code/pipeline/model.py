"""Core data types shared by the audit pipeline.

Everything here is deliberately plain: frozen dataclasses, tuples and strings so
that the whole run is deterministic and serialisable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Tuple

# ---------------------------------------------------------------------------
# Phases / operations / states.  Ordering below is the canonical order that the
# problem statement mandates inside each phase.
# ---------------------------------------------------------------------------
PHASE_ORDER = {"FINDING": 0, "REMEDIATION": 1, "VERIFICATION": 2}

OPERATION_ORDER = {
    "REPORT_REMAINING_RECORD": 0,
    "REPORT_EXPOSED_PII": 1,
    "REPORT_ACTIVE_DATA_FLOW": 2,
    "REPORT_RESTORE_RISK": 3,
    "DISABLE_DATA_FLOW": 4,
    "BLOCK_RESTORE": 5,
    "DELETE_RECORD": 6,
    "REDACT_RECORD": 7,
    "ANONYMIZE_RECORD": 8,
    "VERIFY_RECORD_ABSENT": 9,
    "VERIFY_RECORD_PRESENT": 10,
    "VERIFY_RECORD_REDACTED": 11,
    "VERIFY_RECORD_ANONYMIZED": 12,
    "VERIFY_DATA_FLOW_DISABLED": 13,
    "VERIFY_RESTORE_BLOCKED": 14,
}

PHASE_OF_OPERATION = {
    "REPORT_REMAINING_RECORD": "FINDING",
    "REPORT_EXPOSED_PII": "FINDING",
    "REPORT_ACTIVE_DATA_FLOW": "FINDING",
    "REPORT_RESTORE_RISK": "FINDING",
    "DISABLE_DATA_FLOW": "REMEDIATION",
    "BLOCK_RESTORE": "REMEDIATION",
    "DELETE_RECORD": "REMEDIATION",
    "REDACT_RECORD": "REMEDIATION",
    "ANONYMIZE_RECORD": "REMEDIATION",
    "VERIFY_RECORD_ABSENT": "VERIFICATION",
    "VERIFY_RECORD_PRESENT": "VERIFICATION",
    "VERIFY_RECORD_REDACTED": "VERIFICATION",
    "VERIFY_RECORD_ANONYMIZED": "VERIFICATION",
    "VERIFY_DATA_FLOW_DISABLED": "VERIFICATION",
    "VERIFY_RESTORE_BLOCKED": "VERIFICATION",
}

STATE_OF_OPERATION = {
    "REPORT_REMAINING_RECORD": "PRESENT",
    "REPORT_EXPOSED_PII": "PRESENT",
    "REPORT_ACTIVE_DATA_FLOW": "ACTIVE",
    "REPORT_RESTORE_RISK": "ACTIVE",
    "DISABLE_DATA_FLOW": "DISABLED",
    "BLOCK_RESTORE": "BLOCKED",
    "DELETE_RECORD": "ABSENT",
    "REDACT_RECORD": "REDACTED",
    "ANONYMIZE_RECORD": "ANONYMIZED",
    "VERIFY_RECORD_ABSENT": "ABSENT",
    "VERIFY_RECORD_PRESENT": "PRESENT",
    "VERIFY_RECORD_REDACTED": "REDACTED",
    "VERIFY_RECORD_ANONYMIZED": "ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED": "DISABLED",
    "VERIFY_RESTORE_BLOCKED": "BLOCKED",
}

# ---------------------------------------------------------------------------
# Data categories (mirrors dataset/deletion_rules.csv data_category values)
# ---------------------------------------------------------------------------
CAT_USER_PROFILE = "USER_PROFILE"
CAT_SECONDARY_IDENTIFIER = "SECONDARY_IDENTIFIER"
CAT_SEARCH_INDEX = "SEARCH_INDEX_ENTRY"
CAT_CACHE = "CACHE_ENTRY"
CAT_SUPPORT_MESSAGE = "SUPPORT_MESSAGE"
CAT_SUPPORT_ATTACHMENT = "SUPPORT_ATTACHMENT"
CAT_ANALYTICS = "ANALYTICS_PROFILE"
CAT_TELEMETRY = "PRODUCT_TELEMETRY"
CAT_USER_EXPORT = "USER_EXPORT"
CAT_MARKETING = "MARKETING_PROFILE"
CAT_EMAIL_LOG = "EMAIL_DELIVERY_LOG"
CAT_SECURITY_AUDIT = "SECURITY_AUDIT_LOG"
CAT_LEGAL_HOLD = "LEGAL_HOLD_RECORD"
CAT_BACKUP = "BACKUP_ARCHIVE"
CAT_TOMBSTONE = "ACCOUNT_TOMBSTONE"

# Categories whose compliance is expressed as a record state (rather than a
# process state such as a connector or a backup policy).
RECORD_CATEGORIES = frozenset(
    {
        CAT_USER_PROFILE,
        CAT_SECONDARY_IDENTIFIER,
        CAT_SEARCH_INDEX,
        CAT_CACHE,
        CAT_SUPPORT_MESSAGE,
        CAT_SUPPORT_ATTACHMENT,
        CAT_ANALYTICS,
        CAT_TELEMETRY,
        CAT_USER_EXPORT,
        CAT_MARKETING,
        CAT_EMAIL_LOG,
        CAT_SECURITY_AUDIT,
        CAT_LEGAL_HOLD,
        CAT_TOMBSTONE,
    }
)

# ---------------------------------------------------------------------------
# Resource states
# ---------------------------------------------------------------------------
ST_PRESENT = "PRESENT"          # record exists and still carries identifiers
ST_ABSENT = "ABSENT"            # record does not exist
ST_REDACTED = "REDACTED"        # exists, direct identifiers removed
ST_ANONYMIZED = "ANONYMIZED"    # exists, unlinked from the subject
ST_FLOW_ACTIVE = "FLOW_ACTIVE"
ST_FLOW_DISABLED = "FLOW_DISABLED"
ST_RESTORE_ALLOWED = "RESTORE_ALLOWED"
ST_RESTORE_BLOCKED = "RESTORE_BLOCKED"
ST_RETAINED = "RETAINED"
ST_LINK = "LINK"                # identity link only; not a record state


@dataclass(frozen=True)
class Coverage:
    """Scope of a point-in-time snapshot: which accounts it could see.

    A snapshot that covers ``account_service``/``USER_PROFILE`` and does not
    contain an account id is positive evidence that the account was absent at
    that instant.
    """

    system_id: str
    category: str
    observed_at: str
    evidence_id: str
    present_resource_ids: Tuple[str, ...]


@dataclass(frozen=True)
class Observation:
    """One fact extracted from one evidence artefact about one resource."""

    resource_id: str
    system_id: str
    category: str
    state: str
    observed_at: str                     # ISO8601 Z string, may be ""
    evidence_id: str                     # file_id / activity_id / audit_id
    source_kind: str                     # json|sql|log|text|pdf|image|activity|audit_log
    refs: Tuple[str, ...] = ()           # user_<n> style references
    emails: Tuple[str, ...] = ()
    phones: Tuple[str, ...] = ()
    hashes: Tuple[str, ...] = ()
    names: Tuple[str, ...] = ()
    # Free-form origin hints used for policy decisions (e.g. record_kind)
    kind: str = ""
    detail: str = ""

    def identifiers(self) -> FrozenSet[str]:
        out = set()
        out.update(self.refs)
        out.update(self.emails)
        out.update(self.phones)
        out.update(self.hashes)
        return frozenset(out)


@dataclass
class Subject:
    """A resolved person: the deletion subject of a case."""

    user_id: str
    emails: Tuple[str, ...] = ()
    phones: Tuple[str, ...] = ()
    hashes: Tuple[str, ...] = ()
    names: Tuple[str, ...] = ()
    resolution: str = ""                 # how the user_id was resolved

    def email_set(self) -> FrozenSet[str]:
        return frozenset(e.lower() for e in self.emails)


@dataclass
class Finding:
    """A policy violation plus the remediation it requires."""

    operation: str                       # REPORT_* operation
    remediation: str                     # DISABLE_DATA_FLOW / BLOCK_RESTORE / ...
    system_id: str
    resource_id: str
    evidence_id: str
    reason: str = ""


@dataclass
class Verification:
    operation: str
    system_id: str
    resource_id: str
    evidence_id: str = ""
    reason: str = ""


@dataclass
class Step:
    step_no: int
    phase: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "step_no": self.step_no,
            "phase": self.phase,
            "system_id": self.system_id,
            "resource_id": self.resource_id,
            "operation": self.operation,
            "state": self.state,
            "evidence_id": self.evidence_id,
        }

    def sort_key(self):
        return (
            PHASE_ORDER[self.phase],
            OPERATION_ORDER[self.operation],
            self.system_id,
            self.resource_id,
        )


@dataclass
class CaseResult:
    case_id: str
    status: str
    steps: Tuple[Step, ...] = ()

    def as_row(self):
        import json

        return {
            "case_id": self.case_id,
            "deletion_status": self.status,
            "steps_json": json.dumps(
                [s.as_dict() for s in self.steps],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        }
