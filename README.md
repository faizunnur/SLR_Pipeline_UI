# slr-pipeline

A guided, auditable pipeline for running a systematic literature review (SLR)
end to end: protocol scoping, multi-source search, deduplication, screening,
open-access PDF retrieval, data extraction, quality appraisal, and reporting
against PRISMA 2020 and the Kitchenham & Charters guidelines. The mechanical
parts of the method are implemented as small, deterministic Python scripts;
the judgement calls stay with the reviewer.

The worked example used throughout the documentation is **machine-learning /
AI-based intrusion detection systems (ML-IDS)**. Swap the search terms, the
PICOC table, and the inclusion/exclusion criteria for your own topic; the
pipeline and the method do not change.

## What makes it different

- **Citation verification is a pipeline stage.** Every reference's DOI is
  resolved against Crossref and its title matched against the record Crossref
  holds, so fabricated or mis-attributed references are caught by the tool
  rather than by a reader of the finished review.
- **The audit trail is a file you own.** Searches, deduplication decisions,
  screening decisions and their reasons, and verification outcomes are written
  to an append-only provenance log that belongs to the project. The PRISMA
  counts and the first draft of the methods paragraph are generated from that
  record, not reconstructed from memory.
- **Local-first.** The pipeline runs entirely on your own machine. Its only
  network traffic is to the scholarly APIs it searches and to Crossref. Where
  AI assistance is used, it is a manual-paste step into a chatbot you choose,
  and every suggestion passes a human review gate before it becomes a
  decision - so the assistance can be disclosed accurately.

## Documentation

- [The 7-stage method](docs/methodology.md) - the methodology the pipeline
  implements, stage by stage, with the integrity and end-to-end checklists.
- [Script reference](docs/script-reference.md) - every stage script and the
  `src/srp/` library, command by command.
- [AI-assist screening](docs/ai-assist.md) - where AI fits in the pipeline,
  the prompt packs it uses, and the guardrails around it.

## Installing

**Quickest: `pipx install` directly from GitHub** -- no `git clone` needed,
works identically on Windows/macOS/Linux, and pulls in every dependency
(pandas, matplotlib, etc. -- the full `dependencies` list in `pyproject.toml`)
automatically.

`pipx` is a separate tool, not something Python/pip ships with -- if
`pipx install ...` prints "not recognized as an internal or external
command" (Windows) or "command not found" (macOS/Linux), that's this, not a
problem with this project. Install it once, then **close and reopen your
terminal** (this step edits your PATH, which an already-open terminal won't
pick up):

```bash
python -m pip install --user pipx
python -m pipx ensurepath
# close and reopen your terminal here
```

Then install and run the tool itself:

```bash
pipx install git+https://github.com/nayeem-hossain/slr-pipeline.git
slr
```

(`pipx` isolates the install in its own environment so it can't collide with
another project's packages. Plain `pip install git+...` also works if you'd
rather not use pipx -- but then you're managing your own virtual environment
instead of getting one for free.)

This installs the exact same code as the git-clone paths below --
`src/scripts/*.py` ship alongside `src/slr.py` and remain individually readable and
directly runnable from the installed copy, same as from a clone. Installing
via pipx is a convenience for finding and updating the tool, not a reason to
trust it any less than reading the source directly.

**Keeping up to date.** The guided TUI checks GitHub's latest release once at
startup -- a single non-blocking network call, silent if you're offline or
already current -- and prints a one-line notice if a newer version exists. It
never auto-updates and never blocks you from continuing on an old version (see
`CHANGELOG.md` for why: forcing an update mid-review risks changing dedup or
counting behavior partway through a review's own corpus, which is worse than
staying on the version you started with). Update yourself with:

```bash
pipx upgrade --force slr-pipeline
```

The `--force` matters here specifically because this installs from
`git+https://...` rather than a pinned PyPI release, so every upgrade
rebuilds the wheel from source -- the launcher pip generates for `slr.exe`
comes out byte-different almost every time even when nothing you'd care
about changed, and pipx's own shim-safety-check (which protects a shim it
doesn't recognize as its own from being silently overwritten) flags that as
a mismatch and refuses to touch it. Without `--force` you can see the
package itself upgrade correctly (`pipx list` shows the new version) while
`slr` on your PATH keeps running the old one, with a warning like:

```
File exists at C:\Users\<you>\.local\bin\slr.exe and does not match
...\pipx\venvs\slr-pipeline\Scripts\slr.exe. Not modifying.
```

`--force` tells pipx to overwrite it as part of the same upgrade -- no
separate reinstall step needed. If you already hit this warning on a plain
`pipx upgrade`, either re-run with `--force` or run
`pipx reinstall slr-pipeline` once to get back in sync.

You can also check on demand from the guided TUI's consolidation menu's
**"Check for updates"** action, which always reports a result (up to date /
outdated / couldn't check) rather than staying silent. Note this only works
going forward: the check can only run from a copy of the tool that already
has the checking code in it. If you installed before this feature existed,
`pipx upgrade` once gets you a copy that checks for itself from then on.

## Two ways to run this

**a) Guided (recommended): `python src/slr.py`** (or just `slr` if pip/pipx-installed)

An interactive terminal (needs `questionary` + `rich`, see [Running the
pipeline](#running-the-pipeline) below) that walks you through a setup wizard
-- topic, keywords, eligibility criteria, year range, contact email, optional
API keys, which sources to search, how many search phases, which web chatbot
you'll paste screening prompts into, your reviewer name -- then runs each phase
for you: search -> dedup -> prescreen skeleton -> manual-paste AI-assist
screening -> human review gate -> query expansion into the next phase.
Once every phase is done it opens a consolidation menu: merge TA-included
studies across phases, run **citation snowballing** (backward + forward,
Wohlin 2014) and merge its results in, download PDFs, **full-text screening**,
verify citations, build the extraction sheet, generate figures, export
references, export full-text exclusions with reasons, compute **inter-rater
agreement**, draft the search-methods paragraph, write a review
self-appraisal checklist, and write the provenance report.

> **Terminology:** the between-phase step is **query expansion** -- it suggests
> extra keywords drawn from the terms frequent in your included titles. It is not
> citation snowballing (Wohlin 2014), which walks the reference/citation graph.
> The two are not interchangeable: snowballing exists to escape your search
> string's keyword bias, whereas expanding the query with words you already found
> narrows toward that same vocabulary. If your protocol promises snowballing, use
> the consolidation menu's **Citation snowballing** action (or `src/scripts/snowball.py`
> directly in the manual path) and record it as a separate identification method.

It is **resumable**: re-run `python src/slr.py`, choose "Resume an existing
review," and it fast-forwards past every already-completed stage using saved
checkpoints. Every review lives under its own `runs/<run-id>/` workspace
(gitignored -- see [The srp/ library](#the-srp-library-config-state-provenance-export)
below).

A pipeline CSV that exists but can't actually be parsed (truncated mid-write,
saved with the wrong encoding) is never treated as if it were simply missing
or empty -- it stops the wizard with a clear message naming the file, instead
of silently reporting "0 records" for a stage that actually failed to read
its input. Fix or remove the named file, then resume the run.

```bash
python src/slr.py
```

**b) Manual (advanced / transparent): run the stage scripts yourself**

Every stage `slr.py` runs under the hood is also a standalone script under
`src/scripts/`, invocable directly. This is the same pipeline with nothing
hidden -- useful if you want to inspect or hand-edit intermediate CSVs
between stages, run a single stage in isolation, or wire the pipeline into
your own automation. See [Running the pipeline](#running-the-pipeline) for
the full command sequence.

```bash
git clone <this-repo-url>
cd slr-pipeline

python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.lock     # exact pins; requirements.txt = minimums

cp .env.example .env                 # fill in a real MAILTO; add any API keys here
                                     # (loaded automatically; flags override it)

python src/scripts/search.py \
    --query '"intrusion detection" AND "machine learning"' \
    --year-from 2020 --year-to 2026 \
    --mailto you@example.com \
    --max-per-source 40 \
    --out output/candidates.csv

python src/scripts/dedup.py  --in output/candidates.csv       --out output/candidates_dedup.csv
python src/scripts/screen.py --in output/candidates_dedup.csv --out output/screening.csv

# Manual-paste AI-assist screening (no API keys) -- see "AI-assist screening" below
python src/scripts/assist.py build --in output/candidates_dedup.csv --stage ta \
    --out prompt_ta.txt --topic "ML-IDS systematic review"
# paste prompt_ta.txt into any free web chatbot; save the reply to reply.txt
python src/scripts/assist.py parse --response reply.txt --into output/screening.csv --stage ta

python src/scripts/download.py --in output/candidates_dedup.csv --mailto you@example.com --outdir pdfs
python src/scripts/verify_citations.py --csv output/candidates_dedup.csv --limit 10

# Once a human has filled in ta_decision/ft_decision in screening.csv:
python src/scripts/extract.py --in output/screening.csv --out output/extraction.csv

# Once a human has filled in venue_tier/R/A/T/C/quality_tier in extraction.csv:
python src/scripts/figures.py --quality output/extraction.csv --outdir figures

# Export the included set for your reference manager:
python src/scripts/export.py --in output/extraction.csv --out-bib references.bib \
    --out-ris references.ris --included-only
```

### Tests

```bash
pip install pytest
python -m pytest tests/
```

The suite covers the functions whose silent breakage would put a wrong number in
a published paper: deduplication in both directions (false merges *and* missed
preprint/published pairs), the PRISMA count derivation and its cross-phase
totals, the chatbot-reply parser's refusal to fabricate or misattribute a
decision, BibTeX key collisions, Cohen's kappa against its textbook worked
example, and the guarantee that API keys never reach `runs/<id>/config.json`.
Run it after changing anything under `src/srp/` or `src/scripts/`.

A real run of either path writes `output/` and `pdfs/` (manual path) or
`runs/<run-id>/` (guided path) into your working directory -- both are
gitignored. This repository ships with a **committed demonstration run**
instead, under `examples/`: `examples/demo_output/` holds the real CSVs
produced by an actual ML-IDS search (candidates, dedup, screening, download
log, citation verification). The open-access PDFs that `src/scripts/download.py`
fetched for that same run are not tracked in git (12 PDFs, ~50 MB of
example data that every `pipx`/`pip install git+...` would otherwise
re-download for no reason) and are not distributed with the releases;
the CSVs in `examples/demo_output/` are the demonstration record. `examples/` additionally keeps a small curated
sample of each output -- including a fully-decided screening sheet, a
filled-in extraction/quality-scoring sheet, and the figures `src/scripts/figures.py`
renders from them (`examples/figures/`) -- so you can see the CSV and figure
shapes at a glance; see `examples/README.md` for the exact commands used and
one real API gotcha hit along the way.

### What you change vs. what you don't

Every script under `src/scripts/` follows the same convention, stated right after
its module docstring: the **command-line flags are what you're meant to
change** day to day (search terms, thresholds, paths, `--mailto`) -- run
`python src/scripts/<script>.py --help` to see them. The code *below* that point
is marked in two ways: `# --- API internals ---` over anything that builds a
request URL or parses a response from an external API (breaks only if that
API's contract changes), and `# --- core logic ---` over the dedup match
loop, the PRISMA count derivation, and the plotting geometry (safe to leave
alone unless you're deliberately extending the tool). You should not need to
edit anything below either marker for a normal review. `slr.py` and the
`src/srp/` library follow the same two markers for the same reason -- the wizard
prompts and consolidation menu are what you interact with; the phase
orchestration and query-expansion logic underneath are marked `# --- core logic ---`
and don't need touching for a normal review either.

## Repository layout

```
slr-pipeline/
├── src/
│   ├── slr.py                  # guided interactive entry point (run this)
│   ├── srp/                    # importable library
│   │   ├── config.py           # ReviewConfig (secrets are never persisted)
│   │   ├── env.py              # .env loader
│   │   ├── normalize.py        # THE definition of "same DOI" / "same title"
│   │   ├── state.py            # run workspace, atomic checkpoints, decision log
│   │   ├── decisions.py        # the single AI-decision ingestion path
│   │   ├── agreement.py        # Cohen's kappa + conflict lists
│   │   ├── appraisal.py        # field -> critical-appraisal instrument registry
│   │   ├── methods_report.py   # auto-draft the search-methods paragraph
│   │   ├── llm_assist.py       # screening prompt builder + reply parser
│   │   ├── provenance.py       # append-only log + PROVENANCE.md
│   │   └── export.py           # BibTeX / RIS
│   └── scripts/                # the individual stage CLIs (manual / advanced use)
│       ├── search.py           # query up to 10 scholarly APIs
│       ├── dedup.py            # DOI-exact then fuzzy-title+author deduplication
│       ├── screen.py           # build the title/abstract + full-text screening sheet
│       ├── assist.py           # manual-paste AI-assist screening (build prompt / parse reply)
│       ├── snowball.py         # real citation snowballing (backward + forward, OpenAlex)
│       ├── download.py         # resolve + fetch open-access PDFs (arXiv, Unpaywall, OpenAlex, S2, CORE)
│       ├── verify_citations.py # fabricated-citation guard: DOI -> Crossref -> diff
│       ├── extract.py          # build the Stage-4/5 extraction + quality-scoring template
│       ├── figures.py          # PRISMA flow diagrams + quality-/venue-tier charts
│       └── export.py           # export included studies to BibTeX / RIS
├── docs/                       # the full guides: methodology, script reference, AI-assist
├── tests/                      # pytest suite (`python -m pytest tests/`)
├── examples/                   # demo inputs, a completed demo run, and generated figures
│   ├── README.md
│   ├── *_sample.csv            # small curated sample of each stage's output
│   ├── demo_output/            # CSV outputs of the ML-IDS demonstration run
│   └── figures/                # prisma_flow, prisma_2020, quality_tiers, venue_tiers (.png + .pdf)
│   # demo_pdfs/ (the open-access PDFs from that run) is NOT tracked here and is
│   # not distributed with the releases, so a `pipx`/`pip install git+...` never
│   # has to download 50 MB of example PDFs to run the tool.
├── .github/workflows/tests.yml # CI: runs the pytest suite on push/PR
├── pyproject.toml              # package metadata; `slr` console-script entry point for pipx/pip
├── requirements.txt            # minimum versions
├── requirements.lock           # exact pins -- use these for a review you'll publish
├── .env.example
├── CITATION.cff                # citation metadata for the software
├── LICENSE.txt                 # MIT -- applies to the code (src/slr.py, src/srp/, src/scripts/)
├── LICENSE-docs                # CC-BY-4.0 -- applies to README.md, docs/, examples/README.md
└── .gitignore                  # ignores output/, pdfs/, runs/ -- a real run's working files
```

## The end-to-end workflow

```
Protocol  ->  Search  ->  Screen  ->  Extract  ->  Quality-assess  ->  Synthesize  ->  Report
 (PICOC,       (multi-    (title/     (per-study    (R/A/T/C           (narrative      (PRISMA
  RQs,          round      abstract    schema,       rubric,            by RQ,          flow +
  criteria,     Boolean    then       verbatim       Low/Some/          consistency     checklist,
  pre-reg)      queries)   full-text)  numbers)      High per dim)      assessment)     data avail.)
```

`src/scripts/search.py` -> `src/scripts/dedup.py` -> `src/scripts/screen.py` cover Search
and the mechanical half of Screening; `src/scripts/assist.py` optionally
manual-paste-AI-assists the screening decisions, but the decision columns
stay a human call either way. `src/scripts/snowball.py` adds a second,
non-keyword identification method -- real backward/forward citation
snowballing (Wohlin 2014) seeded from your included set, as distinct from
the between-phase query-expansion loop. `src/scripts/download.py` retrieves full
text for the full-text screening and extraction stages. `src/scripts/extract.py`
builds the Extract + Quality-assess template (`R`/`A`/`T`/`C` rubric columns,
again left blank for a human), optionally extended with a field-appropriate
instrument's verbatim domains from `src/srp/appraisal.py`. `src/scripts/verify_citations.py`
runs continuously, against every citation you plan to use, from extraction
through final manuscript. `src/scripts/figures.py` covers Report's PRISMA flow
diagram(s) and the quality-/venue-tier distribution charts; `src/srp/methods_report.py`
drafts the search-methods paragraph from the same recorded search-strategy
log. `src/scripts/export.py` covers Report's reference-list handoff to your
citation manager. `slr.py` orchestrates all of the above end to end, plus the
query-expansion loop between search rounds and the always-on provenance log.

---

## Citing

If slr-pipeline contributes to a published review, please cite the software
using the metadata in `CITATION.cff` (GitHub renders it as the "Cite this
repository" button). Reviews run with the pipeline should also report the
version used; the provenance report records it automatically.

## License

This repository's licensing is split by content type:

- **Code** (`slr.py`, `src/srp/`, and every script under `src/scripts/` -- `search.py`,
  `dedup.py`, `screen.py`, `assist.py`, `download.py`, `verify_citations.py`,
  `extract.py`, `figures.py`, `export.py`) is licensed under the **MIT
  License** -- see [`LICENSE`](LICENSE).
- **Documentation** (this README and `examples/README.md`) is licensed under
  **Creative Commons Attribution 4.0 International (CC-BY-4.0)** -- see
  [`LICENSE-docs`](LICENSE-docs). You're free to reuse, adapt, and redistribute
  the methodology write-up for any purpose, including commercially, as long as
  you give appropriate credit.

Copyright (c) 2026 Md Nayeem Hossain.
