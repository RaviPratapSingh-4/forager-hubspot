from __future__ import annotations

import re
from dataclasses import dataclass

SENIORITY_PATTERNS = {
    "c_level": r"\bchief\b|\b(?:ceo|cto|cfo|coo|cmo|cio|ciso|cpo|cro)\b|(?<!vice )(?<!vice-)\bpresident\b|\bco-?founders?\b|\bfounders?\b|\bowner\b",
    "vp": r"\b(?:vp|svp|evp|avp)\b|\bvice[ -]president\b",
    "director": r"\bdirector\b",
    "head": r"\bhead\b",
    "manager": r"\bmanager\b|\blead\b",
}
SERVER_TERMS = {
    "c_level": ["chief", "ceo", "cto", "cfo", "coo", "founder", "president"],
    "vp": ["vp", "vice president"],
    "director": ["director"],
    "head": ["head"],
    "manager": ["manager"],
}
ORDER = list(SENIORITY_PATTERNS)
_COMPILED = {k: re.compile(v) for k, v in SENIORITY_PATTERNS.items()}


def _term_re(term: str) -> re.Pattern:
    parts = [re.escape(p) for p in term.lower().split()]
    return re.compile(r"(?<!\w)" + r"[\s\-]+".join(parts) + r"(?:s|ing|ed)?(?!\w)")


def _norm(t: str | None) -> str:
    return " ".join((t or "").lower().split())


@dataclass(frozen=True)
class TitleFilter:
    """Match = (any include, if given) AND (any seniority level, if given) AND NOT (any exclude)."""

    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    seniority: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        bad = [s for s in self.seniority if s not in SENIORITY_PATTERNS]
        if bad:
            raise ValueError(f"unknown seniority {bad}; valid: {ORDER}")

    def levels(self, title: str | None) -> list[str]:
        t = _norm(title)
        return [k for k, rx in _COMPILED.items() if rx.search(t)]

    def matches(self, title: str | None) -> bool:
        t = _norm(title)
        if not t:
            return False
        if any(_term_re(x).search(t) for x in self.exclude):
            return False
        if self.include and not any(_term_re(x).search(t) for x in self.include):
            return False
        if self.seniority and not set(self.levels(t)) & set(self.seniority):
            return False
        return True

    def rank(self, title: str | None) -> int:
        return min((ORDER.index(l) for l in self.levels(title)), default=len(ORDER))

    def server_query(self) -> str | None:
        terms = list(self.include) or [x for l in self.seniority for x in SERVER_TERMS[l]]
        return " OR ".join(f'"{x}"' for x in dict.fromkeys(terms)) or None
