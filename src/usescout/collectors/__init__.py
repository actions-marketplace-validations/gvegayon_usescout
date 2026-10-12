"""Collectors turn a rendered profile into Candidates. Each exposes collect(ctx) -> list[Candidate]."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import Config
from ..http import Http
from ..models import Candidate


@dataclass
class Context:
    cfg: Config
    profile: dict
    http: Http
    state: dict = field(default_factory=dict)  # per-source cursors, mutated by collectors
    token: str = ""          # token used for PRs (the bot)
    search_token: str = ""   # token for GitHub code search; falls back to `token`
    warn: Callable[[str], None] = lambda m: None


def registry() -> dict[str, Callable[[Context], list[Candidate]]]:
    from . import ecosystems, europepmc, github, openalex

    return {
        "github": github.collect,
        "ecosystems": ecosystems.collect,
        "openalex": openalex.collect,
        "europepmc": europepmc.collect,
    }
