import io
from datetime import date

import httpx
import pytest
import respx

from conftest import show

PIL = pytest.importorskip("PIL")
from PIL import Image, ImageDraw  # noqa: E402

from test_e2e import FakeAPI  # noqa: E402
from whouses import visual  # noqa: E402
from whouses.config import Config  # noqa: E402
from whouses.models import Candidate  # noqa: E402
from whouses.runner import run  # noqa: E402


def img(kind):
    im = Image.new("RGB", (300, 300), "white")
    d = ImageDraw.Draw(im)
    if kind == "net":
        for x, y in ((50, 50), (250, 60), (150, 250), (60, 200)):
            d.ellipse([x - 12, y - 12, x + 12, y + 12], fill="blue")
        d.line([50, 50, 250, 60, 150, 250, 60, 200, 50, 50], fill="black", width=3)
    else:
        d.rectangle([20, 200, 80, 280], fill="red")
        d.rectangle([120, 100, 180, 280], fill="red")
    return im


def png(kind):
    b = io.BytesIO()
    img(kind).save(b, "PNG")
    return b.getvalue()


def pdf_with(*kinds):
    b = io.BytesIO()
    first, *rest = [img(k) for k in kinds]
    first.save(b, "PDF", save_all=True, append_images=rest)
    return b.getvalue()


def test_extract_and_prefilter_ranks_similar_first():
    figs = visual.extract_figures(pdf_with("bars", "net"))
    assert len(figs) == 2
    top = visual.prefilter([png("net")], figs, top_k=1)
    assert top[0].page == 2  # the network page


OUT_YES = '{"match": "yes", "confidence": 0.8, "rationale": "same layout"}'


@respx.mock
def test_visual_match_goes_to_review_never_auto_accepted(repo, tmp_path, monkeypatch):
    work, origin = repo
    (work / ".whouses").mkdir()
    (work / ".whouses/ref.png").write_bytes(png("net"))
    cand = Candidate(source="fake", kind="paper", ref_id="10.9/p", url="https://doi.org/10.9/p", title="P",
                     snippet="unrelated", extra={"pdf_url": "https://pdfs.test/p.pdf"})
    import whouses.runner as r
    monkeypatch.setattr(r, "registry", lambda: {"fake": lambda ctx: [cand]})
    respx.get("https://pdfs.test/p.pdf").mock(return_value=httpx.Response(200, content=pdf_with("net")))
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(200, json={"content": [{"text": OUT_YES}]}))
    cfg = Config(profile="python", name="pkg", repo="me/pkg", sources=["fake"],
                 llm={"provider": "anthropic", "api_key": "k"},
                 overrides={"visual": {"examples": [{"path": ".whouses/ref.png", "description": "network"}]}})
    api = FakeAPI()
    res = run(cfg, work, api=api, today=date(2026, 3, 2), scratch=tmp_path / "s")
    assert res.counts["accepted"] == 0 and res.counts["to_review"] == 1 and api.created == 1
    assert "visual" in show(work, "whouses/review", "data/review/pending.jsonl")
    # same paper is not scanned twice
    res2 = run(cfg, work, api=api, today=date(2026, 3, 9), scratch=tmp_path / "s2")
    assert res2.counts["to_review"] == 0
