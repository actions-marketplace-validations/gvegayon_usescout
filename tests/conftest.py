import json
import subprocess

import pytest

from whouses.models import Finding


def git(*a, cwd):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A working repo with a bare 'origin' that has one commit on main."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    git("config", "user.email", "human@example.org", cwd=work)
    git("config", "user.name", "A Human", cwd=work)
    (work / "README.md").write_text("x")
    git("add", "-A", cwd=work)
    git("commit", "-q", "-m", "init", cwd=work)
    git("push", "-q", "origin", "HEAD:main", cwd=work)
    return work, origin


def show(work, branch, path):
    git("fetch", "-q", "origin", cwd=work)
    return git("show", f"origin/{branch}:{path}", cwd=work)


def rows(work, branch, path):
    return [json.loads(x) for x in show(work, branch, path).splitlines() if x]


def commits_by_bot_only(work, branch):
    """Every commit the tool made on `branch` (everything after main) is authored AND committed by the bot."""
    git("fetch", "-q", "origin", cwd=work)
    log = git("log", "--format=%an|%ae|%cn|%ce|%B%x00", f"origin/{branch}", "--not", "origin/main", cwd=work)
    entries = [e for e in log.split("\x00") if e.strip()]
    assert entries
    for e in entries:
        an, ae, cn, ce, body = e.strip().split("|", 4)
        assert an == cn == "github-actions[bot]", e
        assert ae == ce and "github-actions[bot]" in ae, e
        assert "co-authored-by" not in body.lower() and "claude" not in body.lower(), e
    return len(entries)


def mk(i, conf=0.6, cls="mentions", kind="paper"):
    return Finding(id=f"id{i:04d}", source="t", kind=kind, url=f"https://x/{i}", title=f"t{i}",
                   usage_class=cls, confidence=conf, snippet="s", first_seen="2026-01-01",
                   last_seen="2026-01-01")
