"""Confidence routing, review-PR files, and the flood safeguards."""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from statistics import median

from .config import ReviewCfg
from .models import Finding

BACKLOG_COLS = ["id", "kind", "title", "url", "source", "usage_class",
                "cites_package", "confidence", "first_seen", "where", "evidence_url", "snippet"]


class Routed:
    def __init__(self):
        self.accept: list[Finding] = []
        self.review: list[Finding] = []
        self.dropped: list[Finding] = []
        self.spike = False


def route(findings: list[Finding], cfg: ReviewCfg, known_ids: set[str],
          rejected_ids: set[str], history: list[int]) -> Routed:
    """Split findings by confidence. `known_ids` = already accepted; `history` = past new-counts."""
    r = Routed()
    fresh = [f for f in findings if f.id not in known_ids and f.id not in rejected_ids]
    if history and len(fresh) > cfg.spike_factor * max(median(history), 1) + 20:
        r.spike = True
    for f in fresh:
        if f.usage_class == "false_positive" or f.confidence < cfg.review:
            r.dropped.append(f)
        elif f.confidence >= cfg.accept and not r.spike:
            r.accept.append(f)
        else:
            r.review.append(f)
    r.accept.sort(key=lambda f: -f.confidence)
    if len(r.accept) > cfg.max_auto_accept_per_run:
        r.review.extend(r.accept[cfg.max_auto_accept_per_run:])
        r.accept = r.accept[: cfg.max_auto_accept_per_run]
    return r


def _rank(fs: list[Finding]) -> list[Finding]:
    return sorted(fs, key=lambda f: (-f.confidence, f.date or "", f.id))


def read_jsonl(p: Path) -> list[Finding]:
    if not p.is_file():
        return []
    return [Finding(**json.loads(x)) for x in p.read_text().splitlines() if x.strip()]


def read_backlog(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    with p.open(newline="") as fh:
        return list(csv.DictReader(fh))


def build_review_files(root: Path, new_items: list[Finding], cfg: ReviewCfg, spike: bool = False,
                       highlight: list[str] | None = None) -> dict:
    """Write pending.jsonl (<= max_items_per_pr), backlog.csv (rest) and REVIEW.md under root/data/review.

    Existing pending + backlog are preserved (the PR is reused while open). Returns counts.
    """
    d = root / "data" / "review"
    d.mkdir(parents=True, exist_ok=True)
    pending = {f.id: f for f in read_jsonl(d / "pending.jsonl")}
    full: dict[str, Finding] = {}
    for f in new_items:
        if f.id not in pending:
            full[f.id] = f
    # backlog rows are summaries only; promote them via the full records kept in backlog.jsonl
    stash = {f.id: f for f in read_jsonl(d / "backlog.jsonl")}
    stash.update(full)
    for i in list(stash):
        if i in pending:
            del stash[i]
    room = max(cfg.max_items_per_pr - len(pending), 0)
    ranked = _rank(list(stash.values()))
    for f in ranked[:room]:
        pending[f.id] = f
        stash.pop(f.id)
    rest = _rank(list(stash.values()))
    with (d / "backlog.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=BACKLOG_COLS, extrasaction="ignore")
        w.writeheader()
        for f in rest:
            w.writerow(f.model_dump())
    (d / "backlog.jsonl").write_text("".join(json.dumps(f.model_dump(), sort_keys=True) + "\n" for f in rest))
    (d / "pending.jsonl").write_text(
        "".join(json.dumps(f.model_dump(), sort_keys=True) + "\n" for f in _rank(list(pending.values()))))
    _write_review_md(d / "REVIEW.md", _rank(list(pending.values())), len(rest), spike, highlight or [])
    return {"pending": len(pending), "backlog": len(rest)}


def _write_review_md(p: Path, items: list[Finding], backlog: int, spike: bool, highlight: list[str]) -> None:
    lines = ["# Uncertain findings for review", ""]
    if spike:
        lines += ["> **Warning:** this run found unusually many new candidates. This can mean a "
                  "name collision; consider tightening `false_positive_guards` in your profile.", ""]
    lines += ["Delete a record from `data/review/pending.jsonl` to reject it, then merge to accept the rest. "
              "Closing this PR rejects everything in it (configurable via `review.on_close`).", ""]
    if backlog:
        lines += [f"{backlog} more candidates are queued in `data/review/backlog.csv` and will be "
                  "proposed in later PRs. Delete rows there to reject them in bulk.", ""]
    for f in items:
        lines += [f"## [{f.title or f.url}]({f.url})",
                  f"- id `{f.id}` · {f.kind} · {f.source} · class **{f.usage_class}** · "
                  f"cites package: {f.cites_package} · confidence {f.confidence:.2f}"]
        if f.where:
            link = f" ([open]({f.evidence_url}))" if f.evidence_url else ""
            lines.append(f"- **Found in:** {f.where}{link}")
        if f.rationale:
            lines.append(f"- {f.rationale}")
        lines.append("")
        lines += _excerpt(f, highlight)
        lines.append("")
    p.write_text("\n".join(lines))


def _excerpt(f: Finding, highlight: list[str]) -> list[str]:
    """Code goes in a fenced block (multi-line safe); prose is quoted with the matched term in bold."""
    if not f.snippet:
        return ["_No excerpt available: nothing in the open text to show._"]
    if f.kind in ("repo", "package"):
        fence = "```" if "```" not in f.snippet else "````"
        return [fence, f.snippet, fence]
    text = f.snippet
    for term in highlight:
        text = re.sub(r"(?<![\w.])(" + re.escape(term) + r")(?!\w)", r"**\1**", text, flags=re.I)
    return ["> " + ln for ln in text.splitlines()]


def settle_merged(root: Path, store, before_ids: set[str]) -> tuple[int, int]:
    """After the review PR was merged: pending items still in the file are accepted; removed ones rejected.

    `before_ids` = ids the bot originally proposed (from state). Returns (accepted, rejected).
    """
    d = root / "data" / "review"
    kept = {f.id: f for f in read_jsonl(d / "pending.jsonl")}
    rejected = sorted(before_ids - set(kept))
    if kept:
        store.upsert(list(kept.values()))
    store.reject(rejected)
    return len(kept), len(rejected)


def settle_closed(store, proposed_ids: set[str], on_close: str, state: dict, today: str) -> None:
    if on_close == "reject":
        store.reject(sorted(proposed_ids))
    else:
        snooze = state.setdefault("snoozed", {})
        for i in proposed_ids:
            snooze[i] = today


def sync_backlog(root: Path, store) -> list[str]:
    """If an admin deleted rows from backlog.csv, treat those ids as rejected (bulk reject)."""
    d = root / "data" / "review"
    csv_p, jsonl_p = d / "backlog.csv", d / "backlog.jsonl"
    if not csv_p.is_file() or not jsonl_p.is_file():
        return []
    keep = {r["id"] for r in read_backlog(csv_p)}
    full = read_jsonl(jsonl_p)
    gone = [f.id for f in full if f.id not in keep]
    if gone:
        store.reject(gone)
        jsonl_p.write_text("".join(json.dumps(f.model_dump(), sort_keys=True) + "\n"
                                   for f in full if f.id in keep))
    return gone


def clear_pending(root: Path) -> None:
    d = root / "data" / "review"
    for n in ("pending.jsonl", "REVIEW.md"):
        (d / n).unlink(missing_ok=True)
