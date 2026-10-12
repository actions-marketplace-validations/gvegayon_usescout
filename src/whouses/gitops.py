"""git (subprocess) and GitHub REST helpers for the data and review branches."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .http import Http

BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
BOT_ENV = {"GIT_AUTHOR_NAME": BOT_NAME, "GIT_AUTHOR_EMAIL": BOT_EMAIL,
           "GIT_COMMITTER_NAME": BOT_NAME, "GIT_COMMITTER_EMAIL": BOT_EMAIL}


class Git:
    def __init__(self, repo: Path, push: bool = True):
        self.repo, self.push_enabled = Path(repo), push

    def run(self, *args: str, cwd: Path | None = None, check=True) -> str:
        p = subprocess.run(["git", *args], cwd=cwd or self.repo, capture_output=True, text=True,
                           env={**os.environ, **BOT_ENV})
        if check and p.returncode:
            raise RuntimeError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
        return p.stdout.strip()

    def remote_has(self, branch: str) -> bool:
        return bool(self.run("ls-remote", "--heads", "origin", branch, check=False))

    def fetch(self, branch: str) -> bool:
        if not self.remote_has(branch):
            return False
        self.run("fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
        return True

    def worktree(self, path: Path, branch: str, base: str | None = None, reset: bool = False) -> Path:
        """Create a worktree on `branch`: remote tip if present, else new branch off `base` (or orphan)."""
        path = Path(path)
        if self.fetch(branch) and not reset:
            self.run("worktree", "add", "-B", branch, str(path), f"origin/{branch}")
        elif base:
            self.run("worktree", "add", "-B", branch, str(path), base)
        else:
            self.run("worktree", "add", "--detach", str(path))
            self.run("checkout", "--orphan", branch, cwd=path)
            self.run("rm", "-rf", "--quiet", ".", cwd=path, check=False)
        return path

    def commit_all(self, path: Path, msg: str) -> bool:
        self.run("add", "-A", cwd=path)
        if not self.run("status", "--porcelain", cwd=path):
            return False
        # bot is both author and committer; no co-author trailers, no signing with a personal key
        self.run("-c", "commit.gpgsign=false", "commit", "-q", "-m", msg, cwd=path)
        return True

    def push(self, path: Path, branch: str, force: bool = False) -> None:
        if self.push_enabled:
            self.run("push", *(["--force"] if force else []), "origin", f"HEAD:refs/heads/{branch}", cwd=path)


class GitHubAPI:
    def __init__(self, http: Http, repo: str, token: str):
        self.http, self.repo = http, repo
        self.h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        self.base = f"https://api.github.com/repos/{repo}"

    def pr(self, number: int) -> dict:
        return self.http.json(f"{self.base}/pulls/{number}", headers=self.h)

    def create_pr(self, head: str, base: str, title: str, body: str, labels: list[str]) -> int:
        r = self.http.client.post(f"{self.base}/pulls", headers=self.h,
                                  json={"head": head, "base": base, "title": title, "body": body})
        r.raise_for_status()
        n = r.json()["number"]
        if labels:
            self.http.client.post(f"{self.base}/issues/{n}/labels", headers=self.h, json={"labels": labels})
        return n

    def update_pr(self, number: int, title: str, body: str) -> None:
        r = self.http.client.patch(f"{self.base}/pulls/{number}", headers=self.h,
                                   json={"title": title, "body": body})
        r.raise_for_status()
