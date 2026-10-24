"""Plain-text business documents.

The corpus contains one shift-handover sheet whose rows are already a
category/state matrix:

    2026-10-01T12:00:00Z | account_service | account_146 | USER_PROFILE | NOT_FOUND | e.lindberg18@yahoo.com

That is the highest-quality evidence in the whole dataset because the category
is stated explicitly rather than inferred from a table name.
"""
from __future__ import annotations

import re
from typing import List, Tuple

from ..model import (
    CAT_BACKUP, CAT_SEARCH_INDEX, CAT_SECURITY_AUDIT,
    Coverage, Observation, ST_ABSENT, ST_ANONYMIZED, ST_FLOW_ACTIVE,
    ST_FLOW_DISABLED, ST_PRESENT, ST_REDACTED, ST_RETAINED,
    ST_RESTORE_ALLOWED, ST_RESTORE_BLOCKED,
)
from . import common

ROW_RE = re.compile(
    r"^\s*-?\s*(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s*\|\s*"
    r"(?P<system>[a-z_]+)\s*\|\s*(?P<resource>[a-z0-9_]+)\s*\|\s*"
    r"(?P<category>[A-Z_]+)\s*\|\s*(?P<state>[A-Z_]+)\s*\|\s*(?P<identifier>.+?)\s*$"
)
SNAPSHOT_RE = re.compile(r"Snapshot:\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)")


def _state(category: str, raw: str) -> str:
    raw = raw.upper()
    if raw in ("NOT_FOUND", "MISSING"):
        return ST_ABSENT
    if raw in ("ANONYMIZED",):
        return ST_ANONYMIZED
    if raw in ("REDACTED",):
        return ST_REDACTED
    if raw in ("BLOCKED",):
        return ST_RESTORE_BLOCKED
    if raw in ("ACTIVE",):
        return ST_FLOW_ACTIVE if category == "DATA_FLOW" else ST_RESTORE_ALLOWED
    if raw in ("DISABLED", "INACTIVE"):
        return ST_FLOW_DISABLED
    if raw in ("IDENTIFIABLE", "EXPOSED"):
        return ST_PRESENT
    if raw == "FOUND":
        if category == CAT_SECURITY_AUDIT:
            return ST_RETAINED
        return ST_PRESENT
    return ST_PRESENT


def extract(text: str, file_id: str, captured_at: str):
    obs: List[Observation] = []
    scopes: List[Coverage] = []
    m = SNAPSHOT_RE.search(text)
    if m:
        captured_at = m.group(1)

    present_accounts: List[str] = []
    saw_account_scope = False

    for line in text.splitlines():
        row = ROW_RE.match(line)
        if not row:
            continue
        category = common.category_for_record_type(row.group("category"))
        if category is None:
            continue
        identifier = row.group("identifier").strip()
        emails = tuple(sorted({e.lower() for e in re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", identifier)}))
        refs = tuple(sorted({r for r in re.findall(r"user_\d+", identifier)}))
        system = common.normalise_system(row.group("system"))
        state = _state(category, row.group("state"))
        rid = row.group("resource")
        obs.append(Observation(
            resource_id=rid,
            system_id=system,
            category=category,
            state=state,
            observed_at=row.group("ts"),
            evidence_id=file_id,
            source_kind="text",
            refs=refs,
            emails=emails,
            kind="handover",
        ))
        if category == "USER_PROFILE":
            saw_account_scope = True
            if state != ST_ABSENT:
                present_accounts.append(rid)

    if saw_account_scope:
        scopes.append(Coverage(
            system_id="account_service",
            category="USER_PROFILE",
            observed_at=captured_at,
            evidence_id=file_id,
            present_resource_ids=tuple(present_accounts),
        ))
    return obs, scopes
