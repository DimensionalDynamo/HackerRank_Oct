"""Log-family parser.

Handles every log layout in the corpus (logfmt, JSON lines, RFC5424 syslog,
pipe-delimited, Go runtime logs, bracket layout) by normalising each line with
``keyscan`` and then joining ``*_started`` / ``*_completed`` events on their
trace id, because the subject identifier is printed on the "started" line while
the resource and the observed state are printed on the "completed" line.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..model import CAT_ANALYTICS, CAT_SECURITY_AUDIT, CAT_SUPPORT_ATTACHMENT, CAT_SUPPORT_MESSAGE, Observation, ST_ABSENT, ST_ANONYMIZED, ST_FLOW_ACTIVE, ST_FLOW_DISABLED, ST_PRESENT, ST_REDACTED, ST_RESTORE_ALLOWED, ST_RESTORE_BLOCKED, ST_RETAINED
from . import common
from .keyscan import parse_text

IDENTIFIER_FIELDS = ("lookup_key", "match_key", "scan_key", "subject_ref",
                     "correlation_key", "subject_reference", "customer_email",
                     "requester_email", "owner_email", "email", "matched_value",
                     "subject_key", "customer_key")
REF_FIELDS = ("customer_reference", "subject_reference", "subject_ref", "user_id")
HASH_FIELDS = ("email_hash", "contact_hash")
PHONE_FIELDS = ("phone", "requester_phone", "owner_phone")

TERMINAL_EVENTS = ("profile_lookup_completed", "index_query_completed",
                   "analytics_profile_read_completed", "contact_lookup_completed",
                   "support_ticket_read_completed", "connector_config_read_completed",
                   "restore_policy_read_completed", "audit_record_read_completed",
                   "export_object_read_completed", "attachment_scan_completed",
                   "contact_query_completed", "record_lookup", "message_pii_scan",
                   "attachment_scan", "attachment_pii_scan", "connector_heartbeat",
                   "restore_candidate_evaluated", "restore_policy_check_completed",
                   "analytics_identity_scan")

# Events whose resource is a legacy *contact index* row: the row stores the
# subject's e-mail / e-mail hash so the contact centre can find them.  Under
# deletion_rules.csv that is a SECONDARY_IDENTIFIER copy, not an analytics
# profile, so it is deleted rather than anonymized.
CONTACT_EVENTS = ("contact_lookup_completed", "contact_query_completed")


def _is_email(value: str) -> bool:
    return "@" in (value or "") and "." in (value or "").rsplit("@", 1)[-1]


def _collect(ev: dict, pending: Dict[str, dict]) -> None:
    """Remember identifiers seen on a *_started line, keyed by trace id."""
    trace = ev.get("trace") or ""
    if not trace:
        return
    fields = ev["fields"]
    bucket = pending.setdefault(trace, {})
    for key in IDENTIFIER_FIELDS + REF_FIELDS + HASH_FIELDS + PHONE_FIELDS:
        val = fields.get(key)
        if val and val not in ("null", "None", ""):
            bucket.setdefault(key, val)


def _identifiers(ev: dict, pending: Dict[str, dict]) -> dict:
    fields = dict(pending.get(ev.get("trace") or "", {}))
    for key in IDENTIFIER_FIELDS + REF_FIELDS + HASH_FIELDS + PHONE_FIELDS:
        val = ev["fields"].get(key)
        if val and val not in ("null", "None", ""):
            fields[key] = val

    emails = sorted({v.strip().lower() for k, v in fields.items()
                     if k in IDENTIFIER_FIELDS + PHONE_FIELDS and _is_email(v)})
    refs = sorted({v.strip() for k, v in fields.items()
                   if k in REF_FIELDS and v.startswith("user_")})
    # correlation_key carries *either* a user reference or an e-mail hash
    # depending on the service, so classify it by shape.
    correlation = (fields.get("correlation_key") or "").strip()
    if correlation.startswith("user_") and correlation not in refs:
        refs = sorted(set(refs) | {correlation})
    hashes = sorted({v.strip().lower() for k, v in fields.items()
                     if k in HASH_FIELDS and len(v.strip()) == 64})
    if len(correlation) == 64 and correlation.lower() not in hashes:
        hashes = sorted(set(hashes) | {correlation.lower()})
    phones = sorted({v.strip() for k, v in fields.items()
                     if k in PHONE_FIELDS and not _is_email(v) and any(c.isdigit() for c in v)})
    names = sorted({v.strip() for v in (fields.get("requester_name"), fields.get("full_name"),
                                        fields.get("display_name"))
                    if v and v not in ("null", "None")})
    return {"emails": tuple(emails), "refs": tuple(refs),
            "hashes": tuple(hashes), "phones": tuple(phones), "names": tuple(names)}


def _boolish(value: str) -> Optional[bool]:
    if value is None:
        return None
    val = str(value).strip().lower()
    if val in ("true", "1", "yes", "enabled"):
        return True
    if val in ("false", "0", "no", "disabled"):
        return False
    return None


def _state_from_fields(event: str, system: str, category: str, f: dict,
                       ids: dict) -> Optional[str]:
    rows = f.get("rows")
    http_status = str(f.get("http_status") or "")
    result = (f.get("result") or "").strip().lower()

    if event == "record_lookup":
        res = (f.get("result") or "").strip().upper()
        if res in ("NOT_FOUND", "MISSING"):
            if category in (CAT_SECURITY_AUDIT,):
                return None
            return ST_ABSENT
        if res == "FOUND":
            return ST_RETAINED if category == CAT_SECURITY_AUDIT else ST_PRESENT
        return None

    if event in ("profile_lookup_completed", "profile_lookup"):
        if str(rows) == "0" or http_status == "404":
            return ST_ABSENT
        if str(rows) == "1" or http_status == "200":
            return ST_PRESENT
        return None

    if event in ("index_query_completed", "index_query"):
        if result == "hit":
            return ST_PRESENT
        if result == "miss" or str(f.get("total_hits")) == "0":
            return ST_ABSENT
        return None

    if event == "analytics_identity_scan":
        matched = (f.get("matched_value") or "").strip()
        direct = str(f.get("direct_identifiers") or "").strip()
        if direct in ("1", "2", "3") or (matched and matched.lower() != "null"):
            return ST_PRESENT
        return ST_ANONYMIZED

    if event == "analytics_identity_scan":
        matched = (f.get("matched_value") or "").strip()
        direct = str(f.get("direct_identifiers") or "").strip()
        if direct in ("1", "2", "3") or (matched and matched.lower() != "null"):
            return ST_PRESENT
        return ST_ANONYMIZED

    if event in ("analytics_profile_read_completed", "analytics_profile_read"):
        direct = any(ids[k] for k in ("emails", "phones", "hashes", "refs"))
        flag = (f.get("identifiable") or "").lower()
        if flag in ("true", "false"):
            return ST_PRESENT if flag == "true" else ST_ANONYMIZED
        return ST_PRESENT if direct else ST_ANONYMIZED

    if event in ("contact_lookup_completed", "contact_lookup", "contact_query_completed"):
        # A contact index row that still answers with a match to the subject's
        # e-mail or e-mail hash is a live identifier copy.
        return ST_PRESENT

    if event in ("support_ticket_read_completed", "support_ticket_read"):
        direct = any(ids[k] for k in ("emails", "phones", "names"))
        return ST_PRESENT if direct else ST_REDACTED

    if event in ("connector_config_read_completed", "connector_config_read",
                 "connector_heartbeat"):
        enabled = _boolish(f.get("enabled"))
        if enabled is None:
            return None
        return ST_FLOW_ACTIVE if enabled else ST_FLOW_DISABLED

    if event in ("restore_policy_read_completed", "restore_policy_read"):
        allowed = _boolish(f.get("restore_allowed"))
        if allowed is None:
            return None
        return ST_RESTORE_ALLOWED if allowed else ST_RESTORE_BLOCKED

    if event in ("audit_record_read_completed", "audit_record_read",
                 "audit_record_query_completed"):
        if str(rows) == "0":
            return ST_ABSENT
        return ST_RETAINED

    if event in ("message_pii_scan", "attachment_scan", "pii_scan",
                 "attachment_pii_scan"):
        matched = (f.get("matched_value") or "").strip()
        if matched and matched.lower() not in ("null", "none", ""):
            return ST_PRESENT
        if result in ("clean", "no_match", "none"):
            return ST_REDACTED
        return ST_PRESENT

    if event == "restore_candidate_evaluated":
        eligible = _boolish(f.get("eligible"))
        blocked = _boolish(f.get("deletion_block"))
        if blocked is True:
            return ST_RESTORE_BLOCKED
        if eligible is True:
            return ST_RESTORE_ALLOWED
        if eligible is False:
            return ST_RESTORE_BLOCKED
        return None

    if event in ("export_object_read_completed", "export_object_read"):
        if str(rows) == "0":
            return ST_ABSENT
        return ST_PRESENT

    if event in ("attachment_scan_completed", "attachment_scan"):
        if result in ("found", "hit"):
            return ST_PRESENT
        if result in ("not_found", "miss"):
            return ST_ABSENT
        return None

    return None


def extract(text: str, file_id: str, captured_at: str) -> List[Observation]:
    events = parse_text(text)
    pending: Dict[str, dict] = {}
    out: List[Observation] = []
    for ev in events:
        event = (ev.get("event") or "").strip()
        if not event:
            continue
        if event.endswith("_started"):
            _collect(ev, pending)
            continue
        if event not in TERMINAL_EVENTS:
            continue

        f = ev["fields"]
        resource_id = (f.get("resource_id") or "").strip()
        if not resource_id or resource_id == "null":
            continue

        ids = _identifiers(ev, pending)

        system = common.normalise_system(f.get("system") or ev.get("service") or "")
        prefix_system, prefix_category = common.system_for_resource(resource_id)
        if not system:
            system = prefix_system

        record_type = f.get("record_type") or f.get("category") or ""
        category = common.category_for_record_type(record_type) or prefix_category
        if event in ("analytics_profile_read_completed", "analytics_profile_read",
                     "analytics_identity_scan"):
            category = CAT_ANALYTICS
            system = system or "analytics_platform"
        elif event in CONTACT_EVENTS:
            # A contact-index copy holds the subject's e-mail / e-mail hash so
            # that agents can look them up: a secondary identifier, not a
            # profile and not analytics.  Policy: delete, not anonymize.
            category = "SECONDARY_IDENTIFIER"
        elif event in ("support_ticket_read_completed", "support_ticket_read"):
            category = category or CAT_SUPPORT_MESSAGE
        elif event in ("attachment_scan", "attachment_pii_scan", "attachment_scan_completed"):
            category = CAT_SUPPORT_ATTACHMENT
            system = system or "support_platform"
        elif event in ("message_pii_scan", "pii_scan"):
            category = CAT_SUPPORT_MESSAGE
            system = system or "support_platform"
        elif event == "record_lookup" and record_type:
            category = common.category_for_record_type(record_type) or category

        if not category:
            continue
        if not system:
            system = common.DEFAULT_SYSTEM_FOR_CATEGORY.get(category, "")

        state = _state_from_fields(event, system, category, f, ids)
        if state is None:
            continue

        kind = "contact_index" if event in CONTACT_EVENTS else event

        out.append(
            Observation(
                resource_id=resource_id,
                system_id=system,
                category=category,
                state=state,
                observed_at=ev.get("timestamp") or captured_at,
                evidence_id=file_id,
                source_kind="log",
                refs=ids["refs"],
                emails=ids["emails"],
                phones=ids["phones"],
                hashes=ids["hashes"],
                names=ids["names"],
                kind=kind,
                detail=event,
            )
        )
    return out
