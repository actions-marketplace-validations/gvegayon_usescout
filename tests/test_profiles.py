import re

import pytest

from whouses import profile

# profile -> (name, snippets that must match a usage pattern, snippets that must not)
CASES = {
    "r": ("rgexf", ["library(rgexf)", "x <- rgexf::read.gexf(f)"], ["library(rgexfx)", "# rgexf is nice"]),
    "python": ("my-pkg", ["import my_pkg", "from my_pkg.sub import f"], ["import my_pkg2x", "# import my_pkg"]),
    "npm": ("left-pad", ["const p = require('left-pad')", "import x from 'left-pad/lib'"], ["require('left-padx')"]),
    "cpp": ("fmt", ["#include <fmt/core.h>", "find_package(fmt REQUIRED)"], ["#include <format>"]),
    "julia": ("Graphs", ["using Graphs"], ["using GraphsX"]),
    "cli": ("samtools", ["$ samtools -h", "RUN apt-get install samtools"], ["echo hi"]),
}


@pytest.mark.parametrize("pid", sorted(CASES))
def test_builtin_profile_patterns(pid):
    name, yes, no = CASES[pid]
    pats = [re.compile(p, re.M) for p in profile.resolve([pid], name)["code"]["usage_patterns"]]
    assert all(any(p.search(s) for p in pats) for s in yes)
    assert not any(p.search(s) for p in pats for s in no)


def test_user_profile_extends_builtin_and_multi_profile(tmp_path):
    (tmp_path / "mine.yml").write_text("id: mine\nextends: python\nliterature:\n  context_terms: [gexf]\n")
    prof = profile.resolve(["cpp", "mine"], "rgexf", search_dir=tmp_path)
    assert "gexf" in prof["literature"]["context_terms"] and "C++" in prof["literature"]["context_terms"]
    assert {"C++", "Python"} <= set(prof["code"]["languages"])          # multi-profile: lists are combined
    pats = [re.compile(p, re.M) for p in prof["code"]["usage_patterns"]]
    assert any(p.search("#include <rgexf/x.h>") for p in pats) and any(p.search("import rgexf") for p in pats)
    over = profile.resolve(["python"], "x", overrides={"literature": {"context_terms": ["only"]}})
    assert over["literature"]["context_terms"] == ["only"]               # overrides: lists are replaced
    assert not any(re.search(p, "library(aXb)") for p in profile.resolve(["r"], "a.b")["code"]["usage_patterns"])
