"""User configuration (.usescout.yml)."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator


class ReviewCfg(BaseModel):
    accept: float = 0.85
    review: float = 0.4
    max_items_per_pr: int = 100
    max_auto_accept_per_run: int = 200
    spike_factor: float = 5.0
    on_close: str = "reject"  # reject | snooze
    snooze_weeks: int = 8

    @model_validator(mode="after")
    def _check(self):
        if not 0 <= self.review <= self.accept <= 1:
            raise ValueError("require 0 <= review <= accept <= 1")
        if self.on_close not in ("reject", "snooze"):
            raise ValueError("on_close must be 'reject' or 'snooze'")
        return self


class LLMCfg(BaseModel):
    provider: str = "none"  # none | anthropic | openai | gemini | ollama
    model: str = ""
    api_key: str = ""
    url: str = ""  # ollama base URL
    max_calls_per_run: int = 200


class Config(BaseModel):
    profile: str | list[str]
    name: str
    import_name: str | None = None
    repo: str = ""  # owner/name; excluded from results
    doi: list[str] = Field(default_factory=list)
    exclude_owners: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=lambda: ["github", "ecosystems", "openalex", "europepmc"])
    overrides: dict = Field(default_factory=dict)
    profiles_dir: str = ".usescout/profiles"
    review: ReviewCfg = Field(default_factory=ReviewCfg)
    llm: LLMCfg = Field(default_factory=LLMCfg)
    max_evidence_fetches: int = 60   # open-access full texts fetched per run to excerpt where a paper mentions the package
    data_branch: str = "usescout"
    # Folder (inside the data branch) holding data/, state.json, badge.json and REPORT.md. Default: the branch
    # root for a dedicated branch, ".usescout" when the data branch is the branch the workflow checked out.
    data_dir: str | None = None
    review_branch: str = "usescout-review"

    @property
    def profile_ids(self) -> list[str]:
        return [self.profile] if isinstance(self.profile, str) else list(self.profile)

    @property
    def owner_excludes(self) -> set[str]:
        owners = {o.lower() for o in self.exclude_owners}
        if "/" in self.repo:
            owners.add(self.repo.split("/")[0].lower())
        return owners


def load(path: str | Path) -> Config:
    data = yaml.safe_load(Path(path).read_text()) or {}
    return Config(**data)
