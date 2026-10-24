"""Identity resolution: request e-mail -> user_id -> full identifier bundle.

The request e-mail may be the account's *current* address or one of its
historical addresses (recorded in user_audit_log.csv).  A subject's identifier
bundle is the union of every identifier the person has ever been known by
inside the supplied data.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

from . import dataset
from .model import Subject

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def normalise_email(value: str) -> str:
    return (value or "").strip().strip('"').strip("'").lower()


def normalise_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    # Keep a stable representation; drop a leading country-code separator only
    # if it is unambiguous (leading zeros are significant in some regions).
    return digits


def normalise_hash(value: str) -> str:
    return (value or "").strip().lower()


def normalise_name(value: str) -> str:
    return re.sub(r"[^a-z ]", "", (value or "").lower()).strip()


def extract_emails(text: str) -> Tuple[str, ...]:
    return tuple(sorted({normalise_email(m) for m in _EMAIL_RE.findall(text or "")}))


class IdentityIndex:
    """Resolves request e-mails to users and builds identifier bundles."""

    def __init__(self) -> None:
        self.identities: List[dataset.Identity] = dataset.load_identities()
        self.audit_log: List[dataset.AuditLogEntry] = dataset.load_audit_log()

        self._by_email: Dict[str, dataset.Identity] = {}
        for ident in self.identities:
            self._by_email.setdefault(normalise_email(ident.email), ident)

        self._historical_email: Dict[str, str] = {}
        self._audit_by_user: Dict[str, List[dataset.AuditLogEntry]] = defaultdict(list)
        for entry in self.audit_log:
            self._audit_by_user[entry.user_id].append(entry)
            if entry.field_name == "email" and entry.old_value:
                self._historical_email.setdefault(
                    normalise_email(entry.old_value), entry.user_id
                )

        self._subjects: Dict[str, Subject] = {}

    # -- resolution ---------------------------------------------------------
    def resolve(self, email: str) -> Tuple[Optional[str], str]:
        """Return (user_id, how) for a request e-mail."""
        key = normalise_email(email)
        ident = self._by_email.get(key)
        if ident is not None:
            return ident.user_id, "current_email"
        user_id = self._historical_email.get(key)
        if user_id is not None:
            return user_id, "historical_email"
        return None, "unresolved"

    # -- bundles ------------------------------------------------------------
    def subject(self, user_id: str) -> Subject:
        cached = self._subjects.get(user_id)
        if cached is not None:
            return cached

        ident = next((i for i in self.identities if i.user_id == user_id), None)
        emails: Set[str] = set()
        phones: Set[str] = set()
        hashes: Set[str] = set()
        names: Set[str] = set()

        if ident is not None:
            emails.add(normalise_email(ident.email))
            if ident.phone:
                phones.add(normalise_phone(ident.phone))
            if ident.email_hash:
                hashes.add(normalise_hash(ident.email_hash))
            if ident.name:
                names.add(normalise_name(ident.name))

        for entry in self._audit_by_user.get(user_id, []):
            old = entry.old_value or ""
            if entry.field_name == "email" and old:
                emails.add(normalise_email(old))
            elif entry.field_name == "email_hash" and old:
                hashes.add(normalise_hash(old))
            elif entry.field_name == "phone" and old:
                phones.add(normalise_phone(old))
            elif entry.field_name == "name" and old:
                names.add(normalise_name(old))

        # Secondary identifiers derived from a historical address are still the
        # same person; keep the hash of the *current* address too.
        bundle = Subject(
            user_id=user_id,
            emails=tuple(sorted(e for e in emails if e)),
            phones=tuple(sorted(p for p in phones if p)),
            hashes=tuple(sorted(h for h in hashes if h)),
            names=tuple(sorted(n for n in names if n)),
        )
        self._subjects[user_id] = bundle
        return bundle

    def subject_for_email(self, email: str) -> Tuple[Optional[Subject], str]:
        user_id, how = self.resolve(email)
        if user_id is None:
            return None, how
        subject = self.subject(user_id)
        # Preserve the exact address that was used in the request even when it
        # is not in the identity table (it should be, but be defensive).
        if normalise_email(email) not in subject.emails:
            subject = Subject(
                user_id=subject.user_id,
                emails=tuple(sorted(set(subject.emails) | {normalise_email(email)})),
                phones=subject.phones,
                hashes=subject.hashes,
                names=subject.names,
                resolution=how,
            )
        else:
            subject = Subject(
                user_id=subject.user_id,
                emails=subject.emails,
                phones=subject.phones,
                hashes=subject.hashes,
                names=subject.names,
                resolution=how,
            )
        return subject, how

    def all_user_ids(self) -> Iterable[str]:
        return (i.user_id for i in self.identities)
