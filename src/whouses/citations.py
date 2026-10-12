"""Does a paper cite the package? Uses OpenAlex referenced_works against the configured DOIs."""
from __future__ import annotations

from .http import Http

OA = "https://api.openalex.org/works"
MAILTO = "whouses@users.noreply.github.com"


def target_ids(http: Http, dois: list[str]) -> set[str]:
    ids = set()
    for d in dois:
        r = http.get(f"{OA}/doi:{d}", params={"select": "id", "mailto": MAILTO})
        if r.status_code == 200:
            ids.add(r.json()["id"])
    return ids


def cites(http: Http, doi: str, targets: set[str]) -> str:
    """Return yes/no/unknown for a paper DOI."""
    if not targets or not doi.startswith("10."):
        return "unknown"
    r = http.get(f"{OA}/doi:{doi}", params={"select": "referenced_works", "mailto": MAILTO})
    if r.status_code != 200:
        return "unknown"
    refs = r.json().get("referenced_works") or []
    if not refs:
        return "unknown"  # no reference list indexed; absence is not evidence
    return "yes" if targets & set(refs) else "no"
