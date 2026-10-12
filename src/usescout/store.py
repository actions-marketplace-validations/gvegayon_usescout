"""Flat-file database. Operates on a directory (a checkout of the data branch)."""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from .models import Finding


def oneline(s: str, n: int = 200) -> str:
    s = " ⏎ ".join(x.strip() for x in s.splitlines() if x.strip())
    return s if len(s) <= n else s[: n - 1] + "…"


class Store:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        (self.root / "data").mkdir(parents=True, exist_ok=True)

    # -- jsonl helpers ---------------------------------------------------
    def _read(self, rel: str) -> list[dict]:
        p = self.root / rel
        if not p.is_file():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

    def _write(self, rel: str, rows: list[dict], key="id") -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        rows = sorted(rows, key=lambda r: r[key])
        p.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))

    # -- findings --------------------------------------------------------
    def findings(self) -> dict[str, Finding]:
        return {r["id"]: Finding(**r) for r in self._read("data/findings.jsonl")}

    def upsert(self, new: list[Finding], today: str | None = None) -> int:
        """Merge findings by id. Returns number of newly added records."""
        today = today or date.today().isoformat()
        cur = self.findings()
        added = 0
        for f in new:
            old = cur.get(f.id)
            if old:
                f.first_seen = old.first_seen
                if old.usage_class == f.usage_class and old.snippet:   # same verdict: keep the original evidence
                    f.snippet, f.rationale = old.snippet, old.rationale
                    f.where, f.evidence_url = old.where, old.evidence_url
            else:
                added += 1
            f.last_seen = today if old is None else max(old.last_seen, today)
            if old and old.model_dump(exclude={"last_seen"}) == f.model_dump(exclude={"last_seen"}):
                f.last_seen = old.last_seen  # keep file stable when nothing changed
            cur[f.id] = f
        self._write("data/findings.jsonl", [f.model_dump() for f in cur.values()])
        self._write_views(list(cur.values()))
        return added

    def _write_views(self, fs: list[Finding]) -> None:
        cols = ["id", "kind", "title", "owner", "url", "source", "usage_class",
                "cites_package", "confidence", "first_seen", "where", "evidence_url", "snippet"]
        for name, kinds in (("repos", ("repo", "package")), ("papers", ("paper",))):
            rows = sorted((f for f in fs if f.kind in kinds), key=lambda f: f.id)
            with (self.root / f"data/{name}.csv").open("w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(cols)
                for f in rows:
                    d = f.model_dump()
                    d["snippet"] = oneline(d["snippet"])
                    w.writerow([d[c] for c in cols])

    # -- rejected / state -----------------------------------------------
    def rejected(self) -> dict[str, dict]:
        return {r["id"]: r for r in self._read("data/rejected.jsonl")}

    def reject(self, ids: list[str], today: str | None = None) -> None:
        rej = self.rejected()
        for i in ids:
            rej.setdefault(i, {"id": i, "rejected": today or date.today().isoformat()})
        self._write("data/rejected.jsonl", list(rej.values()))

    def state(self) -> dict:
        p = self.root / "state.json"
        return json.loads(p.read_text()) if p.is_file() else {}

    def save_state(self, st: dict) -> None:
        (self.root / "state.json").write_text(json.dumps(st, indent=1, sort_keys=True) + "\n")

    # -- badge / report ---------------------------------------------------
    def write_badge(self, label: str = "used by") -> dict:
        fs = list(self.findings().values())
        repos = sum(1 for f in fs if f.kind in ("repo", "package") and f.usage_class in ("uses", "depends"))
        papers = sum(1 for f in fs if f.kind == "paper" and f.usage_class in ("uses", "mentions"))
        badge = {"schemaVersion": 1, "label": label,
                 "message": f"{repos} repos · {papers} papers", "color": "blue"}
        (self.root / "badge.json").write_text(json.dumps(badge, indent=1) + "\n")
        return badge
