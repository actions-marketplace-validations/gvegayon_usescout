"""Core data models shared by collectors, detectors and the store."""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["repo", "package", "paper"]
UsageClass = Literal["uses", "depends", "mentions", "false_positive", "unknown"]
CitesPackage = Literal["yes", "no", "unknown"]


class Candidate(BaseModel):
    """Raw hit from a collector, before classification."""

    source: str
    kind: Kind
    url: str
    title: str = ""
    owner: str = ""  # repo owner or paper authors
    date: str = ""
    snippet: str = ""  # evidence text, kept short
    ref_id: str = ""  # stable id within the source (repo full name, DOI, ...)
    cites_package: CitesPackage = "unknown"
    extra: dict = Field(default_factory=dict)

    def key(self) -> str:
        return f"{self.kind}:{(self.ref_id or self.url).lower()}"


class Finding(BaseModel):
    id: str
    source: str
    kind: Kind
    url: str
    title: str = ""
    owner: str = ""
    date: str = ""
    snippet: str = ""
    usage_class: UsageClass = "unknown"
    cites_package: CitesPackage = "unknown"
    confidence: float = 0.0
    rationale: str = ""
    first_seen: str = Field(default_factory=lambda: date.today().isoformat())
    last_seen: str = Field(default_factory=lambda: date.today().isoformat())

    @staticmethod
    def make_id(c: Candidate) -> str:
        return hashlib.sha1(c.key().encode()).hexdigest()[:12]

    @classmethod
    def from_candidate(
        cls, c: Candidate, usage_class: UsageClass, confidence: float, rationale: str = ""
    ) -> Finding:
        return cls(
            id=cls.make_id(c),
            source=c.source,
            kind=c.kind,
            url=c.url,
            title=c.title,
            owner=c.owner,
            date=c.date,
            snippet=c.snippet[:300],
            usage_class=usage_class,
            cites_package=c.cites_package,
            confidence=confidence,
            rationale=rationale,
        )


def evidence_hash(c: Candidate) -> str:
    return hashlib.sha1(f"{c.key()}|{c.snippet}".encode()).hexdigest()[:16]
