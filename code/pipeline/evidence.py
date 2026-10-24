"""Evidence loading: manifest -> parsed observations / snapshot scopes / links.

The corpus is small (94 files, ~4 MB), so everything is parsed once and then
filtered per case by ``observed_at <= audit_at``.  Filtering happens at query
time, never at load time, because a single file can serve many cases with
different audit instants.
"""
from __future__ import annotations

import re

TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")

import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import dataset
from .model import Coverage, Observation, ST_LINK
from .parsers import jsonbundle, logs, pdfdoc, sqlsnap, textdoc
from .parsers import imagedoc
from .parsers.imagedoc import extract as image_extract

LINK_DETAIL_RE = re.compile(
    r"linked\s+(?P<resource>[a-z0-9_]+).*?to\s+(?P<email>[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"
    r".*?and\s+(?P<user>user_\d+)",
    re.IGNORECASE,
)
EXPORT_REF_RE = re.compile(
    r"linked\s+(?P<resource>[a-z0-9_]+)\s*\((?P<alias>[^)]*)\).*?to\s+"
    r"(?P<email>[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}).*?and\s+(?P<user>user_\d+)",
    re.IGNORECASE,
)

# Operation types a deletion workflow applies to a record, and the resulting
# policy treatment they imply.  These rows are *claims by the workflow*; the
# pipeline never trusts their reported_result as proof of state, but the
# operation type is a durable statement of which treatment the record needs.
TREATMENT_EVENTS = {
    "DELETE_RECORD": "DELETE",
    "REDACT_RECORD": "REDACT",
    "ANONYMIZE_RECORD": "ANONYMIZE",
    "DISABLE_DATA_FLOW": "DISABLE_FLOW",
    "BLOCK_RESTORE": "BLOCK_RESTORE",
    "CHECK_DATA_FLOW": "DISABLE_FLOW",
    "CHECK_RESTORE_ACCESS": "BLOCK_RESTORE",
    "RETAIN_RECORD": "RETAIN",
}


@dataclass
class TreatmentHint:
    treatment: str
    event_type: str
    event_time: str
    activity_id: str
    reported_result: str


@dataclass
class EvidenceIndex:
    observations: List[Observation] = field(default_factory=list)
    coverages: List[Coverage] = field(default_factory=list)
    treatments: Dict[str, List[TreatmentHint]] = field(default_factory=lambda: defaultdict(list))
    links: List[Observation] = field(default_factory=list)
    file_errors: List[str] = field(default_factory=list)
    loaded_files: int = 0
    file_captured_at: Dict[str, str] = field(default_factory=dict)

    # -- loading ------------------------------------------------------------
    @classmethod
    def build(cls, root: Optional[Path] = None) -> "EvidenceIndex":
        index = cls()
        root = root or dataset.repo_root()
        manifest = dataset.load_evidence_files()

        for entry in manifest:
            path = root / "dataset" / entry.file_path
            if not path.exists():
                index.file_errors.append(f"missing:{entry.file_path}")
                continue
            suffix = path.suffix.lower()
            if suffix == ".json":
                obs, scopes = jsonbundle.extract(
                    path.read_text(encoding="utf-8", errors="replace"),
                    entry.file_id, entry.captured_at)
            elif suffix == ".sql":
                obs, scopes = sqlsnap.extract(
                    path.read_text(encoding="utf-8", errors="replace"),
                    entry.file_id, entry.captured_at)
            elif suffix in (".log", ".txt"):
                text = path.read_text(encoding="utf-8", errors="replace")
                if suffix == ".log":
                    obs, scopes = logs.extract(text, entry.file_id, entry.captured_at), []
                else:
                    obs, scopes = textdoc.extract(text, entry.file_id, entry.captured_at)
            elif suffix == ".pdf":
                # A PDF that cannot be read is recorded, never skipped in
                # silence: losing one artefact quietly changes verdicts.
                try:
                    obs, scopes = pdfdoc.extract(
                        str(path), entry.file_id, entry.captured_at), []
                except Exception as exc:
                    index.file_errors.append(
                        f"unreadable:{entry.file_path}:{type(exc).__name__}")
                    obs, scopes = [], []
            elif suffix == ".png":
                obs, scopes = image_extract(str(path), entry.file_id, entry.captured_at), []
                if not imagedoc.has_transcript(str(path)):
                    index.file_errors.append(
                        f"untranscribed:{entry.file_path}")
            else:
                obs, scopes = [], []

            index.loaded_files += 1
            index.file_captured_at[entry.file_id] = entry.captured_at
            for ob in obs:
                if ob.state == ST_LINK:
                    index.links.append(ob)
                else:
                    index.observations.append(ob)
            index.coverages.extend(scopes)

        index._load_activity()
        return index

    def _load_activity(self) -> None:
        for act in dataset.load_activity():
            if act.event_type == "LINK_RESOURCE_IDENTITY":
                m = EXPORT_REF_RE.search(act.details) or LINK_DETAIL_RE.search(act.details)
                if not m:
                    continue
                system, category = _system_category(m.group("resource"))
                self.links.append(Observation(
                    resource_id=m.group("resource"),
                    system_id=system,
                    category=category,
                    state=ST_LINK,
                    observed_at=act.event_time,
                    evidence_id=act.activity_id,
                    source_kind="activity",
                    refs=(m.group("user"),),
                    emails=(m.group("email").lower(),),
                    kind="catalog_link",
                ))
                continue
            treatment = TREATMENT_EVENTS.get(act.event_type)
            if treatment is None:
                continue
            self.treatments[act.resource_id].append(TreatmentHint(
                treatment=treatment,
                event_type=act.event_type,
                event_time=act.event_time,
                activity_id=act.activity_id,
                reported_result=act.reported_result,
            ))

    # -- queries ------------------------------------------------------------
    @staticmethod
    def admits(audit_at: str) -> bool:
        """Is ``audit_at`` a usable instant?

        The window test is a string comparison, so arbitrary text can compare
        *greater* than every real timestamp (``"2026-..." <= "not-a-date"`` is
        true) and would silently admit the whole corpus.  A malformed instant
        must authorise nothing.
        """
        return bool(TIMESTAMP_RE.match(audit_at or ""))

    def _usable(self, observation: Observation, audit_at: str) -> bool:
        """Can this observation testify about the state at ``audit_at``?

        Two conditions.  The observation's own timestamp must not be later than
        the audit instant, and the artefact must have been captured by then:
        the manifest timestamp is the capture instant of the export as a whole,
        so a bundle exported after the audit instant cannot testify about it
        even when it carries older inner snapshots.

        Identity is treated differently: *who* a resource belongs to does not
        change over time, so linking uses every artefact (see
        ``all_observations``); only state reconstruction is time-gated.
        """
        if not self.admits(audit_at):
            return False
        captured = self.file_captured_at.get(observation.evidence_id)
        if captured is not None and captured > audit_at:
            return False
        return observation.observed_at <= audit_at

    @property
    def all_observations(self) -> List[Observation]:
        """Every observation, regardless of time - used only for identity."""
        return self.observations

    @property
    def all_links(self) -> List[Observation]:
        return self.links

    def observations_at(self, audit_at: str) -> List[Observation]:
        return [o for o in self.observations if self._usable(o, audit_at)]

    def links_at(self, audit_at: str) -> List[Observation]:
        return [o for o in self.links if self._usable(o, audit_at)]

    def coverages_at(self, audit_at: str, category: str) -> List[Coverage]:
        """Snapshots inside the audit window that cover ``category``.

        Both gates apply: the snapshot must describe a state at or before
        ``audit_at`` *and* the artefact itself must have been captured by then,
        otherwise a later export could testify about an earlier audit.
        """
        if not self.admits(audit_at):
            return []
        return [c for c in self.coverages
                if c.observed_at <= audit_at and c.category == category
                and self.file_captured_at.get(c.evidence_id, "") <= audit_at]

    def treatment_for(self, resource_id: str, audit_at: str) -> Optional[TreatmentHint]:
        if not self.admits(audit_at):
            return None
        hints = [h for h in self.treatments.get(resource_id, ()) if h.event_time <= audit_at]
        if not hints:
            return None
        hints.sort(key=lambda h: (h.event_time, h.activity_id))
        return hints[-1]


def _system_category(resource_id: str) -> Tuple[str, str]:
    from .parsers import common

    return common.system_for_resource(resource_id)
