# Script reference

Every stage script, plus the shared `src/srp/` library. Back to the [README](../README.md).

## Running the pipeline

Dependency-light: the individual stage scripts under `src/scripts/` need only
`requests`, `pandas`, `rapidfuzz`, and `matplotlib`. The guided `slr.py`
entry point additionally needs `questionary` and `rich` for its terminal UI
-- both are already in `requirements.txt`. Requires Python 3.10+ (some type
hints use the 3.10 `X | None` union syntax; see `pyproject.toml`'s
`requires-python`).

```bash
pip install -r requirements.txt
```

This section documents each stage script individually, for the manual /
advanced path. `python src/slr.py` (see [Two ways to run this](#two-ways-to-run-this))
runs the same scripts for you, in order, with checkpointed resume.

### `src/scripts/search.py` -- query up to ten scholarly APIs, write `candidates.csv`

```bash
python src/scripts/search.py \
    --query '"intrusion detection" AND "machine learning"' \
    --year-from 2020 --year-to 2026 \
    --mailto you@example.com \
    --max-per-source 40 \
    --out output/candidates.csv
```

Writes one row per candidate to `output/candidates.csv` (columns: `id, source,
title, authors, year, venue, doi, url, abstract`). A single source's failure
(network error, exhausted rate limit) is logged to stderr and skipped, not
fatal to the whole run. `--mailto` is required (falls back to the `MAILTO`
environment variable) -- OpenAlex and Crossref use it for their "polite pool"
of better rate limits, and NCBI requires it on every PubMed call.

#### Sources and their optional API keys

**Every API key is optional.** Six sources need no key at all. For the four
that do, a missing key means that source is *skipped with a note on stderr* --
never an error, and never fatal to the run. Select a subset with `--sources`
(default: all ten).

| Source | Key | Without a key | Documented rate limit |
|---|---|---|---|
| `openalex` | none | full access | polite pool via `--mailto` |
| `crossref` | none | full access | polite pool via `--mailto` |
| `arxiv` | none | full access | ~1 request/3 s (informal) |
| `doaj` | none | full access | 2 requests/s |
| `semanticscholar` | `--s2-api-key` | works, shared anonymous pool (~100 req/5 min across *all* keyless users) | 1 request/s with a key |
| `pubmed` | `--pubmed-api-key` | works at 3 requests/s | 10 requests/s with a key |
| `ieee` | `--ieee-api-key` | **skipped** | 10 calls/s, 200 calls/day |
| `scopus` | `--scopus-api-key` | **skipped** | 9 requests/s, 20,000/week |
| `springer` | `--springer-api-key` | **skipped** | 100 req/min, 500/day (free Basic tier) |
| `core` | `--core-api-key` | **skipped** | 25 req/min personal, 10 req/min academic |
| `wos` | `--wos-api-key` | **skipped** | 2/3/5 req/s (Basic/Advanced/Premium), plus an annual full-record quota |

Each key also falls back to an environment variable (`S2_API_KEY`,
`IEEE_API_KEY`, `SCOPUS_API_KEY`, `SPRINGER_API_KEY`, `PUBMED_API_KEY`,
`CORE_API_KEY`, `WOS_API_KEY`), which `.env` supplies -- see `.env.example`. Precedence is
**flag > exported env var > `.env`**. The throttles above are enforced in
`search.py` as documented constants, each carrying its source URL in a comment;
HTTP 429 is retried with backoff on every source.

Keys are never written into your run folder. `runs/<id>/config.json` holds no
credentials, because that folder is the artifact you are meant to share with
co-authors and attach as a reproducibility appendix. A key typed at the guided
prompt lasts for that session only; **`.env` is the durable home for a key** and
is what a resumed run reads them back from. Keys reach the stage scripts through
the subprocess environment rather than the command line, so they stay out of your
terminal scrollback and out of the process table on a shared machine.

#### The search-strategy log (PRISMA items 6 and 7)

Every run writes `<out>_search_strategy.csv` (the guided path puts it at
`runs/<id>/phase_N/search_strategy.csv`) with one row per source: the **exact
query string sent to that source**, the UTC timestamp it was sent, how many
records came back, how many that source says exist, and whether the result was
truncated.

You need this and it cannot be reconstructed afterwards. PRISMA item 7 requires
the full search strategy for each database including filters and limits, and
item 6 the date last searched -- and the query is *transformed per source*
(arXiv gets `all:{q}`, Scopus gets `TITLE-ABS-KEY(...) AND PUBYEAR > ...`,
Crossref gets `query.bibliographic`, DOAJ gets a Lucene range). Those have
materially different field scopes, and `candidates.csv` records only a `source`
label.

The log also makes **truncation visible**. `--max-per-source` caps every source,
and that cap was previously reported as "records identified". It is not: it is a
relevance-ranked sample drawn by each API's own undocumented ranking. When a
source is truncated, `search.py` says so on stderr and the guided path warns you
and logs which sources to provenance. Raise `--max-per-source` for a real search
round, or report the truncation explicitly in your methods.

One thing the log will show you immediately: a query you wrote as `"a" AND "b"`
can report millions of "available" records on Crossref, because
`query.bibliographic` scores relevance rather than applying your boolean. Read
the `total_available` column before trusting any source's hit count as a
database-level result.

#### Five things worth knowing before you rely on these

**IEEE, Scopus, and Web of Science need an institutional subscription.** None
is realistically obtainable by an unaffiliated researcher: IEEE limits API
access to "current IEEE customers", a Scopus key is authenticated against your
institution's IP range, and a Web of Science Expanded API key requires a
separate licensing agreement with Clarivate on top of your institution's WoS
subscription. Get the access sorted before planning a review around them.

**Scopus only works on campus, unless you have an institutional token.** Pass
`--scopus-insttoken` (or set `SCOPUS_INSTTOKEN`) to use a Scopus key from off
your university network; Elsevier support must enable it for your account.

**Scopus abstracts cost throughput.** Abstracts live only in Scopus's COMPLETE
view, which caps pages at 25 records instead of 200. `search.py` defaults to
`--scopus-view COMPLETE` and automatically falls back to `STANDARD` if your key
is refused -- in which case Scopus rows arrive with an **empty abstract**, and
title-only screening is all they can support.

**IEEE's 200 calls/day is enforced per run, not per day.** `search.py` keeps no
cross-run counter, so several runs in one day can still exhaust the quota.

**Web of Science's annual full-record quota is real and shared across every
run.** Unlike IEEE's per-run call counter, this one isn't tracked locally at
all -- a Basic-tier institutional key typically gets 50,000 full records/year
across your whole institution, not just this tool. Keep `--max-per-source`
modest while testing.

### `src/scripts/dedup.py` -- de-duplicate by DOI, then fuzzy title + author match

```bash
python src/scripts/dedup.py --in output/candidates.csv --out output/candidates_dedup.csv \
    --title-threshold 92
```

Exact (normalized) DOI match first; then `rapidfuzz` `token_sort_ratio` fuzzy
title matching across **all** remaining records, corroborated by a shared author
surname. Adds `doi_norm`, `title_norm`, `duplicate_of`, `dedup_method` columns;
canonical (non-duplicate) records have an empty `duplicate_of`.

This step deletes records before a human ever sees them and its output is the
PRISMA "duplicates removed" number, so the matching rules are deliberately
asymmetric -- a missed duplicate costs one manual catch during screening, a false
merge silently deletes a study from your review forever. Three rules follow from
that:

- **`token_sort_ratio`, not `token_set_ratio`.** The latter scores 100 whenever one
  title's token set is a subset of the other's, so "Anomaly Detection in IoT
  Networks" and "Anomaly Detection in IoT Networks Using Federated Learning" scored
  a perfect match and the second paper was deleted. No threshold could fix that.
- **A length guard** (`--min-length-ratio`, default 0.75). A title that is a strict
  extension of another ("... : A Survey") is a different paper.
- **A shared author surname is required** for any fuzzy merge. Generic titles are
  common in this field -- the sample corpus alone contains two distinct book
  chapters both titled "Machine Learning for Intrusion Detection", by different
  authors -- and title similarity alone is not evidence they are the same work. If
  either record has no parseable author, the merge is refused.

Fuzzy matching runs across year boundaries and across records that already have
DOIs, because the most common duplicate in a real review is an arXiv preprint and
its published version, which differ in **both** year and DOI. Those merges are
labelled `fuzzy_title_crossdoi_<score>` so you can audit them.

**Canonical-record selection**, once a cluster of duplicates is identified, is a
strict priority order: has a DOI (stays citable) > has an abstract (screenable
without hunting down the full text) > first-seen. DOI-presence is deliberately
primary -- `references.bib` is built from the canonical record, and an uncitable
survivor is a bigger loss than a thin one -- so a DOI-bearing record stays
canonical even if a DOI-less duplicate happens to carry a richer abstract.
Abstract-presence only breaks ties among records that are otherwise equal on
citability; it exists because keyed sources like Scopus/IEEE are frequently
abstract-less (entitlement-gated), so without this a paper found only by two
abstract-less sources would silently end up with no abstract for TA screening,
even when the corpus elsewhere had one.

### `src/scripts/screen.py` -- build the screening spreadsheet

```bash
python src/scripts/screen.py --in output/candidates_dedup.csv --out output/screening.csv
```

Writes one row per canonical record with blank `ta_decision`/`ta_reason`
(title/abstract stage) and `ft_decision`/`ft_reason` (full-text stage) columns,
an `abstract` column (so both you and the AI-assist prompt can screen on it), and
a `reviewer` column. Duplicate this file per reviewer before computing
inter-rater agreement -- see [Inter-rater agreement](#inter-rater-agreement-cohens-kappa).

**Re-running is safe.** If the output already exists, decisions already recorded
in it are carried over onto the rebuilt sheet, matched by record id, and the
counts are reported. Adding a search source and re-running the pipeline is an
ordinary thing to do, and it used to destroy every decision in the file silently,
with exit code 0. Pass `--force` to deliberately start over from blank; it tells
you how many decisions it is discarding. A record that is no longer canonical
(now flagged a duplicate) is dropped, and if it carried a decision you get a
warning rather than silence.

### `src/scripts/assist.py` -- manual-paste AI-assist screening

No API keys, no cost, no automated calls to any LLM provider. `assist.py
build` compiles a ready-to-paste screening prompt with the undecided records
embedded in it; you paste that prompt into **any** free web chatbot (ChatGPT,
Claude, Gemini, Copilot, or any other), save its reply to a text file, and
`assist.py parse` reads the reply back and writes `include`/`exclude`/`maybe`
decisions plus a one-sentence reason into `screening.csv`.

```bash
python src/scripts/assist.py build --in output/candidates_dedup.csv --stage ta \
    --out prompt_ta.txt --topic "ML-IDS systematic review"
# -> paste prompt_ta.txt into your chatbot of choice; save the reply to reply.txt

python src/scripts/assist.py parse --response reply.txt --into output/screening.csv \
    --stage ta --prompt prompt_ta.txt
```

`build` also writes `prompt_ta.txt.ids.json` alongside the prompt file -- exactly
the ids sent in that batch. Pass that same prompt path to `parse` via `--prompt`
so it can validate the reply against the batch actually sent, not every id in the
whole sheet; without `--prompt`, `parse` still works but warns that a hallucinated
or echoed id belonging to some other undecided row elsewhere in the sheet
wouldn't be caught.

`--stage` is `ta` (title/abstract) or `ft` (full text) -- run `build`/`parse`
once per stage. `--batch-size` (default 20) and `--start` let you keep each
paste small enough for a chatbot's context window and work through a large
corpus in batches without re-sending already-screened rows. `build` already
skips any row with a decision filled in, so re-running it after a partial
`parse` only re-prompts what's still undecided.

**Screen against your protocol's criteria, not "relevance".** Pass
`--criteria-file` (or `--criteria`, or `--run <run-dir>` to read them from the
review's `config.json`) so the prompt applies your pre-specified eligibility
criteria verbatim. Without one of those, `build` **refuses to write the
prompt** (exit code 2): the fallback would be a generic "include if plausibly
relevant" instruction, which is exactly the unstructured judgement
pre-registration exists to prevent, and a review screened that way cannot
claim to have applied its stated criteria. Pass `--allow-generic-fallback` if
you genuinely intend to screen that way anyway -- it still runs, with a loud
warning on stderr, but it is now an explicit choice rather than a silent
default. The guided path (`slr.py`) asks the same question interactively
before running AI-assist with no criteria configured. The criteria file may
use `INCLUDE:` / `EXCLUDE:` section headers.

**The AI only assists -- it does not decide.** Every `assist.py`-parsed
decision is a *proposed* decision, written into the same `ta_decision`/
`ft_decision` columns a human would fill in by hand, with `reviewer` set to
`ai-assisted` so it's traceable later. The actual inclusion/exclusion call
happens at the human review gate (`slr.py`'s "Human review gate" step in the
guided path; a manual read-through of `screening.csv` in the manual path) --
see the integrity guardrails below.

Four things the parser refuses to do, because each one silently corrupts the
screening log:

- **It will not decide a record that was not in the batch.** Replies are
  reconciled against the ids actually sent (the guided path always does this
  correctly; the manual CLI needs `--prompt` -- see above); an id the model
  invents or echoes from an earlier batch is reported and dropped, not written
  onto whatever record happens to carry that number.
- **It will not overwrite a decision that is already recorded.** Re-parsing a stale
  reply used to replace a human's decision with the model's while leaving the
  human's initials on the row. Pass `--overwrite` if you mean it; attribution then
  moves to `ai-assisted` along with the decision.
- **It will not accept a "maybe" at the full-text stage.** `ft_decision` is
  PRISMA's terminal stage (include or exclude only) -- the prompt doesn't offer
  MAYBE there, and a chatbot that answers one anyway has it refused rather than
  silently written, where it would vanish from the PRISMA counts with no
  exclusion reason recorded.
- **It will not drop anything silently.** Unparseable lines, out-of-batch ids,
  contradictory duplicate decisions, and studies that got no reply back are all
  counted and reported.

This is a different tool for a different job than the "AI-tools integration"
section further down: that section is about using an LLM to draft or read
prose (search-term expansion, section drafting, synthesis summaries);
`assist.py` is specifically the screening-assist step, and unlike those tasks
it needs no account, API key, or paid subscription -- any free web chatbot
works.

### Full-text screening -- where "included" is actually decided

In the guided path (`python src/slr.py`), the consolidation menu's **Full-text
screening** step walks you through the merged TA-included set one study at a
time and records `ft_decision` / `ft_reason` into `included_final.csv`. An
exclusion requires a reason, because PRISMA item 16b requires you to cite the
reports you excluded at full text and say why.

This matters more than it sounds. PRISMA 2020 defines the "Studies included in
synthesis" box as studies that survived **full-text eligibility assessment**.
Title/abstract includes are papers you thought looked promising, not papers you
read. The PRISMA `included` count is therefore sourced from `ft_decision ==
include` only: until you record full-text decisions, `included` is 0 and the
tool tells you why rather than quietly reporting your TA-includes as included
studies.

If you prefer, edit the `ft_decision` / `ft_reason` columns of
`included_final.csv` by hand and re-run the step to fold them into the audit
trail.

### Inter-rater agreement (Cohen's kappa)

Two reviewers screen the same records independently, then you compute kappa
**before** reconciling. Kitchenham SS6.3 and Cochrane SS4.6.1 both require
this, and single-reviewer screening is a named validity threat -- the first
question a peer reviewer asks about a review's screening is what the agreement
was.

To do it: copy a phase's `screening.csv`, have the second reviewer fill in
`ta_decision` without seeing the first reviewer's calls, then use the guided
path's **Inter-rater agreement** menu step and point it at both files. You get
Cohen's kappa with its Landis & Koch band, raw percent agreement, a confusion
matrix, and `conflicts_<stage>.csv` listing every disagreement to reconcile.
Say in your methods how you resolved them (discussion, or a third reviewer).

Only records **both** reviewers decided are compared -- that is what kappa is
defined over; records only one of them touched are missing data, not
disagreement. One caveat the tool enforces rather than hides: if both reviewers
used a single identical label (everything included, say), chance agreement is
100%, kappa is mathematically undefined, and reporting it as 1.0 would be a
misreported statistic. In that case you get the raw agreement and an
explanation instead of a number.

### `src/scripts/download.py` -- resolve and fetch open-access PDFs

```bash
python src/scripts/download.py --in output/candidates_dedup.csv --mailto you@example.com \
    --outdir pdfs --max-downloads 50
```

Tries, per record, in order: an arXiv direct-link pattern (no network lookup
needed to resolve), then Unpaywall, OpenAlex, Semantic Scholar, and CORE (any
record with a DOI; CORE also falls back to a title search). `--mailto` is
required by Unpaywall's API on every request and used as the polite-pool
contact for OpenAlex. CORE is optional -- pass `--core-api-key` (or set
`CORE_API_KEY` in `.env`, see `.env.example`) to enable it; without a key
that source is skipped. `--sources` overrides the order/set of sources tried
(default `arxiv,unpaywall,openalex,semanticscholar,core`).

Writes `output/download_log.csv` (columns: `id, doi, oa_status, method,
saved_path`); records with no open-access copy are logged as such and
skipped, not force-downloaded. Every record that ends without a saved PDF is
also written to `--report` (default `output/manual_download_needed.csv`,
columns: `id, doi, title, url, tried, reason`) so you know exactly which
PDFs need manual retrieval, and why each automated source failed.

**A real gotcha:** Unpaywall specifically rejects `@example.com`/`@example.org`
placeholder addresses with an HTTP 422 ("please use your own email address").
OpenAlex and Crossref don't mind a placeholder address, so this only surfaces
once you run `download.py` -- use a real, deliverable mailbox. See
`examples/README.md` for the exact error hit during this repo's own
verification run.

**Do not skip the manual verification step after this script runs.**
Automated resolution gets the wrong PDF often enough to matter. Check every
downloaded PDF's title page against the expected title before it enters
extraction.

### `src/scripts/extract.py` -- build the Stage-4/5 extraction + quality-scoring template

```bash
python src/scripts/extract.py --in output/screening.csv --out output/extraction.csv \
    --include-col ft_decision
```

Filters `output/screening.csv` down to the rows a human marked `include` in
`--include-col` (default `ft_decision`, the full-text stage; falls back to
`ta_decision` if `ft_decision` is missing or entirely blank, so the script is
also runnable as a dry run right after `screen.py`). `authors` is joined in by
`id` from `--candidates` (default `output/candidates_dedup.csv`), since the
screening sheet itself doesn't carry it. Writes `output/extraction.csv` with
columns `id, title, authors, year, venue, doi, thematic_class, study_type,
contribution, key_findings, rq_mapping, limitations, venue_tier, R, A, T, C,
quality_tier` -- the first six are pre-filled metadata; everything else is the
Stage-4/5 template, left blank for a human reviewer.

`venue_tier` is `T1`/`T2`/`T3` per the README Stage 5 definitions above.
`R`/`A`/`T`/`C` are each rated `Low`/`Some`/`High` concern -- **R**igor,
**A**rtifact availability, **T**hreat-model completeness, and **C**urrency
(see Stage 5 for the full rubric) -- and `quality_tier` is the aggregate
`A`/`B`/`C` letter grade a reviewer derives from them via a published
aggregation formula, decided before scoring begins. `extraction_reviewer`,
`extraction_date`, and `notes` are always appended last, for piloting/audit
traceability (see Stage 5's "Field-driven critical appraisal" above).

```bash
# Append a field-appropriate appraisal instrument's verbatim domains
python src/scripts/extract.py --in output/screening.csv --instruments dyba_dingsoyr

# Pilot: sample 10 studies into a second sheet for independent double extraction
python src/scripts/extract.py --in output/screening.csv --pilot 10

# Compare two completed extraction sheets -- Cohen's kappa + conflicts per column
python src/scripts/extract.py --compare-a extraction.csv --compare-b extraction_pilot.csv
```

### `src/scripts/snowball.py` -- real citation snowballing (backward + forward)

```bash
python src/scripts/snowball.py --seeds runs/<id>/final_dedup.csv --mailto you@example.com \
    --direction both --max-per-seed 50 --out output/candidates_snowball.csv
```

This is Wohlin (2014) citation snowballing -- distinct from `slr.py`'s
between-phase "query expansion," which mines keywords from included titles and
was previously (incorrectly) also called snowballing. This script walks the
actual citation graph via OpenAlex: **backward**, reading each seed study's
reference list; **forward**, finding papers that cite each seed. Point
`--seeds` at `included_final.csv` or `final_dedup.csv`; results are written in
the same `Candidate` shape as `search.py`'s output, with a `snowball_from`
column recording which seed(s) surfaced each record.

Run the output through `dedup.py` and `screen.py` exactly like a database
search's hits -- a citation hit is an identification candidate, not an
automatic include. In the guided path, use the consolidation menu's **Citation
snowballing** action (which does this for you, into `runs/<id>/snowball/`) and
then **Merge citation-snowball results into included set** once you've screened
them, which namespaces their ids (`sb_N`) and tags them `phase="snowball"` so
they can never collide with a database-search id and stay distinguishable in
PRISMA reporting as their own identification method.

A title-search fallback (used when a seed has no DOI) is guarded by a
`rapidfuzz` similarity check against the seed's own title -- OpenAlex's title
search always returns its single best hit, even for a title with no genuine
match, so an ungated fallback would silently resolve an unrelated paper and
pull in its entire citation subgraph.

### `src/scripts/verify_citations.py` -- the fabricated-citation guard

```bash
# Check every DOI in a CSV against what it actually resolves to on Crossref
python src/scripts/verify_citations.py --csv output/candidates_dedup.csv \
    --doi-col doi --title-col title --limit 10 \
    --out output/citation_verification.csv

# Check a single ad hoc citation
python src/scripts/verify_citations.py --doi 10.1002/ett.4150 \
    --claimed-title "Network intrusion detection system: A systematic study of machine learning and deep learning approaches"
```

Resolves each DOI against `api.crossref.org/works/<doi>` and fuzzy-matches the
claimed title against what the DOI actually returns (threshold 85/100). Exits
0 only if every checked citation passes -- wire it into a pre-submission check.
Run this against **every** citation before it enters a manuscript, not just
ones that look suspicious: a fabricated citation can sit inside an otherwise
legitimate-looking paper that has already passed title-level screening.

### Figures (after screening + scoring)

`src/scripts/figures.py` generates the Stage 7 reporting figures directly from
the pipeline's own CSVs -- no manual diagramming tool needed. Every figure is
written as both a 300 dpi PNG (for a manuscript/slide) and a vector PDF (for
print/typesetting).

```bash
python src/scripts/figures.py \
    --screening output/screening.csv \
    --dedup output/candidates_dedup.csv \
    --candidates output/candidates.csv \
    --quality output/extraction.csv \
    --outdir figures
```

`--screening`/`--dedup`/`--candidates` each read exactly ONE file, and
`--screening`'s `ft_decision` is read from that same file -- correct for a
single-phase review (or the worked example above, where one sheet carries
both `ta_decision` and `ft_decision`). **A guided-TUI run with more than one
search phase (`n_phases > 1`) needs `--run-dir` instead**, or `identified`/
`screened`/`excluded_ta` silently undercount (only one phase's files are
read) and `excluded_ft`/`included` silently read as 0 (`ft_decision` lives in
the run's `included_final.csv`, never in any single phase's `screening.csv`):

```bash
python src/scripts/figures.py --run-dir runs/<run-id> \
    --quality runs/<run-id>/extraction.csv --outdir figures
```

This reads every `phase_N/` subdirectory under `--run-dir` plus its
`included_final.csv` and sums across all of them -- the same logic the
guided TUI's "Generate PRISMA + tier figures" action already uses
(`src/srp/prisma.py`, one shared implementation for both entry points). Either
way, an unbalanced result (e.g. `included` exceeding `assessed_ft`) prints a
`PRISMA check:` warning to stderr rather than silently shipping a
self-contradicting diagram.

Four figures land in `--outdir`:

1. **`prisma_flow.png`/`.pdf`** -- the simplified PRISMA 2020 flow diagram
   (Identification -> Screening -> Eligibility -> Included), drawn with
   matplotlib patches and arrows. Counts are derived automatically: records
   identified from `--candidates`; duplicates removed from `--dedup`'s
   `duplicate_of` column; records screened, and excluded/assessed/included
   counts, from `--screening`'s `ta_decision`/`ft_decision` columns
   (full-text exclusion reasons are summarized from `ft_reason`, if
   present). Pass `--identified`/`--duplicates-removed`/`--screened`/
   `--excluded-ta`/`--assessed-ft`/`--excluded-ft`/`--included` to override
   any single box with a final, manually-decided number -- e.g. once the
   review is complete and the published diagram should match the exact
   numbers in the manuscript. Every count actually used (derived or
   overridden) is printed to stdout.
2. **`prisma_2020.png`/`.pdf`** -- the **official PRISMA 2020 three-column
   template**: previous studies | new studies via databases & registers |
   new studies via other methods. Note that the later phases are additional
   *database* searches with an expanded query, not identification by another
   method -- if you populate the "other methods" column, it should be from a
   genuine non-database pass (citation snowballing, contacting authors, grey
   literature), which you do by hand. Every number the pipeline actually
   knows -- identified, duplicates removed, screened, excluded at TA,
   assessed at full text, included -- is filled in automatically, exactly
   like `prisma_flow.png`. Boxes the pipeline has no way to know
   (registers, automation tools used, a previous-version count, and most of
   the "other methods" column) are left as `(n = )`, exactly like the
   fillable official template, for the reviewer to complete by hand. See
   `examples/figures/prisma_2020.png` for a rendered example.
3. **`quality_tiers.png`/`.pdf`** -- a bar chart of the `A`/`B`/`C`
   `quality_tier` counts from `--quality` (default `output/extraction.csv`).
4. **`venue_tiers.png`/`.pdf`** -- a bar chart of the `T1`/`T2`/`T3`
   `venue_tier` counts from the same file.

Both tier charts render a clear "no data yet" placeholder instead of crashing
if their column is missing or entirely blank -- expected on a first run,
before a human has filled in `extraction.csv`. See `examples/figures/` for
what all four look like once the extraction/quality sheet is actually filled
in (rendered from `examples/screening_decided_sample.csv` and
`examples/quality_scored_sample.csv`; commands in `examples/README.md`).

### `src/scripts/export.py` -- export references for your citation manager

```bash
python src/scripts/export.py --in output/extraction.csv --out-bib references.bib \
    --out-ris references.ris --included-only
```

Turns the final included-studies set into `references.bib` (BibTeX, for
LaTeX/Overleaf) and `references.ris` (RIS, for Zotero/Mendeley/EndNote).
`--in` accepts `output/extraction.csv` (has `authors`) or, before extraction
has run, `output/screening.csv` (no `authors` column, so exported entries
will be missing it). `--included-only` filters to rows marked `include` in
`ft_decision` (falling back to `ta_decision` if `ft_decision` isn't present
or is entirely blank). `--formats` (default `bibtex,ris`) restricts output to
one format if you only need one.

---

## The `src/srp/` library (config, state, provenance, export)

`slr.py` and `src/scripts/assist.py`/`src/scripts/export.py` are built on a small
importable library under `src/srp/`, rather than reimplementing run bookkeeping
per script:

- **`src/srp/config.py`** -- `ReviewConfig`: the review's settings (topic,
  keywords/keyword blocks, year range, sources, thresholds, reviewer name,
  which chatbot is doing AI-assist, research field and chosen appraisal
  instruments) as one JSON-serializable dataclass, saved to and loaded from
  `runs/<run-id>/config.json`; secrets are hydrated from the environment and
  never persisted to that file.
- **`src/srp/env.py`** -- loads `.env` into the process environment (flag >
  exported env var > `.env` precedence; see "Sources and their optional API
  keys" above).
- **`src/srp/normalize.py`** -- the single definition of "same DOI" / "same title"
  (`normalize_doi()`, `normalize_title()`, `record_key()`), shared by
  `src/scripts/dedup.py` and `src/srp/state.py` so the two can never silently disagree
  on what counts as a duplicate.
- **`src/srp/state.py`** -- `RunState`: a per-review workspace under
  `runs/<run-id>/`, with a `state.json` of per-phase/per-stage checkpoints
  (what `slr.py`'s "Resume" option fast-forwards past) and an append-only
  `decisions.jsonl` decision log. That log doubles as a cross-phase
  "already-judged" cache keyed by normalized DOI or title
  (`record_key()`) -- so when phase 2's snowball search re-surfaces a record
  already screened in phase 1, it's silently skipped rather than re-asked
  about.
- **`src/srp/decisions.py`** -- the single ingestion path for a proposed decision
  (from `src/scripts/assist.py` or the guided AI-assist step) into `ta_decision`/
  `ft_decision`, and the shared "maybe proceeds like include" policy
  (`pick_progressed()`) that `extract.py`, `export.py`, and the PRISMA count
  derivation all read from, so they can't silently diverge on which rows count
  as included.
- **`src/srp/agreement.py`** -- Cohen's kappa, its Landis & Koch band, and the
  confusion matrix behind [Inter-rater agreement](#inter-rater-agreement-cohens-kappa)
  and `extract.py --compare-a/--compare-b`.
- **`src/srp/appraisal.py`** -- the field -> critical-appraisal-instrument
  registry behind [Field-driven critical appraisal](#field-driven-critical-appraisal-prisma-item-11):
  `instrument_columns()` for primary-study extraction-sheet columns,
  `render_review_self_appraisal()` for the review-level checklist file, and
  `compose_appraisal_disclosure()` for the `PROVENANCE.md` paragraph.
- **`src/srp/quality_tier.py`** -- `compute_quality_tier()`, the mechanical R/A/T/C
  aggregation formula from Stage 5 above. Used by the consolidation menu's
  "Review/correct an extraction record" action to auto-fill `quality_tier`
  whenever R/A/T/C are all set, and to detect when a hand-set `quality_tier`
  disagrees with the formula (logged as a manual override, not silently
  overwritten or silently accepted).
- **`src/srp/prisma.py`** -- `derive_prisma_counts_for_run()`, the multi-phase
  PRISMA-count derivation (sum identified/screened/excluded-ta across every
  phase, read excluded-ft/included from the run's `included_final.csv`) and
  `prisma_residuals()`'s balance check. The single implementation behind both
  `slr.py`'s guided "Generate PRISMA + tier figures" action and
  `src/scripts/figures.py --run-dir` (see "Figures" above) -- extracted after
  the standalone script's own single-sheet derivation was found to silently
  zero `excluded_ft`/`included` on a real multi-phase run, since it has no
  way to know `ft_decision` lives in a different file.
- **`src/srp/methods_report.py`** -- drafts the search-methods paragraph and its
  supplementary tables from the recorded search-strategy log and PRISMA
  counts (the consolidation menu's "Draft methods paragraph" action).
- **`src/srp/provenance.py`** -- `Provenance`: an always-on run manifest. Every
  search query, its date, source, and hit count; every dedup pass; every
  AI-assisted screening batch and its parsed counts; every human review-gate
  override -- all logged as one JSON line per event to
  `runs/<run-id>/provenance.jsonl`, then rendered to a human-readable
  `runs/<run-id>/PROVENANCE.md` (via the consolidation menu's "Write
  provenance report", or `Provenance.render_markdown()` directly). This is
  your PRISMA methods audit trail -- it includes an explicit
  **AI-assistance disclosure** that names the specific chatbot used for
  screening (from the `assist_tool_name` you gave the setup wizard), or
  states plainly that no AI assistance was used if you left it blank.
- **`src/srp/export.py`** -- the BibTeX/RIS field-formatting logic shared by
  `src/scripts/export.py` and `slr.py`'s consolidation-menu export step.
- **`src/srp/llm_assist.py`** -- the manual-paste prompt builder and reply
  parser behind `src/scripts/assist.py` (see above) and `slr.py`'s AI-assist
  phase step.

None of this needs to be imported directly for the manual `src/scripts/`
workflow -- `src/scripts/export.py` always works CSV-only, and `src/scripts/assist.py`
only touches `src/srp/state.py`'s cross-phase cache if you pass it `--run
<run-dir>`; without that flag it also works CSV-only.

---
