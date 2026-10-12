"""OpenAlex: works that cite the package DOI(s), plus full-text matches for profile terms."""
from __future__ import annotations

from ..models import Candidate
from . import Context

API = "https://api.openalex.org/works"
MAILTO = "whouses@users.noreply.github.com"
MAX_PAGES = 5


def _work(w: dict, how: str, snippet: str, cites: str) -> Candidate:
    doi = (w.get("doi") or "").replace("https://doi.org/", "").lower()
    authors = ", ".join(a["author"]["display_name"] for a in w.get("authorships", [])[:3])
    return Candidate(
        source="openalex", kind="paper", ref_id=doi or w["id"], url=w.get("doi") or w["id"],
        title=w.get("display_name") or "", owner=authors, date=(w.get("publication_date") or ""),
        snippet=snippet, cites_package=cites, extra={"how": how, "openalex_id": w["id"],
                                                    "precision": "high" if how == "cites" else "low",
                                                    "no_snippet": how != "cites"})


def _cursor_pages(ctx: Context, params: dict):
    cursor = "*"
    for _ in range(MAX_PAGES):
        data = ctx.http.json(API, params={**params, "cursor": cursor, "per-page": 100, "mailto": MAILTO})
        yield from data.get("results", [])
        cursor = data.get("meta", {}).get("next_cursor")
        if not cursor:
            return


def collect(ctx: Context) -> list[Candidate]:
    out: dict[str, Candidate] = {}
    for doi in ctx.cfg.doi:
        base = ctx.http.json(f"{API}/doi:{doi}", params={"mailto": MAILTO, "select": "id"})
        for w in _cursor_pages(ctx, {"filter": f"cites:{base['id'].rsplit('/', 1)[-1]}"}):
            c = _work(w, "cites", f"Cites {doi}", "yes")
            out[c.key()] = c
    for term in ctx.profile.get("literature", {}).get("terms", []):
        for w in _cursor_pages(ctx, {"filter": f"fulltext.search:{term}"}):
            c = _work(w, "fulltext", f"Full text matches “{term}”", "unknown")
            out.setdefault(c.key(), c)
    return list(out.values())
