"""
service.py -- every pipeline operation the desktop app offers, with no UI code.

The terminal app (slr.py) mixes orchestration with `questionary` prompts, which
would block a window app. This module re-expresses the same orchestration as
plain functions that take arguments and return JSON-safe dicts, while keeping
the method identical:

* the same stage scripts are run with the same arguments (srp/scripts),
* the same checkpoints are written to state.json (RunState.mark_stage),
* the same events go to provenance.jsonl and decisions.jsonl,
* the pure helpers of slr.py are imported and reused, not copied.

Anything here that changes a decision records it in the audit trail exactly
as the terminal app would, so a run can be continued in either interface.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import shutil
import sys
from collections import Counter
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import slr as tui  # the original terminal app: its pure helpers are reused as-is
from rich.console import Console
from srp import UPSTREAM_VERSION
from srp import __version__ as VERSION
from srp import export as srp_export
from srp import llm_assist
from srp.agreement import compare_reviewers
from srp.appraisal import (FIELD_PROFILES, INSTRUMENTS, PRIMARY_STUDY, REVIEW_SELF_CHECK,
                           compose_appraisal_disclosure, render_review_self_appraisal)
from srp.config import SECRET_ENV_VARS, ReviewConfig
from srp.decisions import AI_REVIEWER, apply_decisions, pick_progressed, ta_proceeds_mask
from srp.env import load_dotenv, parse_env_text, set_env_var, unset_env_var
from srp.methods_report import render_search_methods, render_search_strategy_table
from srp.prisma import prisma_residuals
from srp.provenance import Provenance
from srp.quality_tier import compute_quality_tier
from srp.state import RunState, record_key

from slr_desktop import importer
from slr_desktop.jobs import JobContext, JobManager

APP_NAME = "SLR Pipeline UI"
UPSTREAM = "slr-pipeline by Md Nayeem Hossain (https://github.com/nayeem-hossain/slr-pipeline)"

BATCH_SIZE = tui._BATCH_SIZE
EXCLUDE_SAMPLE_SIZE = tui._EXCLUDE_SAMPLE_SIZE
ALL_SOURCES = list(tui._ALL_SOURCE_NAMES)
KEYLESS_SOURCES = ["openalex", "semanticscholar", "crossref", "arxiv", "pubmed", "doaj"]
KEYED_SOURCE_FIELD = dict(tui._KEYED_SOURCE_FIELD)
MANAGED_API_KEYS = list(tui._MANAGED_API_KEYS)

# Where each key comes from, shown next to the key on the API keys page.
#   access:   "free" (anyone can sign up) or "institution" (needs a subscription)
#   optional: True if the source still works without the key
# Keep in step with the notes in .env.example.
KEY_HELP = {
    "S2_API_KEY": {
        "access": "free", "optional": True,
        "effect": "Semantic Scholar works without a key, on a slow pool shared by everyone. "
                  "A key gives you your own limit of 1 request per second.",
        "steps": ["Open the Semantic Scholar API page.",
                  "Fill in the API key request form.",
                  "The key is sent to you by email. Paste it here."],
        "url": "https://www.semanticscholar.org/product/api", "site": "semanticscholar.org"},
    "PUBMED_API_KEY": {
        "access": "free", "optional": True,
        "effect": "PubMed works without a key at 3 requests per second. A key raises that to 10.",
        "steps": ["Sign in to your NCBI account (creating one is free).",
                  "Open Account settings and find API Key Management.",
                  "Create an API key, copy it and paste it here."],
        "url": "https://www.ncbi.nlm.nih.gov/account/", "site": "ncbi.nlm.nih.gov"},
    "CORE_API_KEY": {
        "access": "free", "optional": False,
        "effect": "Without a key CORE is skipped. With one, CORE is searched and is also "
                  "used as an extra source of open-access PDFs.",
        "steps": ["Open the CORE API page.",
                  "Register for a free API key with your email address.",
                  "The key is sent to you by email. Paste it here."],
        "url": "https://core.ac.uk/services/api", "site": "core.ac.uk"},
    "IEEE_API_KEY": {
        "access": "institution", "optional": False,
        "effect": "Without a key IEEE Xplore is skipped. Keys are normally given only to "
                  "people whose institution subscribes to IEEE Xplore.",
        "steps": ["Create an account on the IEEE developer portal.",
                  "Register for a key for the Metadata Search API.",
                  "Wait for IEEE to approve it, then copy the key from your account and paste it here."],
        "url": "https://developer.ieee.org", "site": "developer.ieee.org"},
    "SCOPUS_API_KEY": {
        "access": "institution", "optional": False,
        "effect": "Without a key Scopus is skipped. The key only works from your institution's "
                  "network (on campus or through its VPN) unless you also have an institutional token.",
        "steps": ["Sign in to the Elsevier developer portal with your institutional email.",
                  "Create an API key (any label and website address will do).",
                  "Copy the key and paste it here."],
        "url": "https://dev.elsevier.com", "site": "dev.elsevier.com"},
    "SCOPUS_INSTTOKEN": {
        "access": "institution", "optional": True,
        "effect": "Only needed to use your Scopus key away from campus. It cannot be created "
                  "online: Elsevier issues it on request. Treat it like a password.",
        "steps": ["Ask your librarian, or Elsevier support, for an institutional token for your Scopus API key.",
                  "When you receive it, paste it here. Leave this empty if you work on campus."],
        "url": "https://dev.elsevier.com", "site": "dev.elsevier.com"},
    "SPRINGER_API_KEY": {
        "access": "free", "optional": False,
        "effect": "Without a key Springer Nature is skipped. The free plan allows 500 requests a day.",
        "steps": ["Sign up on the Springer Nature developer portal.",
                  "Choose the Meta API, not the Open Access API: Open Access misses every "
                  "paywalled Springer paper.",
                  "Copy the key from your account and paste it here."],
        "url": "https://dev.springernature.com", "site": "dev.springernature.com"},
    "WOS_API_KEY": {
        "access": "institution", "optional": False,
        "effect": "Without a key Web of Science is skipped. Keys are normally given only to "
                  "people whose institution subscribes to Web of Science.",
        "steps": ["Register on the Clarivate developer portal with your institutional email.",
                  "Create an application and subscribe it to the Web of Science API Expanded.",
                  "Wait for Clarivate to approve it, then copy the key and paste it here."],
        "url": "https://developer.clarivate.com/apis/wos", "site": "developer.clarivate.com"},
}
EXTRACTION_FIELDS = list(tui._EXTRACTION_EDIT_FIELDS)
EXTRACTION_ENUMS = dict(tui._EXTRACTION_ENUM_CHOICES)
CANDIDATE_COLUMNS = ["id", "source", "title", "authors", "year", "venue", "doi", "url", "abstract"]
# One-click exclusion reasons a new review starts with; each review can edit its own list.
DEFAULT_REASONS = ["Wrong topic", "Wrong population", "Wrong study design", "Not primary research",
                   "Wrong publication type", "Not in English", "Outside date range", "No full text"]
PHASE_STAGES = ("search", "dedup", "prescreen", "assist_ta", "review_gate", "snowball")

# Files a finished review produces, in the order the Outputs page lists them.
OUTPUT_FILES = [
    ("included_final.csv", "Studies that passed title/abstract screening, with full-text decisions"),
    ("final_dedup.csv", "Bibliographic records of the included set"),
    ("extraction.csv", "Data-extraction and quality-appraisal sheet"),
    ("citation_verification.csv", "DOI verification results against Crossref"),
    ("manual_download_needed.csv", "Studies whose PDF must be fetched by hand"),
    ("download_log.csv", "PDF download log"),
    ("references.bib", "BibTeX of the included studies"),
    ("references.ris", "RIS of the included studies"),
    ("excluded_full_text.csv", "Full-text exclusions with reasons (PRISMA 16b)"),
    ("excluded_full_text.bib", "BibTeX of the full-text exclusions"),
    ("search_methods.md", "Draft methods paragraph and search-strategy tables"),
    ("PROVENANCE.md", "Audit trail and AI-assistance disclosure"),
]


class ServiceError(Exception):
    """A problem the user can act on; its message is shown as-is."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _clean(v):
    """Make a pandas/numpy value JSON-safe: NaN -> "", numpy scalar -> Python."""
    if v is None:
        return ""
    item = getattr(v, "item", None)
    if callable(item):
        try:
            v = item()
        except (ValueError, AttributeError):
            pass
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        if v.is_integer():
            return int(v)
    if isinstance(v, str) and v.strip().lower() == "nan":
        return ""
    return v


def _rows(df: pd.DataFrame, cols: list[str]) -> list[dict]:
    out = []
    for _, row in df.iterrows():
        out.append({c: _clean(row.get(c, "")) for c in cols})
    return out


def _quiet_console() -> Console:
    """slr.py's helpers print progress to a rich Console; capture it instead."""
    return Console(file=io.StringIO(), width=120, no_color=True)


def _blank_mask(series: pd.Series) -> pd.Series:
    return series.isna() | (series.astype(str).str.strip().isin(["", "nan"]))


def _decisions(series: pd.Series) -> pd.Series:
    """A decision column as clean lowercase strings, blank for undecided. An
    all-blank column reads back from CSV as float NaN, so cast before cleaning."""
    cleaned = series.astype(object).where(series.notna(), "").astype(str).str.strip().str.lower()
    return cleaned.where(cleaned != "nan", "")


def _script(name: str) -> str:
    return tui._script_path(name)


def _py() -> str:
    """The interpreter for stage scripts. A GUI launch can run under pythonw.exe,
    which works just as well for a child that only writes to a pipe."""
    return sys.executable


# ---------------------------------------------------------------------------
# the service
# ---------------------------------------------------------------------------
class ReviewService:
    def __init__(self, workspace: Path):
        self.jobs = JobManager()
        self._opened: set[str] = set()
        self.set_workspace(workspace)

    # --- workspace ---------------------------------------------------------
    def set_workspace(self, workspace: Path) -> None:
        workspace = Path(workspace).expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        self.workspace = workspace
        self.runs_dir = workspace / "runs"
        self.runs_dir.mkdir(exist_ok=True)
        self.env_path = workspace / ".env"
        self.jobs.cwd = str(workspace)
        # Keys live in the workspace's .env, exactly like the terminal app's
        # cwd/.env; they are loaded into the environment, never into a run folder.
        load_dotenv(self.env_path, override=True)

    def _guard_idle(self) -> None:
        job = self.jobs.busy()
        if job is not None:
            raise ServiceError(f"'{job.title}' is still running. Wait for it to finish first.")

    # --- loading a run -----------------------------------------------------
    def _load(self, run_id: str):
        run_dir = self.runs_dir / run_id
        if not (run_dir / "state.json").exists():
            raise ServiceError(f"No review called '{run_id}' in {self.runs_dir}.")
        state = RunState.load(run_dir)
        cfg = ReviewConfig.from_dict(state.config)
        cfg.hydrate_secrets_from_env()
        prov = Provenance(state.run_dir / "provenance.jsonl")
        if run_id not in self._opened:
            self._opened.add(run_id)
            prov.log("tool_version", version=VERSION, interface="desktop",
                     based_on=f"slr-pipeline {UPSTREAM_VERSION}")
        return state, cfg, prov

    # --- app-level info ----------------------------------------------------
    def app_info(self) -> dict:
        return {
            "app": APP_NAME, "version": VERSION, "upstream": UPSTREAM,
            "upstream_version": UPSTREAM_VERSION,
            "workspace": str(self.workspace), "runs_dir": str(self.runs_dir),
            "env_path": str(self.env_path), "python": sys.version.split()[0],
        }

    def wizard_options(self) -> dict:
        """Everything the New-review form needs to render its choices."""
        fields = []
        for key, prof in FIELD_PROFILES.items():
            fields.append({
                "key": key, "label": prof.label,
                "primary": [{"key": k, "name": INSTRUMENTS[k].name}
                            for k in prof.primary_study_instruments if k in INSTRUMENTS],
                "certainty": ({"key": prof.certainty_framework,
                               "name": INSTRUMENTS[prof.certainty_framework].name}
                              if prof.certainty_framework in INSTRUMENTS else None),
                "review_level": prof.review_level_instrument,
                "justification": prof.justification,
            })
        primary = [{"key": k, "name": inst.name} for k, inst in INSTRUMENTS.items()
                   if inst.level == PRIMARY_STUDY and not k.startswith("mmat_")
                   or k == "mmat_screening"]
        review_level = [{"key": k, "name": inst.name} for k, inst in INSTRUMENTS.items()
                        if inst.level == REVIEW_SELF_CHECK]
        return {
            "fields": fields, "primary_instruments": primary,
            "review_level_instruments": review_level,
            "mmat_designs": [
                {"key": "mmat_qualitative", "name": "Qualitative"},
                {"key": "mmat_rct", "name": "Randomized controlled trial"},
                {"key": "mmat_nonrandomized", "name": "Non-randomized quantitative"},
                {"key": "mmat_descriptive", "name": "Quantitative descriptive"},
                {"key": "mmat_mixed", "name": "Mixed methods"},
            ],
            "sources": ALL_SOURCES, "keyless_sources": KEYLESS_SOURCES,
            "keyed_sources": {src: SECRET_ENV_VARS[f] for src, f in KEYED_SOURCE_FIELD.items()},
            "keys_present": self._keys_present(),
            "defaults": {"year_from": 2020, "year_to": date.today().year,
                         "max_per_source": 60, "n_phases": 1, "title_threshold": 92},
        }

    # --- runs list -----------------------------------------------------------
    def list_runs(self) -> list[dict]:
        out = []
        for run_id in RunState.list_runs(self.runs_dir):
            try:
                state = RunState.load(self.runs_dir / run_id)
            except (OSError, ValueError) as e:
                out.append({"run_id": run_id, "topic": run_id, "error": str(e)})
                continue
            cfg = state.config
            stages = state.state.get("stages", {})
            last = max([s.get("ts", "") for s in stages.values()] or [state.state.get("created", "")])
            summary = self._progress(state, ReviewConfig.from_dict(cfg))
            out.append({
                "run_id": run_id, "topic": cfg.get("topic") or run_id,
                "created": state.state.get("created", ""), "updated": last,
                "n_phases": cfg.get("n_phases", 1), "progress": summary["percent"],
                "step": summary["label"], "reviewer": cfg.get("reviewer", ""),
            })
        out.sort(key=lambda r: r.get("updated") or "", reverse=True)
        return out

    # --- creating a run ----------------------------------------------------
    def create_run(self, form: dict) -> dict:
        topic = (form.get("topic") or "").strip()
        if not topic:
            raise ServiceError("A review topic is required.")
        mailto = (form.get("mailto") or "").strip()
        if "@" not in mailto:
            raise ServiceError("A real contact email is required (OpenAlex, Crossref and "
                               "Unpaywall use it).")
        blocks = [[t.strip() for t in block if t and t.strip()]
                  for block in (form.get("keyword_blocks") or [])]
        blocks = [b for b in blocks if b]
        field_key = form.get("research_field") or "software_engineering"
        if field_key not in FIELD_PROFILES:
            raise ServiceError(f"Unknown research field '{field_key}'.")
        profile = FIELD_PROFILES[field_key]

        primary = [k for k in (form.get("primary_study_instruments")
                               or profile.primary_study_instruments) if k in INSTRUMENTS]
        if "mmat_screening" in primary:
            primary += [k for k in form.get("mmat_designs") or [] if k in INSTRUMENTS]
        certainty = profile.certainty_framework if form.get("use_certainty", True) else ""

        grey = bool(form.get("grey_literature_included"))
        grey_why = (form.get("grey_literature_justification") or "").strip()
        if not grey and not grey_why:
            grey_why = ("Grey literature was excluded to prioritize peer-reviewed quality "
                        "control; no formal multivocal-review protocol (e.g. Garousi, Felderer "
                        "& Mantyla 2019) was applied in this review.")
        if form.get("will_meta_analyze"):
            bias = (form.get("reporting_bias_assessment") or "").strip() or (
                "Meta-analysis is planned, but no specific reporting-bias assessment method "
                "was recorded -- add one (e.g. a funnel plot and Egger's test) before the "
                "synthesis stage.")
        else:
            bias = ("This review synthesizes findings narratively rather than through "
                    "meta-analysis; a formal reporting-bias assessment (funnel plot, Egger's "
                    "test) is not applicable without pooled effect sizes, per PRISMA 2020 item "
                    "21's own scope.")

        def _int(name, default, minimum=None):
            try:
                v = int(form.get(name, default))
            except (TypeError, ValueError):
                raise ServiceError(f"'{name}' must be a whole number.")
            if minimum is not None and v < minimum:
                raise ServiceError(f"'{name}' must be at least {minimum}.")
            return v

        sources = [s for s in form.get("sources") or [] if s in ALL_SOURCES] or list(KEYLESS_SOURCES)
        cfg = ReviewConfig(
            topic=topic, keyword_blocks=blocks,
            year_from=_int("year_from", 2020, 1900), year_to=_int("year_to", date.today().year, 1900),
            mailto=mailto, sources=sources,
            max_per_source=_int("max_per_source", 60, 1), n_phases=_int("n_phases", 1, 1),
            title_threshold=min(_int("title_threshold", 92, 0), 100),
            assist_tool_name=(form.get("assist_tool_name") or "").strip(),
            reviewer=(form.get("reviewer") or "").strip(),
            inclusion_criteria=(form.get("inclusion_criteria") or "").strip(),
            exclusion_criteria=(form.get("exclusion_criteria") or "").strip(),
            registration_id=(form.get("registration_id") or "").strip(),
            research_field=field_key, primary_study_instruments=primary,
            certainty_framework=certainty, review_level_instrument=profile.review_level_instrument,
            appraisal_justification=compose_appraisal_disclosure(
                field_key, primary, certainty, profile.review_level_instrument),
            funding_statement=(form.get("funding_statement") or "").strip(),
            competing_interests=(form.get("competing_interests") or "").strip(),
            language_restriction=(form.get("language_restriction") or "").strip(),
            grey_literature_included=grey, grey_literature_justification="" if grey else grey_why,
            reporting_bias_assessment=bias,
        )
        if cfg.year_from > cfg.year_to:
            raise ServiceError("'Year from' is after 'Year to'.")

        run_id = tui._unique_run_id(self.runs_dir, tui._slugify(topic) or "review")
        state = RunState.create(self.runs_dir, run_id, cfg.to_dict())
        prov = Provenance(state.run_dir / "provenance.jsonl")
        prov.log("review_created", run_id=run_id, topic=cfg.topic, keywords=cfg.display_keywords())
        self._opened.add(run_id)
        prov.log("tool_version", version=VERSION, interface="desktop",
                     based_on=f"slr-pipeline {UPSTREAM_VERSION}")
        return {"run_id": run_id, "query": cfg.search_query()}

    def start_example(self):
        """A ready-made practice review: 21 real records about ML-based intrusion
        detection, bundled with the app, so every screen can be tried without
        setting anything up or touching the network. The search stage is marked
        done with these records; de-duplication and the screening sheet are then
        built by the real stage scripts."""
        sample = Path(__file__).resolve().parent / "sample" / "candidates.csv"
        if not sample.is_file():
            raise ServiceError("The example data is missing from this installation.")
        self._guard_idle()
        cfg = ReviewConfig(
            topic="Example review: machine-learning intrusion detection (sample data)",
            keyword_blocks=[["intrusion detection", "IDS"], ["machine learning", "deep learning"]],
            year_from=2015, year_to=date.today().year, sources=["openalex"], max_per_source=30,
            inclusion_criteria="proposes or evaluates a machine-learning intrusion detection "
                               "approach; reports an empirical evaluation",
            exclusion_criteria="not about intrusion detection; no empirical evaluation",
            research_field="software_engineering",
            primary_study_instruments=list(FIELD_PROFILES["software_engineering"].primary_study_instruments),
            review_level_instrument=FIELD_PROFILES["software_engineering"].review_level_instrument,
            grey_literature_justification="Example data only.",
            reporting_bias_assessment="Example data only.",
        )
        run_id = tui._unique_run_id(self.runs_dir, "example-review")
        state = RunState.create(self.runs_dir, run_id, cfg.to_dict())
        prov = Provenance(state.run_dir / "provenance.jsonl")
        prov.log("review_created", run_id=run_id, topic=cfg.topic, keywords=cfg.display_keywords())
        self._opened.add(run_id)
        pdir = state.phase_dir(1)
        shutil.copy2(sample, pdir / "candidates.csv")
        n_hits = len(tui._read_csv_safe(pdir / "candidates.csv"))
        state.state["phase_1_query"] = cfg.search_query()
        state.mark_stage(1, "search", counts={"n_hits": n_hits})
        # Not a "search_run" event: these records were not retrieved by this review.
        prov.log("example_data_loaded", phase=1, n_hits=n_hits,
                 note="bundled sample records; no search was run")

        def task(ctx: JobContext):
            st, c, pv = self._load(run_id)
            ctx.log(f"Loaded {n_hits} bundled sample records (no search was run).")
            self._run_search_dedup_prescreen(ctx, st, c, pv, 1, st.phase_dir(1), c.search_query())
            return {"run_id": run_id}

        job = self.jobs.start("Preparing the example review", task, run_id)
        return job, run_id

    @staticmethod
    def _need_email(cfg) -> None:
        if "@" not in (cfg.mailto or ""):
            raise ServiceError("This step contacts scholarly services that ask for a contact "
                               "email. Add yours under Overview > Edit search settings first.")

    def preview_query(self, blocks: list) -> str:
        cfg = ReviewConfig(keyword_blocks=[[t for t in b if t and t.strip()] for b in blocks or []])
        return cfg.search_query()

    # --- run overview ------------------------------------------------------
    def _phase_info(self, state: RunState, cfg: ReviewConfig, phase: int) -> dict:
        pdir = state.run_dir / f"phase_{phase}"
        stages = {}
        for s in PHASE_STAGES:
            entry = state.state.get("stages", {}).get(f"{phase}:{s}")
            stages[s] = ({"status": entry.get("status"), "ts": entry.get("ts"),
                          "counts": entry.get("counts", {})} if entry else None)
        screening = tui._read_csv_safe(pdir / "screening.csv") if pdir.exists() else pd.DataFrame()
        n_rows = len(screening)
        n_undecided = n_inc = n_exc = n_maybe = 0
        if not screening.empty and "ta_decision" in screening.columns:
            d = _decisions(screening["ta_decision"])
            n_undecided = int(d.eq("").sum())
            n_inc, n_exc, n_maybe = int(d.eq("include").sum()), int(d.eq("exclude").sum()), int(d.eq("maybe").sum())
        query = state.state.get(f"phase_{phase}_query") or (cfg.search_query() if phase == 1 else "")
        unlocked = phase == 1 or state.state.get("current_phase", 1) >= phase
        return {
            "phase": phase, "query": query, "unlocked": unlocked, "stages": stages,
            "n_rows": n_rows, "n_undecided": n_undecided, "n_include": n_inc,
            "n_exclude": n_exc, "n_maybe": n_maybe,
            "searched": bool(stages["prescreen"] and stages["prescreen"]["status"] == "done"),
            "gated": bool(stages["review_gate"] and stages["review_gate"]["status"] == "done"),
            "expanded": bool(stages["snowball"] and stages["snowball"]["status"] == "done"),
            "is_last": phase == cfg.n_phases,
        }

    def _progress(self, state: RunState, cfg: ReviewConfig) -> dict:
        """Where the review is, as one label + a rough percent for the card."""
        steps_total = cfg.n_phases * 3 + 3
        done = 0
        label = None
        target = None
        for p in range(1, cfg.n_phases + 1):
            info = self._phase_info(state, cfg, p)
            if info["searched"]:
                done += 1
            elif label is None:
                label, target = (f"Phase {p}: run the search", {"tab": "search", "phase": p})
            if info["searched"] and info["n_undecided"] == 0:
                done += 1
            elif label is None:
                label, target = (f"Phase {p}: screen {info['n_undecided']} record(s)",
                                 {"tab": "screening", "phase": p})
            if info["gated"]:
                done += 1
            elif label is None:
                label, target = (f"Phase {p}: review gate", {"tab": "gate", "phase": p})
            if p < cfg.n_phases and not info["expanded"] and label is None:
                label, target = (f"Phase {p}: query expansion", {"tab": "gate", "phase": p})
        ft_done = state.is_stage_done(1, "full_text_screening")
        if ft_done:
            done += 1
        elif label is None:
            label, target = ("Full-text screening", {"tab": "fulltext"})
        has_extraction = (state.run_dir / "extraction.csv").exists()
        if has_extraction:
            done += 1
        elif label is None:
            label, target = ("Build the extraction sheet", {"tab": "extraction"})
        has_report = (state.run_dir / "PROVENANCE.md").exists()
        if has_report:
            done += 1
        elif label is None:
            label, target = ("Figures, exports and reports", {"tab": "outputs"})
        return {"percent": round(100 * done / steps_total),
                "label": label or "Complete: outputs are ready",
                "target": target or {"tab": "outputs"}}

    def get_run(self, run_id: str) -> dict:
        state, cfg, _ = self._load(run_id)
        phases = [self._phase_info(state, cfg, p) for p in range(1, cfg.n_phases + 1)]
        counts = tui._compute_prisma_counts(state, cfg)
        missing_keys = [src for src in cfg.sources if src in KEYED_SOURCE_FIELD
                        and not getattr(cfg, KEYED_SOURCE_FIELD[src], "")]
        conf = cfg.to_dict()
        conf["search_query"] = cfg.search_query()
        conf["display_keywords"] = cfg.display_keywords()
        conf["field_label"] = FIELD_PROFILES[cfg.research_field].label \
            if cfg.research_field in FIELD_PROFILES else cfg.research_field
        conf["instrument_names"] = [INSTRUMENTS[k].name for k in cfg.primary_study_instruments
                                    if k in INSTRUMENTS]
        return {
            "run_id": run_id, "run_dir": str(state.run_dir), "config": conf,
            "created": state.state.get("created", ""),
            "current_phase": state.state.get("current_phase", 1),
            "phases": phases, "prisma": counts, "prisma_warnings": prisma_residuals(counts),
            "progress": self._progress(state, cfg), "missing_keys": missing_keys,
            "fulltext_done": state.is_stage_done(1, "full_text_screening"),
            "has_included": (state.run_dir / "included_final.csv").exists(),
            "has_extraction": (state.run_dir / "extraction.csv").exists(),
            "has_snowball": (state.run_dir / "snowball" / "screening.csv").exists(),
        }

    def update_search_settings(self, run_id: str, changes: dict) -> dict:
        """The mechanical settings the terminal app also lets you change after a
        run started (mailto, years, cap, sources). Keywords and criteria are
        protocol decisions and stay as created -- same rule as slr.py."""
        self._guard_idle()
        state, cfg, prov = self._load(run_id)
        changed = []
        if "mailto" in changes and changes["mailto"].strip() and changes["mailto"].strip() != cfg.mailto:
            if "@" not in changes["mailto"]:
                raise ServiceError("The contact email must contain @.")
            cfg.mailto = changes["mailto"].strip()
            changed.append("mailto")
        for name in ("year_from", "year_to", "max_per_source"):
            if name in changes and changes[name] not in (None, ""):
                v = int(changes[name])
                if v != getattr(cfg, name):
                    setattr(cfg, name, v)
                    changed.append(name)
        if "sources" in changes:
            picked = [s for s in changes["sources"] if s in ALL_SOURCES]
            if picked and picked != cfg.sources:
                cfg.sources = picked
                changed.append("sources")
        for name in ("reviewer", "assist_tool_name"):
            if name in changes and (changes[name] or "").strip() != getattr(cfg, name):
                setattr(cfg, name, (changes[name] or "").strip())
                changed.append(name)
        if changed:
            state.save_config(cfg.to_dict())
            prov.log("settings_edited", changed_fields=changed, interface="desktop")
        return {"changed": changed}

    # --- phase pipeline: search -> dedup -> screening sheet ----------------
    def start_search(self, run_id: str, phase: int, rerun: bool = False):
        state, cfg, prov = self._load(run_id)
        phase = int(phase)
        if phase > 1 and state.state.get("current_phase", 1) < phase:
            raise ServiceError(f"Phase {phase} opens after phase {phase - 1}'s review gate "
                               f"and query expansion are done.")
        if rerun or not state.is_stage_done(phase, "search"):
            self._need_email(cfg)

        def task(ctx: JobContext):
            st, c, pv = self._load(run_id)
            pdir = st.phase_dir(phase)
            # A later phase keeps the expanded query chosen at the previous
            # gate; phase 1 always follows the review's current settings.
            query = st.state.get(f"phase_{phase}_query")
            if not query or (rerun and phase == 1):
                query = c.search_query()
            st.state[f"phase_{phase}_query"] = query
            st.save()
            if rerun:
                for s in ("search", "dedup", "prescreen", "assist_ta", "review_gate"):
                    st.state.get("stages", {}).pop(f"{phase}:{s}", None)
                st.save()
                # screen.py carries decisions over by row id when the sheet already
                # exists. After a NEW search the same id can be a different paper,
                # so a re-run starts from a blank sheet; the old one is kept aside.
                sheet = pdir / "screening.csv"
                if sheet.exists():
                    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                    backup = pdir / f"screening.before-rerun-{stamp}.csv"
                    shutil.copy2(sheet, backup)
                    ctx.log(f"Previous screening sheet kept as {backup.name}")
            ctx.log(f"Query: {query}")
            self._run_search_dedup_prescreen(ctx, st, c, pv, phase, pdir, query, fresh_sheet=rerun)
            n_hits = st.state.get("stages", {}).get(f"{phase}:search", {}).get("counts", {}).get("n_hits")
            if rerun:
                outcome = "done" if st.stage_status(phase, "search") == "done" else "search_failed"
                pv.log("rerun_search", phase=phase, changed_fields=[], confirmed=True,
                       outcome=outcome, n_hits=n_hits)
            return {"n_hits": n_hits}

        return self.jobs.start(f"Phase {phase}: search, dedup, screening sheet", task, run_id)

    def _run_search_dedup_prescreen(self, ctx: JobContext, state, cfg, prov, phase, pdir, query,
                                    fresh_sheet: bool = False):
        # Mirrors slr._run_search_dedup_prescreen; a stage already done is kept.
        candidates_path = pdir / "candidates.csv"
        strategy_path = pdir / "search_strategy.csv"
        dedup_path = pdir / "candidates_dedup.csv"
        screening_path = pdir / "screening.csv"

        if not state.is_stage_done(phase, "search"):
            cmd = [_py(), _script("search.py"), "--query", query,
                   "--year-from", str(cfg.year_from), "--year-to", str(cfg.year_to),
                   "--mailto", cfg.mailto, "--max-per-source", str(cfg.max_per_source),
                   "--sources", ",".join(cfg.sources), "--out", str(candidates_path),
                   "--strategy-log", str(strategy_path)]
            if cfg.scopus_insttoken:
                cmd += ["--scopus-insttoken", cfg.scopus_insttoken]
            if not ctx.run(cmd, f"Phase {phase}: searching sources", secret_env=cfg.secret_env()):
                raise ServiceError("The search did not finish. See the log above, then retry.")
            # Records imported from files are part of this phase's record set: a
            # search (or re-search) must not drop them.
            n_imported = self._fold_imports(state, phase, pdir)
            if n_imported:
                ctx.log(f"Added the {n_imported} record(s) imported from files to the search results.")
            n_hits = len(tui._read_csv_safe(candidates_path))
            state.mark_stage(phase, "search", counts={"n_hits": n_hits, "n_imported": n_imported})
            prov.log("search_run", phase=phase, query=query, sources=cfg.sources, n_hits=n_hits,
                     n_imported=n_imported)
            strategy = tui._read_csv_safe(strategy_path)
            if not strategy.empty and "truncated" in strategy.columns:
                truncated = strategy.loc[strategy["truncated"].astype(str).str.lower().eq("true"),
                                         "source"].tolist()
                if truncated:
                    ctx.log(f"⚠ Truncated by max-per-source ({cfg.max_per_source}): "
                            f"{', '.join(truncated)} -- this phase's hits are a sample.")

        if not state.is_stage_done(phase, "dedup"):
            cmd = [_py(), _script("dedup.py"), "--in", str(candidates_path), "--out", str(dedup_path),
                   "--title-threshold", str(cfg.title_threshold)]
            if not ctx.run(cmd, f"Phase {phase}: de-duplicating"):
                raise ServiceError("De-duplication did not finish. See the log above.")
            n_in = len(tui._read_csv_safe(candidates_path))
            dedup_df = tui._read_csv_safe(dedup_path)
            n_dupes = int(dedup_df["duplicate_of"].notna().sum()) if "duplicate_of" in dedup_df.columns else 0
            n_out = len(dedup_df) - n_dupes
            state.mark_stage(phase, "dedup", counts={"n_in": n_in, "n_out": n_out, "n_dupes": n_dupes})
            prov.log("dedup_run", phase=phase, n_in=n_in, n_out=n_out, n_dupes=n_dupes)

        if not state.is_stage_done(phase, "prescreen"):
            cmd = [_py(), _script("screen.py"), "--in", str(dedup_path), "--out", str(screening_path)]
            if fresh_sheet:
                cmd.append("--force")
            if not ctx.run(cmd, f"Phase {phase}: building screening sheet"):
                raise ServiceError("Building the screening sheet did not finish.")
            n_rows = len(tui._read_csv_safe(screening_path))
            state.mark_stage(phase, "prescreen", counts={"n_rows": n_rows})
            prov.log("prescreen_run", phase=phase, n_rows=n_rows)

    def search_strategy(self, run_id: str, phase: int) -> list[dict]:
        state, _, _ = self._load(run_id)
        df = tui._read_csv_safe(state.run_dir / f"phase_{int(phase)}" / "search_strategy.csv")
        cols = ["source", "query_sent", "retrieved_at", "n_retrieved", "total_available",
                "truncated", "status", "detail"]
        return _rows(df, [c for c in cols if c in df.columns])

    # --- importing records exported from a database website ----------------
    @staticmethod
    def _import_problem(fn, *args):
        try:
            return fn(*args)
        except importer.ImportProblem as e:
            raise ServiceError(str(e))

    def preview_import(self, path: str) -> dict:
        """What a file holds, before anything is added to the review."""
        file_path = Path((path or "").strip().strip('"'))
        info = self._import_problem(importer.parse_file, file_path)
        recs = info["records"]
        low = file_path.name.lower()
        guess = next((label for hint, label in (
            ("scopus", "Scopus"), ("savedrecs", "Web of Science"), ("wos", "Web of Science"),
            ("ieee", "IEEE Xplore"), ("export20", "IEEE Xplore"), ("pubmed", "PubMed"),
            ("embase", "Embase"), ("acm", "ACM Digital Library"), ("sciencedirect", "ScienceDirect"),
            ("ebsco", "EBSCOhost"), ("proquest", "ProQuest")) if hint in low),
            "PubMed" if info["format"] == "MEDLINE" else "")
        return {"path": str(file_path.resolve()), "name": file_path.name,
                "format": info["format"], "n_records": len(recs), "n_no_title": info["n_no_title"],
                "n_abstract": sum(1 for r in recs if r["abstract"]),
                "n_doi": sum(1 for r in recs if r["doi"]),
                "n_year": sum(1 for r in recs if r["year"]),
                "sample": [r["title"] for r in recs[:5]], "source_guess": guess}

    @staticmethod
    def _imports(state, phase: int) -> list:
        return state.state.setdefault(f"phase_{phase}_imports", [])

    def list_imports(self, run_id: str, phase: int) -> list[dict]:
        state, _, _ = self._load(run_id)
        return list(state.state.get(f"phase_{int(phase)}_imports", []))

    @staticmethod
    def _append_candidates(candidates_path: Path, rows: pd.DataFrame) -> int:
        """Append records to candidates.csv with fresh ids; existing ids never change."""
        existing = tui._read_csv_safe(candidates_path)
        start = 1
        if not existing.empty and "id" in existing.columns:
            start = int(pd.to_numeric(existing["id"], errors="coerce").max()) + 1
        rows = rows[[c for c in CANDIDATE_COLUMNS if c != "id"]].copy()
        rows.insert(0, "id", range(start, start + len(rows)))
        out = rows if existing.empty else pd.concat([existing, rows], ignore_index=True)
        out["year"] = pd.to_numeric(out["year"], errors="coerce").astype("Int64")
        out.to_csv(candidates_path, index=False, encoding="utf-8")
        return len(rows)

    @staticmethod
    def _append_strategy(strategy_path: Path, entries: list) -> None:
        cols = ["source", "query_sent", "retrieved_at", "n_retrieved", "total_available",
                "truncated", "status", "detail"]
        rows = pd.DataFrame([{
            "source": e["source"], "query_sent": e.get("query", ""), "retrieved_at": e.get("searched_on", ""),
            "n_retrieved": e["n_records"], "total_available": e["n_records"], "truncated": False,
            "status": "imported", "detail": f"file import: {e['file']} ({e['format']})"} for e in entries],
            columns=cols)
        existing = tui._read_csv_safe(strategy_path)
        out = rows if existing.empty else pd.concat([existing, rows], ignore_index=True)
        out.to_csv(strategy_path, index=False, encoding="utf-8")

    def _fold_imports(self, state, phase: int, pdir: Path) -> int:
        """After search.py rewrote candidates.csv and search_strategy.csv, put the
        phase's file imports back. Returns how many records were added."""
        entries = state.state.get(f"phase_{phase}_imports", [])
        stored = tui._read_csv_safe(pdir / "imported.csv")
        if not entries or stored.empty:
            return 0
        n = self._append_candidates(pdir / "candidates.csv", stored)
        self._append_strategy(pdir / "search_strategy.csv", entries)
        return n

    def start_import(self, run_id: str, phase: int, path: str, source: str,
                     query: str = "", searched_on: str = ""):
        """Add the records in an exported file to a phase, then rebuild the
        duplicates check and the screening sheet. Decisions already made stay."""
        state, _, _ = self._load(run_id)
        phase = int(phase)
        if phase > 1 and state.state.get("current_phase", 1) < phase:
            raise ServiceError(f"Phase {phase} is not open yet.")
        source = re.sub(r"\s+", " ", source or "").strip()
        if not source:
            raise ServiceError("Say which database the file came from. It is reported with "
                               "your search (PRISMA item 6).")
        file_path = Path((path or "").strip().strip('"'))
        parsed = self._import_problem(importer.parse_file, file_path)
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        if any(e.get("sha256") == digest for e in state.state.get(f"phase_{phase}_imports", [])):
            raise ServiceError(f"{file_path.name} was already imported into phase {phase}. "
                               "Importing it twice would count every record twice.")
        query = (query or "").strip()
        searched_on = (searched_on or "").strip() or date.today().isoformat()

        def task(ctx: JobContext):
            st, c, pv = self._load(run_id)
            pdir = st.phase_dir(phase)
            sheet_path = pdir / "screening.csv"
            old_sheet = tui._read_csv_safe(sheet_path)
            search_entry = st.state.get("stages", {}).get(f"{phase}:search")
            imported_only = search_entry is None or bool(search_entry.get("counts", {}).get("imported_only"))

            rows = pd.DataFrame(parsed["records"])
            rows.insert(0, "source", source)
            ctx.log(f"Read {len(rows)} record(s) from {file_path.name} ({parsed['format']}).")
            if parsed["n_no_title"]:
                ctx.log(f"Skipped {parsed['n_no_title']} entr(ies) with no title.")

            entry = {"file": file_path.name, "sha256": digest, "format": parsed["format"],
                     "source": source, "query": query, "searched_on": searched_on,
                     "n_records": len(rows), "imported_at": datetime.now().isoformat(timespec="seconds")}
            stored = tui._read_csv_safe(pdir / "imported.csv")
            keep = rows[[col for col in CANDIDATE_COLUMNS if col != "id"]]
            (keep if stored.empty else pd.concat([stored, keep], ignore_index=True)) \
                .to_csv(pdir / "imported.csv", index=False, encoding="utf-8")
            self._append_candidates(pdir / "candidates.csv", rows)
            self._append_strategy(pdir / "search_strategy.csv", [entry])
            self._imports(st, phase).append(entry)
            if not st.state.get(f"phase_{phase}_query"):
                st.state[f"phase_{phase}_query"] = c.search_query()
            # The duplicates check and the sheet are rebuilt over the larger set.
            for s in ("dedup", "prescreen"):
                st.state.get("stages", {}).pop(f"{phase}:{s}", None)
            counts = {"n_hits": len(tui._read_csv_safe(pdir / "candidates.csv")),
                      "n_imported": sum(e["n_records"] for e in self._imports(st, phase))}
            if imported_only:
                counts["imported_only"] = True
            st.mark_stage(phase, "search", counts=counts)

            self._run_search_dedup_prescreen(ctx, st, c, pv, phase, pdir, st.state[f"phase_{phase}_query"])
            n_rescued = self._rescue_decisions(pdir, old_sheet)
            if n_rescued:
                ctx.log(f"{n_rescued} decision(s) moved to the better copy of the same record "
                        "(the imported copy has a DOI or an abstract the earlier one lacked).")
            sheet = tui._read_csv_safe(sheet_path)
            n_new = max(len(sheet) - len(old_sheet), 0)
            n_undecided = (int(_blank_mask(sheet["ta_decision"]).sum())
                           if "ta_decision" in sheet.columns else len(sheet))
            if n_undecided:
                # new records need a decision, so an earlier gate no longer covers the phase
                for s in ("assist_ta", "review_gate"):
                    st.state.get("stages", {}).pop(f"{phase}:{s}", None)
                st.save()
            pv.log("records_imported", phase=phase, file=file_path.name, sha256=digest,
                   format=parsed["format"], source=source, query=query, searched_on=searched_on,
                   n_records=len(rows), n_new_to_screen=n_new, n_duplicates=len(rows) - n_new,
                   interface="desktop")
            ctx.log(f"Done: {len(rows)} imported, {n_new} new record(s) to screen, "
                    f"{len(rows) - n_new} already in the review or repeated in the file.")
            return {"n_records": len(rows), "n_new": n_new, "n_duplicates": len(rows) - n_new}

        return self.jobs.start(f"Phase {phase}: import {file_path.name}", task, run_id)

    @staticmethod
    def _rescue_decisions(pdir: Path, old_sheet: pd.DataFrame) -> int:
        """dedup.py keeps the best copy of a duplicated record (DOI first, then
        abstract), so an imported copy can replace an earlier one as the row on
        the sheet. A decision made on the earlier copy belongs to the same paper:
        move it to the row that replaced it instead of losing it."""
        if old_sheet.empty or "ta_decision" not in old_sheet.columns:
            return 0
        sheet_path = pdir / "screening.csv"
        sheet = tui._read_csv_safe(sheet_path)
        dedup = tui._read_csv_safe(pdir / "candidates_dedup.csv")
        if sheet.empty or dedup.empty or "duplicate_of" not in dedup.columns:
            return 0
        now_on_sheet = set(sheet["id"].map(tui._id_to_str))
        replaced_by = {tui._id_to_str(r["id"]): tui._id_to_str(r["duplicate_of"])
                       for _, r in dedup.iterrows() if pd.notna(r["duplicate_of"])}
        cols = [c for c in ("ta_decision", "ta_reason", "ft_decision", "ft_reason", "reviewer")
                if c in old_sheet.columns and c in sheet.columns]
        for col in cols:
            sheet[col] = sheet[col].astype(object)
        moved = 0
        for _, old in old_sheet.iterrows():
            rid = tui._id_to_str(old["id"])
            if rid in now_on_sheet or _blank_mask(pd.Series([old.get("ta_decision")])).iloc[0]:
                continue
            target = sheet.index[sheet["id"].map(tui._id_to_str) == replaced_by.get(rid, "")]
            if len(target) and _blank_mask(sheet.loc[[target[0]], "ta_decision"]).iloc[0]:
                for col in cols:
                    sheet.at[target[0], col] = tui._disp(old.get(col, ""))
                moved += 1
        if moved:
            sheet.to_csv(sheet_path, index=False, encoding="utf-8")
        return moved

    # --- reading aids for screening: highlighted words, one-click reasons ---
    def _aids(self, state, cfg) -> dict:
        saved = state.state.get("screening_aids", {})
        queries = [cfg.search_query()] + [state.state.get(f"phase_{p}_query", "")
                                          for p in range(1, cfg.n_phases + 1)]
        terms, seen = [], set()
        for q in queries:
            for phrase, word in re.findall(r'"([^"]+)"|([^\s()"]+)', q or ""):
                term = (phrase or word).strip()
                if term and term.upper() not in ("AND", "OR", "NOT") and term.lower() not in seen:
                    seen.add(term.lower())
                    terms.append(term)
        return {"keywords": terms, "use_keywords": bool(saved.get("use_keywords", True)),
                "include": list(saved.get("include", [])), "exclude": list(saved.get("exclude", [])),
                "reasons": list(saved.get("reasons", DEFAULT_REASONS))}

    def set_screening_aids(self, run_id: str, aids: dict) -> dict:
        """Words to highlight while reading and the list of one-click exclusion
        reasons. These help the reviewer read; they are not part of the protocol
        and change no decision, but the change is logged."""
        state, cfg, prov = self._load(run_id)

        def words(key, limit):
            raw = aids.get(key) or []
            if isinstance(raw, str):
                raw = re.split(r"[,\n]", raw)
            out, seen = [], set()
            for w in raw:
                w = re.sub(r"\s+", " ", str(w)).strip()[:80]
                if w and w.lower() not in seen:
                    seen.add(w.lower())
                    out.append(w)
            return out[:limit]

        saved = {"use_keywords": bool(aids.get("use_keywords", True)),
                 "include": words("include", 40), "exclude": words("exclude", 40),
                 "reasons": words("reasons", 9)}
        state.state["screening_aids"] = saved
        state.save()
        prov.log("screening_aids_edited", n_include_words=len(saved["include"]),
                 n_exclude_words=len(saved["exclude"]), reasons=saved["reasons"], interface="desktop")
        return self._aids(state, cfg)

    # --- undo ----------------------------------------------------------------
    def restore_decision(self, run_id: str, sheet: str, phase: int, rid: str,
                         decision: str = "", reason: str = "", reviewer: str = "") -> dict:
        """Undo: put one record back exactly as it was before the reviewer's last
        decision, including back to 'no decision'. The undo is itself written to
        decisions.jsonl, so the audit trail shows both the slip and the correction."""
        self._guard_idle()
        decision = (decision or "").strip().lower()
        allowed = ("include", "exclude", "") if sheet == "ft" else ("include", "exclude", "maybe", "")
        if sheet not in ("ta", "snowball", "ft") or decision not in allowed:
            raise ServiceError("That decision cannot be restored.")
        state, cfg, prov = self._load(run_id)
        phase = int(phase or 0)
        if sheet == "ta":
            path, col = state.run_dir / f"phase_{phase}" / "screening.csv", "ta"
            df = tui._read_csv_safe(path)
        elif sheet == "snowball":
            path, col = state.run_dir / "snowball" / "screening.csv", "ta"
            df = tui._read_csv_safe(path)
        else:
            path, col = state.run_dir / "included_final.csv", "ft"
            df = self._merged(state, cfg)
        if df.empty or "id" not in df.columns:
            raise ServiceError("There is no sheet to undo on.")
        columns = [f"{col}_decision", f"{col}_reason"] + (["reviewer"] if sheet != "ft" else [])
        for c in columns:
            df[c] = df[c].astype(object) if c in df.columns else ""
        matches = df.index[df["id"].map(tui._id_to_str) == str(rid)]
        if len(matches) == 0:
            raise ServiceError(f"No record with id {rid} on this sheet.")
        idx = matches[0]
        df.at[idx, f"{col}_decision"] = decision
        df.at[idx, f"{col}_reason"] = (reason or "").strip()
        if sheet != "ft":
            df.at[idx, "reviewer"] = (reviewer or "").strip()
        df.to_csv(path, index=False, encoding="utf-8")
        row = df.loc[idx]
        state.record_decision(
            record_key=record_key(row.get("doi", ""), row.get("title", "")),
            id=f"sb_{rid}" if sheet == "snowball" else tui._native(row.get("id")),
            decision=decision, stage=col, phase=phase if sheet == "ta" else 0,
            reason=(reason or "").strip(), source="human-review/undo",
            title=row.get("title", ""), doi=row.get("doi", ""))
        if sheet == "ft" and not decision and state.is_stage_done(1, "full_text_screening"):
            # a study is undecided again, so full-text screening is no longer complete
            state.state.get("stages", {}).pop("1:full_text_screening", None)
            state.save()
            prov.log("full_text_screening_reopened", reason="decision undone", id=str(rid))
        return {"id": str(rid), "decision": decision,
                "remaining": int(_blank_mask(df[f"{col}_decision"]).sum())}

    # --- title/abstract screening ------------------------------------------
    def get_screening(self, run_id: str, phase: int) -> dict:
        state, cfg, _ = self._load(run_id)
        phase = int(phase)
        path = state.run_dir / f"phase_{phase}" / "screening.csv"
        df = tui._read_csv_safe(path)
        if df.empty:
            return {"rows": [], "criteria": self._criteria(cfg), "reviewer": cfg.reviewer,
                    "aids": self._aids(state, cfg)}
        dedup = tui._read_csv_safe(state.run_dir / f"phase_{phase}" / "candidates_dedup.csv")
        extra = {}
        if not dedup.empty and "id" in dedup.columns:
            for _, r in dedup.iterrows():
                extra[tui._id_to_str(r.get("id"))] = {"authors": _clean(r.get("authors", "")),
                                                      "url": _clean(r.get("url", "")),
                                                      "source": _clean(r.get("source", ""))}
        # Decisions recorded in OTHER phases for the same record: shown as a hint,
        # the same cross-phase cache the terminal app uses to skip re-judging.
        earlier = {}
        for d in state.decisions():
            if d.get("stage") == "ta" and d.get("phase") != phase and d.get("record_key"):
                earlier[d["record_key"]] = d.get("decision", "")
        cols = ["id", "title", "year", "venue", "doi", "abstract", "ta_decision", "ta_reason", "reviewer"]
        rows = _rows(df, [c for c in cols if c in df.columns])
        for r in rows:
            r["id"] = tui._id_to_str(r.get("id", ""))
            r.update(extra.get(r["id"], {}))
            rk = record_key(r.get("doi", ""), r.get("title", ""))
            r["earlier_decision"] = earlier.get(rk, "")
            r["ta_decision"] = str(r.get("ta_decision", "")).strip().lower()
        return {"rows": rows, "criteria": self._criteria(cfg), "reviewer": cfg.reviewer,
                "topic": cfg.topic, "aids": self._aids(state, cfg)}

    @staticmethod
    def _criteria(cfg) -> dict:
        return {"inclusion": cfg.inclusion_criteria, "exclusion": cfg.exclusion_criteria}

    def set_ta_decision(self, run_id: str, phase: int, rid: str, decision: str, reason: str = "") -> dict:
        """A reviewer's own title/abstract decision -- the 'decide by hand'
        path the terminal app points to, with the audit entry it implies."""
        self._guard_idle()
        decision = (decision or "").strip().lower()
        if decision not in ("include", "exclude", "maybe"):
            raise ServiceError("Decision must be include, exclude or maybe.")
        state, cfg, _ = self._load(run_id)
        phase = int(phase)
        path = state.run_dir / f"phase_{phase}" / "screening.csv"
        df = tui._read_csv_safe(path)
        if df.empty or "id" not in df.columns:
            raise ServiceError("This phase has no screening sheet yet.")
        for col in ("ta_decision", "ta_reason", "reviewer"):
            df[col] = df[col].astype(object) if col in df.columns else ""
        matches = df.index[df["id"].map(tui._id_to_str) == str(rid)]
        if len(matches) == 0:
            raise ServiceError(f"No record with id {rid} in phase {phase}.")
        idx = matches[0]
        df.at[idx, "ta_decision"] = decision
        df.at[idx, "ta_reason"] = (reason or "").strip()
        df.at[idx, "reviewer"] = cfg.reviewer or "human-review"
        df.to_csv(path, index=False, encoding="utf-8")
        row = df.loc[idx]
        state.record_decision(record_key=record_key(row.get("doi", ""), row.get("title", "")),
                              id=tui._native(row.get("id")), decision=decision, stage="ta",
                              phase=phase, reason=(reason or "").strip(), source="human-review",
                              title=row.get("title", ""), doi=row.get("doi", ""))
        remaining = int(_blank_mask(df["ta_decision"]).sum())
        return {"id": str(rid), "decision": decision, "remaining": remaining}

    # --- AI-assist (manual paste) ------------------------------------------
    def assist_batch(self, run_id: str, phase: int, skipped_ids: list | None = None) -> dict:
        """Next batch of undecided records as a paste-ready prompt (same batch
        size, order, prompt and saved prompt file as the terminal app)."""
        state, cfg, _ = self._load(run_id)
        phase = int(phase)
        pdir = state.phase_dir(phase)
        skipped = {str(s) for s in skipped_ids or []}
        criteria = llm_assist.compose_criteria("ta", cfg.inclusion_criteria, cfg.exclusion_criteria)

        dedup_df = tui._read_csv_safe(pdir / "candidates_dedup.csv")
        if dedup_df.empty:
            return {"done": True, "message": "No candidates to screen in this phase yet."}
        if "duplicate_of" in dedup_df.columns:
            dedup_df = dedup_df[dedup_df["duplicate_of"].isna()]
        screening_df = tui._read_csv_safe(pdir / "screening.csv")
        decided = set()
        if not screening_df.empty and "ta_decision" in screening_df.columns:
            mask = ~_blank_mask(screening_df["ta_decision"])
            decided = {tui._id_to_str(v) for v in screening_df.loc[mask, "id"]}
        cross = state.decided_keys("ta")
        undecided = []
        for _, row in dedup_df.iterrows():
            rid = tui._id_to_str(row.get("id"))
            if rid in decided or rid in skipped:
                continue
            rk = record_key(row.get("doi", ""), row.get("title", ""))
            if rk and rk in cross:
                continue
            undecided.append(row)
        if not undecided:
            return {"done": True, "message": "No undecided records left for AI-assist in this phase."}

        batch = undecided[:BATCH_SIZE]
        start = len(dedup_df) - len(undecided)
        records = [{"id": r.get("id"), "title": r.get("title", ""), "abstract": r.get("abstract", ""),
                    "year": r.get("year", ""), "venue": r.get("venue", "")} for r in batch]
        prompt = llm_assist.build_screening_prompt(
            records, stage="ta", topic=cfg.topic, criteria=criteria,
            batch_label=f"phase={phase} stage=ta rows={start}-{start + len(records) - 1}")
        (pdir / f"prompt_ta_{start}.txt").write_text(prompt, encoding="utf-8")
        return {
            "done": False, "prompt": prompt, "start": start,
            "batch_ids": [tui._id_to_str(r.get("id")) for r in batch],
            "batch_titles": [_clean(r.get("title", "")) for r in batch],
            "n_undecided": len(undecided), "n_batches": -(-len(undecided) // BATCH_SIZE),
            "criteria_missing": not criteria, "tool": cfg.assist_tool_name or "your web chatbot",
            "prompt_file": str(pdir / f"prompt_ta_{start}.txt"),
        }

    def assist_apply(self, run_id: str, phase: int, start: int, batch_ids: list, reply: str) -> dict:
        self._guard_idle()
        if not (reply or "").strip():
            raise ServiceError("Paste the chatbot's reply first.")
        state, cfg, prov = self._load(run_id)
        phase = int(phase)
        pdir = state.phase_dir(phase)
        # Keep the reply on disk next to its prompt, as the terminal flow does.
        (pdir / f"reply_ta_{int(start)}.txt").write_text(reply, encoding="utf-8")
        valid = [int(i) if str(i).isdigit() else i for i in batch_ids]
        parsed = llm_assist.parse_screening_response(reply, valid_ids=valid)
        applied = apply_decisions(pdir / "screening.csv", parsed, state, phase, stage="ta")
        prov.log("assist_response_parsed", phase=phase, n_decided=len(applied.matched),
                 n_include=applied.counts.get("include", 0),
                 n_exclude=applied.counts.get("exclude", 0), tool=cfg.assist_tool_name)
        return {"counts": dict(applied.counts), "n_applied": len(applied.matched),
                "problems": parsed.problems() + applied.problems()}

    def assist_finish(self, run_id: str, phase: int, skipped_ids: list | None = None,
                      totals: dict | None = None) -> dict:
        """Close an AI-assist session: mark the stage done only when nothing in
        the phase is left without a decision (slr.run_ai_assist_loop's rule)."""
        state, _, prov = self._load(run_id)
        phase = int(phase)
        screening = state.run_dir / f"phase_{phase}" / "screening.csv"
        skipped = {str(s) for s in skipped_ids or []}
        remaining = tui._undecided_ta_ids(screening) - skipped
        if remaining:
            prov.log("assist_incomplete", phase=phase, n_undecided=len(remaining),
                     n_skipped_for_manual=len(skipped))
            return {"complete": False, "remaining": len(remaining)}
        state.mark_stage(phase, "assist_ta", counts=dict(Counter(totals or {})))
        return {"complete": True, "remaining": 0}

    # --- review gate -------------------------------------------------------
    def get_gate(self, run_id: str, phase: int) -> dict:
        state, _, _ = self._load(run_id)
        phase = int(phase)
        df = tui._read_csv_safe(state.run_dir / f"phase_{phase}" / "screening.csv")
        if df.empty or "ta_decision" not in df.columns:
            return {"empty": True, "included": [], "excluded": [], "n_undecided": 0}
        decisions = df["ta_decision"].astype(str).str.strip().str.lower()
        reviewers = (df["reviewer"].astype(str).str.strip().str.lower() if "reviewer" in df.columns
                     else pd.Series([""] * len(df), index=df.index))
        inc_mask = ta_proceeds_mask(decisions)
        exc_mask = decisions.eq("exclude")
        ai_exc = df[exc_mask & reviewers.eq(AI_REVIEWER)]
        sample_ids = set()
        if not ai_exc.empty:
            n = min(EXCLUDE_SAMPLE_SIZE, len(ai_exc))
            sample = ai_exc.sample(n=n, random_state=phase) if n < len(ai_exc) else ai_exc
            sample_ids = {tui._id_to_str(v) for v in sample["id"]}
        cols = ["id", "title", "year", "venue", "doi", "abstract", "ta_decision", "ta_reason", "reviewer"]
        cols = [c for c in cols if c in df.columns]
        included = _rows(df[inc_mask], cols)
        excluded = _rows(df[exc_mask], cols)
        for r in included + excluded:
            r["id"] = tui._id_to_str(r["id"])
            r["ai"] = str(r.get("reviewer", "")).strip().lower() == AI_REVIEWER
            r["in_sample"] = r["id"] in sample_ids
        return {
            "empty": False, "included": included, "excluded": excluded,
            "n_ai_excluded": len(ai_exc), "sample_size": len(sample_ids),
            "n_undecided": int(_blank_mask(df["ta_decision"]).sum()),
            "done": state.is_stage_done(phase, "review_gate"),
        }

    def apply_gate(self, run_id: str, phase: int, to_exclude: list, to_include: list) -> dict:
        """slr.run_review_gate without the prompts: flip in either direction,
        audit every flip, and mark the gate done only when nothing is undecided."""
        self._guard_idle()
        state, cfg, prov = self._load(run_id)
        phase = int(phase)
        path = state.run_dir / f"phase_{phase}" / "screening.csv"
        df = tui._read_csv_safe(path)
        if df.empty or "ta_decision" not in df.columns:
            state.mark_stage(phase, "review_gate", counts={"n_included": 0, "n_overridden": 0})
            prov.log("phase_review", phase=phase, n_included=0, n_overridden=0)
            self._advance_after_gate(state, cfg, phase)
            return {"complete": True, "n_included": 0, "n_to_exclude": 0, "n_to_include": 0}
        for col in ("ta_decision", "ta_reason", "reviewer"):
            if col in df.columns:
                df[col] = df[col].astype(object)
        decisions = df["ta_decision"].astype(str).str.strip().str.lower()
        inc_mask = ta_proceeds_mask(decisions)
        exc_mask = decisions.eq("exclude")
        flip_exc = {str(i).strip() for i in to_exclude or [] if str(i).strip()}
        flip_inc = {str(i).strip() for i in to_include or [] if str(i).strip()}

        n_exc = n_inc = 0
        for idx, row in df.iterrows():
            rid = tui._id_to_str(row.get("id", ""))
            new = None
            if rid in flip_exc and inc_mask.get(idx, False):
                new = "exclude"
            elif rid in flip_inc and exc_mask.get(idx, False):
                new = "include"
            if new is None:
                continue
            df.at[idx, "ta_decision"] = new
            prior = tui._disp(df.at[idx, "ta_reason"]) if "ta_reason" in df.columns else ""
            df.at[idx, "ta_reason"] = (prior + " [reviewer override]").strip()
            if "reviewer" in df.columns:
                df.at[idx, "reviewer"] = cfg.reviewer or "human-review"
            state.record_decision(record_key=record_key(row.get("doi", ""), row.get("title", "")),
                                  id=tui._native(row.get("id")), decision=new, stage="ta",
                                  phase=phase, reason="reviewer override", source="human-review",
                                  title=row.get("title", ""), doi=row.get("doi", ""))
            if new == "exclude":
                n_exc += 1
            else:
                n_inc += 1
        if n_exc or n_inc:
            df.to_csv(path, index=False, encoding="utf-8")

        n_included = int(inc_mask.sum()) - n_exc + n_inc
        n_undecided = int(_blank_mask(df["ta_decision"]).sum())
        if n_undecided:
            prov.log("review_gate_incomplete", phase=phase, n_undecided=n_undecided)
            return {"complete": False, "n_undecided": n_undecided, "n_included": n_included,
                    "n_to_exclude": n_exc, "n_to_include": n_inc}
        state.mark_stage(phase, "review_gate",
                         counts={"n_included": n_included, "n_overridden": n_exc + n_inc})
        prov.log("phase_review", phase=phase, n_included=n_included, n_overridden=n_exc + n_inc)
        self._advance_after_gate(state, cfg, phase)
        return {"complete": True, "n_included": n_included, "n_to_exclude": n_exc,
                "n_to_include": n_inc}

    @staticmethod
    def _advance_after_gate(state, cfg, phase: int) -> None:
        # The last phase has no expansion step; finishing its gate ends the loop.
        if phase >= cfg.n_phases and state.state.get("current_phase", 1) <= phase:
            state.state["current_phase"] = phase + 1
            state.save()

    # --- query expansion ---------------------------------------------------
    def expansion_terms(self, run_id: str, phase: int) -> dict:
        state, cfg, _ = self._load(run_id)
        phase = int(phase)
        df = tui._read_csv_safe(state.run_dir / f"phase_{phase}" / "screening.csv")
        titles = []
        if not df.empty and "ta_decision" in df.columns:
            titles = df.loc[ta_proceeds_mask(df["ta_decision"]), "title"].dropna().astype(str).tolist()
        return {"terms": tui._extract_expansion_terms(titles, cfg.all_keywords()),
                "current_query": state.state.get(f"phase_{phase}_query") or cfg.search_query()}

    def apply_expansion(self, run_id: str, phase: int, picked: list) -> dict:
        self._guard_idle()
        state, cfg, prov = self._load(run_id)
        phase = int(phase)
        if not state.is_stage_done(phase, "review_gate"):
            raise ServiceError(f"Finish phase {phase}'s review gate first.")
        if phase >= cfg.n_phases:
            raise ServiceError("This is the last phase; there is no next search to expand.")
        picked = [t.strip() for t in picked or [] if t and t.strip()]
        if cfg.keyword_blocks:
            nxt = replace(cfg, keyword_blocks=list(cfg.keyword_blocks) + [[t] for t in picked])
        else:
            nxt = replace(cfg, keywords=list(cfg.keywords) + picked)
        query = nxt.search_query()
        state.state[f"phase_{phase + 1}_query"] = query
        state.mark_stage(phase, "snowball", counts={"n_added": len(picked)})
        prov.log("query_expansion", from_phase=phase, added_keywords=picked)
        state.state["current_phase"] = phase + 1
        state.save()
        return {"next_query": query}

    # --- consolidation: merge + full text ----------------------------------
    @staticmethod
    def _merged(state, cfg, force: bool = False) -> pd.DataFrame:
        """slr._ensure_merged, without losing work when the set is rebuilt.

        The terminal app rebuilds included_final.csv from the phase sheets
        whenever a screening decision changed (or on an explicit merge), and
        the rebuilt file starts with blank full-text columns and without any
        citation-snowball rows merged in earlier. In a window where the gate
        can be re-confirmed with one click, that would silently discard
        full-text decisions already recorded in decisions.jsonl. So: full-text
        decisions are carried over by study id for every study still in the
        set, and merged snowball rows are kept.
        """
        path = state.run_dir / "included_final.csv"
        old = tui._read_csv_safe(path)
        before = path.stat().st_mtime_ns if path.exists() else None
        merged = tui._ensure_merged(state, cfg, _quiet_console(), force=force)
        after = path.stat().st_mtime_ns if path.exists() else None
        if old.empty or before == after or "id" not in old.columns:
            return merged

        changed = False
        for col in ("ft_decision", "ft_reason"):
            merged[col] = merged[col].astype(object) if col in merged.columns else ""
        if "ft_decision" in old.columns:
            kept = {tui._id_to_str(r["id"]): r for _, r in old.iterrows()
                    if not _blank_mask(pd.Series([r.get("ft_decision")])).iloc[0]}
            for idx, rid in merged["id"].map(tui._id_to_str).items():
                prev = kept.get(rid)
                if prev is not None:
                    merged.at[idx, "ft_decision"] = prev.get("ft_decision")
                    merged.at[idx, "ft_reason"] = tui._disp(prev.get("ft_reason", ""))
                    changed = True
        snow = old[old["id"].astype(str).str.startswith("sb_")]
        if not snow.empty:
            common = [c for c in merged.columns if c in snow.columns]
            merged = pd.concat([merged, snow[common]], ignore_index=True)
            changed = True
        if changed:
            merged.to_csv(path, index=False, encoding="utf-8")
            dedup_cols = [c for c in ("id", "doi", "url", "title", "authors", "year", "venue")
                          if c in merged.columns]
            merged[dedup_cols].to_csv(state.run_dir / "final_dedup.csv", index=False, encoding="utf-8")
        return merged

    def merge(self, run_id: str) -> dict:
        self._guard_idle()
        state, cfg, prov = self._load(run_id)
        merged = self._merged(state, cfg, force=True)
        prov.log("merge_included", n_included=len(merged))
        return {"n_merged": len(merged)}

    def get_fulltext(self, run_id: str) -> dict:
        state, cfg, _ = self._load(run_id)
        merged = self._merged(state, cfg)
        if merged.empty:
            return {"rows": [], "criteria": self._criteria(cfg), "aids": self._aids(state, cfg)}
        cols = ["id", "title", "authors", "year", "venue", "doi", "url", "abstract",
                "ta_decision", "ft_decision", "ft_reason", "phase"]
        rows = _rows(merged, [c for c in cols if c in merged.columns])
        pdf_dir = state.run_dir / "pdfs"
        for r in rows:
            r["id"] = tui._id_to_str(r["id"])
            r["ft_decision"] = str(r.get("ft_decision", "")).strip().lower()
            pdf = pdf_dir / f"{r['id']}.pdf"
            r["pdf"] = str(pdf) if pdf.exists() else ""
        return {"rows": rows, "criteria": self._criteria(cfg), "aids": self._aids(state, cfg),
                "done": state.is_stage_done(1, "full_text_screening")}

    def set_ft_decision(self, run_id: str, rid: str, decision: str, reason: str = "") -> dict:
        self._guard_idle()
        decision = (decision or "").strip().lower()
        if decision not in ("include", "exclude"):
            raise ServiceError("A full-text decision is include or exclude (there is no maybe "
                               "at full text).")
        if decision == "exclude" and not (reason or "").strip():
            raise ServiceError("An exclusion needs a reason (PRISMA item 16b).")
        state, cfg, prov = self._load(run_id)
        path = state.run_dir / "included_final.csv"
        df = self._merged(state, cfg)
        for col in ("ft_decision", "ft_reason"):
            df[col] = df[col].astype(object) if col in df.columns else ""
        matches = df.index[df["id"].map(tui._id_to_str) == str(rid)]
        if len(matches) == 0:
            raise ServiceError(f"No study with id {rid} in the merged set.")
        idx = matches[0]
        df.at[idx, "ft_decision"] = decision
        df.at[idx, "ft_reason"] = (reason or "").strip()
        df.to_csv(path, index=False, encoding="utf-8")
        row = df.loc[idx]
        state.record_decision(record_key=record_key(row.get("doi", ""), row.get("title", "")),
                              id=tui._native(row.get("id")), decision=decision, stage="ft",
                              phase=0, reason=(reason or "").strip(), source="human-review",
                              title=row.get("title", ""), doi=row.get("doi", ""))
        return self._ft_tally(state, prov, final=False)

    def finish_fulltext(self, run_id: str) -> dict:
        state, _, prov = self._load(run_id)
        return self._ft_tally(state, prov, final=True)

    @staticmethod
    def _ft_tally(state, prov, final: bool) -> dict:
        final_df = tui._read_csv_safe(state.run_dir / "included_final.csv")
        ft = final_df.get("ft_decision", pd.Series(dtype=object)).astype(str).str.strip().str.lower()
        n_inc, n_exc = int(ft.eq("include").sum()), int(ft.eq("exclude").sum())
        n_left = len(final_df) - n_inc - n_exc
        out = {"n_included": n_inc, "n_excluded": n_exc, "n_left": n_left, "complete": False}
        if final or n_left == 0:
            prov.log("full_text_screening", n_included=n_inc, n_excluded=n_exc, n_undecided=n_left)
            if n_left:
                prov.log("full_text_screening_incomplete", n_undecided=n_left)
            else:
                state.mark_stage(1, "full_text_screening",
                                 counts={"n_included": n_inc, "n_excluded": n_exc})
                out["complete"] = True
        return out

    # --- consolidation: background stage scripts ---------------------------
    def start_download(self, run_id: str):
        self._need_email(self._load(run_id)[1])

        def task(ctx):
            state, cfg, prov = self._load(run_id)
            self._merged(state, cfg)
            rd = state.run_dir
            cmd = [_py(), _script("download.py"), "--in", str(rd / "final_dedup.csv"),
                   "--mailto", cfg.mailto, "--outdir", str(rd / "pdfs"),
                   "--report", str(rd / "manual_download_needed.csv"), "--log", str(rd / "download_log.csv")]
            if not ctx.run(cmd, "Downloading open-access PDFs", secret_env=cfg.secret_env()):
                raise ServiceError("PDF download did not finish.")
            n_manual = len(tui._read_csv_safe(rd / "manual_download_needed.csv"))
            n_total = len(tui._read_csv_safe(rd / "final_dedup.csv"))
            n_ok = max(n_total - n_manual, 0)
            prov.log("download_pdfs", n_downloaded=n_ok, n_manual=n_manual)
            ctx.log(f"{n_ok} downloaded, {n_manual} need manual retrieval. Zero downloads usually "
                    f"means the set is mostly closed-access, not a failure.")
            return {"n_downloaded": n_ok, "n_manual": n_manual}
        return self.jobs.start("Download PDFs", task, run_id)

    def start_verify(self, run_id: str):
        def task(ctx):
            state, cfg, prov = self._load(run_id)
            self._merged(state, cfg)
            rd = state.run_dir
            cmd = [_py(), _script("verify_citations.py"), "--csv", str(rd / "included_final.csv"),
                   "--out", str(rd / "citation_verification.csv")]
            # exit 1 means "some DOIs failed", which is still a completed check
            if not ctx.run(cmd, "Verifying citation DOIs against Crossref", success_codes=(0, 1)):
                prov.log("citation_verification", status="did_not_run")
                raise ServiceError("Citation verification did not complete -- do not treat this "
                                   "as a pass.")
            res = tui._read_csv_safe(rd / "citation_verification.csv")
            if res.empty or "status" not in res.columns:
                prov.log("citation_verification", status="no_results")
                raise ServiceError("No verification results were written; the guard has not run.")
            n_failed = int(res["status"].astype(str).str.strip().str.upper().eq("FAIL").sum())
            prov.log("citation_verification", status="ran", n_checked=len(res), n_failed=n_failed)
            return {"n_checked": len(res), "n_failed": n_failed}
        return self.jobs.start("Verify citations", task, run_id)

    def verification_results(self, run_id: str) -> list[dict]:
        state, _, _ = self._load(run_id)
        df = tui._read_csv_safe(state.run_dir / "citation_verification.csv")
        return _rows(df, list(df.columns)) if not df.empty else []

    def start_extract(self, run_id: str):
        def task(ctx):
            state, cfg, prov = self._load(run_id)
            self._merged(state, cfg)
            rd = state.run_dir
            cmd = [_py(), _script("extract.py"), "--in", str(rd / "included_final.csv"),
                   "--out", str(rd / "extraction.csv"), "--candidates", str(rd / "final_dedup.csv")]
            if cfg.primary_study_instruments:
                cmd += ["--instruments", ",".join(cfg.primary_study_instruments)]
            previous = tui._read_csv_safe(rd / "extraction.csv")
            if not ctx.run(cmd, "Building the extraction sheet"):
                raise ServiceError("Building the extraction sheet did not finish.")
            n_kept = self._carry_extraction(rd / "extraction.csv", previous)
            if n_kept:
                ctx.log(f"Kept the values already entered for {n_kept} study/studies.")
            n = len(tui._read_csv_safe(rd / "extraction.csv"))
            prov.log("build_extraction_sheet", n_rows=n, n_rows_carried_over=n_kept)
            return {"n_rows": n, "n_kept": n_kept}
        return self.jobs.start("Build extraction sheet", task, run_id)

    @staticmethod
    def _carry_extraction(path: Path, previous: pd.DataFrame) -> int:
        """extract.py writes a blank template every time. Put back what a
        reviewer had already typed, for studies (matched by id) still in the set."""
        _BIB = {"id", "title", "authors", "year", "venue", "doi"}
        fresh = tui._read_csv_safe(path)
        if previous.empty or fresh.empty or "id" not in previous.columns:
            return 0
        old = {tui._id_to_str(r["id"]): r for _, r in previous.iterrows()}
        cols = [c for c in fresh.columns if c not in _BIB and c in previous.columns]
        for col in cols:
            fresh[col] = fresh[col].astype(object)
        kept = 0
        for idx, rid in fresh["id"].map(tui._id_to_str).items():
            prev = old.get(rid)
            if prev is None:
                continue
            touched = False
            for col in cols:
                value = tui._disp(prev.get(col, ""))
                if value:
                    fresh.at[idx, col] = prev.get(col)
                    touched = True
            kept += touched
        if kept:
            fresh.to_csv(path, index=False, encoding="utf-8")
        return kept

    def figures_check(self, run_id: str) -> dict:
        state, cfg, _ = self._load(run_id)
        counts = tui._compute_prisma_counts(state, cfg)
        return {"counts": counts, "warnings": prisma_residuals(counts)}

    def figures_skip(self, run_id: str, warnings: list) -> dict:
        _, _, prov = self._load(run_id)
        prov.log("figures_skipped", reason="unbalanced PRISMA counts", warnings=warnings)
        return {}

    def start_figures(self, run_id: str):
        def task(ctx):
            state, cfg, prov = self._load(run_id)
            counts = tui._compute_prisma_counts(state, cfg)
            warnings = prisma_residuals(counts)
            for w in warnings:
                ctx.log(f"⚠ PRISMA check: {w}")
            rd = state.run_dir
            cmd = [_py(), _script("figures.py"), "--run-dir", str(rd),
                   "--quality", str(rd / "extraction.csv"), "--outdir", str(rd / "figures"),
                   "--identified", str(counts["identified"]),
                   "--duplicates-removed", str(counts["duplicates_removed"]),
                   "--screened", str(counts["screened"]), "--excluded-ta", str(counts["excluded_ta"]),
                   "--assessed-ft", str(counts["assessed_ft"]), "--excluded-ft", str(counts["excluded_ft"]),
                   "--included", str(counts["included"])]
            if not ctx.run(cmd, "Drawing PRISMA and tier figures"):
                raise ServiceError("Figure generation did not finish.")
            prov.log("figures_generated", counts=counts, unbalanced_warnings=warnings)
            return {}
        return self.jobs.start("Generate figures", task, run_id)

    def figures(self, run_id: str) -> list[dict]:
        state, _, _ = self._load(run_id)
        out = []
        fig_dir = state.run_dir / "figures"
        if fig_dir.exists():
            for png in sorted(fig_dir.glob("*.png")):
                data = base64.b64encode(png.read_bytes()).decode("ascii")
                pdf = png.with_suffix(".pdf")
                out.append({"name": png.stem.replace("_", " "), "path": str(png),
                            "pdf": str(pdf) if pdf.exists() else "",
                            "src": f"data:image/png;base64,{data}"})
        return out

    def start_snowball(self, run_id: str, direction: str = "both", max_per_seed: int = 50):
        if direction not in ("both", "backward", "forward"):
            raise ServiceError("Direction must be both, backward or forward.")
        self._need_email(self._load(run_id)[1])

        def task(ctx):
            state, cfg, prov = self._load(run_id)
            merged = self._merged(state, cfg)
            if merged.empty:
                raise ServiceError("No included studies to snowball from yet -- finish a review gate first.")
            sdir = state.run_dir / "snowball"
            sdir.mkdir(exist_ok=True)
            cand, dedup, screening = sdir / "candidates.csv", sdir / "candidates_dedup.csv", sdir / "screening.csv"
            if not ctx.run([_py(), _script("snowball.py"), "--seeds", str(state.run_dir / "final_dedup.csv"),
                            "--mailto", cfg.mailto, "--direction", direction,
                            "--max-per-seed", str(int(max_per_seed)), "--out", str(cand)],
                           "Citation snowballing"):
                raise ServiceError("Snowballing did not finish.")
            n_found = len(tui._read_csv_safe(cand))
            if n_found == 0:
                prov.log("citation_snowball", direction=direction, n_found=0)
                return {"n_found": 0, "n_unique": 0}
            if not ctx.run([_py(), _script("dedup.py"), "--in", str(cand), "--out", str(dedup),
                            "--title-threshold", str(cfg.title_threshold)], "De-duplicating snowball candidates"):
                raise ServiceError("De-duplication did not finish.")
            if not ctx.run([_py(), _script("screen.py"), "--in", str(dedup), "--out", str(screening)],
                           "Building the snowball screening sheet"):
                raise ServiceError("Building the snowball screening sheet did not finish.")
            n_unique = len(tui._read_csv_safe(dedup))
            prov.log("citation_snowball", direction=direction, n_found=n_found, n_unique=n_unique)
            return {"n_found": n_found, "n_unique": n_unique}
        return self.jobs.start("Citation snowballing", task, run_id)

    def merge_snowball(self, run_id: str) -> dict:
        self._guard_idle()
        state, _, prov = self._load(run_id)
        sdf = tui._read_csv_safe(state.run_dir / "snowball" / "screening.csv")
        if sdf.empty or "ta_decision" not in sdf.columns:
            raise ServiceError("No snowball screening sheet yet -- run citation snowballing first.")
        proceed = sdf[ta_proceeds_mask(sdf["ta_decision"])].copy()
        if proceed.empty:
            raise ServiceError("No included/maybe rows in the snowball screening sheet yet.")
        path = state.run_dir / "included_final.csv"
        existing = tui._read_csv_safe(path)
        if existing.empty:
            raise ServiceError("Merge the TA-included studies first.")
        proceed["id"] = [f"sb_{i}" for i in proceed["id"]]
        proceed["phase"] = "snowball"
        for col in ("ft_decision", "ft_reason"):
            if col not in proceed.columns:
                proceed[col] = ""
        keys = set(existing.apply(lambda r: record_key(r.get("doi", ""), r.get("title", "")), axis=1))
        proceed["record_key"] = proceed.apply(lambda r: record_key(r.get("doi", ""), r.get("title", "")), axis=1)
        new_rows = proceed[~proceed["record_key"].isin(keys)].drop(columns=["record_key"])
        n_dupe = len(proceed) - len(new_rows)
        if new_rows.empty:
            return {"n_added": 0, "n_duplicate": n_dupe}
        common = [c for c in existing.columns if c in new_rows.columns]
        pd.concat([existing, new_rows[common]], ignore_index=True).to_csv(path, index=False, encoding="utf-8")
        prov.log("merge_citation_snowball", n_added=len(new_rows), n_duplicate=n_dupe)
        return {"n_added": len(new_rows), "n_duplicate": n_dupe}

    # --- snowball sheet screening (same UI as a phase) ---------------------
    def get_snowball_screening(self, run_id: str) -> dict:
        state, cfg, _ = self._load(run_id)
        df = tui._read_csv_safe(state.run_dir / "snowball" / "screening.csv")
        cols = ["id", "title", "year", "venue", "doi", "abstract", "ta_decision", "ta_reason", "reviewer"]
        rows = _rows(df, [c for c in cols if c in df.columns]) if not df.empty else []
        for r in rows:
            r["id"] = tui._id_to_str(r["id"])
            r["ta_decision"] = str(r.get("ta_decision", "")).strip().lower()
            r["earlier_decision"] = ""
        return {"rows": rows, "criteria": self._criteria(cfg), "reviewer": cfg.reviewer,
                "topic": cfg.topic, "aids": self._aids(state, cfg)}

    def set_snowball_decision(self, run_id: str, rid: str, decision: str, reason: str = "") -> dict:
        self._guard_idle()
        decision = (decision or "").strip().lower()
        if decision not in ("include", "exclude", "maybe"):
            raise ServiceError("Decision must be include, exclude or maybe.")
        state, cfg, _ = self._load(run_id)
        path = state.run_dir / "snowball" / "screening.csv"
        df = tui._read_csv_safe(path)
        if df.empty:
            raise ServiceError("No snowball screening sheet yet.")
        for col in ("ta_decision", "ta_reason", "reviewer"):
            df[col] = df[col].astype(object) if col in df.columns else ""
        matches = df.index[df["id"].map(tui._id_to_str) == str(rid)]
        if len(matches) == 0:
            raise ServiceError(f"No snowball record with id {rid}.")
        idx = matches[0]
        df.at[idx, "ta_decision"] = decision
        df.at[idx, "ta_reason"] = (reason or "").strip()
        df.at[idx, "reviewer"] = cfg.reviewer or "human-review"
        df.to_csv(path, index=False, encoding="utf-8")
        row = df.loc[idx]
        # phase 0 + source "snowball" keeps these apart from the search phases' decisions
        state.record_decision(record_key=record_key(row.get("doi", ""), row.get("title", "")),
                              id=f"sb_{tui._id_to_str(row.get('id'))}", decision=decision, stage="ta",
                              phase=0, reason=(reason or "").strip(), source="human-review/snowball",
                              title=row.get("title", ""), doi=row.get("doi", ""))
        return {"id": str(rid), "decision": decision,
                "remaining": int(_blank_mask(df["ta_decision"]).sum())}

    # --- extraction editor -------------------------------------------------
    def get_extraction(self, run_id: str) -> dict:
        state, cfg, _ = self._load(run_id)
        df = tui._read_csv_safe(state.run_dir / "extraction.csv")
        if df.empty:
            return {"rows": [], "fields": EXTRACTION_FIELDS, "enums": EXTRACTION_ENUMS, "columns": []}
        rows = _rows(df, list(df.columns))
        for r in rows:
            r["id"] = tui._id_to_str(r.get("id", ""))
        return {"rows": rows, "fields": EXTRACTION_FIELDS, "enums": EXTRACTION_ENUMS,
                "columns": list(df.columns)}

    def update_extraction(self, run_id: str, rid: str, field: str, value: str) -> dict:
        """slr._menu_review_extraction for one field: audit the edit, and keep
        quality_tier consistent with R/A/T/C (or log a manual override)."""
        self._guard_idle()
        state, cfg, prov = self._load(run_id)
        path = state.run_dir / "extraction.csv"
        df = tui._read_csv_safe(path)
        if df.empty:
            raise ServiceError("No extraction sheet yet -- build it first.")
        editable = set(EXTRACTION_FIELDS) | {c for c in df.columns if c not in
                                             ("id", "title", "authors", "year", "venue", "doi")}
        if field not in editable:
            raise ServiceError(f"'{field}' is not an editable extraction field.")
        value = str(value or "").strip()
        if field in EXTRACTION_ENUMS and value and value not in EXTRACTION_ENUMS[field]:
            raise ServiceError(f"{field} must be one of {', '.join(EXTRACTION_ENUMS[field])}.")
        for f in EXTRACTION_FIELDS + ["extraction_reviewer", "extraction_date", field]:
            df[f] = df[f].astype(object) if f in df.columns else ""
        matches = df.index[df["id"].astype(str).str.strip() == str(rid)]
        if len(matches) == 0:
            matches = df.index[df["id"].map(tui._id_to_str) == str(rid)]
        if len(matches) == 0:
            raise ServiceError(f"No study with id {rid} in extraction.csv.")
        idx = matches[0]
        old = tui._disp(df.at[idx, field])
        if old == value:
            return {"changed": False, "row": self._ext_row(df, idx)}
        reviewer = cfg.reviewer or "human-review"
        df.at[idx, field] = value
        prov.log("extraction_field_edited", study_id=str(rid), field=field, old_value=old,
                 new_value=value, reviewer=reviewer)
        note = ""
        r, a, t, c = (df.at[idx, "R"], df.at[idx, "A"], df.at[idx, "T"], df.at[idx, "C"])
        computed = compute_quality_tier(r, a, t, c)
        if field == "quality_tier":
            if computed is not None and value.upper() != computed:
                note = f"Overrides the computed tier '{computed}' -- logged as a manual override."
                prov.log("quality_tier_manual_override", study_id=str(rid), computed=computed,
                         override=value, reviewer=reviewer)
        elif field in ("R", "A", "T", "C") and computed is not None:
            prior = tui._disp(df.at[idx, "quality_tier"])
            df.at[idx, "quality_tier"] = computed
            if prior and prior != computed:
                note = f"quality_tier recomputed from R/A/T/C: '{prior}' -> '{computed}'."
            prov.log("quality_tier_recomputed", study_id=str(rid), quality_tier=computed,
                     r=r, a=a, t=t, c=c)
        df.at[idx, "extraction_reviewer"] = reviewer
        df.at[idx, "extraction_date"] = date.today().isoformat()
        df.to_csv(path, index=False, encoding="utf-8")
        return {"changed": True, "note": note, "row": self._ext_row(df, idx)}

    @staticmethod
    def _ext_row(df, idx) -> dict:
        row = {c: _clean(df.at[idx, c]) for c in df.columns}
        row["id"] = tui._id_to_str(row.get("id", ""))
        return row

    # --- reports and exports -----------------------------------------------
    def export_references(self, run_id: str) -> dict:
        self._guard_idle()
        state, _, prov = self._load(run_id)
        rd = state.run_dir
        df, src = pd.DataFrame(), ""
        if (rd / "extraction.csv").exists():
            df, src = tui._read_csv_safe(rd / "extraction.csv"), "extraction.csv"
        if df.empty and (rd / "included_final.csv").exists():
            df, src = tui._read_csv_safe(rd / "included_final.csv"), "included_final.csv"
        if df.empty:
            raise ServiceError("Nothing to export yet -- merge the included studies first.")
        filtered, used = pick_progressed(df, "ft_decision")
        note = ""
        if filtered.empty and len(df):
            note = f"No rows progressed on '{used}' -- exported all {len(df)} rows unfiltered."
        else:
            df = filtered
        records = df.to_dict("records")
        (rd / "references.bib").write_text(srp_export.to_bibtex(records), encoding="utf-8")
        (rd / "references.ris").write_text(srp_export.to_ris(records), encoding="utf-8")
        prov.log("export_references", n_records=len(records), source=src)
        return {"n_records": len(records), "source": src, "note": note}

    def export_exclusions(self, run_id: str) -> dict:
        self._guard_idle()
        state, _, prov = self._load(run_id)
        df = tui._read_csv_safe(state.run_dir / "included_final.csv")
        if df.empty or "ft_decision" not in df.columns:
            raise ServiceError("No full-text decisions recorded yet.")
        exc = df[df["ft_decision"].astype(str).str.strip().str.lower().eq("exclude")]
        if exc.empty:
            raise ServiceError("No full-text exclusions recorded.")
        exc.to_csv(state.run_dir / "excluded_full_text.csv", index=False, encoding="utf-8")
        (state.run_dir / "excluded_full_text.bib").write_text(
            srp_export.to_bibtex(exc.to_dict("records")), encoding="utf-8")
        missing = int(_blank_mask(exc["ft_reason"]).sum()) if "ft_reason" in exc.columns else len(exc)
        prov.log("export_ft_exclusions", n_excluded=len(exc), n_missing_reason=missing)
        return {"n_excluded": len(exc), "n_missing_reason": missing}

    def write_provenance(self, run_id: str) -> dict:
        self._guard_idle()
        state, cfg, prov = self._load(run_id)
        counts = tui._compute_prisma_counts(state, cfg)
        out = state.run_dir / "PROVENANCE.md"
        prov.render_markdown(out, config=cfg.to_dict(), prisma=tui._prisma_report_rows(counts))
        return {"path": str(out), "warnings": prisma_residuals(counts)}

    def write_methods(self, run_id: str) -> dict:
        self._guard_idle()
        state, cfg, prov = self._load(run_id)
        phases = tui._load_search_strategy_rows(state, cfg)
        counts = tui._compute_prisma_counts(state, cfg)
        paragraph = render_search_methods(cfg.to_dict(), phases, counts)
        table = render_search_strategy_table(phases)
        reasons = counts.get("ft_reasons") or {}
        reasons_md = ("\n".join(["| Reason | Count |", "|---|---|"] +
                                [f"| {k} | {v} |" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])])
                      if reasons else "_(no full-text exclusions recorded yet)_")
        out = state.run_dir / "search_methods.md"
        out.write_text(
            "# Search methods (auto-drafted -- read before submitting)\n\n"
            "This paragraph and the tables below are drafted directly from this run's "
            "recorded search strategy and screening counts. Review it: it states what "
            "the pipeline recorded, not necessarily everything your methods section "
            "should say.\n\n## Methods paragraph\n\n" + paragraph + "\n\n"
            "## Table S1: per-source search strategy\n\n" + table + "\n\n"
            "## Table S2: full-text exclusion reasons\n\n" + reasons_md + "\n", encoding="utf-8")
        prov.log("methods_report_drafted", n_phases=len(phases))
        return {"path": str(out), "paragraph": paragraph}

    def write_self_appraisal(self, run_id: str, instrument: str | None = None) -> dict:
        self._guard_idle()
        state, cfg, prov = self._load(run_id)
        key = instrument or cfg.review_level_instrument
        inst = INSTRUMENTS.get(key)
        if inst is None or inst.level != REVIEW_SELF_CHECK:
            raise ServiceError("Pick a review-level instrument (AMSTAR 2, ROBIS, DARE or MECCIR).")
        out = state.run_dir / f"review_self_appraisal_{key}.md"
        out.write_text(render_review_self_appraisal(key), encoding="utf-8")
        prov.log("review_self_appraisal_drafted", instrument=key)
        return {"path": str(out), "instrument": inst.name}

    def kappa(self, run_id: str, path_a: str, path_b: str, stage: str) -> dict:
        state, _, prov = self._load(run_id)
        if stage not in ("ta_decision", "ft_decision"):
            raise ServiceError("Stage must be ta_decision or ft_decision.")
        df_a, df_b = tui._read_csv_safe(path_a), tui._read_csv_safe(path_b)
        if df_a.empty or df_b.empty:
            raise ServiceError("Could not read one or both sheets (empty or missing).")
        if stage not in df_a.columns or stage not in df_b.columns:
            raise ServiceError(f"Both sheets need a '{stage}' column.")
        rows_a = {tui._id_to_str(r.get("id", "")): r.to_dict() for _, r in df_a.iterrows()} if "id" in df_a.columns else {}
        rows_b = {tui._id_to_str(r.get("id", "")): r.to_dict() for _, r in df_b.iterrows()} if "id" in df_b.columns else {}
        res = compare_reviewers(rows_a, rows_b, stage_col=stage)
        conflicts_path = ""
        if res.conflicts:
            out = state.run_dir / f"conflicts_{stage}.csv"
            pd.DataFrame(res.conflicts).to_csv(out, index=False, encoding="utf-8")
            conflicts_path = str(out)
        prov.log("inter_rater_agreement", stage=stage, n_compared=res.n_compared, n_agreed=res.n_agreed,
                 kappa=res.kappa, percent_agreement=res.percent_agreement,
                 n_conflicts=len(res.conflicts), sheet_a=str(path_a), sheet_b=str(path_b), note=res.note)
        return {
            "summary": res.summary(), "kappa": res.kappa, "note": res.note,
            "n_compared": res.n_compared, "n_agreed": res.n_agreed,
            "percent": res.percent_agreement, "labels": list(res.labels),
            "matrix": {a: {b: res.matrix.get(a, {}).get(b, 0) for b in res.labels} for a in res.labels},
            "conflicts": [{k: _clean(v) for k, v in c.items()} for c in res.conflicts[:200]],
            "conflicts_path": conflicts_path,
        }

    def diagnose(self, run_id: str) -> dict:
        state, _, prov = self._load(run_id)
        phases = sorted({int(k.split(":", 1)[0]) for k in state.state.get("stages", {})})
        out, mismatches = [], []
        for p in phases:
            pdir = state.run_dir / f"phase_{p}"
            rows, zero = [], False
            for key, filename, count_key, label in tui._PHASE_FUNNEL_STAGES:
                status = state.stage_status(p, key)
                count = state.state.get("stages", {}).get(f"{p}:{key}", {}).get("counts", {}).get(count_key)
                exists = filename is None or (pdir / filename).exists()
                if status == "done" and filename and not exists:
                    mismatches.append(f"Phase {p}: '{label}' is marked done but {pdir / filename} is missing.")
                first_zero = count == 0 and not zero
                zero = zero or first_zero
                rows.append({"label": label, "status": status or "not run", "count": count,
                             "file": filename or "", "exists": exists, "first_zero": first_zero})
            out.append({"phase": p, "rows": rows})
        prov.log("diagnose_run", phases=phases, file_mismatches=len(mismatches))
        return {"phases": out, "mismatches": mismatches}

    def provenance_events(self, run_id: str, limit: int = 400) -> list[dict]:
        _, _, prov = self._load(run_id)
        events = prov.events()[-limit:]
        return [{k: _clean(v) if not isinstance(v, (list, dict)) else v for k, v in e.items()}
                for e in reversed(events)]

    def outputs(self, run_id: str) -> dict:
        state, _, _ = self._load(run_id)
        rd = state.run_dir
        files = []
        for name, desc in OUTPUT_FILES:
            p = rd / name
            files.append({"name": name, "desc": desc, "path": str(p), "exists": p.exists(),
                          "size": p.stat().st_size if p.exists() else 0})
        for p in sorted(rd.glob("review_self_appraisal_*.md")) + sorted(rd.glob("conflicts_*.csv")):
            files.append({"name": p.name, "desc": "", "path": str(p), "exists": True, "size": p.stat().st_size})
        n_pdfs = len(list((rd / "pdfs").glob("*.pdf"))) if (rd / "pdfs").exists() else 0
        return {"run_dir": str(rd), "files": files, "n_pdfs": n_pdfs,
                "pdf_dir": str(rd / "pdfs"), "fig_dir": str(rd / "figures")}

    def read_text(self, run_id: str, name: str) -> str:
        """Preview a text output from the run folder (never outside it)."""
        state, _, _ = self._load(run_id)
        p = (state.run_dir / name).resolve()
        if state.run_dir.resolve() not in p.parents or not p.is_file():
            raise ServiceError("That file is not part of this review.")
        if p.stat().st_size > 2_000_000:
            raise ServiceError("File too large to preview; open it instead.")
        return p.read_text(encoding="utf-8", errors="replace")

    # --- API keys (.env in the workspace) ----------------------------------
    def _keys_present(self) -> dict:
        values = parse_env_text(self.env_path.read_text(encoding="utf-8")) if self.env_path.is_file() else {}
        return {var: bool((values.get(var) or os.environ.get(var) or "").strip())
                for _, var in MANAGED_API_KEYS}

    def api_keys(self) -> dict:
        present = self._keys_present()
        return {"env_path": str(self.env_path),
                "keys": [{"label": label, "var": var, "set": present[var], "help": KEY_HELP.get(var)}
                         for label, var in MANAGED_API_KEYS]}

    def set_api_key(self, var: str, value: str) -> dict:
        if var not in {v for _, v in MANAGED_API_KEYS}:
            raise ServiceError(f"Unknown key {var}.")
        value = (value or "").strip()
        if not value:
            raise ServiceError("Enter a value, or use Remove.")
        set_env_var(self.env_path, var, value)
        os.environ[var] = value
        return self.api_keys()

    def delete_api_key(self, var: str) -> dict:
        if var not in {v for _, v in MANAGED_API_KEYS}:
            raise ServiceError(f"Unknown key {var}.")
        unset_env_var(self.env_path, var)
        os.environ.pop(var, None)
        return self.api_keys()

    # --- delete a run (with the user's confirmation in the UI) -------------
    def delete_run(self, run_id: str) -> dict:
        self._guard_idle()
        run_dir = (self.runs_dir / run_id).resolve()
        if self.runs_dir.resolve() not in run_dir.parents or not (run_dir / "state.json").exists():
            raise ServiceError("Not a review folder.")
        shutil.rmtree(run_dir)
        self._opened.discard(run_id)
        return {"deleted": run_id}

    def export_settings_json(self) -> str:
        return json.dumps(self.app_info(), indent=2)
