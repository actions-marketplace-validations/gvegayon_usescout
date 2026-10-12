"""GitHub code search. Needs a token; code search is limited to ~10 requests/minute and 1000 hits/query."""
from __future__ import annotations

from ..models import Candidate
from . import Context

API = "https://api.github.com/search/code"
MAX_PAGES = 10  # 100 per page -> GitHub caps at 1000 results per query


def collect(ctx: Context) -> list[Candidate]:
    token = ctx.search_token or ctx.token
    if not token:
        ctx.warn("github: no token provided, skipping code search")
        return []
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.text-match+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    own = (ctx.cfg.repo or "").lower()
    pkg = ctx.cfg.name.lower()
    excluded = ctx.cfg.owner_excludes
    frags: dict[str, dict[str, str]] = {}      # repo -> {path: fragment}
    meta: dict[str, Candidate] = {}
    for q in ctx.profile.get("code", {}).get("search_queries", []):
        for page in range(1, MAX_PAGES + 1):
            r = ctx.http.get(API, params={"q": q, "per_page": 100, "page": page}, headers=headers)
            if r.status_code in (403, 422):
                ctx.warn(f"github: query {q!r} stopped with HTTP {r.status_code}")
                break
            r.raise_for_status()
            items = r.json().get("items", [])
            for it in items:
                repo = it["repository"]
                full = repo["full_name"]
                if repo.get("fork") or full.lower() == own or repo["owner"]["login"].lower() in excluded:
                    continue
                if full.split("/")[-1].lower() == pkg:  # mirrors/copies of the package itself (e.g. cran/<pkg>)
                    continue
                key = full.lower()
                meta.setdefault(key, Candidate(
                    source="github", kind="repo", ref_id=full, url=repo["html_url"], title=full,
                    owner=repo["owner"]["login"]))
                frag = " ".join(m.get("fragment", "") for m in it.get("text_matches", []))
                frags.setdefault(key, {}).setdefault(it.get("path", ""), frag)
            ctx.http.sleep(6.5)  # stay under the code-search rate limit
            if len(items) < 100:
                break
    # Search returns files in a different order from run to run; sort by path so the evidence is stable.
    seen: dict[str, Candidate] = {}
    for key, cand in meta.items():
        by_path = sorted(frags.get(key, {}).items())
        cand.snippet = " … ".join(f for _, f in by_path if f)[:600]
        cand.extra = {"path": by_path[0][0] if by_path else ""}
        seen[key] = cand
    return list(seen.values())
