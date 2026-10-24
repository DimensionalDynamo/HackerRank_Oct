"""JSON evidence parser.

Four shapes occur:

* ``service_exports``  - per-service readback export (records grouped by service)
* ``snapshots``        - point-in-time snapshot bundle of several collections
* ``records`` + ``record_type``/``result`` - audit-ledger style flat export
* ``records`` + ``collection``             - single-collection export
"""
from __future__ import annotations

import json
from typing import Iterable, List, Optional, Tuple

from ..model import (
    CAT_ANALYTICS, CAT_SEARCH_INDEX, CAT_SECURITY_AUDIT, CAT_SUPPORT_MESSAGE,
    CAT_TOMBSTONE, CAT_USER_EXPORT, CAT_USER_PROFILE,
    Coverage, Observation, ST_ABSENT, ST_ANONYMIZED, ST_FLOW_ACTIVE,
    ST_FLOW_DISABLED, ST_PRESENT, ST_REDACTED, ST_RETAINED,
    ST_RESTORE_ALLOWED, ST_RESTORE_BLOCKED,
)
from . import common

EMAIL_KEYS = ("email", "owner_email", "requester_email", "customer_email")
REF_KEYS = ("customer_reference", "subject_reference", "user_id", "subject_ref")
PHONE_KEYS = ("phone", "owner_phone", "requester_phone")
HASH_KEYS = ("email_hash", "contact_hash")
NAME_KEYS = ("full_name", "name", "requester_name", "display_name", "customer_name")


def _vals(record: dict, keys: Iterable[str]) -> Tuple[str, ...]:
    out = set()
    for key in keys:
        val = record.get(key)
        if isinstance(val, str) and val.strip() and val.strip().lower() != "null":
            out.add(val.strip())
    return tuple(sorted(out))


def _emails(record: dict) -> Tuple[str, ...]:
    """E-mail-ish values, wherever the export chooses to put them.

    Ledger-style exports put the customer address in ``subject_reference``
    rather than in an ``email`` field, so the shape of the value decides.
    """
    candidates = _vals(record, EMAIL_KEYS + REF_KEYS + ("subject_reference",))
    return tuple(sorted({c.lower() for c in candidates if "@" in c}))


def _refs(record: dict) -> Tuple[str, ...]:
    out = set()
    for key in REF_KEYS:
        val = record.get(key)
        if isinstance(val, str) and val.startswith("user_"):
            out.add(val.strip())
    return tuple(sorted(out))


def _identifiers(record: dict) -> dict:
    emails = _emails(record)
    hashes = tuple(sorted(h.lower() for h in _vals(record, HASH_KEYS) if len(h.strip()) == 64))
    phones = tuple(sorted(p for p in _vals(record, PHONE_KEYS)))
    names = tuple(sorted(n for n in _vals(record, NAME_KEYS)))
    return {"emails": emails, "hashes": hashes, "phones": phones,
            "names": names, "refs": _refs(record)}


def _state_for(category: str, record: dict, ids: dict) -> Optional[str]:
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
        enabled = record.get("enabled")
        if isinstance(enabled, bool):
            return ST_FLOW_ACTIVE if enabled else ST_FLOW_DISABLED
        return ST_FLOW_ACTIVE if str(enabled).lower() in ("1", "true") else ST_FLOW_DISABLED
    if category == "BACKUP_ARCHIVE":
        allowed = record.get("restore_allowed")
        if isinstance(allowed, bool):
            return ST_RESTORE_ALLOWED if allowed else ST_RESTORE_BLOCKED
        return ST_RESTORE_ALLOWED if str(allowed).lower() in ("1", "true") else ST_RESTORE_BLOCKED
    if category == CAT_USER_EXPORT:
        if record.get("download_available") is False:
            return ST_ABSENT
        return ST_PRESENT
    if category in (CAT_USER_PROFILE, CAT_SEARCH_INDEX):
        return ST_PRESENT
    return ST_PRESENT


def _make(resource_id: str, system: str, category: str, record: dict,
          observed_at: str, file_id: str, kind: str = "") -> Observation:
    ids = _identifiers(record)
    state = _state_for(category, record, ids)
    return Observation(
        resource_id=resource_id,
        system_id=system or common.DEFAULT_SYSTEM_FOR_CATEGORY.get(category, ""),
        category=category,
        state=state or ST_PRESENT,
        observed_at=observed_at,
        evidence_id=file_id,
        source_kind="json",
        refs=ids["refs"],
        emails=ids["emails"],
        phones=ids["phones"],
        hashes=ids["hashes"],
        names=ids["names"],
        kind=kind,
    )


def _search_hits(row: dict) -> List[dict]:
    hits = row.get("hits") or {}
    docs = hits.get("documents") or []
    out = []
    for doc in docs:
        if isinstance(doc, dict):
            out.append(doc)
    return out


def _from_service_exports(doc: dict, file_id: str, captured_at: str):
    obs: List[Observation] = []
    scopes: List[Coverage] = []
    exports = doc.get("service_exports") or {}
    for service, kinds in exports.items():
        if not isinstance(kinds, dict):
            continue
        for kind_name, rows in kinds.items():
            if not isinstance(rows, list):
                continue
            category = common.category_for_collection(kind_name)
            system = common.normalise_system(service)
            present = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                observed_at = (row.get("observed_at") or captured_at or "")
                if kind_name == "profile_lookups":
                    rid = row.get("resource_id") or ""
                    if not rid:
                        continue
                    profile = row.get("profile")
                    status = row.get("status_code")
                    state = ST_ABSENT if (profile in (None, {}) and status in (404, "404")) else ST_PRESENT
                    if state == ST_PRESENT and isinstance(profile, dict):
                        obs.append(_make(rid, system, CAT_USER_PROFILE, profile,
                                         observed_at, file_id))
                    else:
                        obs.append(Observation(
                            resource_id=rid, system_id=system or "account_service",
                            category=CAT_USER_PROFILE, state=ST_ABSENT,
                            observed_at=observed_at, evidence_id=file_id,
                            source_kind="json", detail="profile_lookup",
                        ))
                    continue
                if kind_name == "index_queries":
                    hits = _search_hits(row)
                    if hits:
                        for doc_row in hits:
                            rid = doc_row.get("resource_id") or row.get("resource_id") or ""
                            if rid:
                                obs.append(_make(rid, system, CAT_SEARCH_INDEX, doc_row,
                                                 observed_at, file_id))
                    elif row.get("resource_id"):
                        obs.append(Observation(
                            resource_id=row["resource_id"], system_id=system or "search_service",
                            category=CAT_SEARCH_INDEX, state=ST_ABSENT,
                            observed_at=observed_at, evidence_id=file_id,
                            source_kind="json", detail="index_query",
                        ))
                    continue

                rid = row.get("resource_id") or ""
                if not rid:
                    continue
                cat = category
                if cat is None:
                    _, cat = common.system_for_resource(rid)
                if cat is None:
                    continue
                obs.append(_make(rid, system, cat, row, observed_at, file_id))
                if cat == CAT_USER_PROFILE:
                    present.append(rid)
            if category == CAT_USER_PROFILE and kind_name == "profile_lookups":
                scopes.append(Coverage(
                    system_id=system or "account_service",
                    category=CAT_USER_PROFILE,
                    observed_at=captured_at,
                    evidence_id=file_id,
                    present_resource_ids=tuple(present),
                ))
    return obs, scopes


def _from_snapshots(doc: dict, file_id: str, captured_at: str):
    obs: List[Observation] = []
    scopes: List[Coverage] = []
    for snap in doc.get("snapshots") or []:
        if not isinstance(snap, dict):
            continue
        collection = snap.get("collection") or ""
        data_store = common.normalise_system(snap.get("data_store") or "")
        observed_at = snap.get("captured_at") or captured_at
        category = common.category_for_collection(collection)
        records = snap.get("records") or []
        present = []
        for record in records:
            if not isinstance(record, dict):
                continue
            rid = record.get("resource_id") or ""
            if not rid:
                continue
            cat = category
            if cat is None:
                _, cat = common.system_for_resource(rid)
            if cat is None:
                continue
            obs.append(_make(rid, data_store, cat, record, observed_at, file_id,
                             kind=f"{collection}"))
            if cat == CAT_USER_PROFILE:
                present.append(rid)
        if category == CAT_USER_PROFILE:
            scopes.append(Coverage(
                system_id=data_store or "account_service",
                category=CAT_USER_PROFILE,
                observed_at=observed_at,
                evidence_id=file_id,
                present_resource_ids=tuple(present),
            ))
    return obs, scopes


def _from_records(doc: dict, file_id: str, captured_at: str):
    obs: List[Observation] = []
    scopes: List[Coverage] = []
    collection = doc.get("collection") or ""
    default_category = common.category_for_collection(collection)
    default_system = common.normalise_system(
        doc.get("origin") or doc.get("source") or "")
    if not default_system.startswith(("account", "search", "analytics", "support",
                                      "export", "backup", "security", "identity",
                                      "integration", "marketing", "cache")):
        default_system = ""
    present = []
    for record in doc.get("records") or []:
        if not isinstance(record, dict):
            continue
        rid = record.get("resource_id") or ""
        if not rid:
            continue
        observed_at = (record.get("recorded_at") or record.get("captured_at")
                       or doc.get("captured_at") or captured_at)
        system = common.normalise_system(record.get("system") or "") or default_system
        category = (common.category_for_record_type(record.get("record_type") or "")
                    or default_category)
        if category is None:
            prefix_system, prefix_cat = common.system_for_resource(rid)
            category = prefix_cat
            system = system or prefix_system
        if not category:
            continue
        result = str(record.get("result") or "").strip().upper()
        if result in ("NOT_FOUND", "MISSING"):
            obs.append(Observation(
                resource_id=rid, system_id=system or common.DEFAULT_SYSTEM_FOR_CATEGORY.get(category, ""),
                category=category, state=ST_ABSENT, observed_at=observed_at,
                evidence_id=file_id, source_kind="json",
                refs=_refs(record),
                emails=_emails(record),
                detail="record_lookup",
            ))
            continue
        obs.append(_make(rid, system, category, record, observed_at, file_id,
                         kind=collection))
        if category == CAT_USER_PROFILE:
            present.append(rid)
    if default_category == CAT_USER_PROFILE:
        scopes.append(Coverage(
            system_id=default_system or "account_service",
            category=CAT_USER_PROFILE,
            observed_at=doc.get("captured_at") or captured_at,
            evidence_id=file_id,
            present_resource_ids=tuple(present),
        ))
    return obs, scopes


def extract(text: str, file_id: str, captured_at: str):
    try:
        doc = json.loads(text)
    except Exception:
        return [], []
    if not isinstance(doc, dict):
        return [], []

    if "service_exports" in doc:
        return _from_service_exports(doc, file_id, captured_at)
    if "snapshots" in doc:
        return _from_snapshots(doc, file_id, captured_at)
    if "records" in doc:
        return _from_records(doc, file_id, captured_at)
    return [], []
