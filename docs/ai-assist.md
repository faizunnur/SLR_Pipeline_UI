# AI-assist screening

Where AI assistance fits in the pipeline, the prompt packs it uses, and the guardrails around it. AI suggestions are proposals only: a human review gate decides. Back to the [README](../README.md).

## AI-tools integration

This section is about using an LLM to help you **write and read** -- expand
search terms, draft prose, summarize synthesis findings. That's a different
job from `src/scripts/assist.py`'s manual-paste **screening** assist (see
"Running the pipeline" above): screening-assist needs no account or key and
writes its output straight into `screening.csv` as a proposed decision for
the human review gate; the tasks below are open-ended drafting/reading help
that stays outside the pipeline's CSVs entirely.

### Where AI fits in the pipeline

| Stage | AI role | Human role |
|---|---|---|
| Search-term generation | Expand/suggest synonym blocks from an initial PICOC | Approve every term before it enters a Boolean string; run the actual queries yourself |
| Title/abstract screening | High-volume first-pass triage against a strict schema (via `src/scripts/assist.py`, manual-paste, no API key) | Review every AI decision, especially every "exclude" -- false exclusions are invisible if unchecked; confirm at the review gate |
| Data extraction | Populate the fixed schema (Stage 4) from a PDF's text | Spot-check extracted quantitative findings against the source PDF directly, not just plausibility-check the prose |
| Cross-study synthesis | Draft convergence/conflict summaries from an evidence table you provide | Verify every claimed convergence/conflict against the actual studies; AI tends to over-smooth disagreements into false consensus |
| Section drafting | Draft prose from an evidence table + outline | Full edit pass; run through a readability/humanizing pass (below) |
| Readability/humanizing | Rewrite for plain, non-AI-sounding academic prose | Confirm no meaning was altered in the rewrite |
| Citation verification | Never -- use `src/scripts/verify_citations.py`, not an LLM | Run the DOI-resolution check yourself; treat any mismatch as a hard stop |

### Prompt templates

**(a) Generating/expanding Boolean search terms**

```
You are helping expand a systematic literature review's search terms. The review's PICOC
scoping is:
  Population: <paste>
  Intervention: <paste>
  Comparison: <paste>
  Outcome: <paste>
  Context: <paste>

I have this initial thematic block: [<term1>, <term2>, <term3>]

Propose up to 8 additional synonyms/near-synonyms/common alternate phrasings that authors in
this field use for the SAME concept (not adjacent concepts). For each, state which of the
seed terms it is a synonym/variant of, and flag any term that is also a common false-positive
source in an unrelated field. Do not propose terms that broaden the concept -- only lexical
variants of the same concept. Output as a table: term | variant-of | false-positive-risk (Y/N,
reason).
```

Human step: review every proposed term against the PICOC before it enters any
Boolean string; reject anything that's actually a scope expansion disguised as
a synonym.

**(b) Batch title/abstract screening**

```
You are screening candidate records for a systematic literature review at the title/abstract
stage. Apply ONLY the criteria below -- do not use outside judgment about topic importance.

INCLUDE if: <paste inclusion criteria verbatim>
EXCLUDE if: <paste exclusion criteria verbatim>
MAYBE if: the abstract does not give enough information to decide against the criteria above
  (do not guess -- use MAYBE, a human will resolve it at full-text stage).

Output STRICT JSON, one object per record, matching this schema exactly:
{
  "id": <int>,
  "decision": "include" | "exclude" | "maybe",
  "reason": "<one sentence citing which specific criterion drove the decision>"
}
Return a JSON array of these objects, nothing else -- no prose before or after. The "id" must
be copied EXACTLY from each record below -- do not renumber, re-sequence, add, omit, merge,
or reorder the records. Record ids are not necessarily sequential (e.g. 12, 47, 61); answering
positionally (treating the first record as id 1) silently misattributes the decision to a
different, unrelated record.

Do not include a decision for any record where the abstract is missing or under 20 words --
mark those "maybe" with reason "insufficient abstract" instead of guessing from the title alone.

Records:
<id, title, abstract, venue, year for each candidate>
```

Human step: spot-check a random sample of both "include" and "exclude"
decisions (not just borderline "maybe" ones); a systematic false-exclusion
pattern (e.g., excluding everything that doesn't use exact terminology) is the
main failure mode to watch for.

**(c) Structured data extraction**

```
Extract the following fields from the attached paper into the exact schema below. Use only
information stated in the paper -- do not infer or estimate a number that is not explicitly
given. If a field is not addressed in the paper, write "not reported," do not leave it blank
and do not guess.

Schema: thematic_class, venue, venue_tier (1/2/3 per: <paste your tier definitions>),
study_type, contribution, key_findings (verbatim numbers, with the dataset/
split/baseline context needed to interpret them), rq_mapping (which of RQ1-RQ4 this
evidences), limitations (per the paper's own stated limitations, not your assessment),
quality_tier (do not fill this in -- quality scoring is a separate human pass).

Paper: <attach or paste full text>
```

Human step: cross-check every `key_findings` entry against the source PDF
directly -- this is the field most likely to contain a subtly wrong number
(right order of magnitude, wrong dataset split, wrong baseline) that reads as
plausible.

**(d) Cross-study consistency synthesis**

```
I have N studies bearing on this claim: "<state the specific claim, e.g., 'transformer-based
NIDS architectures outperform tree-based baselines on recall for rare/minority attack
classes'>"

For each study below, I've given: study id, the specific finding relevant to this claim,
and its measurement context (dataset, split, evaluation protocol).

<list studies with finding + context>

Assess: do these studies converge or conflict on DIRECTION (not magnitude)? If magnitudes
differ substantially, explain the likely methodological driver (different dataset/split/
evaluation protocol) rather than treating the difference as a real disagreement. Explicitly
flag if any pair of studies conflicts even on direction -- that is a genuine disagreement,
not a measurement-context artifact, and needs separate discussion. If you cannot identify a
plausible methodological driver from the context given, say so explicitly rather than
proposing one speculatively -- an invented-but-plausible-sounding explanation is worse than
admitting the discrepancy is unexplained.

Do not average or pool the numbers. State whether meta-analysis would even be appropriate
here, and why or why not.
```

Human step: verify the "methodological driver" explanation against the actual
papers before it goes in the manuscript -- an LLM will readily generate a
plausible-sounding explanation for a discrepancy that isn't the real one.

**(e) Drafting a section from an evidence table**

```
Draft the RQ<N> synthesis section from the evidence table below. Requirements:
- Organize by sub-theme within the RQ, not study-by-study.
- Every claim must cite the specific study id(s) that support it.
- State explicit convergence/conflict per the consistency-assessment output above.
- Do not introduce any claim, number, or study characterization not present in the evidence
  table -- if the table doesn't cover something, don't fill the gap from general knowledge.
- Flag, in a trailing [NEEDS HUMAN CHECK] note, any sentence where you are not fully certain
  the evidence table supports the specific phrasing used.

Evidence table: <paste extraction-matrix rows relevant to this RQ>
```

Human step: resolve every `[NEEDS HUMAN CHECK]` flag before this draft moves
forward; treat an unflagged draft with the same scrutiny anyway.

**(f) Readability/humanizing pass**

```
Rewrite the following academic prose to read as plainly and directly as a careful human
author would write it. Remove: AI-vocabulary tells (delve, leverage, underscore, pivotal,
seamless, crucial, robust used as filler, "it is important to note that"), formulaic
rule-of-three constructions, uniform sentence-length rhythm, and negative-parallelism
padding ("not only... but also," "it's not just X, it's Y"). Preserve every factual claim,
citation, number, and hedge/qualifier exactly -- this is a style edit, not a content edit.
If a sentence's meaning would change by simplifying it, leave it as is and flag it instead
of guessing.

Text: <paste section draft>
```

Human step: diff the rewrite against the original for any dropped hedge,
qualifier, or citation -- a style pass that silently drops a "may" or a
citation is a content bug wearing a style-edit disguise.

**(g) Citation verification**

Do not use a prompt for this step -- use `src/scripts/verify_citations.py`. An LLM
asked to verify a citation is itself unreliable for the exact failure mode
you're trying to catch (see the integrity guardrails below).

### Integrity guardrails

These are non-negotiable, not best-effort suggestions.

1. **Never trust an LLM-produced (or human-produced) citation without
   independently verifying the DOI resolves to the claimed title and
   authors.** A citation that cites a completely unrelated paper as evidence
   is a real, documented failure mode in systematic reviews -- not a
   hypothetical. Whether it originates from a human author cutting corners or
   an LLM hallucinating a reference, the mitigation is identical: resolve
   every DOI you cite against a real registry (`src/scripts/verify_citations.py`,
   which checks Crossref -- a bare arXiv identifier with no DOI isn't
   resolvable by it) and confirm title and authors match, before it enters
   your corpus or your own manuscript.
2. **Keep a human in the loop for every single inclusion/exclusion decision.**
   AI-assisted triage (`src/scripts/assist.py` included) can propose a decision;
   it cannot be the decision. This applies at both the title/abstract and
   full-text stages -- `slr.py`'s human review gate exists specifically to
   enforce this in the guided path. Both the review gate and full-text
   screening mechanically refuse to mark themselves complete -- and the
   pipeline's resume pointer refuses to advance past them -- while any record
   still has no decision, with no override; the phase cannot silently move on
   with records left unaccounted for.
3. **Verify every downloaded PDF's title page against its expected title
   before extraction.** Automated resolution (`src/scripts/download.py`) can fetch the
   wrong file -- a similarly-titled paper, or a landing page mislabeled as a
   PDF. Check the actual PDF, every time, before it enters your extraction
   matrix.
4. **Log every AI-assisted decision.** If an LLM proposed a screening
   decision, an extracted field, or a drafted paragraph, record that it did --
   a `decision_source` or `ai_assisted` column in your screening/extraction
   spreadsheets costs nothing to add and is exactly what the next guardrail
   needs.
5. **Track AI-usage disclosure requirements per venue.** Many venues now
   require an explicit AI-usage disclosure statement (what tools were used,
   for what stages, with what human oversight). Keep a running record of where
   AI assisted throughout the pipeline -- not reconstructed from memory at
   submission time -- so the disclosure statement can be written accurately.

---
