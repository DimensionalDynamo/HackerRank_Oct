"""Deletion-intent classification.

A request only authorises destructive action when the customer actually asks
for erasure.  Access, correction, portability, marketing opt-out and login
help requests are ``NOT_APPLICABLE`` even though they mention the account.

The corpus is multilingual (English, Spanish, Portuguese, French, Japanese,
Korean) and several templates deliberately *look* like deletions while
withholding authorisation ("do not close the account", "leave my profile in
place", "not a request to erase").  The classifier therefore works on two
scores: destructive intent versus explicit non-deletion intent, with the
non-deletion markers winning ties because an ambiguous request must never
authorise irreversible deletion.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Pattern, Tuple

# Verbs that, in these languages, ask for the record/account to go away.
DESTRUCTIVE_PATTERNS = [
    r"\bdelet(e|ed|ing|ion)\b", r"\beras(e|ed|ing|ure)\b", r"\bremov(e|ed|ing|al)\b",
    r"\bclos(e|ed|ing)\b", r"\bpurge\b", r"\bdestroy\b", r"\bterminat(e|ion)\b",
    r"suppress", r"effac", r"fermer", r"résili",
    r"elimin", r"borr", r"exclus", r"cancel", r"cerrar",
    r"\bgone\b", r"\bno longer (exist|be found|available)\b", r"\bwipe\b",
    r"\bde-?identif", r"\bunlink",
    r"削除", r"消去", r"退会", r"삭제", r"탈퇴", r"해지",
]

# Phrases that explicitly withhold or redirect the request.  These are written
# as intentions ("I still need ...", "leave ... in place"), not as verb lists,
# because a bare "not delete" would also match "do not delete the wrong record".
NON_DELETION_PATTERNS = [
    # explicit withdrawal
    r"\bnot (asking|requesting|a request) (for|to)\b.*\b(delet|eras|remov|clos)",
    r"\bnot a request to (erase|delete)\b",
    r"\b(i am|i'm) not (asking|requesting) for (deletion|erasure)\b",
    r"\bdo(n't| not) (delete|erase|remove|close)\b",
    r"\bplease do not (delete|erase|remove|close)\b",
    r"\bwithout (removing|deleting|closing)\b",
    r"\bno (borrar|eliminar|suprimir)\b",
    r"\bne pas (supprimer|effacer)\b",
    r"\b削除しない", r"\b삭제하지",
    # keep / leave in place
    r"\b(leave|keep)\b[^.]{0,60}\b(account|profile|records?|history)\b[^.]{0,40}"
    r"\b(active|open|in place|as (it )?is|intact|available)\b",
    r"\bignore\b[^.]{0,30}\b(deletion|erase)\b",
    # stated ongoing need for the account
    r"\bstill (need|use|want)\b[^.]{0,40}\b(account|order history|receipts|service)\b",
    r"\b(i|we) (still )?(use|need) (it|this account|the account)\b",
    r"\bcontinue using the (service|account)\b",
    r"\bwant to (continue|keep) using\b",
    r"\bnot (to )?(close|closing) (the|my) account\b",
    # marketing opt-out / partial scope only
    r"\bmarketing (lists?|emails?|messages?|communications?)\b[^.]{0,30}\bonly\b",
    r"\b(remove|unsubscribe)\b[^.]{0,30}\bfrom (your |the )?(marketing|promotional)\b",
    r"\bstop the (promotional|marketing)\b",
    # access / portability / correction intents
    r"\b(i'?d like to|i want to|i need|i would like)\b[^.]{0,40}"
    r"\b(download|export|a copy|copy of|portable)\b",
    r"\b(please|could you|can you)\s+(send|provide)\s+(me\s+)?(a\s+)?copy\b",
    r"\b(what|which) personal (information|data)\b",
    r"\binformation request\b",
    r"\bcorrect (it|the|my|this)\b",
    r"\b(misspelled|spelled wrong|out of date|incorrect|wrong)\b[^.]{0,60}\b(please )?(correct|replace|update)\b",
    r"\bplease (replace|update|change)\b[^.]{0,40}\b(mobile|phone|email|number|surname|name|address)\b",
    r"\breset(ting)? (the |my )?(login|password)\b",
    r"\b(can't|cannot|unable to) (access|log ?in)\b",
    r"\bneed help\b[^.]{0,40}\b(reset|access|login)\b",
]

# A deletion request must still point at an account/record; requests about a
# field value alone are corrections.
SCOPE_PATTERNS = [r"\baccount\b", r"\bprofile\b", r"\brecords?\b", r"\bdata\b",
                  r"cuenta", r"compte", r"conta", r"アカウント", r"계정"]

# Sentence-scoped negation.  Several languages put the negator after the verb
# ("Bitte löschen Sie mein Konto nicht"), so a look-behind window is not enough:
# if the sentence that carries a destructive verb also carries a negator, the
# request is withholding authorisation.  "no longer" is excluded because it is
# itself an erasure statement ("the account no longer exists").
NEGATION_PATTERNS = [
    r"\bnot\b", r"\bno\b(?!\s+longer)", r"\bnever\b", r"\bdo(?:n't| not)\b",
    r"\bca(?:n't|nnot)\b", r"\bwon't\b", r"\bnicht\b", r"\bkein\w*\b",
    r"\bn[ãa]o\b", r"\bnunca\b", r"\bne\b", r"\bpas\b", r"\bjamais\b",
    r"\bniemals\b", r"\bsin\b",
]
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n+")

_COMPILED_DESTRUCTIVE: List[Pattern] = [re.compile(p, re.IGNORECASE) for p in DESTRUCTIVE_PATTERNS]
_COMPILED_NON: List[Pattern] = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in NON_DELETION_PATTERNS]
_COMPILED_SCOPE: List[Pattern] = [re.compile(p, re.IGNORECASE) for p in SCOPE_PATTERNS]
_COMPILED_NEGATION: List[Pattern] = [re.compile(p, re.IGNORECASE) for p in NEGATION_PATTERNS]


@dataclass(frozen=True)
class Intent:
    authorises_deletion: bool
    destructive_hits: Tuple[str, ...]
    non_deletion_hits: Tuple[str, ...]
    reason: str


def _hits(patterns: List[Pattern], text: str) -> Tuple[str, ...]:
    found: List[str] = []
    for pattern in patterns:
        m = pattern.search(text)
        if m:
            found.append(m.group(0)[:60])
    return tuple(sorted(set(found)))


_NEGATION_WINDOW = 40
_CLAUSE_SPLIT = re.compile(r"[.;:!?,\n]+|\band\b", re.IGNORECASE)


def _negated_requests(text: str) -> Tuple[str, ...]:
    """Destructive verbs that sit next to a negator.

    Clause proximity, not sentence scope.  "löschen Sie mein Konto nicht" and
    "ne supprimez pas" postfix the negator, so a look-behind is not enough;
    but templates such as "confirm that the profile is gone and that other
    records cannot identify me" ask for erasure while a later clause merely
    contains a negator.  Requiring verb and negator inside one clause keeps the
    first two and rejects the third.
    """
    found: List[str] = []
    for clause in _CLAUSE_SPLIT.split(text):
        if not clause:
            continue
        destructive = [m for p in _COMPILED_DESTRUCTIVE for m in p.finditer(clause)]
        if not destructive:
            continue
        if any(p.search(clause) for p in _COMPILED_NEGATION):
            found.append(clause.strip()[:60])
    return tuple(sorted(set(found)))


def classify(text: str) -> Intent:
    raw = unicodedata.normalize("NFKC", text or "")
    destructive = _hits(_COMPILED_DESTRUCTIVE, raw)
    non_deletion = tuple(sorted(set(_hits(_COMPILED_NON, raw)
                                    + _negated_requests(raw))))

    if non_deletion:
        return Intent(False, destructive, non_deletion, "explicit_non_deletion_marker")
    if not destructive:
        # No destructive verb at all: nothing authorises erasure.
        return Intent(False, destructive, non_deletion, "no_destructive_intent")
    return Intent(True, destructive, non_deletion, "destructive_intent")
