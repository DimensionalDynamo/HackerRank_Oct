"""Policy application: deletion_rules.csv -> required end state -> step plan.

``deletion_rules.csv`` states the treatment each data category requires.  For a
record category that means a target state (gone / redacted / anonymized /
retained); for a process category it means a target operating state (flow
disabled, restore blocked).  This module is the only place that decides
whether an observed state violates policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from . import dataset
from .model import (
    CAT_ANALYTICS, CAT_BACKUP, CAT_CACHE, CAT_EMAIL_LOG, CAT_LEGAL_HOLD,
    CAT_MARKETING, CAT_SEARCH_INDEX, CAT_SECONDARY_IDENTIFIER,
    CAT_SECURITY_AUDIT, CAT_SUPPORT_ATTACHMENT, CAT_SUPPORT_MESSAGE,
    CAT_TELEMETRY, CAT_TOMBSTONE, CAT_USER_EXPORT, CAT_USER_PROFILE,
    ST_ABSENT, ST_ANONYMIZED, ST_FLOW_ACTIVE, ST_FLOW_DISABLED, ST_PRESENT,
    ST_REDACTED, ST_RETAINED, ST_RESTORE_ALLOWED, ST_RESTORE_BLOCKED,
)

# category -> the action named in deletion_rules.csv
_FALLBACK_ACTIONS = {
    CAT_USER_PROFILE: "DELETE",
    CAT_SECONDARY_IDENTIFIER: "DELETE",
    CAT_SEARCH_INDEX: "DELETE",
    CAT_CACHE: "DELETE",
    CAT_SUPPORT_MESSAGE: "REDACT",
    CAT_SUPPORT_ATTACHMENT: "DELETE",
    CAT_ANALYTICS: "ANONYMIZE",
    CAT_TELEMETRY: "ANONYMIZE",
    CAT_USER_EXPORT: "DELETE",
    CAT_MARKETING: "DELETE",
    CAT_EMAIL_LOG: "REDACT",
    CAT_SECURITY_AUDIT: "RETAIN",
    CAT_LEGAL_HOLD: "RETAIN",
    CAT_BACKUP: "RETAIN",
    CAT_TOMBSTONE: "REDACT",
    "DATA_FLOW": "DISABLE_FLOW",
}


@dataclass(frozen=True)
class Assessment:
    """Whether one resource complies, and the steps policy requires for it."""

    compliant: bool
    required_action: str
    finding_operation: Optional[str]
    remediation_operation: Optional[str]
    verification_operation: Optional[str]
    reason: str


class Policy:
    def __init__(self) -> None:
        self.actions: Dict[str, str] = dict(_FALLBACK_ACTIONS)
        for rule in dataset.load_rules():
            action = rule.required_action.strip().upper()
            if rule.data_category in self.actions:
                # The CSV is authoritative; RETAIN on a backup archive still
                # requires the restore path to be blocked (rule_14 condition).
                self.actions[rule.data_category] = action
        self.actions[CAT_BACKUP] = "RETAIN"
        self.rules: List[dataset.Rule] = dataset.load_rules()

    # -- helpers -----------------------------------------------------------
    def required_action(self, category: str) -> str:
        return self.actions.get(category, "DELETE")

    def assess(self, category: str, state: str) -> Assessment:
        action = self.required_action(category)

        if category == "DATA_FLOW" or action == "DISABLE_FLOW":
            if state == ST_FLOW_DISABLED:
                return Assessment(True, "DISABLE_FLOW", None, None,
                                  "VERIFY_DATA_FLOW_DISABLED", "flow already disabled")
            return Assessment(False, "DISABLE_FLOW", "REPORT_ACTIVE_DATA_FLOW",
                              "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED",
                              "connector can still copy subject data")

        if category == CAT_BACKUP or action == "RETAIN" and category == CAT_BACKUP:
            if state == ST_RESTORE_BLOCKED:
                return Assessment(True, "BLOCK_RESTORE", None, None,
                                  "VERIFY_RESTORE_BLOCKED", "restore already blocked")
            return Assessment(False, "BLOCK_RESTORE", "REPORT_RESTORE_RISK",
                              "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED",
                              "archive could restore the deleted profile")

        if action == "RETAIN":
            if state == ST_ABSENT:
                return Assessment(True, "RETAIN", None, None, None,
                                  "retained record absent; nothing to preserve")
            return Assessment(True, "RETAIN", None, None,
                              "VERIFY_RECORD_PRESENT", "record must remain available")

        if action == "DELETE":
            if state == ST_ABSENT:
                return Assessment(True, "DELETE", None, None,
                                  "VERIFY_RECORD_ABSENT", "record already removed")
            return Assessment(False, "DELETE", "REPORT_REMAINING_RECORD",
                              "DELETE_RECORD", "VERIFY_RECORD_ABSENT",
                              "record should have been deleted")

        if action == "REDACT":
            if state == ST_REDACTED:
                return Assessment(True, "REDACT", None, None,
                                  "VERIFY_RECORD_REDACTED", "identifiers already removed")
            return Assessment(False, "REDACT", "REPORT_EXPOSED_PII",
                              "REDACT_RECORD", "VERIFY_RECORD_REDACTED",
                              "record still exposes direct identifiers")

        if action == "ANONYMIZE":
            if state == ST_ANONYMIZED:
                return Assessment(True, "ANONYMIZE", None, None,
                                  "VERIFY_RECORD_ANONYMIZED", "record already unlinked")
            return Assessment(False, "ANONYMIZE", "REPORT_EXPOSED_PII",
                              "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED",
                              "record still attributable to the subject")

        return Assessment(False, action, "REPORT_REMAINING_RECORD",
                          "DELETE_RECORD", "VERIFY_RECORD_ABSENT",
                          f"unmapped action {action}")
