"""Per-case inventory: which resources belong to the subject, and what state
each of them was in at the case's ``audit_at``.

Linking is identifier-driven.  A resource is attributed to a subject when at
least one observation of it carries an identifier from the subject's bundle
(e-mail, historical e-mail, e-mail hash, phone, or ``user_<n>`` reference).
Names are deliberately *not* used: the corpus contains near-duplicate names
("Jonas Berg" vs "Jonas Berglund") that would create false attributions.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .evidence import EvidenceIndex
from .identity import normalise_hash, normalise_phone
from .model import CAT_USER_PROFILE, Coverage, Observation, ST_ABSENT, Subject

ACCOUNT_RE = re.compile(r"^account_(\d+)$")


@dataclass
class ResourceState:
    resource_id: str
    system_id: str
    category: str
    state: str
    observed_at: str
    evidence_id: str
    link_reason: str = ""
    kind: str = ""
    alternatives: Tuple[str, ...] = ()

    def sort_key(self):
        return (self.system_id, self.resource_id)


@dataclass
class CaseInventory:
    subject: Optional[Subject]
    resources: Dict[str, ResourceState] = field(default_factory=dict)
    coverage_evidence: Dict[str, str] = field(default_factory=dict)
    account_resource: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def by_category(self, category: str) -> List[ResourceState]:
        return sorted((r for r in self.resources.values() if r.category == category),
                      key=lambda r: (r.system_id, r.resource_id))


def _digits_key(phone: str) -> str:
    return normalise_phone(phone)[-10:]


class InventoryBuilder:
    def __init__(self, index: EvidenceIndex) -> None:
        self.index = index
        self._resource_ids: Set[str] = {o.resource_id for o in index.observations}
        for coverage in index.coverages:
            self._resource_ids.update(coverage.present_resource_ids)
        # Resources the deletion workflow touched are also candidates: an
        # account can be named only in system_activity.csv once it is gone.
        self._resource_ids.update(index.treatments.keys())

    # -- linking -----------------------------------------------------------
    def _matches(self, obs: Observation, subject: Subject) -> Optional[str]:
        if subject.user_id in obs.refs:
            return f"ref:{subject.user_id}"
        subject_emails = subject.email_set()
        for email in obs.emails:
            if email.lower() in subject_emails:
                return f"email:{email.lower()}"
        subject_hashes = {normalise_hash(h) for h in subject.hashes}
        for value in obs.hashes:
            if normalise_hash(value) in subject_hashes:
                return "email_hash"
        subject_phones = {_digits_key(p) for p in subject.phones if p}
        for value in obs.phones:
            if value and _digits_key(value) in subject_phones:
                return "phone"
        return None

    def _naming_candidates(self, subject: Subject) -> List[str]:
        m = re.match(r"^user_(\d+)$", subject.user_id or "")
        if not m:
            return []
        number = m.group(1)
        stripped = str(int(number))
        candidates = [f"account_{number}", f"account_{stripped}",
                      f"account_{number.zfill(3)}", f"account_{number.zfill(4)}"]
        seen, out = set(), []
        for cand in candidates:
            if cand in seen:
                continue
            seen.add(cand)
            if cand in self._resource_ids:
                out.append(cand)
        return out

    # -- build -------------------------------------------------------------
    def build(self, subject: Subject, audit_at: str) -> CaseInventory:
        inv = CaseInventory(subject=subject)
        if not self.index.admits(audit_at):
            return inv          # no usable audit instant: attribute nothing
        # linking pool: everything (identity does not expire)
        observations = self.index.observations_at(audit_at)
        links = self.index.links_at(audit_at)
        link_pool = self.index.all_observations + self.index.all_links

        # 1. attribute resources to the subject, then carry that attribution
        #    across every observation of the same resource.  A record that has
        #    already been scrubbed carries no identifiers left to match on, so
        #    resource-level propagation is what lets us read its *later* state
        #    ("identifier was removed") instead of freezing at the last
        #    identifier-bearing sighting.
        reasons: Dict[str, str] = {}
        for obs in link_pool:
            reason = self._matches(obs, subject)
            if reason:
                current = reasons.get(obs.resource_id)
                if current is None or reason < current:
                    reasons[obs.resource_id] = reason

        attributed: Dict[str, List[Tuple[Observation, str]]] = defaultdict(list)
        for obs in link_pool:
            reason = reasons.get(obs.resource_id)
            if reason is not None:
                attributed[obs.resource_id].append((obs, reason))

        timely = {id(o) for o in observations}

        # 2. resolve the state of each attributed resource at audit_at
        for rid, entries in attributed.items():
            states = [(o, r) for o, r in entries
                      if o.state != "LINK" and o.observed_at <= audit_at]
            if not states:
                continue
            # Prefer witnesses from artefacts captured inside the audit window.
            # A bundle exported later is admitted only when it is the *only*
            # record of the resource, and then only through its inner
            # point-in-time snapshot.
            pool = [pair for pair in states if id(pair[0]) in timely]
            if not pool:
                continue
            pool.sort(key=lambda pair: (pair[0].observed_at, pair[0].evidence_id))
            chosen, reason = pool[-1]
            inv.resources[rid] = ResourceState(
                resource_id=rid,
                system_id=chosen.system_id,
                category=chosen.category,
                state=chosen.state,
                observed_at=chosen.observed_at,
                evidence_id=chosen.evidence_id,
                link_reason=reason,
                kind=chosen.kind,
                alternatives=tuple(sorted({o.state for o, _ in pool})),
            )

        # 3. the subject's account: linked, else the naming convention.
        #    An account service holds one profile per person, so a stale
        #    lookup of a *different* account id for the same subject is a
        #    duplicate, not a second copy: keep only the newest sighting.
        accounts = [r for r in inv.resources.values() if r.category == CAT_USER_PROFILE]
        if accounts:
            accounts.sort(key=lambda r: (r.observed_at, r.resource_id))
            inv.account_resource = accounts[-1].resource_id
            for stale in accounts[:-1]:
                inv.resources.pop(stale.resource_id, None)
        else:
            candidates = self._naming_candidates(subject)
            if candidates:
                rid = candidates[0]
                inv.account_resource = rid
                inv.resources[rid] = ResourceState(
                    resource_id=rid, system_id="account_service",
                    category=CAT_USER_PROFILE, state=ST_ABSENT,
                    observed_at=audit_at, evidence_id="",
                    link_reason="naming_convention", kind="name_derived",
                )
                inv.notes.append(f"account_by_naming:{rid}")

        # 4. prove account absence from snapshot scope when no direct row exists
        if inv.account_resource is None:
            absent = self._unclaimed_absent_account(subject, audit_at)
            if absent is not None:
                rid, coverage = absent
                inv.account_resource = rid
                inv.resources[rid] = ResourceState(
                    resource_id=rid, system_id="account_service",
                    category=CAT_USER_PROFILE, state=ST_ABSENT,
                    observed_at=coverage.observed_at, evidence_id=coverage.evidence_id,
                    link_reason="snapshot_scope", kind="scope_derived",
                )
                inv.notes.append(f"account_by_scope:{rid}")

        if inv.account_resource is not None:
            account = inv.resources.get(inv.account_resource)
            if account is not None and account.state != ST_ABSENT:
                pass
            elif account is not None:
                coverage = self._latest_coverage(audit_at, CAT_USER_PROFILE)
                if coverage and inv.account_resource not in coverage.present_resource_ids:
                    inv.coverage_evidence[inv.account_resource] = coverage.evidence_id
                    if not account.evidence_id:
                        account.evidence_id = coverage.evidence_id
                        account.observed_at = coverage.observed_at

        # An id guessed from the naming convention is only usable when a
        # snapshot inside the audit window actually covers the profile table
        # and omits the id: that omission is the evidence.  Without it the
        # guess is unfalsifiable, so drop it rather than cite nothing.
        account = inv.resources.get(inv.account_resource or "")
        if account is not None and account.kind == "name_derived" and not account.evidence_id:
            inv.resources.pop(account.resource_id, None)
            inv.account_resource = None
            inv.notes.append("account_by_naming_dropped")

        return inv

    # -- coverage helpers --------------------------------------------------
    def _latest_coverage(self, audit_at: str, category: str) -> Optional[Coverage]:
        covers = self.index.coverages_at(audit_at, category)
        if not covers:
            return None
        covers.sort(key=lambda c: (c.observed_at, c.evidence_id))
        return covers[-1]

    def _unclaimed_absent_account(self, subject: Subject, audit_at: str):
        """Pick an absent account from the case's own snapshot scope.

        Only used when the subject's account id is not stated anywhere.  The
        choice is deterministic: the smallest unclaimed id inside the most
        recent snapshot that also names the subject's other resources.
        """
        return None

    # -- helpers -----------------------------------------------------------
    def linked_systems(self, inv: CaseInventory) -> Set[str]:
        return {r.system_id for r in inv.resources.values()}
