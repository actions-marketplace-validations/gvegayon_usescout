# whouses

Find out who uses your research software, even when they don't cite it.

`whouses` runs weekly as a GitHub Action in the repo of the package you maintain. It looks for evidence of use in package registries, GitHub code and the literature, classifies each hit (optionally with an LLM), checks whether the paper cites you, and keeps a database plus a "used by" badge on a data branch. Anything it is unsure about goes into **one review pull request** instead of the database.

## Quick start

1. Add `.whouses.yml` to your repo (see [`examples/`](examples)):

   ```yaml
   profile: r            # r | python | npm | cpp | julia | cli, or a list (lists such as queries and patterns are combined)
   name: rgexf
   repo: gvegayon/rgexf
   doi: [10.xxxx/your-package-doi]   # lets whouses detect citations
   ```
2. Add [`examples/workflow.yml`](examples/workflow.yml) as `.github/workflows/whouses.yml`.
3. Run it once with *workflow_dispatch*. Results appear on the `whouses-data` branch.
4. Badge: `https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/<owner>/<repo>/whouses-data/badge.json`

The action uses the prebuilt image `ghcr.io/gvegayon/whouses:v0.1.2`, so nothing is built on your runner. Locally: `docker run --rm -v $PWD:/github/workspace -e GITHUB_WORKSPACE=/github/workspace ghcr.io/gvegayon/whouses:v0.1.1 dry-run --workspace /github/workspace` or `pipx install whouses`.

## Where the data lives

| `data_branch` | Where files go | Review PR targets |
|---|---|---|
| `whouses-data` (default) | root of that dedicated orphan branch | `whouses-data` |
| the branch your workflow checks out (e.g. `main`) | `.whouses/` folder on that branch (`data_dir` to change it) | `main` |

The default keeps weekly bot commits out of `main`, out of package tarballs (CRAN, PyPI, npm) and away from branch protection. Using `main` is simpler and puts everything in one place, but then: add `.whouses` to `.Rbuildignore`/`MANIFEST.in`/`.npmignore`, and allow `github-actions[bot]` to push to `main` (a protected `main` that requires PRs will reject the weekly update). If someone pushes to the branch mid-run, the bot rebases and retries.

## Who authors what

Everything whouses writes is authored by **`github-actions[bot]`**: the database commits, the review-branch commits and the review PR. Commits carry no co-author trailers and are never signed with a personal key.

- Keep `github-token` at its default (`${{ github.token }}`); that is what makes the PR bot-authored. Do not pass a personal token there.
- GitHub code search sometimes rejects the default token. If so, add a PAT as `search-token`; it is used **only** to call the search API, never to commit or open PRs.
- Enable *Settings → Actions → General → "Allow GitHub Actions to create and approve pull requests"*, or the review PR cannot be opened. (PRs opened with the default token do not trigger other workflows, which is intended here.)

## Software profiles

A profile is YAML that says how use of a kind of software shows up: registry, code patterns, manifest files, code-search queries, literature terms. Built-ins live in [`src/whouses/profiles`](src/whouses/profiles); inspect one with `whouses profile show python --name my-pkg`. Put your own in `.whouses/profiles/<id>.yml` (use `extends:` to start from a built-in) or tweak a built-in with `overrides:` in `.whouses.yml`. Profiles are validated against [`schema.json`](src/whouses/profiles/schema.json).

## Sources

| Source | Status |
|---|---|
| `ecosystems`: dependents (packages.ecosyste.ms) and paper mentions (papers.ecosyste.ms) | implemented |
| `github`: code search | implemented; needs a token, and a PAT may be required |
| `openalex`: works citing your DOI + full-text search | implemented |
| `europepmc`: open-access full text | implemented |
| GitLab, arXiv PDFs, Semantic Scholar, social media | not yet |
| Bitbucket | no public cross-workspace code search API, so unsupported |

## Review flow

- confidence ≥ `review.accept` (0.85) → written to `whouses-data`.
- between `review.review` (0.4) and `accept` → added to a single PR from `whouses/review`. While it is open, new items are appended to it; no second PR is opened.
- Delete a line in `data/review/pending.jsonl` to reject an item, then merge to accept the rest. Closing the PR rejects everything in it (`review.on_close: snooze` re-proposes after `snooze_weeks`).
- Flood safeguard: at most `max_items_per_pr` (100) items are detailed in a PR. The rest go to `data/review/backlog.csv` (delete rows to bulk-reject) and are promoted in later PRs. If a run finds far more than usual (`spike_factor`), nothing is auto-accepted and the PR warns about a possible name collision.

## LLMs

`llm-provider`: `anthropic`, `openai`, `gemini`, `ollama` or `none` (heuristics only). Only ambiguous hits are sent, capped by `llm.max_calls_per_run`, and answers are cached by evidence hash. Snippets of at most 300 characters are what get stored. Prompts treat snippets as data, but an LLM can still be fooled by hostile text, which is one reason uncertain items go to human review.

## Development

```
pip install -e '.[dev]'
pytest -q && ruff check .
```
