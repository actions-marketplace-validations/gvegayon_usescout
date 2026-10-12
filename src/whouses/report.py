"""REPORT.md for the data branch."""
from __future__ import annotations

from pathlib import Path

from .models import Finding
from .store import oneline


def _item(f: Finding) -> str:
    line = f"- [{f.title or f.url}]({f.url}) — {f.owner}"
    if f.where:
        line += f"  \n  _{f.where}_"
    if f.snippet:
        line += "  \n  > " + oneline(f.snippet, 240)
    return line


def write_report(root: Path, name: str, findings: list[Finding], new_ids: set[str], pr_number: int | None) -> None:
    repos = [f for f in findings if f.kind in ("repo", "package")]
    papers = [f for f in findings if f.kind == "paper"]
    uncited = [f for f in papers if f.cites_package == "no" and f.usage_class in ("uses", "mentions")]
    new = [f for f in findings if f.id in new_ids]
    L = [f"# Who uses {name}", "",
         f"- Repositories / packages: **{len(repos)}**",
         f"- Papers: **{len(papers)}** ({len(uncited)} appear not to cite the package)", ""]
    if pr_number:
        L += [f"Uncertain items are awaiting review in PR #{pr_number}.", ""]
    if new:
        L += ["## New this run", ""] + [f"- [{f.title or f.url}]({f.url}) ({f.kind}, {f.usage_class})" for f in new[:50]]
        if len(new) > 50:
            L.append(f"- … and {len(new) - 50} more")
        L.append("")
    if uncited:
        L += ["## Papers that use it without citing it", ""] + [
            _item(f) for f in sorted(uncited, key=lambda f: f.date)[-50:]]
        L.append("")
    (root / "REPORT.md").write_text("\n".join(L))
