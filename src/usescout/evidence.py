"""Evidence excerpts: show exactly where a package was found, so findings are easy to validate.

Code: the matching line plus one line of context. Papers: a window around the term, taken from the
open-access full text (Europe PMC) with the section it appears in.
"""
from __future__ import annotations

import html
import re

from .http import Http

EPMC = "https://www.ebi.ac.uk/europepmc/webservices/rest"
MAX_XML = 8_000_000
LINE_MAX = 160
_TAG = re.compile(r"<[^>]+>")
# a match is excerpted from the paragraph (or reference entry) that contains it, never across them
_BLOCK_START = re.compile(r"</p>|<p[ >]|</title>|<ref[ >]|</ref>|</sec>|<abstract|</abstract>|<ref-list|</ref-list>")
_BLOCK_END = re.compile(r"</p>|</ref>|<title|<sec[ >]|</sec>|</abstract>|<ref[ >]")
_SPACE = re.compile(r"\s+")


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def code_patterns(profile: dict) -> list[str]:
    code = profile.get("code", {})
    return list(code.get("usage_patterns", [])) + [m["regex"] for m in code.get("manifest_patterns", [])]


def match_line(lines: list[str], patterns: list[str], name: str) -> int | None:
    for p in patterns:
        try:
            rx = re.compile(p)
        except re.error:
            continue
        for i, ln in enumerate(lines):
            if rx.search(ln):
                return i
    low = name.lower()
    for i, ln in enumerate(lines):
        if low in ln.lower():
            return i
    return None


def focus_code(fragment: str, patterns: list[str], name: str, context: int = 1) -> str:
    """The line that matches, plus `context` lines either side. Falls back to the head of the fragment."""
    lines = [ln.rstrip() for ln in fragment.replace("\r", "").split("\n")]
    i = match_line(lines, patterns, name)
    if i is None:
        return _clip(_SPACE.sub(" ", fragment).strip(), 300)
    chunk = lines[max(i - context, 0): i + context + 1]
    while chunk and not chunk[0].strip():
        chunk.pop(0)
    while chunk and not chunk[-1].strip():
        chunk.pop()
    return "\n".join(_clip(ln, LINE_MAX) for ln in chunk)


def has_match(fragment: str, patterns: list[str]) -> bool:
    lines = fragment.replace("\r", "").split("\n")
    for p in patterns:
        try:
            rx = re.compile(p)
        except re.error:
            continue
        if any(rx.search(ln) for ln in lines):
            return True
    return False


def window(text: str, term: str, before: int = 140, after: int = 200) -> str | None:
    """Text around the first whole-word occurrence of `term` (case-insensitive)."""
    m = re.search(r"(?<![\w.])" + re.escape(term) + r"(?!\w)", text, re.I)
    if not m:
        return None
    s, e = max(m.start() - before, 0), min(m.end() + after, len(text))
    return ("…" if s else "") + text[s:e].strip() + ("…" if e < len(text) else "")


def _plain(xml_fragment: str) -> str:
    xml_fragment = re.sub(r"^[^<>]*>", "", xml_fragment)    # slice began inside a tag: drop its tail
    xml_fragment = re.sub(r"<[^>]*$", "", xml_fragment)     # slice ended inside a tag: drop its head
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", xml_fragment))).strip()


def strip_tags(s: str) -> str:
    return _SPACE.sub(" ", html.unescape(_TAG.sub("", s or ""))).strip()


def lookup_pmcid(http: Http, doi: str) -> str | None:
    r = http.get(f"{EPMC}/search", params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "lite"})
    if r.status_code != 200:
        return None
    for hit in r.json().get("resultList", {}).get("result", []):
        if hit.get("pmcid") and hit.get("isOpenAccess") == "Y":
            return hit["pmcid"]
    return None


def paper_excerpt(http: Http, pmcid: str, terms: list[str]) -> tuple[str, str] | None:
    """(where, excerpt) from the open-access full text, or None if the term is not found there."""
    r = http.get(f"{EPMC}/{pmcid}/fullTextXML")
    if r.status_code != 200 or len(r.content) > MAX_XML:
        return None
    raw = r.text
    best = None
    for term in terms:
        rx = re.compile(r"(?<![\w.])" + re.escape(term) + r"(?!\w)", re.I)
        hits = []
        for m in rx.finditer(raw):
            pos = m.start()
            if raw.rfind("<", 0, pos) > raw.rfind(">", 0, pos):
                continue  # inside a tag (attribute value), not text
            in_refs = raw.rfind("<ref-list", 0, pos) > raw.rfind("</ref-list>", 0, pos)
            hits.append((in_refs, pos))
        if not hits:
            continue
        body = [h for h in hits if not h[0]]
        in_refs, pos = (body or hits)[0]
        before = list(_BLOCK_START.finditer(raw, max(pos - 4000, 0), pos))
        after = _BLOCK_END.search(raw, pos, pos + 4000)
        block = raw[before[-1].end() if before else max(pos - 600, 0): after.start() if after else pos + 600]
        text = window(_plain(block), term)
        if not text:
            continue
        if in_refs:
            where = "full text, reference list"
        else:
            t = raw.rfind("<title>", 0, pos)
            title = _plain(raw[t: raw.find("</title>", t)]) if t >= 0 else ""
            where = f"full text, section “{_clip(title, 60)}”" if title else "full text"
        n = len(hits)
        where += f" ({n} mention{'s' if n != 1 else ''})"
        best = (where, text)
        break
    return best
