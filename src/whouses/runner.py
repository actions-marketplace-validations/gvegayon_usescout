"""One whouses run: settle the previous review PR, collect, classify, route, commit, open/update the PR."""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import citations, detect, review
from . import profile as profiles
from .collectors import Context, registry
from .config import Config
from .gitops import Git, GitHubAPI
from .http import Http
from .llm import Classifier
from .models import Candidate, Finding, evidence_hash
from .report import write_report
from .store import Store

PR_TITLE = "whouses: uncertain findings to review"
MAX_HISTORY = 12


@dataclass
class Result:
    counts: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _classify_all(cands: list[Candidate], prof: dict, cfg: Config, clf: Classifier,
                  http: Http, targets: set[str], state: dict) -> list[Finding]:
    out = []
    dropped = state.setdefault("dropped", {})
    for c in cands:
        fid = Finding.make_id(c)
        h = evidence_hash(c)
        if dropped.get(fid) == h:
            continue  # already judged irrelevant on identical evidence
        cls, conf, why = detect.classify(c, prof)
        # a placeholder snippet ("full text matches …") has no evidence for an LLM to judge
        if clf.enabled and detect.is_ambiguous(conf) and not c.extra.get("no_snippet"):
            res = clf.classify(h, c.snippet, cfg.name, context=f"{c.kind} from {c.source}")
            if res:
                cls, conf, why = res["usage_class"], res["confidence"], "LLM: " + res["rationale"]
                if c.cites_package == "unknown" and res.get("cites") in ("yes", "no"):
                    c.cites_package = res["cites"]
        if c.kind == "paper" and c.cites_package == "unknown" and targets:
            c.cites_package = citations.cites(http, c.ref_id, targets)
        out.append(Finding.from_candidate(c, cls, conf, why))
    return out


def dedupe(findings: list[Finding]) -> list[Finding]:
    """One finding per id: keep the most confident, but never lose a known citation status."""
    best: dict[str, Finding] = {}
    for f in findings:
        cur = best.get(f.id)
        if cur is None:
            best[f.id] = f
            continue
        keep, other = (f, cur) if f.confidence > cur.confidence else (cur, f)
        if keep.cites_package == "unknown" and other.cites_package != "unknown":
            keep.cites_package = other.cites_package
        for attr in ("title", "owner", "date"):  # sources differ in what they know; fill the gaps
            if not getattr(keep, attr) and getattr(other, attr):
                setattr(keep, attr, getattr(other, attr))
        best[f.id] = keep
    return list(best.values())


def run(cfg: Config, workspace: Path, **kw) -> Result:
    scratch = Path(kw.pop("scratch", None) or tempfile.mkdtemp(prefix="whouses-"))
    try:
        return _run(cfg, workspace, scratch=scratch, **kw)
    finally:
        git = Git(workspace)
        for name in ("data", "review"):
            if (scratch / name).exists():
                git.run("worktree", "remove", "--force", str(scratch / name), check=False)
        git.run("worktree", "prune", check=False)


def _run(cfg: Config, workspace: Path, token: str = "", search_token: str = "", http: Http | None = None,
         api: GitHubAPI | None = None, push: bool = True, today: date | None = None,
         scratch: Path | None = None) -> Result:
    today = today or date.today()
    iso = today.isoformat()
    http = http or Http()
    res = Result()
    warn = res.warnings.append
    git = Git(workspace, push=push)
    repo = cfg.repo or ""
    if api is None and token and repo and push:
        api = GitHubAPI(http, repo, token)

    # --- data branch checkout ------------------------------------------------
    same_branch = git.current_branch() == cfg.data_branch   # e.g. data_branch: main
    sub = cfg.data_dir if cfg.data_dir is not None else (".whouses" if same_branch else "")
    data_wt = git.worktree(scratch / "data", cfg.data_branch, detached=same_branch)
    droot = data_wt / sub if sub else data_wt
    store = Store(droot)
    state = store.state()
    state.setdefault("cache", {})

    # --- settle the previous review PR --------------------------------------
    proposed = set(state.get("proposed", []))
    pr_no = state.get("review_pr")
    pr_open = False
    if pr_no and api:
        pr = api.pr(pr_no)
        if pr["state"] == "open":
            pr_open = True
        else:
            # re-read the merged content from the data branch (it now contains pending.jsonl)
            git.fetch(cfg.data_branch)
            if pr.get("merged"):
                acc, rej = review.settle_merged(droot, store, proposed)
                res.counts.update(review_accepted=acc, review_rejected=rej)
            else:
                review.settle_closed(store, proposed, cfg.review.on_close, state, iso)
                res.counts["review_closed"] = len(proposed)
            review.clear_pending(droot)
            state.pop("review_pr", None)
            state["proposed"] = []
            pr_no = None
    review.sync_backlog(droot, store)

    # snoozed items are re-proposed after snooze_weeks
    snoozed = state.get("snoozed", {})
    cutoff = (today - timedelta(weeks=cfg.review.snooze_weeks)).isoformat()
    for i in [i for i, d in snoozed.items() if d <= cutoff]:
        del snoozed[i]

    # --- collect + classify --------------------------------------------------
    prof = profiles.resolve(cfg.profile_ids, cfg.name, cfg.import_name, cfg.overrides,
                            Path(workspace) / cfg.profiles_dir)
    ctx = Context(cfg=cfg, profile=prof, http=http, state=state, token=token,
                  search_token=search_token, warn=warn)
    reg = registry()
    cands: list[Candidate] = []
    for src in cfg.sources:
        fn = reg.get(src)
        if not fn:
            warn(f"unknown source {src!r}")
            continue
        try:
            got = fn(ctx)
            res.counts[f"src_{src}"] = len(got)
            cands += got
        except Exception as e:  # one failing source must not sink the run
            warn(f"{src}: {type(e).__name__}: {e}")
    targets = citations.target_ids(http, cfg.doi) if cfg.doi else set()
    clf = Classifier(cfg.llm, http, state["cache"], warn)
    findings = _classify_all(cands, prof, cfg, clf, http, targets, state)

    findings = dedupe(findings)
    known = set(store.findings())
    rejected_ids = set(store.rejected()) | set(snoozed)
    in_review = set(proposed) if pr_open else set()
    history = state.get("history", [])
    routed = review.route(findings, cfg.review, known | in_review, rejected_ids, history)
    for f in routed.dropped:
        state.setdefault("dropped", {})[f.id] = next(
            (evidence_hash(c) for c in cands if Finding.make_id(c) == f.id), "")
    # refresh already-accepted findings (last_seen / citation status)
    refresh = [f for f in findings if f.id in known and f.usage_class != "false_positive"]
    added = store.upsert(routed.accept + refresh, iso)
    res.counts.update(found=len(findings), accepted=len(routed.accept), new_in_data=added,
                      to_review=len(routed.review), dropped=len(routed.dropped), spike=routed.spike,
                      llm_calls=clf.calls)

    # --- review branch / PR --------------------------------------------------
    bl = droot / "data/review/backlog.jsonl"
    backlog_pending = bl.is_file() and bl.read_text().strip() != ""
    if routed.review or (backlog_pending and not pr_open):
        # commit the data branch first so the review branch is based on it
        git.commit_all(data_wt, f"whouses: update {cfg.name} ({iso})")
        git.push(data_wt, cfg.data_branch)
        data_sha = git.run("rev-parse", "HEAD", cwd=data_wt)
        rev_wt = git.worktree(scratch / "review", cfg.review_branch, base=data_sha, reset=not pr_open)
        if not pr_open:  # fresh PR cycle: start from the current data branch
            git.run("reset", "--hard", data_sha, cwd=rev_wt)
        rroot = rev_wt / sub if sub else rev_wt
        review.sync_backlog(rroot, Store(rroot))
        counts = review.build_review_files(rroot, routed.review, cfg.review, routed.spike)
        res.counts.update(pr_pending=counts["pending"], pr_backlog=counts["backlog"])
        pend_ids = {f.id for f in review.read_jsonl(rroot / "data/review/pending.jsonl")}
        state["proposed"] = sorted(set(state.get("proposed", [])) | pend_ids) if pr_open else sorted(pend_ids)
        body = _pr_body(cfg, counts, routed.spike)
        if git.commit_all(rev_wt, f"whouses: review candidates ({iso})") or not pr_open:
            git.push(rev_wt, cfg.review_branch, force=not pr_open)
        if api:
            if pr_open:
                api.update_pr(pr_no, PR_TITLE, body)
            else:
                state["review_pr"] = api.create_pr(cfg.review_branch, cfg.data_branch, PR_TITLE, body,
                                                   ["whouses-review"])
                res.counts["pr_opened"] = state["review_pr"]
        # the review-state fields live on the data branch, so re-write state there
    if routed.accept or routed.review:  # only record runs that found something, so no-op runs don't churn
        history.append(len(routed.accept) + len(routed.review))
    state["history"] = history[-MAX_HISTORY:]
    state["last_run"] = iso
    store.save_state(state)
    store.write_badge()
    write_report(droot, cfg.name, list(store.findings().values()),
                 {f.id for f in routed.accept}, state.get("review_pr"))
    if git.commit_all(data_wt, f"whouses: update {cfg.name} ({iso})"):
        git.push(data_wt, cfg.data_branch)
        res.counts["committed"] = True
    return res


def _pr_body(cfg: Config, counts: dict, spike: bool) -> str:
    lines = [f"whouses found candidates for **{cfg.name}** that it is not sure about.", "",
             f"- Items in this PR: **{counts['pending']}** (max {cfg.review.max_items_per_pr})",
             f"- Queued for later PRs: **{counts['backlog']}** (`data/review/backlog.csv`)", "",
             "Open `data/review/REVIEW.md` to read them. **Delete a line from "
             "`data/review/pending.jsonl` to reject an item**, then merge to accept the rest. "
             "Closing without merging " + ("rejects all of them." if cfg.review.on_close == "reject"
                                           else "snoozes them.")]
    if spike:
        lines += ["", "> ⚠️ Unusually many new candidates this run — possible name collision. "
                      "Consider adding `false_positive_guards` to your profile."]
    return "\n".join(lines)
