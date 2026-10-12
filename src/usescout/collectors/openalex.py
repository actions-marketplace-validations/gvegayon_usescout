"""OpenAlex: works that cite the package DOI(s), plus full-text matches for profile terms."""
from __future__ import annotations

from .. import evidence
from ..models import Candidate
from . import Context

API = "https://api.openalex.org/works"
MAILTO = "usescout@users.noreply.github.com"
MAX_PAGES = 5


def _work(w: dict, how: str, where: str, cites: str) -> Candidate:
    doi = (w.get("doi") or "").replace("https://doi.org/", "").lower()
    authors = ", ".join(a["author"]["display_name"] for a in w.get("authorships", [])[:3])
    return Candidate(
        source="openalex", kind="paper", ref_id=doi or w["id"], url=w.get("doi") or w["id"],
        title=evidence.strip_tags(w.get("display_name") or ""), owner=authors, date=(w.get("publication_date") or ""),
        where=where, evidence_url=w.get("doi") or w["id"], cites_package=cites,
        extra={"how": how, "fulltext_match": how == "fulltext", "openalex_id": w["id"],
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
    # the package's own paper, and its CRAN DOI record, are not users of the package
    own = {d.lower() for d in ctx.cfg.doi} | {f"10.32614/cran.package.{ctx.cfg.name.lower()}"}
    for doi in ctx.cfg.doi:
        base = ctx.http.json(f"{API}/doi:{doi}", params={"mailto": MAILTO, "select": "id"})
        for w in _cursor_pages(ctx, {"filter": f"cites:{base['id'].rsplit('/', 1)[-1]}"}):
            c = _work(w, "cites", f"its reference list cites {doi} (OpenAlex)", "yes")
            if c.ref_id not in own:
                out[c.key()] = c
    for term in ctx.profile.get("literature", {}).get("terms", []):
        for w in _cursor_pages(ctx, {"filter": f"fulltext.search:{term}"}):
            c = _work(w, "fulltext", "", "unknown")
            if c.ref_id not in own:
                out.setdefault(c.key(), c)
    return list(out.values())
