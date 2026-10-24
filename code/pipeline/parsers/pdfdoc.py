"""PDF business documents -> identity links.

PDFs in this corpus are business artefacts (customer statements, compliance
journal extracts, retention registers, support attachments).  They do not
contain deletion verdicts, but they do state which person a resource id
belongs to, which is exactly the identity-resolution evidence the audit needs.
"""
from __future__ import annotations

import re
from typing import List

from ..model import (CAT_LEGAL_HOLD, CAT_SUPPORT_ATTACHMENT,
                     CAT_SUPPORT_MESSAGE, Observation, ST_LINK, ST_PRESENT)
from . import common

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
RESOURCE_RE = re.compile(
    r"\b(account_\d+|search_\d+|analytics_\d+|support_\d+|export_\d+|data_flow_\d+|"
    r"backup_\d+|audit_record_\d+|tombstone_\d+|marketing_\d+|object_\d+|cache_\d+)\b"
)
USER_RE = re.compile(r"\buser_\d+\b")
PHONE_RE = re.compile(r"\+\d[\d\s\-()]{7,}\d")
HOLD_ROW_RE = re.compile(r"\|\s*(user_\d+)\s*\|.*?Retain until ([0-9]{2} [A-Z][a-z]{2} [0-9]{4})")


def _pdf_text(path: str) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return ""
    try:
        reader = PdfReader(path)
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""


def extract_text_document(text: str, file_id: str, captured_at: str):
    obs: List[Observation] = []
    emails = tuple(sorted({e.lower() for e in EMAIL_RE.findall(text)}))
    refs = tuple(sorted(set(USER_RE.findall(text))))
    resources = sorted(set(RESOURCE_RE.findall(text)))
    phones = tuple(sorted(set(m.strip() for m in PHONE_RE.findall(text))))

    if resources and (emails or refs):
        for rid in resources:
            system, category = common.system_for_resource(rid)
            # A PDF filed under the support platform is itself the uploaded
            # attachment, so the document proves that the attachment object
            # still exists.  Other resources named inside a document are only
            # identity links.
            if category == CAT_SUPPORT_MESSAGE:
                obs.append(Observation(
                    resource_id=rid, system_id=system,
                    category=CAT_SUPPORT_ATTACHMENT, state=ST_PRESENT,
                    observed_at=captured_at, evidence_id=file_id, source_kind="pdf",
                    refs=refs, emails=emails, phones=phones, kind="attachment",
                ))
                continue
            obs.append(Observation(
                resource_id=rid, system_id=system, category=category, state=ST_LINK,
                observed_at=captured_at, evidence_id=file_id, source_kind="pdf",
                refs=refs, emails=emails, phones=phones, kind="document",
            ))

    # Retention register: rows that pin a legal hold to a subject.
    for line in text.splitlines():
        row = HOLD_ROW_RE.search(line)
        if not row:
            continue
        obs.append(Observation(
            resource_id=row.group(1), system_id="legal_service",
            category=CAT_LEGAL_HOLD, state=ST_LINK, observed_at=captured_at,
            evidence_id=file_id, source_kind="pdf", refs=(row.group(1),),
            kind="legal_hold",
        ))
    return obs


def extract(path: str, file_id: str, captured_at: str) -> List[Observation]:
    return extract_text_document(_pdf_text(path), file_id, captured_at)
