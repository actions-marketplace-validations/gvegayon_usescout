"""Heuristic classification of candidates using a rendered profile."""
from __future__ import annotations

import re

from .models import Candidate, UsageClass


def _any(patterns: list[str], text: str, flags=re.MULTILINE) -> bool:
    for p in patterns:
        try:
            if re.search(p, text, flags):
                return True
        except re.error:
            continue
    return False


def classify(c: Candidate, profile: dict) -> tuple[UsageClass, float, str]:
    code = profile.get("code", {})
    lit = profile.get("literature", {})
    text = c.snippet or ""
    guards = profile.get("false_positive_guards", [])

    if guards and _any(guards, text, re.IGNORECASE | re.MULTILINE):
        return "false_positive", 0.9, "matched false-positive guard"

    if c.extra.get("precision") == "high":  # registry dependents / citation links
        if c.kind == "paper":
            if c.extra.get("how") == "cites":
                return "uses", 0.9, "paper cites the package"
            return "mentions", 0.9, f"paper listed as mentioning the package by {c.source}"
        return "depends", 0.95, f"declared dependency via {c.source}"

    if c.kind in ("repo", "package"):
        if _any(code.get("usage_patterns", []), text):
            return "uses", 0.9, "code matches usage pattern"
        for m in code.get("manifest_patterns", []):
            fname = c.extra.get("path", "").rsplit("/", 1)[-1]
            if _file_matches(fname, m["files"]) and _any([m["regex"]], text):
                return "depends", 0.9, f"manifest {fname} lists the package"
        return "unknown", 0.3, "no pattern matched in snippet"

    # papers
    if not text and c.extra.get("fulltext_match"):
        return "mentions", 0.45, "full-text search reports the term; no excerpt available"
    terms = lit.get("terms", [])
    low = text.lower()
    if not any(t.lower() in low for t in terms):
        return "unknown", 0.2, "term absent from snippet"
    ctx = [t.lower() for t in lit.get("context_terms", [])]
    if ctx and any(t in low for t in ctx):
        return "mentions", 0.6, "term appears with context words"
    return "mentions", 0.45, "term appears without context words"


def _file_matches(fname: str, patterns: list[str]) -> bool:
    from fnmatch import fnmatch

    return any(fnmatch(fname, p) for p in patterns)


def is_ambiguous(conf: float, lo: float = 0.3, hi: float = 0.85) -> bool:
    return lo <= conf < hi
