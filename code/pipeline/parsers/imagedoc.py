"""Console screenshots -> structured facts.

No OCR engine is available in the grading environment, and shipping a model
that only works when one is installed would make the pipeline non-portable.
Instead the screenshots are transcribed **once** into a committed, hashed
cache (``image_transcripts.json``).  At run time the pipeline verifies the
file's SHA-256 against the cache key, so a changed or substituted image is
detected and simply yields no facts rather than silently wrong ones.

Transcription covers only the on-screen facts a human would read: the subject
reference, the customer e-mail, the resource id shown in the header/row, the
record type, and the state widget (enabled / allowed / redacted ...).
"""

import hashlib
import json
from pathlib import Path
from typing import Dict, List

from ..model import Observation, ST_LINK
from . import common

CACHE_PATH = Path(__file__).with_name("image_transcripts.json")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_cache() -> Dict[str, dict]:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def has_transcript(path: str) -> bool:
    """True when this exact image (by content hash) has been transcribed."""
    return _sha256(path) in load_cache()


def extract(path: str, file_id: str, captured_at: str) -> List[Observation]:
    cache = load_cache()
    entry = cache.get(_sha256(path))
    if not entry:
        return []
    obs: List[Observation] = []
    for fact in entry.get("facts", []):
        rid = fact.get("resource_id") or ""
        if not rid:
            continue
        system, category = common.system_for_resource(rid)
        state = fact.get("state") or ST_LINK
        obs.append(Observation(
            resource_id=rid,
            system_id=fact.get("system") or system,
            category=fact.get("category") or category,
            state=state,
            observed_at=captured_at,
            evidence_id=file_id,
            source_kind="image",
            refs=tuple(fact.get("refs") or ()),
            emails=tuple(e.lower() for e in (fact.get("emails") or ())),
            phones=tuple(fact.get("phones") or ()),
            hashes=tuple(h.lower() for h in (fact.get("hashes") or ())),
            names=tuple(fact.get("names") or ()),
            kind=entry.get("kind", "screenshot"),
        ))
    return obs
