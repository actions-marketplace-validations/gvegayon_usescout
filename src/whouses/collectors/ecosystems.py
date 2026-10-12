"""ecosyste.ms: dependent packages (packages.ecosyste.ms) and paper mentions (papers.ecosyste.ms)."""
from __future__ import annotations

from urllib.parse import unquote

from ..models import Candidate
from . import Context

PACKAGES = "https://packages.ecosyste.ms/api/v1"
PAPERS = "https://papers.ecosyste.ms/api/v1"
# ecosyste.ms registry host names, keyed by the ecosystem names used in profiles
REGISTRY_HOST = {
    "cran": "cran.r-project.org", "pypi": "pypi.org", "npm": "npmjs.org",
    "conan": "conan.io", "julia": "juliahub.com",
}
MAX_PAGES = 20


def _pages(ctx: Context, url: str):
    for page in range(1, MAX_PAGES + 1):
        r = ctx.http.get(url, params={"page": page, "per_page": 100})
        if r.status_code == 404:
            return
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return
        yield from rows
        if len(rows) < 100:
            return


def collect(ctx: Context) -> list[Candidate]:
    out: list[Candidate] = []
    excluded = ctx.cfg.owner_excludes
    for reg in ctx.profile.get("registries", []):
        eco, name = reg["ecosystem"], reg.get("name", ctx.cfg.name)
        host = REGISTRY_HOST.get(eco)
        if host:
            for p in _pages(ctx, f"{PACKAGES}/registries/{host}/packages/{name}/dependent_packages"):
                repo_url = p.get("repository_url") or ""
                owner = repo_url.rstrip("/").split("/")[-2] if repo_url.count("/") >= 4 else ""
                if owner.lower() in excluded:
                    continue
                out.append(Candidate(
                    source="ecosystems", kind="package", ref_id=f"{eco}:{p['name']}",
                    url=p.get("registry_url") or repo_url, title=p["name"], owner=owner,
                    date=(p.get("latest_release_published_at") or "")[:10],
                    snippet=f"{p['name']} depends on {name} ({eco})",
                    extra={"precision": "high", "ecosystem": eco}))
        # paper mentions (CZI Software Mentions dataset); only the DOI is returned
        for m in _pages(ctx, f"{PAPERS}/projects/{eco}/{name}/mentions"):
            doi = unquote(m.get("paper_url", "").rsplit("/", 1)[-1])
            if not doi:
                continue
            out.append(Candidate(
                source="ecosystems", kind="paper", ref_id=doi.lower(), url=f"https://doi.org/{doi}",
                title="", snippet=f"Listed by papers.ecosyste.ms as mentioning {name}",
                extra={"precision": "high"}))
    return out
