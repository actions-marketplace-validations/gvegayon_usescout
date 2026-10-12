"""Command line: whouses run | dry-run | validate-config | profile show <id>."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml

from . import profile as profiles
from .config import load


def _cfg(args):
    cfg = load(args.config)
    key = os.environ.get("WHOUSES_LLM_API_KEY", "")
    prov = os.environ.get("WHOUSES_LLM_PROVIDER")
    if prov:
        cfg.llm.provider = prov
    if key:
        cfg.llm.api_key = key
    for env, attr in (("WHOUSES_LLM_MODEL", "model"), ("WHOUSES_OLLAMA_URL", "url")):
        if os.environ.get(env):
            setattr(cfg.llm, attr, os.environ[env])
    if not cfg.repo and os.environ.get("GITHUB_REPOSITORY"):
        cfg.repo = os.environ["GITHUB_REPOSITORY"]
    return cfg


def _input(name: str, default: str = "") -> str:
    return os.environ.get("INPUT_" + name.upper().replace(" ", "_"), default).strip()


def _action() -> int:
    """GitHub Action entrypoint. Inputs arrive as INPUT_<NAME> (hyphens are kept)."""
    import subprocess

    from .runner import run

    ws = Path(os.environ.get("GITHUB_WORKSPACE", "."))
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", "*"], check=False)
    cfg = load(ws / (_input("config") or ".whouses.yml"))
    if not cfg.repo:
        cfg.repo = os.environ.get("GITHUB_REPOSITORY", "")
    if _input("llm-provider"):
        cfg.llm.provider = _input("llm-provider")
    for inp, attr in (("llm-model", "model"), ("llm-api-key", "api_key"), ("ollama-url", "url")):
        if _input(inp):
            setattr(cfg.llm, attr, _input(inp))
    if _input("data-branch"):
        cfg.data_branch = _input("data-branch")
    dry = _input("dry-run").lower() == "true"
    token = _input("github-token") or os.environ.get("GITHUB_TOKEN", "")
    res = run(cfg, ws, token=token, search_token=_input("search-token"), push=not dry)
    for w in res.warnings:
        print(f"::warning::{w}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        rows = "\n".join(f"| {k} | {v} |" for k, v in sorted(res.counts.items()))
        Path(summary).write_text(f"### whouses{' (dry run)' if dry else ''}\n\n| metric | value |\n|---|---|\n{rows}\n")
    print(json.dumps(res.counts, indent=1, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="whouses")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "dry-run", "validate-config"):
        p = sub.add_parser(name)
        p.add_argument("--config", default=".whouses.yml")
        p.add_argument("--workspace", default=os.environ.get("GITHUB_WORKSPACE", "."))
    act = sub.add_parser("action", help="entrypoint for the GitHub Action (reads INPUT_* env vars)")
    act.add_argument("--config", default=None)
    act.add_argument("--workspace", default=None)
    pp = sub.add_parser("profile")
    pp.add_argument("action", choices=["show", "list"])
    pp.add_argument("id", nargs="?")
    pp.add_argument("--name", default="NAME")
    args = ap.parse_args(argv)

    if args.cmd == "profile":
        if args.action == "list":
            print("\n".join(profiles.builtin_ids()))
        else:
            print(yaml.safe_dump(profiles.resolve([args.id], args.name), sort_keys=False))
        return 0

    if args.cmd == "action":
        return _action()

    cfg = _cfg(args)
    ws = Path(args.workspace)
    if args.cmd == "validate-config":
        prof = profiles.resolve(cfg.profile_ids, cfg.name, cfg.import_name, cfg.overrides, ws / cfg.profiles_dir)
        print(f"ok: profile {prof['id']}, {len(prof['code'].get('search_queries', []))} code queries")
        return 0

    from .runner import run

    token = os.environ.get("GITHUB_TOKEN", "")
    res = run(cfg, ws, token=token, search_token=os.environ.get("WHOUSES_SEARCH_TOKEN", ""),
              push=args.cmd == "run")
    print(json.dumps(res.counts, indent=1, default=str))
    for w in res.warnings:
        print(f"::warning::{w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
