"""End-to-end: real collectors, LLM adapter, citation check, git branches and review PR lifecycle.

Only the network (respx) and the GitHub PR API (FakeAPI) are faked; git runs against a real bare origin.
"""
import json
from datetime import date

import httpx
import pytest
import respx

from conftest import commits_by_bot_only, git, rows, show
from whouses import collectors
from whouses.config import Config
from whouses.models import Candidate
from whouses.runner import run

D = date(2026, 3, 2)


class FakeAPI:
    def __init__(self):
        self.prs, self.created = {}, 0

    def pr(self, n):
        return self.prs[n]

    def create_pr(self, head, base, title, body, labels):
        self.created += 1
        self.prs[self.created] = {"state": "open", "merged": False, "body": body}
        return self.created

    def update_pr(self, n, title, body):
        self.prs[n]["body"] = body


def cand(i, snippet, kind="repo"):
    return Candidate(source="fake", kind=kind, ref_id=f"o/r{i}", url=f"https://github.com/o/r{i}",
                     title=f"o/r{i}", snippet=snippet, extra={"path": "x.py"})


SURE = "import mypkg\nmypkg.go()"           # heuristics: uses 0.9 -> auto-accept
UNSURE = "mypkg appears in a comment"       # heuristics: unknown 0.3 -> dropped
PAPER = "We used mypkg, a Python library"  # heuristics: mentions 0.6 -> review


@pytest.fixture
def fake(repo, monkeypatch):
    """Run the real runner with a scripted collector."""
    work, _ = repo
    batch = {"items": []}
    monkeypatch.setattr("whouses.runner.registry", lambda: {"fake": lambda ctx: list(batch["items"])})
    cfg = Config(profile="python", name="mypkg", repo="me/mypkg", sources=["fake"])
    return work, batch, cfg


# --------------------------------------------------------------------------------------------
def _openalex(request: httpx.Request) -> httpx.Response:
    u = str(request.url)
    paper = lambda i, doi, title: {"id": f"https://openalex.org/W{i}", "doi": f"https://doi.org/{doi}",  # noqa: E731
                                   "display_name": title, "publication_date": "2025-05-01",
                                   "authorships": [{"author": {"display_name": "Ann"}}]}
    if "/works/doi:10.1/pkg" in u:
        return httpx.Response(200, json={"id": "https://openalex.org/W1"})
    if "/works/doi:10.3/b" in u:  # reference list without our DOI -> uses without citing
        return httpx.Response(200, json={"referenced_works": ["https://openalex.org/W77"]})
    if "/works/doi:" in u:
        return httpx.Response(200, json={"referenced_works": []})
    if "cites%3AW1" in u or "cites:W1" in u:
        return httpx.Response(200, json={"results": [paper(2, "10.2/a", "Cites us")], "meta": {"next_cursor": None}})
    if "fulltext.search" in u:
        return httpx.Response(200, json={"results": [paper(2, "10.2/a", "Cites us"), paper(3, "10.3/b", "Uses us quietly")],
                                         "meta": {"next_cursor": None}})
    return httpx.Response(404)


def _llm(request: httpx.Request) -> httpx.Response:
    body = request.content.decode()
    if "just a comment" in body:
        out = {"usage_class": "false_positive", "confidence": 0.9, "rationale": "unrelated word"}
    else:
        out = {"usage_class": "uses", "confidence": 0.92, "rationale": "methods section says they used it"}
    return httpx.Response(200, json={"content": [{"text": json.dumps(out)}]})


@respx.mock
def test_all_sources_pipeline_with_llm_and_bot_only_commits(repo, tmp_path):
    work, _ = repo
    item = lambda full, fork=False, text="import mypkg": {  # noqa: E731
        "path": "a.py", "text_matches": [{"fragment": text}],
        "repository": {"full_name": full, "html_url": f"https://github.com/{full}", "fork": fork,
                       "owner": {"login": full.split("/")[0]}}}
    respx.get("https://api.github.com/search/code").mock(return_value=httpx.Response(200, json={"items": [
        item("a/one"), item("b/fork", fork=True), item("me/mypkg"),  # fork and own repo are excluded
        item("c/comment", text="# just a comment about mypkg")]}))
    respx.get("https://packages.ecosyste.ms/api/v1/registries/pypi.org/packages/mypkg/dependent_packages").mock(
        return_value=httpx.Response(200, json=[{"name": "dep1", "repository_url": "https://github.com/acme/dep1",
                                                "registry_url": "https://pypi.org/project/dep1"}]))
    respx.get("https://papers.ecosyste.ms/api/v1/projects/pypi/mypkg/mentions").mock(return_value=httpx.Response(
        200, json=[{"paper_url": "https://papers.ecosyste.ms/api/v1/papers/10.3%2Fb"}]))
    respx.route(host="api.openalex.org").mock(side_effect=_openalex)
    respx.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search").mock(return_value=httpx.Response(200, json={
        "nextCursorMark": "*", "resultList": {"result": [
            {"doi": "10.5/e", "title": "T", "abstractText": "We analysed data with <i>mypkg</i>, a Python package.",
             "authorString": "Bo", "firstPublicationDate": "2025-01-01", "source": "MED", "id": "1"}]}}))
    respx.post("https://api.anthropic.com/v1/messages").mock(side_effect=_llm)

    cfg = Config(profile="python", name="mypkg", repo="me/mypkg", doi=["10.1/pkg"],
                 sources=["github", "ecosystems", "openalex", "europepmc"],
                 llm={"provider": "anthropic", "api_key": "k"})
    api = FakeAPI()
    res = run(cfg, work, token="bot-token", search_token="pat", api=api, today=D, scratch=tmp_path / "s")
    assert not res.warnings, res.warnings

    f = {r["url"]: r for r in rows(work, "whouses-data", "data/findings.jsonl")}
    assert f["https://github.com/a/one"]["usage_class"] == "uses"
    assert f["https://pypi.org/project/dep1"]["usage_class"] == "depends"
    assert "https://github.com/b/fork" not in f and "https://github.com/me/mypkg" not in f
    assert "https://github.com/c/comment" not in f                         # LLM said false positive -> dropped
    assert f["https://doi.org/10.2/a"]["cites_package"] == "yes"
    assert f["https://doi.org/10.3/b"]["cites_package"] == "no"            # same paper from 3 sources, one finding
    assert f["https://doi.org/10.5/e"]["rationale"].startswith("LLM:")     # ambiguous snippet judged by the LLM
    assert not f["https://doi.org/10.3/b"]["rationale"].startswith("LLM:")  # no snippet -> no LLM call wasted
    assert len([r for r in f.values() if r["url"] == "https://doi.org/10.3/b"]) == 1
    assert "Uses us quietly" in show(work, "whouses-data", "REPORT.md")      # used without citing
    assert json.loads(show(work, "whouses-data", "badge.json"))["message"] == "2 repos · 3 papers"
    assert api.created == 0 and res.counts["llm_calls"] == 2

    assert commits_by_bot_only(work, "whouses-data") >= 1


# --------------------------------------------------------------------------------------------
def test_review_pr_lifecycle_merge_with_admin_edit(fake, tmp_path):
    work, batch, cfg = fake
    api = FakeAPI()
    kw = dict(api=api, today=D)
    batch["items"] = [cand(1, SURE), cand(2, UNSURE)] + [cand(10 + i, PAPER, "paper") for i in range(5)]
    r1 = run(cfg, work, scratch=tmp_path / "s1", **kw)
    assert (r1.counts["accepted"], r1.counts["to_review"], r1.counts["pr_opened"]) == (1, 5, 1)
    assert len(rows(work, "whouses-data", "data/findings.jsonl")) == 1
    assert len(rows(work, "whouses/review", "data/review/pending.jsonl")) == 5

    # PR still open: a new uncertain item is appended to the SAME PR
    batch["items"].append(cand(99, PAPER, "paper"))
    r2 = run(cfg, work, scratch=tmp_path / "s2", **kw)
    assert api.created == 1 and "pr_opened" not in r2.counts
    assert len(rows(work, "whouses/review", "data/review/pending.jsonl")) == 6

    # admin deletes one line on the review branch, then merges the PR
    git("checkout", "-q", "-B", "tmp-review", "origin/whouses/review", cwd=work)
    p = work / "data/review/pending.jsonl"
    lines = p.read_text().splitlines()
    removed = json.loads(lines[0])["id"]
    p.write_text("\n".join(lines[1:]) + "\n")
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "admin edit", cwd=work)
    git("checkout", "-q", "-B", "tmp-data", "origin/whouses-data", cwd=work)
    git("merge", "-q", "--no-edit", "tmp-review", cwd=work)
    git("push", "-q", "origin", "HEAD:whouses-data", cwd=work)
    git("checkout", "-q", "main", cwd=work)
    api.prs[1].update(state="closed", merged=True)

    r3 = run(cfg, work, scratch=tmp_path / "s3", **kw)
    assert r3.counts["review_accepted"] == 5
    acc = {r["id"] for r in rows(work, "whouses-data", "data/findings.jsonl")}
    rej = {r["id"] for r in rows(work, "whouses-data", "data/rejected.jsonl")}
    assert removed in rej and removed not in acc and len(acc) == 6
    assert api.created == 1 and r3.counts["to_review"] == 0   # rejected items never come back


def test_closed_pr_rejects_and_unchanged_run_makes_no_commit(fake, tmp_path):
    work, batch, cfg = fake
    api = FakeAPI()
    kw = dict(api=api, today=D)
    batch["items"] = [cand(1, SURE)] + [cand(10 + i, PAPER, "paper") for i in range(3)]
    run(cfg, work, scratch=tmp_path / "a", **kw)
    api.prs[1]["state"] = "closed"  # closed without merging
    run(cfg, work, scratch=tmp_path / "b", **kw)
    assert len(rows(work, "whouses-data", "data/rejected.jsonl")) == 3 and api.created == 1
    head = git("rev-parse", "origin/whouses-data", cwd=work)
    run(cfg, work, scratch=tmp_path / "c", **kw)
    git("fetch", "-q", "origin", cwd=work)
    assert git("rev-parse", "origin/whouses-data", cwd=work) == head


def test_flood_is_capped_at_100_with_backlog_then_promoted(fake, tmp_path):
    work, batch, cfg = fake
    api = FakeAPI()
    batch["items"] = [cand(i, PAPER, "paper") for i in range(250)]
    r = run(cfg, work, scratch=tmp_path / "f", api=api, today=D)
    assert (r.counts["pr_pending"], r.counts["pr_backlog"]) == (100, 150)
    csv = show(work, "whouses/review", "data/review/backlog.csv").splitlines()
    assert len(csv) == 151 and csv[0].startswith("id,kind,title,url")
    assert commits_by_bot_only(work, "whouses/review") >= 1
    # merge the PR as-is; the next cycle opens a new PR with the next 100 from the backlog
    git("checkout", "-q", "-B", "tmp-data", "origin/whouses-data", cwd=work)
    git("merge", "-q", "--no-edit", "origin/whouses/review", cwd=work)
    git("push", "-q", "origin", "HEAD:whouses-data", cwd=work)
    git("checkout", "-q", "main", cwd=work)
    api.prs[1].update(state="closed", merged=True)
    r2 = run(cfg, work, scratch=tmp_path / "g", api=api, today=D)
    assert (r2.counts["pr_pending"], r2.counts["pr_backlog"]) == (100, 50) and api.created == 2


def test_spike_sends_everything_to_review_even_if_confident(fake, tmp_path):
    work, batch, cfg = fake
    api = FakeAPI()
    batch["items"] = [cand(1, SURE), cand(2, SURE)]
    run(cfg, work, scratch=tmp_path / "a", api=api, today=D)
    batch["items"] = [cand(100 + i, SURE) for i in range(200)]   # e.g. a name collision
    r = run(cfg, work, scratch=tmp_path / "b", api=api, today=D)
    assert r.counts["spike"] and r.counts["accepted"] == 0
    assert "name collision" in api.prs[1]["body"] or api.created == 1


def test_a_failing_source_does_not_sink_the_run(fake, tmp_path, monkeypatch):
    work, _, cfg = fake

    def boom(ctx):
        raise RuntimeError("down")
    monkeypatch.setattr("whouses.runner.registry", lambda: {"fake": boom})
    res = run(cfg, work, scratch=tmp_path / "x", api=FakeAPI(), today=D)
    assert any("down" in w for w in res.warnings)


def test_registry_lists_real_sources():
    assert set(collectors.registry()) == {"github", "ecosystems", "openalex", "europepmc"}
