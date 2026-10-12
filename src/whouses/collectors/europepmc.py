"""Europe PMC full-text search over open-access articles; returns snippets, so no PDF needed."""
from __future__ import annotations

import re

from ..models import Candidate
from . import Context

API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
MAX_PAGES = 5
_TAG = re.compile(r"<[^>]+>")


def collect(ctx: Context) -> list[Candidate]:
    out: dict[str, Candidate] = {}
    lit = ctx.profile.get("literature", {})
    for term in lit.get("terms", []):
        cursor = "*"
        for _ in range(MAX_PAGES):
            data = ctx.http.json(API, params={
                "query": f'"{term}" AND OPEN_ACCESS:y', "format": "json", "pageSize": 100,
                "resultType": "core", "cursorMark": cursor})
            for r in data.get("resultList", {}).get("result", []):
                doi = (r.get("doi") or "").lower()
                ident = doi or f"{r.get('source')}:{r.get('id')}"
                text = " ".join([r.get("title", ""), r.get("abstractText", "")])
                snippet = _TAG.sub("", text)
                low = snippet.lower()
                # abstract/title rarely contain code; keep a window around the term when present
                i = low.find(term.lower())
                if i >= 0:
                    snippet = snippet[max(i - 120, 0): i + 180]
                else:
                    snippet = f"Full text of this open-access article matches “{term}”."
                c = Candidate(
                    source="europepmc", kind="paper", ref_id=ident,
                    url=f"https://doi.org/{doi}" if doi else f"https://europepmc.org/article/{r.get('source')}/{r.get('id')}",
                    title=r.get("title", ""), owner=r.get("authorString", "")[:120],
                    date=r.get("firstPublicationDate", ""), snippet=snippet,
                    extra={"fulltext_only": i < 0, "no_snippet": i < 0, "precision": "low"})
                out.setdefault(c.key(), c)
            nxt = data.get("nextCursorMark")
            if not nxt or nxt == cursor or len(data.get("resultList", {}).get("result", [])) < 100:
                break
            cursor = nxt
    return list(out.values())
