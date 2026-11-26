/* views-home.js -- sidebar, review list, new-review form, API keys, about. */
"use strict";

const RUN_TABS = [
  ["overview", "Overview"], ["search", "Search"], ["screening", "Screening"], ["gate", "Review gate"],
  ["fulltext", "Full text"], ["extraction", "Extraction"], ["outputs", "Outputs"], ["activity", "Activity"],
];

function renderSidebar() {
  const r = S.route, side = clear($("#sidebar"));
  const item = (label, path, active, dotClass, badge) => h("button.nav-item" + (active ? ".active" : ""),
    { onclick: () => go(path), "aria-current": active ? "page" : null },
    dotClass !== undefined ? h("span.nav-dot" + (dotClass ? "." + dotClass : "")) : null, label,
    badge ? h("span.badge", badge) : null);
  side.append(h("div.brand", h("div.brand-mark", "SLR"),
    h("div", h("div.brand-name", "SLR Pipeline UI"), h("div.faint.small", "v" + (S.info ? S.info.version : "")))));
  side.append(item("All reviews", "/", r.name === "home" || r.name === "new"));
  if (r.name === "run" && S.run) {
    const run = S.run, target = run.progress.target || {};
    side.append(h("div.nav-label", "This review"), h("div.nav-run", { title: run.config.topic }, run.config.topic));
    const undecided = run.phases.reduce((n, p) => n + p.n_undecided, 0);
    const doneTab = {
      search: run.phases.every((p) => p.searched), screening: run.phases.every((p) => p.searched && !p.n_undecided),
      gate: run.phases.every((p) => p.gated), fulltext: run.fulltext_done, extraction: run.has_extraction,
    };
    for (const [tab, label] of RUN_TABS) {
      const dot = tab === "overview" || tab === "outputs" || tab === "activity" ? undefined
        : doneTab[tab] ? "done" : target.tab === tab ? "now" : "";
      side.append(item(label, runPath(run.run_id, tab), r.tab === tab, dot,
        tab === "screening" && undecided ? String(undecided) : null));
    }
  }
  side.append(h("div.sidebar-foot",
    item("API keys", "/keys", r.name === "keys"), item("Settings & about", "/about", r.name === "about")));
}

/* ---- home ---- */
S.views.home = async (el) => {
  const runs = await call("list_runs");
  el.append(h("div.page-head",
    h("div.grow", h("h1", "Your reviews"),
      h("p.muted", "Each review is a folder you own: search results, every screening decision and its reason, and the audit trail.")),
    h("button.btn.primary", { onclick: () => go("/new") }, "New review")));
  if (!runs.length) {
    el.append(h("div.card.empty", h("h2", "No reviews yet"),
      h("p", "Start one to set your question, eligibility criteria and sources. The search runs from there."),
      h("p", { style: "margin-top:16px" }, h("button.btn.primary", { onclick: () => go("/new") }, "Start your first review"))));
  } else {
    const grid = h("div.run-grid");
    for (const run of runs) {
      grid.append(h("button.card.run-card", { onclick: () => go(runPath(run.run_id)) },
        h("h3", run.topic),
        run.error ? h("div.small", { style: "color:var(--exc)" }, "Could not read this review: " + run.error) : [
          h("div.small.muted", run.step), progress(run.progress, 100),
          h("div.row.small.faint", h("span", run.progress + "% · " + run.n_phases + (run.n_phases > 1 ? " phases" : " phase")),
            h("span.right", "Updated " + fmt.date(run.updated)))]));
    }
    el.append(grid);
  }
  el.append(h("p.small.faint", { style: "margin-top:22px" }, "Workspace: ",
    h("a", { onclick: () => openPath(S.info.workspace) }, S.info.workspace)));
};

/* ---- new review ---- */
const WIZ_STEPS = ["Question", "Eligibility", "Reporting", "Appraisal", "Search", "People", "Confirm"];
let wiz = null;
function newWizardState(opts) {
  const d = opts.defaults;
  return {
    step: 0, opts,
    topic: "", blocks: [[""], [""]], inclusion_criteria: "", exclusion_criteria: "", registration_id: "",
    language_restriction: "", grey_literature_included: false, grey_literature_justification: "",
    will_meta_analyze: false, reporting_bias_assessment: "", funding_statement: "", competing_interests: "",
    research_field: opts.fields[0].key, use_recommended: true, primary: [], mmat_designs: [], use_certainty: true,
    year_from: d.year_from, year_to: d.year_to, mailto: "", max_per_source: d.max_per_source, n_phases: d.n_phases,
    title_threshold: d.title_threshold,
    sources: opts.sources.filter((s) => opts.keyless_sources.includes(s) || opts.keys_present[opts.keyed_sources[s]]),
    assist_tool_name: "", reviewer: "", query: "",
  };
}
function wizBlocks() { return wiz.blocks.map((b) => b.join(",").split(",").map((t) => t.trim()).filter(Boolean)).filter((b) => b.length); }
function wizForm() {
  const f = wiz.opts.fields.find((x) => x.key === wiz.research_field);
  return {
    topic: wiz.topic, keyword_blocks: wizBlocks(), inclusion_criteria: wiz.inclusion_criteria,
    exclusion_criteria: wiz.exclusion_criteria, registration_id: wiz.registration_id,
    language_restriction: wiz.language_restriction, grey_literature_included: wiz.grey_literature_included,
    grey_literature_justification: wiz.grey_literature_justification, will_meta_analyze: wiz.will_meta_analyze,
    reporting_bias_assessment: wiz.reporting_bias_assessment, funding_statement: wiz.funding_statement,
    competing_interests: wiz.competing_interests, research_field: wiz.research_field,
    primary_study_instruments: wiz.use_recommended ? f.primary.map((p) => p.key) : wiz.primary,
    mmat_designs: wiz.mmat_designs, use_certainty: wiz.use_certainty, year_from: wiz.year_from, year_to: wiz.year_to,
    mailto: wiz.mailto, max_per_source: wiz.max_per_source, n_phases: wiz.n_phases, title_threshold: wiz.title_threshold,
    sources: wiz.sources, assist_tool_name: wiz.assist_tool_name, reviewer: wiz.reviewer,
  };
}
S.views.new = async (el) => {
  if (!wiz) {
    wiz = newWizardState(await call("wizard_options"));
    wiz.step = Math.min(Math.max(parseInt(S.route.arg, 10) || 0, 0), WIZ_STEPS.length - 1);
  }
  const bind = (key, type) => ({ value: wiz[key], oninput: (e) => { wiz[key] = type === "n" ? e.target.value : e.target.value; } });
  const text = (key, ph) => h("input", { type: "text", placeholder: ph || "", ...bind(key) });
  const area = (key, ph, rows) => h("textarea", { placeholder: ph || "", rows: rows || 3, ...bind(key) });
  const num = (key, min, max) => h("input", { type: "number", min, max, ...bind(key, "n") });
  const check = (key, label, rerender) => h("label.check", h("input", { type: "checkbox", checked: wiz[key],
    onchange: (e) => { wiz[key] = e.target.checked; if (rerender) render(); } }), label);

  el.append(h("div.page-head", h("div.grow", h("h1", "New review"),
    h("p.muted", "Fix the protocol before you see any results. Deciding what counts as eligible after seeing the hits is how reviews drift toward the answer their author expected."))));
  const steps = h("div.steps");
  WIZ_STEPS.forEach((name, i) => steps.append(h("button.step" + (i === wiz.step ? ".on" : i < wiz.step ? ".done" : ""),
    { onclick: () => { wiz.step = i; render(); } }, h("b", i < wiz.step ? "✓" : String(i + 1)), name)));
  el.append(steps);
  const card = h("div.card.stack");
  el.append(card);

  if (wiz.step === 0) {
    const preview = h("div.query-preview", "…");
    const refresh = async () => { wiz.query = (await tryCall("preview_query", wizBlocks())) || ""; preview.textContent = wiz.query || "Add at least one term to see the search string."; };
    const blocks = h("div.stack.tight");
    wiz.blocks.forEach((block, i) => blocks.append(h("div.block-row",
      h("span.block-and", i ? "AND" : "CONCEPT"),
      h("input", { type: "text", "aria-label": "Concept " + (i + 1) + " synonyms", value: block.join(", "),
        placeholder: i === 0 ? "e.g. pair programming, collaborative programming" : "e.g. code quality, defect density",
        oninput: (e) => { wiz.blocks[i] = [e.target.value]; refresh(); } }),
      h("button.btn.ghost.sm", { title: "Remove this concept", "aria-label": "Remove concept " + (i + 1), disabled: wiz.blocks.length < 2,
        onclick: () => { wiz.blocks.splice(i, 1); render(); } }, "✕"))));
    card.append(
      field("Review topic", text("topic", "e.g. Effects of pair programming on code quality"),
        "Names the review folder, seeds the search and labels the AI screening prompt."),
      h("div.stack.tight", h("h3", "Search concepts"),
        h("p.hint", "One row per concept (population, intervention, outcome…). Synonyms in a row are joined with OR; rows are joined with AND. Putting every synonym in one AND chain collapses recall."),
        blocks, h("div", h("button.btn.sm", { onclick: () => { wiz.blocks.push([""]); render(); } }, "Add a concept"))),
      h("div.stack.tight", h("h3", "Search string"), preview));
    refresh();
  } else if (wiz.step === 1) {
    card.append(
      field("Inclusion criteria", area("inclusion_criteria", "peer-reviewed or conference paper; empirically compares …; reports a … metric", 4),
        "A study must meet all of these. Separate criteria with ; or new lines."),
      field("Exclusion criteria", area("exclusion_criteria", "not in English; no empirical evaluation; survey or review paper", 4),
        "Exclude a study if any of these applies."),
      !wiz.inclusion_criteria.trim() && !wiz.exclusion_criteria.trim()
        ? h("div.banner.warn", h("div", h("strong", "No criteria yet. "), "Without them, AI-assist screening can only ask “is this plausibly relevant?”, which is not a defensible criterion for a systematic review.")) : null,
      field("Language restriction (optional)", text("language_restriction", "e.g. English only"), "PRISMA item 5."));
  } else if (wiz.step === 2) {
    card.append(
      field("Protocol registration ID (optional)", text("registration_id", "e.g. OSF: 10.17605/OSF.IO/XXXXX"),
        "PRISMA item 24a. Left blank, the review is recorded as not registered."),
      h("div.stack.tight", check("grey_literature_included", "This review includes grey literature (preprints, theses, technical reports)", true),
        wiz.grey_literature_included ? null : field("Why grey literature is excluded (optional)", text("grey_literature_justification"),
          "PRISMA item 14. A default reason is recorded if you leave this blank.")),
      h("div.stack.tight", check("will_meta_analyze", "This review will meta-analyze (pool) quantitative results", true),
        wiz.will_meta_analyze ? field("Planned reporting-bias assessment", text("reporting_bias_assessment", "e.g. funnel plot + Egger's test on the primary outcome"),
          "PRISMA item 21. A note of intent for your protocol; the app does not compute it.")
          : h("p.hint", "A narrative synthesis is recorded, with the note that funnel plots need pooled effect sizes.")),
      h("div.grid2", field("Funding statement (optional)", text("funding_statement"), "PRISMA item 25."),
        field("Competing interests (optional)", text("competing_interests"), "PRISMA item 26.")));
  } else if (wiz.step === 3) {
    const f = wiz.opts.fields.find((x) => x.key === wiz.research_field);
    const chosen = wiz.use_recommended ? f.primary.map((p) => p.key) : wiz.primary;
    card.append(
      field("Research field", h("select", { onchange: (e) => { wiz.research_field = e.target.value; wiz.use_recommended = true; render(); } },
        wiz.opts.fields.map((x) => h("option", { value: x.key, selected: x.key === wiz.research_field }, x.label))),
        "Picks the critical-appraisal instrument PRISMA item 11 asks for. Cochrane RoB 2 means nothing for a software benchmark; choose the closest match."),
      f.primary.length ? h("div.stack.tight",
        h("div", h("strong", "Recommended: "), f.primary.map((p) => p.name).join(", ")),
        check("use_recommended", "Use the recommended instrument(s)", true),
        wiz.use_recommended ? null : h("div.stack.tight", wiz.opts.primary_instruments.map((p) => h("label.check",
          h("input", { type: "checkbox", checked: wiz.primary.includes(p.key), onchange: (e) => {
            wiz.primary = e.target.checked ? [...wiz.primary, p.key] : wiz.primary.filter((k) => k !== p.key); render(); } }), p.name))))
        : null,
      chosen.includes("mmat_screening") ? h("div.stack.tight", h("h3", "Study designs in your review (MMAT)"),
        wiz.opts.mmat_designs.map((d) => h("label.check", h("input", { type: "checkbox", checked: wiz.mmat_designs.includes(d.key),
          onchange: (e) => { wiz.mmat_designs = e.target.checked ? [...wiz.mmat_designs, d.key] : wiz.mmat_designs.filter((k) => k !== d.key); } }), d.name))) : null,
      f.certainty ? check("use_certainty", "Assess certainty of evidence using " + f.certainty.name) : null,
      f.justification ? h("p.hint", f.justification) : null);
  } else if (wiz.step === 4) {
    const src = h("div.row.wrap");
    for (const s of wiz.opts.sources) {
      const env = wiz.opts.keyed_sources[s], hasKey = !env || wiz.opts.keys_present[env];
      src.append(h("button.chip" + (wiz.sources.includes(s) ? ".on" : ""), {
        "aria-pressed": wiz.sources.includes(s), title: env ? (hasKey ? "Key found" : "Needs " + env + " (API keys page); skipped without it") : "No key needed",
        onclick: () => { wiz.sources = wiz.sources.includes(s) ? wiz.sources.filter((x) => x !== s) : [...wiz.sources, s]; render(); },
      }, s, env && !hasKey ? h("span.faint", "needs key") : null));
    }
    card.append(
      h("div.grid2", field("Year from", num("year_from", 1900, 2100)), field("Year to", num("year_to", 1900, 2100))),
      field("Contact email", h("input", { type: "email", placeholder: "you@university.edu", ...bind("mailto") }),
        "A real address. OpenAlex and Crossref use it for faster access; Unpaywall needs it to find open-access PDFs."),
      h("div.stack.tight", h("h3", "Sources"), src,
        h("p.hint", "OpenAlex, Semantic Scholar, Crossref, arXiv, PubMed and DOAJ need no key. A keyed source without its key is skipped, never an error.")),
      h("div.grid3",
        field("Max results per source", num("max_per_source", 1), "Keep it small (40–60) while testing."),
        field("Search phases", num("n_phases", 1), "More than 1 adds query expansion between rounds."),
        field("Title-dedup threshold", num("title_threshold", 0, 100), "0–100. 92 is a safe default.")));
  } else if (wiz.step === 5) {
    card.append(
      field("Reviewer name or initials (optional)", text("reviewer", "e.g. FN"), "Stamped on your decisions and the audit trail."),
      field("AI chatbot you will paste prompts into (optional)", text("assist_tool_name", "e.g. Claude, ChatGPT, Gemini"),
        "Any web chatbot works. Recorded only as your AI-assistance disclosure. Leave blank to screen fully by hand."));
  } else {
    const f = wiz.form = wizForm();
    const field_ = wiz.opts.fields.find((x) => x.key === f.research_field);
    const dl = h("dl.kv");
    const row = (k, v) => dl.append(h("dt", k), h("dd", v || "–"));
    row("Topic", f.topic); row("Search string", h("span.mono", wiz.query || "(no terms)"));
    row("Years", f.year_from + "–" + f.year_to); row("Sources", f.sources.join(", "));
    row("Max per source", String(f.max_per_source)); row("Phases", String(f.n_phases));
    row("Inclusion", f.inclusion_criteria); row("Exclusion", f.exclusion_criteria);
    row("Field", field_.label); row("Grey literature", f.grey_literature_included ? "included" : "excluded");
    row("Reviewer", f.reviewer); row("AI-assist tool", f.assist_tool_name || "none (manual screening)");
    card.append(h("h2", "Check the protocol"), dl,
      h("p.hint", "Keywords and criteria are fixed once the review is created. Email, years, sources and the per-source cap can be changed later."));
  }

  const last = wiz.step === WIZ_STEPS.length - 1;
  el.append(h("div.wizard-foot",
    h("button.btn.ghost", { onclick: () => { wiz = null; go("/"); } }, "Cancel"),
    h("span.grow"),
    wiz.step ? h("button.btn", { onclick: () => { wiz.step--; render(); } }, "Back") : null,
    h("button.btn.primary", { onclick: async () => {
      if (!last) { wiz.step++; render(); return; }
      const out = await tryCall("create_run", wiz.form);
      if (out) { wiz = null; toast("Review created."); go(runPath(out.run_id)); }
    } }, last ? "Create review" : "Next")));
};

/* ---- API keys ---- */
S.views.keys = async (el) => {
  const data = await call("api_keys");
  el.append(h("div.page-head", h("div.grow", h("h1", "API keys"),
    h("p.muted", "Every key is optional. A key raises a rate limit or unlocks a source. Keys are stored only in your workspace's .env file and are never written into a review folder, so a review stays safe to share."))));
  const rows = data.keys.map((k) => {
    const input = h("input", { type: "password", placeholder: k.set ? "Replace key…" : "Paste key…", "aria-label": k.label + " key", autocomplete: "off" });
    return h("tr", h("td", h("strong", k.label), h("div.mono.faint", k.var)),
      h("td", h("span.badge" + (k.set ? ".inc" : ""), k.set ? "Set" : "Not set")),
      h("td", h("div.row", input,
        h("button.btn.sm", { onclick: async () => { if (await tryCall("set_api_key", k.var, input.value)) { toast(k.label + " key saved."); render(); } } }, "Save"),
        k.set ? h("button.btn.sm.ghost.danger", { onclick: async () => {
          if (await confirmBox("Remove key", "Remove the " + k.label + " key from " + data.env_path + "?", "Remove", true)) { await tryCall("delete_api_key", k.var); render(); } } }, "Remove") : null)));
  });
  el.append(h("div.table-wrap", h("table", h("thead", h("tr", h("th", "Source"), h("th", "Status"), h("th", "Key"))), h("tbody", rows))),
    h("p.small.faint", { style: "margin-top:12px" }, "Stored in " + data.env_path));
};

/* ---- settings & about ---- */
S.views.about = async (el) => {
  const info = S.info;
  const upd = h("span.muted", "");
  el.append(h("div.page-head", h("div.grow", h("h1", "Settings & about"))));
  el.append(h("div.stack",
    h("div.card.stack", h("h2", "Workspace"),
      h("p.muted", "The folder that holds your reviews (runs/) and your API keys (.env)."),
      h("div.row.wrap", h("span.mono", info.workspace),
        h("button.btn.sm", { onclick: () => openPath(info.workspace) }, "Open folder"),
        h("button.btn.sm", { onclick: async () => { const out = await tryCall("choose_workspace"); if (out) { S.info = { ...S.info, ...out }; toast("Workspace changed."); go("/"); } } }, "Change…"))),
    h("div.card.stack", h("h2", "Appearance"),
      seg([["system", "Match system"], ["light", "Light"], ["dark", "Dark"]], info.theme || "system", async (t) => {
        S.info.theme = t; applyTheme(t); await tryCall("set_theme", t); render(); })),
    h("div.card.stack", h("h2", "About"),
      h("dl.kv", h("dt", "App"), h("dd", info.app + " " + info.version), h("dt", "Python"), h("dd", info.python),
        h("dt", "Built on"), h("dd", "slr-pipeline " + (info.upstream_version || "") + " by Md Nayeem Hossain – ",
          h("a", { onclick: () => tryCall("open_url", "https://github.com/nayeem-hossain/slr-pipeline") }, "github.com/nayeem-hossain/slr-pipeline")),
        h("dt", "This app"), h("dd", h("a", { onclick: () => tryCall("open_url", "https://github.com/faizunnur/SLR_Pipeline_UI") }, "github.com/faizunnur/SLR_Pipeline_UI")),
        h("dt", "License"), h("dd", "MIT (code), CC BY 4.0 (documentation). The original pipeline's copyright notice is kept.")),
      h("div.row", h("button.btn.sm", { onclick: async () => {
        upd.textContent = "Checking…";
        const r = await tryCall("check_update");
        upd.textContent = !r ? "" : r.outcome === "outdated" ? "Version " + r.latest + " is available. Update with: pip install -U slr-pipeline-ui"
          : r.outcome === "up_to_date" ? "You are up to date." : r.outcome === "dev_build" ? "Running from source; version check does not apply."
          : "Could not check right now (offline or GitHub unreachable).";
      } }, "Check for updates"), upd))));
};
