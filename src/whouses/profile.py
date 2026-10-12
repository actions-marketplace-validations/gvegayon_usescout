"""Software profiles: declarative descriptions of how usage of a piece of software shows up."""
from __future__ import annotations

import copy
import json
import re
from importlib import resources
from pathlib import Path

import jsonschema
import yaml

PLACEHOLDERS = ("name", "import_name", "Name")


def _builtin_dir():
    return resources.files("whouses.profiles")


def schema() -> dict:
    return json.loads(_builtin_dir().joinpath("schema.json").read_text())


def builtin_ids() -> list[str]:
    return sorted(p.name[:-4] for p in _builtin_dir().iterdir() if p.name.endswith(".yml"))


def validate(data: dict) -> None:
    jsonschema.validate(data, schema())


def _merge(base: dict, over: dict) -> dict:
    """Deep-merge dicts; lists in `over` replace lists in `base`."""
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _union(base: dict, other: dict) -> dict:
    """Combine two profiles of a multi-language tool: lists are concatenated (deduplicated), dicts recurse."""
    out = copy.deepcopy(base)
    for k, v in other.items():
        cur = out.get(k)
        if isinstance(v, dict) and isinstance(cur, dict):
            out[k] = _union(cur, v)
        elif isinstance(v, list) and isinstance(cur, list):
            out[k] = cur + [x for x in v if x not in cur]
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_raw(profile_id: str, search_dir: Path | None = None, _seen: tuple = ()) -> dict:
    """Load a profile by id (user dir first, then built-ins), resolving `extends`."""
    if profile_id in _seen:
        raise ValueError(f"circular profile extends: {' -> '.join(_seen + (profile_id,))}")
    text = None
    if search_dir is not None:
        user = Path(search_dir) / f"{profile_id}.yml"
        if user.is_file():
            text = user.read_text()
    if text is None:
        res = _builtin_dir().joinpath(f"{profile_id}.yml")
        if not res.is_file():
            raise KeyError(f"unknown profile '{profile_id}' (built-in: {', '.join(builtin_ids())})")
        text = res.read_text()
    data = yaml.safe_load(text) or {}
    validate(data)
    parent = data.get("extends")
    if parent:
        data = _merge(load_raw(parent, search_dir, _seen + (profile_id,)), data)
        data["id"] = profile_id
    return data


def _subst(text: str, values: dict[str, str], escape: bool) -> str:
    for key in PLACEHOLDERS:
        v = values[key]
        text = text.replace("{" + key + "}", re.escape(v) if escape else v)
    return text


def render(data: dict, name: str, import_name: str | None = None) -> dict:
    """Fill placeholders. Regex fields get escaped values; query/term fields get raw values."""
    values = {
        "name": name,
        "import_name": import_name or name.replace("-", "_"),
        "Name": name[:1].upper() + name[1:],
    }
    out = copy.deepcopy(data)
    code = out.get("code", {})
    code["usage_patterns"] = [_subst(p, values, True) for p in code.get("usage_patterns", [])]
    for m in code.get("manifest_patterns", []):
        m["regex"] = _subst(m["regex"], values, True)
    code["search_queries"] = [_subst(q, values, False) for q in code.get("search_queries", [])]
    lit = out.get("literature", {})
    lit["terms"] = [_subst(t, values, False) for t in lit.get("terms", [])]
    for r in out.get("registries", []):
        if "name" in r:
            r["name"] = _subst(r["name"], values, False)
    out["code"], out["literature"] = code, lit
    return out


def resolve(profiles: list[str], name: str, import_name: str | None = None,
            overrides: dict | None = None, search_dir: Path | None = None) -> dict:
    """Combine one or more profiles (lists are unioned), apply user overrides (which replace), then render."""
    merged: dict = {}
    for pid in profiles:
        merged = _union(merged, load_raw(pid, search_dir))
    merged["id"] = "+".join(profiles)
    if overrides:
        merged = _merge(merged, overrides)
    validate(merged)
    return render(merged, name, import_name)
