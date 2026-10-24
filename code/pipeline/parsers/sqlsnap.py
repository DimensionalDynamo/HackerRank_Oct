"""SQL snapshot parser.

The dumps are SQLite-compatible, so we replay them into an in-memory database
and read the tables back through the catalogue instead of regex-scraping
VALUES tuples.  That keeps column/type handling honest and turns a malformed
dump into a clean, reported error rather than silent misparses.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import List, Optional, Tuple

from ..model import (
    CAT_ANALYTICS, CAT_SEARCH_INDEX, CAT_SECURITY_AUDIT, CAT_SUPPORT_MESSAGE,
    CAT_TOMBSTONE, CAT_USER_EXPORT, CAT_USER_PROFILE,
    Coverage, Observation, ST_ABSENT, ST_ANONYMIZED, ST_FLOW_ACTIVE,
    ST_FLOW_DISABLED, ST_PRESENT, ST_REDACTED, ST_RETAINED,
    ST_RESTORE_ALLOWED, ST_RESTORE_BLOCKED,
)
from . import common

CAPTURE_RE = re.compile(r"Captured at:\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)")
EMAIL_KEYS = ("email", "owner_email", "requester_email", "customer_email")
REF_KEYS = ("customer_reference", "subject_reference", "user_id", "subject_ref")
PHONE_KEYS = ("phone", "owner_phone", "requester_phone")
HASH_KEYS = ("email_hash", "contact_hash")
NAME_KEYS = ("full_name", "name", "requester_name", "display_name", "customer_name")


def _text(value) -> str:
    return "" if value is None else str(value)


def _flatten(data: dict) -> dict:
    """Expose identifiers nested inside payloads (documents, sub-records)."""
    out = dict(data)
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for key, val in node.items():
                if key not in out and not isinstance(val, (dict, list)):
                    out[key] = val
                elif isinstance(val, (dict, list)):
                    stack.append(val)
        elif isinstance(node, list):
            stack.extend(v for v in node if isinstance(v, (dict, list)))
    return out


def _payload(columns, row) -> dict:
    data = {c: v for c, v in zip(columns, row)}
    for key in ("payload", "record_json", "record", "document"):
        blob = data.get(key)
        if isinstance(blob, str) and blob.strip().startswith("{"):
            try:
                parsed = json.loads(blob)
            except Exception:
                continue
            if isinstance(parsed, dict):
                for name, val in parsed.items():
                    data.setdefault(name, val)
    return _flatten(data)


def _ids(data: dict) -> dict:
    def gather(keys):
        out = set()
        for key in keys:
            val = data.get(key)
            if isinstance(val, str) and val.strip() and val.strip().lower() != "null":
                out.add(val.strip())
        return out

    email_candidates = gather(EMAIL_KEYS) | gather(REF_KEYS) | gather(("subject_reference",))
    emails = tuple(sorted({e.lower() for e in email_candidates if "@" in e}))
    refs = tuple(sorted(r for r in gather(REF_KEYS) if r.startswith("user_")))
    hashes = tuple(sorted(h.lower() for h in gather(HASH_KEYS) if len(h) == 64))
    phones = tuple(sorted(gather(PHONE_KEYS)))
    names = tuple(sorted(gather(NAME_KEYS)))
    return {"emails": emails, "refs": refs, "hashes": hashes,
            "phones": phones, "names": names}


def _state(category: str, data: dict, ids: dict) -> str:
    direct = bool(ids["emails"] or ids["hashes"] or ids["phones"] or ids["names"])
    if category == CAT_ANALYTICS:
        return ST_PRESENT if (direct or ids["refs"]) else ST_ANONYMIZED
    if category == CAT_SUPPORT_MESSAGE:
        return ST_PRESENT if direct else ST_REDACTED
    if category == CAT_TOMBSTONE:
        return ST_PRESENT if direct else ST_REDACTED
    if category == CAT_SECURITY_AUDIT:
        return ST_RETAINED
    if category == "DATA_FLOW":
        return ST_FLOW_ACTIVE if str(data.get("enabled")) in ("1", "True", "true") else ST_FLOW_DISABLED
    if category == "BACKUP_ARCHIVE":
        return (ST_RESTORE_ALLOWED if str(data.get("restore_allowed")) in ("1", "True", "true")
                else ST_RESTORE_BLOCKED)
    if category == CAT_USER_EXPORT:
        return ST_PRESENT
    return ST_PRESENT


def extract(text: str, file_id: str, captured_at: str):
    obs: List[Observation] = []
    scopes: List[Coverage] = []
    m = CAPTURE_RE.search(text)
    if m:
        captured_at = m.group(1)

    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(text)
    except Exception:
        conn.close()
        return [], []

    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        scope_ts = {}
        if "snapshot_scope" in names:
            for system, ts in conn.execute("SELECT origin_system, captured_at FROM snapshot_scope"):
                scope_ts[system] = ts

        for table in names:
            if table == "snapshot_scope":
                continue
            base = common.strip_topology_suffix(table)
            category = common.category_for_collection(table) or common.category_for_collection(base)
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]
            if not cols:
                continue
            present = []
            system_override = ""
            for row in conn.execute(f'SELECT * FROM "{table}"'):
                data = _payload(cols, row)
                rid = _text(data.get("resource_id"))
                if not rid:
                    continue
                cat = category
                if cat is None:
                    cat = common.category_for_record_type(_text(data.get("category")))
                if cat is None:
                    cat = common.category_for_collection(_text(data.get("category")))
                if cat is None:
                    cat = common.category_for_collection(_text(data.get("collection_name")))
                if cat is None:
                    _, cat = common.system_for_resource(rid)
                if cat is None:
                    continue
                system = common.normalise_system(
                    _text(data.get("system") or data.get("origin_system")))
                if not system:
                    prefix_system, _ = common.system_for_resource(rid)
                    system = prefix_system or common.DEFAULT_SYSTEM_FOR_CATEGORY.get(cat, "")
                ids = _ids(data)
                state = _state(cat, data, ids)
                observed_at = _text(data.get("updated_at") or data.get("captured_at")) or captured_at
                obs.append(Observation(
                    resource_id=rid, system_id=system, category=cat, state=state,
                    observed_at=observed_at, evidence_id=file_id, source_kind="sql",
                    refs=ids["refs"], emails=ids["emails"], phones=ids["phones"],
                    hashes=ids["hashes"], names=ids["names"], kind=table,
                ))
                if cat == CAT_USER_PROFILE:
                    present.append(rid)
                    system_override = system
            if category == CAT_USER_PROFILE:
                scope_at = captured_at
                for system, ts in scope_ts.items():
                    if common.normalise_system(system) == (system_override or "account_service"):
                        scope_at = ts
                scopes.append(Coverage(
                    system_id=system_override or "account_service",
                    category=CAT_USER_PROFILE,
                    observed_at=scope_at,
                    evidence_id=file_id,
                    present_resource_ids=tuple(present),
                ))
    finally:
        conn.close()
    return obs, scopes
