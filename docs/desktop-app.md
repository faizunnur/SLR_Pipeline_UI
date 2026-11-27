# The desktop app

`slr-ui` opens a window that runs the whole review. This page describes each
screen, where your data is kept, and how the app is put together.

The method itself is described in [methodology.md](methodology.md); the app
does not change it. Every stage runs the same script, writes the same files and
logs the same events as the guided terminal app (`slr`), so a review started in
one can be continued in the other.

## Starting it

```bash
slr-ui                          # the window
slr-ui --workspace D:\Reviews   # use (and remember) another workspace folder
slr-ui --browser                # no native web view: show it in your browser
slr-ui --shortcut               # add a desktop shortcut (and Start-menu entry), then exit
slr-ui --debug                  # open the web inspector with the window
```

`slr-desktop` is the same program without a console window, for a shortcut.

## Where your data lives

| What | Where |
|---|---|
| Reviews | `<workspace>/runs/<review-id>/` (one folder per review) |
| API keys | `<workspace>/.env` |
| App preferences (workspace path, theme) | `~/.slr-pipeline-ui/settings.json` |

The default workspace is `Documents/SLR Pipeline`. Change it under *Settings &
about*. API keys are loaded into memory when the app starts and are never
written into a review folder, so a review folder can be shared or attached as a
reproducibility appendix without leaking a credential.

## The screens

### All reviews

One card per review with its topic, the next step and a progress bar. With no
reviews yet, the page explains the five steps of a review and offers **Try the
example review**: a practice review made from 21 bundled records, with no
network use. Its provenance says the records were loaded as example data, not
retrieved by a search, and stages that contact outside services stay blocked
until you add a contact email.

**New review** opens a five-page form: question (topic and search concepts),
criteria, search (years, contact email, sources), optional details (reviewer,
AI tool, appraisal instrument, registration, grey literature, reporting-bias
plan, funding), and a confirmation page. Only the topic and the email are
required; the optional page can be skipped and its defaults are recorded. Keywords and criteria are part of the protocol and are
fixed once the review is created. The contact email, years, sources, the
per-source cap, the reviewer name and the AI tool name can be edited later
(*Overview > Edit search settings*).

### Overview

- **Next step**, with a button that takes you there.
- **Search phases**: for each phase, the state of search, screening, review
  gate and query expansion.
- **PRISMA flow so far**: identified, duplicates removed, screened, excluded,
  assessed at full text, included. Derived from the files on disk each time.
- **Protocol**: the search string, criteria and appraisal instrument.

### Search

Runs `search.py`, `dedup.py` and `screen.py` for the phase, in the background,
with the log at the bottom of the window. Afterwards the page shows the counts
and the per-source record (status, retrieved, available, truncated), which is
saved as `search_strategy.csv`.

**Import from a file** adds records you exported from a database website. Use
it for databases that do not give you an API key (Scopus, Web of Science, IEEE
Xplore), or instead of the online search altogether.

1. In the database, run your search, select all results, choose *Export*, and
   pick RIS, BibTeX or CSV with abstracts included. PubMed's *Citation manager*
   file (`.nbib`) also works.
2. In the app, choose the file. A preview shows how many records it holds and
   how many have an abstract, a DOI and a year.
3. Say which database it came from, the date you searched, and paste the search
   string you used there. PRISMA items 6 and 7 ask you to report these; they go
   into the per-source search record and the methods draft.

The records join the phase like search results. Duplicates of records you
already have are removed, existing record ids and decisions are kept, and a
phase's review gate re-opens if the import brought new records to decide. The
same file cannot be imported twice into a phase. The imported records are also
kept as `imported.csv` in the phase folder, so a later search does not drop
them.

**Re-run search** asks first. It starts a blank screening sheet and keeps the
old one as `screening.before-rerun-<time>.csv`, because a new search gives the
same row ids to different papers.

### Screening

Two modes, switched at the top:

- **Screen by hand.** A list on the left, one record on the right. Filter by
  status or text.

  | Key | Action |
  |---|---|
  | `I` / `M` / `E` | Include / maybe / exclude the open record |
  | `1` to `9` | Exclude with that quick reason |
  | `U` or `Ctrl+Z` | Undo your last decision |
  | `J` or `↓`, `K` or `↑` | Next / previous record |
  | `R` | Jump to the reason box |
  | `Esc` | Leave a text box |

  **Highlighted words.** The search keywords of the review are marked in the
  title and abstract. *Highlighted words* (bottom right of the record) lets you
  add words that point to include and words that point to exclude; `*` matches
  any ending (`evaluat*`). The three groups differ by the line under the word
  as well as by colour. They are a reading aid and never change a decision.

  **Quick reasons.** The *Exclude:* row holds up to nine reasons. Pressing a
  number, or clicking a reason, excludes the open record with that reason and
  moves on. *Edit list* changes the list for this review. Typing a reason and
  pressing `E` still works.

  **Undo** takes back the last decision, up to 100 steps, also back to "no
  decision". It is recorded in `decisions.jsonl` with the source
  `human-review/undo`. The undo list is kept while the app is open.

  Include and maybe both go on to full-text reading. A record that was already
  judged in another phase shows that earlier decision as a hint.
- **AI assist (copy and paste).** The app shows a prompt for the next batch of
  20 undecided records. Copy it into any web chatbot, paste the answer back,
  and press *Apply decisions*. Nothing is sent anywhere by the app. A reply
  line for an id that was not in the batch is refused, and a record that
  already has a decision is left alone. *Skip this batch* leaves those records
  for manual screening.

### Review gate

Shows what proceeds to full text and what was excluded. For AI exclusions the
default view is the fixed sample of 20 the method asks you to check (seeded by
the phase number, so it is the same every time); you can also list all of them.
Mark records to exclude or rescue, then **Confirm gate**. The gate cannot be
confirmed while any record in the phase has no decision.

With more than one phase, the gate page then offers **query expansion**: terms
frequent in the titles you kept. Pick some, add your own, and the next phase
opens with the expanded search string.

### Full text

The studies that passed title/abstract screening, across all phases. Record
include or exclude for each; an exclusion requires a reason (PRISMA item 16b),
typed or picked from the quick reasons. Highlighted words and undo work here as
they do at title/abstract screening; undoing a decision after the stage was
complete re-opens it.
*Download open-access PDFs* fetches what is legally available; a downloaded PDF
gets an *Open PDF* button on its record.

### Extraction

*Build extraction sheet* creates one row per included study. Set venue tier and
the R/A/T/C ratings in the table; the quality tier is computed from them.
Choosing a tier by hand is allowed and is logged as a manual override. Text
fields and the appraisal-instrument checklist are edited in the side panel and
saved when you leave a field. Every edit is written to the audit trail.

### Outputs

- **Evidence**: download PDFs, verify citations against Crossref, citation
  snowballing (then screen its results and merge them), inter-rater agreement
  between two sheets, and a per-phase diagnosis for a review that came back
  empty.
- **Figures**: PRISMA flow diagrams and tier charts, PNG and PDF.
- **Exports and reports**: BibTeX/RIS, the full-text exclusion table, the draft
  methods paragraph, `PROVENANCE.md`, and a review self-appraisal checklist.
- **Files**: everything the review has produced, each with an *Open* button.

### Activity

The provenance log, newest first.

## How it is put together

```
 window (pywebview)                Python process
┌──────────────────────┐          ┌──────────────────────────────────────┐
│ web/index.html       │  bridge  │ api.py      one method per call,     │
│ web/*.js  (no build) │ ───────► │             returns {ok, data|error} │
│ web/app.css          │          │ service.py  pipeline operations      │
└──────────────────────┘          │ jobs.py     one background task,     │
                                  │             streamed log, cancel     │
                                  │   │                                  │
                                  │   ├─► srp/*        (library, reused) │
                                  │   └─► scripts/*.py (child processes) │
                                  └──────────────────────────────────────┘
```

- **Front end.** Plain HTML, CSS and JavaScript with no framework and no build
  step, so the installed package is the source you can read. Record titles and
  abstracts come from external APIs, so the page never writes them as HTML:
  every element is created through one helper that sets text only. A
  Content-Security-Policy allows scripts and styles from the app's own files
  only.
- **Bridge.** In the window, calls go through pywebview's in-process bridge. In
  browser mode the same calls go to a small local server (`server.py`).
- **Service.** `service.py` has no UI code and returns plain dictionaries,
  which is what makes it testable without a window
  (`tests/test_desktop_service.py`). It imports the terminal app's pure helper
  functions instead of copying them.
- **Tasks.** A stage script is started as a child process; its output is
  collected line by line on a worker thread and the page polls for new lines.
  API keys are passed through the child's environment, not its command line,
  and a secret flag value is masked in the log.

## Running the tests

```bash
pip install pytest
python -m pytest tests/
```

The desktop tests need no window and no network: the search output is seeded
from `examples/`, and every later stage runs the real script.
