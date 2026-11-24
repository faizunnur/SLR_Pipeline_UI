# The 7-stage method

The methodology slr-pipeline implements, stage by stage. Back to the [README](../README.md).

## The 7-stage method

### Stage 1 -- Protocol & scoping

Fix, before running any search: the review's scope, its research questions,
and its inclusion/exclusion criteria. **PICOC** (Population, Intervention,
Comparison, Outcome, Context) is the Kitchenham & Charters (2007) adaptation of
the clinical-trial PICOS framework for software/systems topics -- use it
instead of PICOS whenever the review isn't about a clinical intervention,
since PICOS's "study design" axis doesn't map cleanly onto systems/security
literature.

**Worked example: ML-IDS PICOC**

| Element | Definition |
|---|---|
| Population | Network intrusion detection systems (NIDS) and related anomaly-detection systems for network security. |
| Intervention | Application of machine learning or AI methods (classical ML, deep learning, ensembles, transformers, GANs, federated learning) to detection, classification, or triage of network intrusions/anomalies. |
| Comparison | Signature-based or rule-based detection baselines, and/or classical (non-deep) ML baselines, where a study reports one. |
| Outcome | Reported detection accuracy/F1/AUC, false-positive rate, computational overhead, robustness to adversarial/evasive traffic, and dataset/reproducibility characteristics. |
| Context | Peer-reviewed and high-quality preprint literature, 2018-2026, in security, networking, and machine-learning venues. |

Derive your research questions (RQs) from this table -- each RQ should trace
to a specific PICOC cell (usually an Intervention sub-area), not be invented
independently of it. A typical ML-IDS review might use: RQ1 (which ML/AI
architectures are applied, and how), RQ2 (what datasets and evaluation
protocols are used, and how comparable are they), RQ3 (what performance and
robustness is reported, under what threat models), RQ4 (cross-cutting:
reproducibility and production-readiness gaps).

**Blank PICOC template**

| Element | Your definition |
|---|---|
| Population | *What systems/subjects/artifacts is this review about?* |
| Intervention | *What technique, algorithm, or approach is being evaluated?* |
| Comparison | *What is it being compared against, where a study reports one?* |
| Outcome | *What outcomes/metrics does the review care about?* |
| Context | *Time window, venue quality floor, language, and any named exceptions.* |

**Inclusion/exclusion criteria template**

Included:
- Primary studies proposing or evaluating an ML/AI method directly in scope.
- Cross-cutting work spanning two or more PICOC-Intervention sub-areas.
- Foundational/survey literature engaged as background evidence, cited but not
  counted as a primary study.

Excluded:
- Unpublished internal/vendor documentation.
- Work in an adjacent field with no specific in-scope angle (e.g., general
  malware classification with no network-intrusion framing).
- Non-English literature without a critical foundational gap.
- Gray literature (vendor blogs, AI-aggregator pages) regardless of topical fit
  -- state this source-quality floor in the protocol up front, not improvised
  mid-screening.

The guided wizard now asks about the grey-literature exclusion decision above
directly (PRISMA item 14 -- excluding it is defensible, but it's also a
publication-bias amplifier, so it needs a stated reason rather than silence),
plus a reporting-bias-assessment question (item 21 -- only meaningful if you
plan to meta-analyze; if you don't, the tool records that the item is scoped
out rather than fabricating a funnel plot for a narrative synthesis), and the
administrative items -- funding, competing interests, language restriction
(items 25, 26, 5). All of these render into `PROVENANCE.md`.

**Pre-register the protocol.** Kitchenham & Charters' guidelines don't mandate
registration the way Cochrane/PRISMA clinical reviews do -- do it anyway.
[OSF Registries](https://osf.io/registries) is the practical default for
CS/security/systems topics: general-purpose, free, and it timestamps a frozen
copy of your PICOC, RQs, search strings, and criteria before you run the first
query. [PROSPERO](https://www.crd.york.ac.uk/prospero/) is the standard
clinical/health registry and will typically reject a registration with no
health outcome -- use OSF instead unless your topic genuinely has a clinical
angle. Registering costs under an hour and gives you a citable, timestamped
artifact proving the RQs and criteria weren't adjusted after seeing results.

---

### Stage 2 -- Search strategy

Two structurally different approaches, usually combined:

- **Boolean queries** run directly against bibliographic databases (IEEE
  Xplore, Scopus, ACM Digital Library, Web of Science) -- high precision on
  field scope, fully reproducible, but requires institutional access.
- **API / web search / snowballing** (arXiv, Semantic Scholar, OpenAlex, DBLP,
  Google Scholar, backward/forward citation chasing per Wohlin 2014 --
  `src/scripts/snowball.py`, or the guided path's **Citation snowballing** menu
  action) -- lower barrier, catches preprints Boolean-indexed databases miss,
  but is less systematic and harder for someone else to reproduce exactly.

#### (a) General fill-in template

Thematic blocks are ANDed together; synonyms within a block are ORed:

```
(<Field>:"<block-1-term-a>" OR <Field>:"<block-1-term-b>" OR <Field>:"<block-1-term-c>")
AND
(<Field>:"<block-2-term-a>" OR <Field>:"<block-2-term-b>" OR <Field>:"<block-2-term-c>")
[AND (<Field>:"<block-3-term-a>" OR ...)]     # optional third thematic block if your
                                                # topic is a three-way intersection
Filters: <year-from>-<year-to>, <document types>, <language>
```

`<Field>` is `Title` for IEEE Xplore and ACM DL command/advanced search; it is
`TITLE-ABS-KEY` (a composite field covering the whole query) for Scopus. Run
one query per pairwise thematic intersection you actually care about -- don't
cram three-plus blocks into one query with nested ANDs; it collapses recall
fast and gets hard to audit later.

The guided wizard (`python src/slr.py`) now builds exactly this block structure for
`search.py`'s automated sources too: it asks for one concept block at a time
(synonyms within a block, comma-separated) and ANDs the blocks together --
`(intrusion detection OR IDS) AND (machine learning OR deep learning)` -- rather
than flattening every term you enter into one giant AND chain. Five keywords
used to become a five-way mandatory AND before this; now they form whatever
block structure you actually intend.

#### (b) Worked example: ML-IDS Boolean strings, three databases

Two thematic blocks: **detection-task terms** and **ML/AI-method terms**.

- Block 1 (detection task): `intrusion detection`, `IDS`, `NIDS`,
  `anomaly detection`, `network intrusion`
- Block 2 (ML/AI method): `machine learning`, `deep learning`,
  `artificial intelligence`, `neural network`, `ensemble`, `transformer`,
  `GAN`, `federated learning`

**IEEE Xplore, Command Search syntax:**

```
("Document Title":"intrusion detection" OR
 "Document Title":"IDS" OR
 "Document Title":"NIDS" OR
 "Document Title":"anomaly detection" OR
 "Document Title":"network intrusion")
AND
("Document Title":"machine learning" OR
 "Document Title":"deep learning" OR
 "Document Title":"artificial intelligence" OR
 "Document Title":"neural network" OR
 "Document Title":"ensemble" OR
 "Document Title":"transformer" OR
 "Document Title":"GAN" OR
 "Document Title":"federated learning")
```

Filters: e.g. 2018-2026, Conferences + Journals, English.

**Scopus, Advanced Search syntax:**

```
TITLE-ABS-KEY ( ( "intrusion detection" OR "IDS" OR "NIDS" OR
  "anomaly detection" OR "network intrusion" )
AND ( "machine learning" OR "deep learning" OR "artificial intelligence" OR
  "neural network" OR "ensemble" OR "transformer" OR "GAN" OR
  "federated learning" ) )
AND PUBYEAR > 2017 AND PUBYEAR < 2027
```

**ACM Digital Library, Advanced Search syntax:**

```
[[Title: "intrusion detection"] OR [Title: "IDS"] OR [Title: "NIDS"] OR
 [Title: "anomaly detection"] OR [Title: "network intrusion"]]
AND
[[Title: "machine learning"] OR [Title: "deep learning"] OR
 [Title: "artificial intelligence"] OR [Title: "neural network"] OR
 [Title: "ensemble"] OR [Title: "transformer"] OR [Title: "GAN"] OR
 [Title: "federated learning"]]
```

**Web of Science, Advanced Search syntax** (this is what `search.py --wos-api-key`
actually sends, via `TS=` -- Topic, which covers title + abstract + author
keywords + Keywords Plus):

```
TS=("intrusion detection" OR "IDS" OR "NIDS" OR "anomaly detection" OR
    "network intrusion")
AND
TS=("machine learning" OR "deep learning" OR "artificial intelligence" OR
    "neural network" OR "ensemble" OR "transformer" OR "GAN" OR
    "federated learning")
AND PY=(2018-2026)
```

**Dataset names (CIC-IDS2017, UNSW-NB15, NSL-KDD) and target venues (IEEE
TIFS, IEEE TDSC, USENIX Security, NDSS, ACM CCS, Computers & Security, IEEE
Access) are deliberately not in the Title-field strings above.** Dataset names
almost never appear in a title -- they live in the abstract or keywords -- so
a Title-only Boolean query using them would starve recall. Two better uses:

1. Run a **separate abstract/keyword-field pass** where the database supports
   it (Scopus's `TITLE-ABS-KEY` already covers abstract; for IEEE Xplore, use
   `"Abstract":"CIC-IDS2017"` etc. as a distinct supplementary query, not
   merged into the Title-field pass, since mixing field scopes inside one
   Boolean string silently changes what "AND" means).
2. Use the venue list to seed a **manual/snowballing pass**: hand-check the
   last 2-3 years of TIFS/TDSC/CCS/NDSS/USENIX Security/Computers &
   Security/IEEE Access tables of contents for ML-IDS papers the Boolean
   queries missed. Scopus can also do this directly with
   `AND SRCTITLE("IEEE Transactions on Information Forensics and Security")`
   appended to a query, as a targeted venue-scoped check.

**A genuine methodological caution specific to this domain:** `"IDS"` as a
bare Title-field acronym is a false-positive magnet (matches "Interactive
Display System," "Integrated Delivery System," etc., in unrelated fields).
Screen its hit list in isolation before trusting the combined AND result -- if
the false-positive rate is high, tighten it to `"IDS" AND ("network" OR
"intrusion")` as its own sub-clause, since ML/AI terms alone won't reliably
filter out a false "IDS" hit from an unrelated field that happens to also
mention, say, "ensemble" in an unrelated statistical sense.

#### Database-specific syntax differences

| | IEEE Xplore | ACM Digital Library | Scopus | Web of Science |
|---|---|---|---|---|
| Field qualifier | Repeat `"Document Title":` before **every** term | Wrap **every** term in its own `[Title: "..."]` bracket | One `TITLE-ABS-KEY(...)` wrapping the *entire* boolean expression | One `TS=(...)` per clause (or `TI=`/`AU=`/`SO=` for title/author/source-only) |
| Default search field | None -- field must be explicit per term | None -- field must be explicit per term | `TITLE-ABS-KEY` covers title + abstract + author keywords, not title alone | `TS=` (Topic) covers title + abstract + author keywords + Keywords Plus |
| Year filter | UI facet or separate filter parameter | UI facet (Publication Date range) | Inline operators: `PUBYEAR > 2017 AND PUBYEAR < 2027` | Inline: `PY=(2018-2026)` |
| Phrase matching | Double quotes | Double quotes | Double quotes | Double quotes |
| Boolean operators | `AND`/`OR`/`NOT`, conventionally capitalized | `AND`/`OR`/`NOT` | `AND`/`OR`/`AND NOT`, case-insensitive | `AND`/`OR`/`NOT`, conventionally capitalized |

Draft each database's string separately from the same thematic block list --
don't try to write one universal string and hope it parses everywhere. Also
decide up front whether you're searching Title-only or Title-Abstract-Keyword:
a database that defaults to a broader field (Scopus's `TITLE-ABS-KEY` and Web
of Science's `TS=` vs. IEEE Xplore/ACM DL's Title-only) will structurally
return more records for the same thematic blocks -- expect and disclose that
yield asymmetry rather than treat
it as a bug.

**Run the full battery of databases in the first formal search round**,
informed by the pre-registered protocol from Stage 1, rather than treating
Boolean-database search as a later phase bolted onto an initial informal
web-search pass. Late-discovered, topically-relevant records that then need a
dedicated access-recovery round are exactly the failure mode this avoids.

---

### Stage 3 -- Screening & selection

PRISMA 2020's two-stage screening: **title/abstract** against the
inclusion/exclusion criteria, then **full-text** eligibility for everything
that survives. Before either pass, de-duplicate -- exact DOI match first, then
fuzzy title match for DOI-less records (arXiv preprints, some gray-adjacent
sources). This is exactly what `src/scripts/dedup.py` automates (see "Running the pipeline" below).

**Illustrative screening funnel** (a generic, illustrative shape -- not real
study results -- to show the level of detail a PRISMA flow diagram needs):

```
Identification                                Screening & Eligibility
  n = X records  ─────────────────────────►  De-duplication (dedup.py)
  (across N search rounds/sources)            + title/abstract screening
                                               + full-text eligibility
                                                         │
                        ┌────────────────────────────────┼──────────────────┐
                        ▼                                                   ▼
              Excluded: n = ...                                   Included: n = ...
              - Round 1: <n> (reason, denominator)
              - Round 2: <n> (reason, denominator)
```

Every number in the excluded box should be traceable to a specific process
step in your methodology section -- name the mechanism (deduplication,
keyword filtering, screening) and give the raw denominator it came from, not
just a bare total.

**Improvements worth building in from the start, not bolted on later:**

1. **Dual independent reviewers with Cohen's kappa.** Have each reviewer
   record their title/abstract decision **before** any discussion -- a
   `reviewer` column per decision, not a merged consensus-only record (see
   `screen.py`'s `screening.csv` schema below), then compute kappa on the two
   independent decision columns. A kappa below about 0.6 signals the criteria
   need tightening before you screen further, not just "agree to disagree."
2. **A dedicated dedup tool, not manual eyeballing.** `src/scripts/dedup.py`
   (exact DOI match, then `rapidfuzz` token_sort_ratio fuzzy title match) is
   deterministic and re-runnable, unlike a human scanning a spreadsheet.
3. **A documented screening log from the first record onward.** Every
   screened record gets an explicit decision + reason at both stages, even
   for the overwhelming majority you exclude in seconds -- this is what makes
   a PRISMA flow diagram auditable rather than asserted.
4. **Verify every downloaded PDF's title page against its expected title
   before it enters extraction.** Automated OA resolution (`src/scripts/download.py`) can
   fetch the wrong PDF -- a similarly-titled paper, a landing page mislabeled
   as a PDF -- often enough to matter. Check the actual file every time.

---

### Stage 4 -- Data extraction

For every study that survives full-text screening, extract a **fixed schema**
of fields, not free-form notes. Fixed fields are what make cross-study
synthesis (Stage 6) and quality scoring (Stage 5) tractable at scale.

**Extraction CSV header** (exactly what `src/scripts/extract.py` writes -- see
"Running the pipeline" below for the full command):

```csv
id,title,authors,year,venue,doi,thematic_class,study_type,contribution,key_findings,rq_mapping,limitations,venue_tier,R,A,T,C,quality_tier,extraction_reviewer,extraction_date,notes
```

Column notes:

- `venue_tier`: numeric 1/2/3, see Stage 5 -- keep it separate from
  `quality_tier`; they are different axes.
- `R`/`A`/`T`/`C`: the per-dimension Rigor/Artifact/Threat-model/Currency
  ratings (`Low`/`Some`/`High` concern) that `quality_tier` is aggregated
  from -- see Stage 5's rubric.
- `key_findings`: copy numbers verbatim from the source, with enough context
  (dataset, split, baseline, hardware) to be usable later without re-reading
  the paper. Do not convert units at extraction time -- do conversions at
  synthesis time, with the original preserved alongside.
- `rq_mapping`: which research question(s) this study evidences; a study can
  map to more than one RQ (semicolon-separated).
- One row per study. If a study is later found to be a duplicate of another
  (e.g., a preprint and its camera-ready), merge into one row and note the
  merge in `notes` -- don't keep both as separate synthesis inputs.

`extract.py` only builds the sheet with blank rows; filling it in (and
correcting a later mistake -- a wrong `R` rating, a typo in `key_findings`)
is a human step. In the guided path, do this through the consolidation menu's
**Review/correct an extraction record** action rather than hand-editing the
CSV: `venue_tier`/`R`/`A`/`T`/`C`/`quality_tier` are edited through a menu so
an invalid value can't be typed, free-text fields keep their current value as
the default so pressing Enter never blanks them, and every change is logged
to `provenance.jsonl` -- unlike a raw CSV edit, which leaves no record of what
changed or when. Editing R/A/T/C recomputes `quality_tier` automatically per
Stage 5's formula.

---

### Stage 5 -- Quality assessment

A structured per-study quality rating, distinct from inclusion/exclusion. It
tells the synthesis stage *how much weight* to put on a given study's
findings, and should be visible to the reader as a rubric, not an implicit
"trust me" judgment.

**The four-dimension rubric (R/A/T/C)**, adapted from Kitchenham & Charters
(2007) and Dybå & Dingsøyr (2008) software-engineering quality criteria -- not
a clinical instrument (don't reach for a medical risk-of-bias tool for a
systems/ML topic; it won't have the right axes):

| Code | Dimension | Criteria |
|---|---|---|
| **R** | Rigor | Appropriateness of methodology for the claims made; statistical validity; threats-to-validity discussion; experimental scale. |
| **A** | Artifact availability | Source code, dataset, and hyperparameter availability; independent verifiability. |
| **T** | Threat-model completeness | Explicit adversary/attack model (e.g., adaptive/adversarial-evasion traffic vs. static test set); stated assumptions; disclosed limitations. |
| **C** | Currency | Use of current datasets/standards (e.g., not solely relying on the outdated KDD Cup 99), or justified deviation. |

Each dimension is rated per study on a three-point scale, styled after the
Cochrane RoB-2 traffic-light convention:

- **L** (Low concern / green) -- dimension fully satisfied
- **S** (Some concerns / yellow) -- partially satisfied, minor gaps
- **H** (High concern / red) -- significant gaps or missing

Score primary studies only -- exclude foundational/definitional references,
standards documents, and studies explicitly engaged as competing/related
surveys rather than primary evidence.

**Publish an explicit, mechanical aggregation formula, decided before scoring
begins** -- otherwise a different reviewer applying the same four L/S/H
ratings can't reproduce the same letter tier. This project's formula, implemented
in `src/srp/quality_tier.py` and applied automatically by the guided TUI's "Review/
correct an extraction record" step whenever R/A/T/C are all set:

```
L = 2 points, S = 1 point, H = 0 points, per dimension.

Hard gate first, no compensation: R = H or T = H -> tier is C, full stop --
a fatal rigor or threat-model gap should not be buyable back by strong
artifact availability or currency. (A = H / C = H do NOT gate -- missing
artifacts and stale datasets are common enough in ML-security papers to be
graded via points, not disqualifying.)

Otherwise, sum all four dimensions (0-8 raw points) and map:
  7-8 points -> A
  5-6 points -> B
  0-4 points -> C
```

Three tiers (A/B/C), not a finer A-/B+/B-/C+ scale -- with 4 dimensions x 3
levels, a 7-bucket scale implies more precision than the inputs support, and
removes the earlier version's "0-2 points -> C+/C, split by reviewer judgment"
step entirely: given any R/A/T/C combination there is exactly one correct
tier, no judgment call left in the formula itself. `quality_tier` can still be
set by hand for a documented reason -- the guided editor logs that as a
distinct "manual override" event (with the computed tier it disagreed with)
rather than letting it silently diverge, and recomputes from the formula again
the next time any of R/A/T/C is edited.

Publish whatever rule you actually use, before applying it -- not
reconstructed after the fact to match intuitions.

**Quality tiers are not venue tiers -- keep the two axes separate.** It's
tempting to conflate "published somewhere prestigious" with "methodologically
strong." Don't:

- **Venue tier** (a property of *where* something was published): Tier-1
  flagship peer-reviewed venues (e.g., IEEE S&P, USENIX Security, ACM CCS,
  NDSS); Tier-2 preprints and workshop papers not yet at a flagship venue;
  Tier-3 other peer-reviewed venues, standards documents, or theses.
- **Quality tier** (a property of *how rigorously the work was done*): the
  A/A-.../C+/C letter grade from the R/A/T/C rubric above.

A Tier-3-venue paper can score A on rigor/artifacts/threat-model/currency (a
well-instrumented, open-source, current-dataset study that hasn't yet reached
a flagship venue), and a Tier-1-venue paper can score C on artifact
availability (a top-venue paper with no released code or dataset split).
Report both axes, and don't let one stand in for the other in your synthesis
prose. Retain C-tier studies (quality-flagged, not excluded) when they're
topically in scope -- exclusion and quality are different decisions.

**Release the per-dimension scores, not just the aggregate tier**, as
supplementary material (a table of study x {R,A,T,C} -> {L,S,H}) -- it's what
lets a reader see *why* a study scored B+ rather than A, and it's the raw
material Cohen's kappa needs.

**Quality-rubric scoring sheet template:**

```csv
study_id,rigor_R,artifact_A,threat_model_T,currency_C,raw_points,aggregate_tier,reviewer,notes
```

### Field-driven critical appraisal (PRISMA item 11)

R/A/T/C above is this project's own bespoke rubric, purpose-built for ML-security
papers -- it is not a validated, externally citable instrument, and PRISMA item
11 expects one. The guided wizard now asks which field your review is in and
auto-recommends a real, cited instrument for it: Dybå & Dingsøyr (2008) for
software engineering/CS/cybersecurity, Cochrane RoB 2 for randomized trials,
ROBINS-I for non-randomized studies, QUADAS-2 for diagnostic-accuracy studies,
CASP for qualitative research, MMAT for mixed-methods/social-science reviews,
JBI for nursing/allied health, Drummond for economic evaluations, and so on --
see `src/srp/appraisal.py` for the full registry, every domain transcribed verbatim
from its cited source. Use R/A/T/C *and* the field instrument together if you
want both the domain-specific security lens and an externally defensible check;
`extract.py --instruments <key>` appends the chosen instrument's verbatim
domains as extra extraction-sheet columns, alongside R/A/T/C, not instead of it.

Certainty of evidence (PRISMA items 15, 22) is handled the same way: GRADE for
quantitative findings (works without meta-analysis too -- see Murad et al. 2017),
GRADE-CERQual for qualitative synthesis findings, or an explicit stated reason
when the field has none (software engineering does not; see the auto-composed
justification in `PROVENANCE.md`). A review-level self-check instrument (AMSTAR
2, ROBIS, DARE, or the Campbell/MECCIR standards) appraises the review itself
rather than its primary studies -- these three things (primary-study appraisal,
certainty of evidence, review self-appraisal) are genuinely different objects,
and conflating them is a common mistake. Its name and citation are recorded in
`PROVENANCE.md` either way; the consolidation menu's **Write review
self-appraisal checklist** action additionally writes
`review_self_appraisal_<key>.md`, a fillable table with the chosen instrument's
actual domains as rows -- the review-level counterpart to how
`extract.py --instruments <key>` appends real columns for a primary-study
instrument. Rating and justification are left blank for you to fill in against
the finished manuscript; nothing here is scored automatically.

**Piloting and double extraction** (Cochrane 5.4, Kitchenham 6.4): pass
`--pilot N` to `extract.py` to also sample N studies into a second,
identically-shaped sheet for a second reviewer to extract independently; then
`extract.py --compare-a <sheet1> --compare-b <sheet2>` reports Cohen's kappa and
every disagreement per categorical column (R/A/T/C, quality_tier, venue_tier,
and any appraisal-instrument columns).

---

### Stage 6 -- Synthesis

Once studies are extracted and quality-scored, synthesize findings
**organized by research question**, not study-by-study. Two families exist:

- **Meta-analysis** -- statistically pools comparable quantitative results
  (same metric, same units, comparable methodology) into a combined effect
  estimate. Requires genuine comparability across studies.
- **Narrative/thematic synthesis** (SWiM-style -- Synthesis Without
  Meta-analysis) -- groups findings thematically by RQ, states what's
  consistent vs. what conflicts, and explains *why* (differing datasets,
  threat models, metrics) rather than pooling numbers that aren't actually the
  same thing.

**When NOT to meta-analyze:** whenever studies report the same *kind* of
claim (e.g., "transformer-based detectors outperform tree-based detectors on
recall for rare attack classes") but measure it on different datasets
(CIC-IDS2017 vs. UNSW-NB15 vs. NSL-KDD), different splits, or different
operating points (offline batch evaluation vs. online/streaming detection). If
two studies' reported numbers for the "same" claim differ by an order of
magnitude because one measured a clean held-out test set and the other
measured performance under adversarial evasion, report this as *directionally*
consistent (direction agrees) rather than pooling the two numbers or treating
either as a precisely comparable point estimate -- a naive meta-analysis here
manufactures false precision.

**Explicit consistency assessment.** For any claim two or more studies bear
on, state outright whether they **converge** or **conflict**, and if they
conflict, say what methodological difference plausibly explains it. Structure
this as a table: claim -> studies bearing on it -> direction of each study's
finding -> consistent/conflicting -> explanation if conflicting.

**Organize by RQ, not by study.** A useful test: if you removed the study
citations from a synthesis paragraph, the paragraph should still make a
coherent claim about the field -- citations should support a stated position,
not be the entire content of the paragraph.

---

### Stage 7 -- Reporting

**PRISMA 2020 flow diagram.** Four boxes: **Identification** (total records
found, across however many search rounds/sources) -> **Screening &
Eligibility** (the process applied) -> two terminal boxes, **Excluded** (with
a reasoned breakdown, not a bare number) and **Included** (the final count).
Every number in the excluded box should name the mechanism and denominator it
came from -- see Stage 3's funnel shape above.

**PRISMA 2020 checklist.** Map your review against the 27-item PRISMA 2020
checklist item by item: **Yes** (fully reported), **Partial** (reported, at a
coarser grain than the item asks for), **NA** (genuinely not applicable --
e.g., meta-analysis-specific items when you did a narrative synthesis), or
**No** (not reported). Grade honestly against what the manuscript and its
supplement actually contain -- a checklist that's all "Yes" on a first attempt
is a sign you're grading against an aspirational review, not the one you
wrote.

**Data-availability / supplement plan.** Decide, before you finish, exactly
what gets released alongside the manuscript:

- A per-study table: every included study, its thematic classification, and
  its aggregate quality tier.
- The verbatim search query strings and applied filters, per database, and the
  date each was run.
- The full screening log (every record, both stages, decision + reason +
  reviewer).
- The full extraction matrix (Stage 4's schema, one row per study).
- The full quality-rubric grid (per-study, per-dimension scores, not just the
  aggregate tier).

Release all of the above -- it's what lets a reader independently check your
synthesis rather than just trust it.

---


## Integrity & reproducibility checklist

- **Provenance-tag every reported number.** Every quantitative claim in your
  synthesis should be traceable to one of: **Measured** (you or a cited study
  directly measured it), **Derived** (computed from measured numbers via a
  stated formula), **Modeled** (from a simulation, not a real system), or
  **Estimated** (an approximation with named uncertainty). Don't let a Modeled
  or Estimated number read as if it were Measured.
- **Keep a complete screening log**, from the first record screened -- every
  title/abstract and full-text decision, its reason, and its reviewer.
- **Archive the exact queries and their execution dates.** Save the verbatim
  Boolean strings per database, the filters applied, and the date each was
  run -- database indices change over time.
- **Pin dataset/tool versions.** If your review depends on a dataset snapshot
  (a specific OpenAlex/Crossref export, a specific database export CSV), keep
  the raw export file, not just a derived candidates list.
- **No-fabrication rules, stated plainly:**
  - Never state a number you have not verified against its source.
  - Never let an LLM-drafted paragraph's citations enter a manuscript without
    independent DOI verification (`src/scripts/verify_citations.py`).
  - Never silently drop a discrepancy between two accounting views of the same
    total (e.g., per-round included counts vs. final corpus count) --
    reconcile and state the reconciliation explicitly.
  - Never present a Modeled or simulation-only number as if it were an
    empirical measurement.

## End-to-end checklist

- [ ] PICOC scoping written down, RQs derived from it
- [ ] Protocol pre-registered (OSF Registries) before the first query is run
- [ ] Inclusion/exclusion criteria written down, including a source-quality floor
- [ ] Boolean query strings drafted for every accessible database, per that
      database's actual syntax
- [ ] Every query's exact string, filters, and execution date archived
- [ ] Candidates pulled via `src/scripts/search.py`, unified into one `candidates.csv`
- [ ] De-duplicated via `src/scripts/dedup.py`: DOI exact match, then fuzzy title match
- [ ] Two independent reviewers screen title/abstract; Cohen's kappa computed
      before reconciling
- [ ] Full-text eligibility assessed for everything that survives; every
      downloaded PDF's title page checked against its expected title
- [ ] Complete screening log kept from the first record, not just included ones
- [ ] Fixed-schema extraction for every included study (`src/scripts/extract.py`
      builds the template); quantitative findings copied verbatim with enough
      context to interpret later
- [ ] Quality-rubric scoring (R/A/T/C) with a published aggregation formula;
      per-dimension scores released, not just the aggregate tier
- [ ] A field-appropriate, externally citable appraisal instrument selected
      (`src/srp/appraisal.py`) alongside R/A/T/C, matching the review's actual field
- [ ] If a review-level self-check instrument applies (AMSTAR 2, ROBIS, DARE,
      MECCIR), its checklist filled in against the finished manuscript
      (consolidation menu's "Write review self-appraisal checklist")
- [ ] Certainty of evidence assessed (GRADE / GRADE-CERQual) or its absence
      explicitly justified for the field
- [ ] Venue tier and quality tier reported as separate axes
- [ ] Citation snowballing (backward + forward, `src/scripts/snowball.py`) run and
      screened as its own identification method -- distinct from database
      search and from title-term query expansion -- if the protocol calls for it
- [ ] Narrative/thematic synthesis by RQ, with explicit convergence/conflict
      assessment; meta-analysis only where studies are genuinely comparable
- [ ] Every citation DOI-verified via `src/scripts/verify_citations.py` before it
      enters the manuscript
- [ ] PRISMA flow diagram(s) (`src/scripts/figures.py`) with a reasoned (not bare)
      excluded-count breakdown
- [ ] PRISMA 2020 checklist graded honestly, gaps disclosed rather than hidden
- [ ] Search-methods paragraph drafted from the pipeline's own recorded search
      strategy (consolidation menu's "Draft methods paragraph"), not retyped
      by hand, so truncated sources carry their caveat automatically
- [ ] Grey-literature inclusion/exclusion and reporting-bias-assessment
      decisions stated explicitly (PRISMA items 14, 21), not left silent
- [ ] Data-availability supplement: search strings, screening log, extraction
      matrix, and full quality-rubric grid all released
- [ ] AI-usage log kept throughout; disclosure statement written from that
      log, not from memory, at submission time

---
