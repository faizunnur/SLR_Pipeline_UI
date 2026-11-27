/* views-run.js -- one review: overview, search, review gate + query expansion, activity. */
"use strict";

function runHead(title, sub, ...actions) {
  return h("div.page-head", h("div.grow", h("h1", title), sub ? h("p.muted", sub) : null), actions);
}
/* phase picker shared by the per-phase tabs; returns the selected phase object */
function phasePicker(el, tab, extra) {
  const run = S.run;
  let phase = parseInt(S.route.arg, 10);
  if (S.route.arg === "snowball" && extra) phase = "snowball";
  else if (!phase || phase > run.phases.length) {
    const t = run.progress.target || {};
    phase = t.phase || Math.min(run.current_phase, run.phases.length) || 1;
  }
  if (run.phases.length > 1 || extra) {
    const opts = run.phases.map((p) => [p.phase, "Phase " + p.phase]);
    if (extra) opts.push(["snowball", "Snowball"]);
    el.append(h("div", { style: "margin-bottom:14px" }, seg(opts, phase, (p) => go(runPath(run.run_id, tab, String(p))))));
  }
  return phase === "snowball" ? "snowball" : run.phases[phase - 1];
}
function lockedNote(p) {
  return h("div.card.empty", h("h2", "Phase " + p.phase + " is not open yet"),
    h("p", "It opens when phase " + (p.phase - 1) + "'s review gate and query expansion are done."),
    h("p", { style: "margin-top:14px" }, h("button.btn", { onclick: () => go(runPath(S.run.run_id, "gate", String(p.phase - 1))) }, "Go to phase " + (p.phase - 1) + " gate")));
}

/* ---- overview ---- */
S.views["run:overview"] = async (el) => {
  const run = S.run, c = run.config, t = run.progress.target || {};
  el.append(runHead(c.topic, "Created " + fmt.date(run.created) + (c.reviewer ? " · Reviewer " + c.reviewer : ""),
    h("button.btn", { onclick: () => openPath(run.run_dir) }, "Open folder")));

  el.append(h("div.nextstep",
    h("div.grow", h("div.label", "Next step"), h("strong", run.progress.label)),
    h("span.pct", run.progress.percent + "% done"),
    h("button.btn", { onclick: () => go(runPath(run.run_id, t.tab, t.phase ? String(t.phase) : "")) }, "Continue")));

  if (run.missing_keys.length) {
    el.append(h("div.banner.warn", { style: "margin-bottom:16px" }, h("div.grow", h("strong", "No API key for: " + run.missing_keys.join(", ") + ". "),
      "Those sources will be skipped."), h("button.btn.sm", { onclick: () => go("/keys") }, "Add keys")));
  }

  const pipe = h("div.pipeline");
  for (const p of run.phases) {
    const st = p.stages, cnt = (s, k) => (st[s] && st[s].counts ? st[s].counts[k] : undefined);
    const now = (tab) => t.tab === tab && t.phase === p.phase;
    const step = (label, value, done, tab, sub) => h("button.pstep" + (done ? ".done" : "") + (now(tab) ? ".now" : "") + (p.unlocked ? "" : ".locked"),
      { disabled: !p.unlocked, onclick: () => go(runPath(run.run_id, tab, String(p.phase))) },
      h("div.t", label, done ? " ✓" : ""), h("div.v", value), sub ? h("div.small.muted", sub) : null);
    pipe.append(h("div.phase-line", h("div.phase-name", "Phase " + p.phase),
      step("Search", p.searched ? fmt.n(cnt("search", "n_hits")) + " hits" : "Not run", p.searched, "search",
        p.searched ? fmt.n(cnt("dedup", "n_dupes")) + " duplicates removed" : null),
      step("Screening", p.searched ? fmt.n(p.n_rows - p.n_undecided) + " / " + fmt.n(p.n_rows) : "–", p.searched && !p.n_undecided, "screening",
        p.searched ? (p.n_undecided ? p.n_undecided + " to decide" : "all decided") : null),
      step("Review gate", p.gated ? fmt.n(cnt("review_gate", "n_included")) + " proceed" : "–", p.gated, "gate",
        p.gated ? fmt.n(cnt("review_gate", "n_overridden")) + " overridden" : null),
      p.is_last ? h("div.pstep.locked", h("div.t", "Query expansion"), h("div.v", "–"), h("div.small.muted", "last phase"))
        : step("Query expansion", p.expanded ? "+" + fmt.n(cnt("snowball", "n_added")) + " terms" : "–", p.expanded, "gate")));
  }
  el.append(h("div.card", { style: "margin-bottom:16px" }, h("div.card-head", h("h2", "Search phases")), pipe));

  const pr = run.prisma;
  const fbox = (label, n, cls) => h("div.fbox" + (cls ? "." + cls : ""), h("span", label), h("b", "n = " + fmt.n(n)));
  const flowRow = (label, n, outLabel, outN) => [fbox(label, n), h("i.fright"), fbox(outLabel, outN, "out"), h("i.fdown")];
  const undecided = pr.undecided_ta + pr.undecided_ft;
  el.append(h("div.card", { style: "margin-bottom:16px" },
    h("div.card-head", h("h2.grow", "PRISMA flow so far"),
      h("button.btn.sm", { onclick: () => go(runPath(run.run_id, "outputs")) }, "Figures and reports")),
    h("div.prisma", { role: "img", "aria-label": "PRISMA flow: " + pr.identified + " identified, " + pr.screened + " screened, " + pr.assessed_ft + " assessed at full text, " + pr.included + " included" },
      flowRow("Records identified", pr.identified, "Duplicates removed", pr.duplicates_removed),
      flowRow("Records screened", pr.screened, "Excluded at title and abstract", pr.excluded_ta),
      flowRow("Assessed at full text", pr.assessed_ft, "Excluded at full text", pr.excluded_ft),
      fbox("Studies included", pr.included, "end")),
    undecided ? h("div.fnote", undecided + " record(s) still have no decision, so the boxes will not add up until they are screened.") : null,
    run.prisma_warnings.filter((w) => !/no title\/abstract decision yet/.test(w)).map((w) => h("div.fnote", w))));

  const dl = h("dl.kv");
  const row = (k, v) => dl.append(h("dt", k), h("dd", v || "–"));
  row("Search string", h("span.mono", c.search_query)); row("Years", c.year_from + "–" + c.year_to);
  row("Sources", c.sources.join(", ")); row("Inclusion criteria", c.inclusion_criteria); row("Exclusion criteria", c.exclusion_criteria);
  row("Field", c.field_label); row("Appraisal", c.instrument_names.join(", ")); row("Registration", c.registration_id || "not registered");
  row("AI-assist tool", c.assist_tool_name || "none");
  el.append(h("div.card", h("div.card-head", h("h2.grow", "Protocol"),
    h("button.btn.sm", { onclick: () => editSettings() }, "Edit search settings")), dl));
};

async function editSettings() {
  const run = S.run, c = run.config, opts = await tryCall("wizard_options");
  if (!opts) return;
  const v = { mailto: c.mailto, year_from: c.year_from, year_to: c.year_to, max_per_source: c.max_per_source,
    sources: [...c.sources], reviewer: c.reviewer, assist_tool_name: c.assist_tool_name };
  const inp = (key, type) => h("input", { type: type || "text", value: v[key], oninput: (e) => { v[key] = e.target.value; } });
  const chips = h("div.row.wrap");
  const drawChips = () => { clear(chips); for (const s of opts.sources) chips.append(h("button.chip" + (v.sources.includes(s) ? ".on" : ""),
    { "aria-pressed": v.sources.includes(s), onclick: () => { v.sources = v.sources.includes(s) ? v.sources.filter((x) => x !== s) : [...v.sources, s]; drawChips(); } }, s)); };
  drawChips();
  modal({ title: "Search settings", sub: "Applies to searches you run from now on. Keywords and criteria are part of the protocol and stay fixed.",
    body: h("div.stack", field("Contact email", inp("mailto", "email")),
      h("div.grid3", field("Year from", inp("year_from", "number")), field("Year to", inp("year_to", "number")), field("Max per source", inp("max_per_source", "number"))),
      h("div.stack.tight", h("strong", "Sources"), chips),
      h("div.grid2", field("Reviewer", inp("reviewer")), field("AI-assist tool", inp("assist_tool_name")))),
    actions: [{ label: "Cancel" }, { label: "Save", primary: true, onclick: async () => {
      const out = await tryCall("update_search_settings", run.run_id, v);
      if (!out) return false;
      toast(out.changed.length ? "Saved: " + out.changed.join(", ") : "Nothing changed."); render();
    } }] });
}

/* ---- import records exported from a database website ----
   Step 1: pick the file and see what is in it. Step 2: say where it came from
   (PRISMA items 6 and 7 ask for the database, the date and the search used),
   then add it. The records join the phase like search results: duplicates are
   removed and decisions already made are kept. */
const IMPORT_SOURCES = ["Scopus", "Web of Science", "IEEE Xplore", "PubMed", "Embase", "ACM Digital Library", "ScienceDirect", "EBSCOhost", "ProQuest", "Google Scholar"];
function importRecords(run, p) {
  const v = { path: "", info: null, source: "", query: "", searched_on: new Date().toISOString().slice(0, 10) };
  const body = h("div.stack");
  const pathInput = h("input", { type: "text", placeholder: "Path of the exported file", "aria-label": "File to import", oninput: (e) => { v.path = e.target.value; } });
  const read = async () => {
    if (!v.path.trim()) { toast("Choose the file first.", true); return; }
    const info = await tryCall("preview_import", v.path);
    if (!info) return;
    v.info = info; v.path = info.path;
    if (!v.source) v.source = info.source_guess || "";
    draw();
  };
  const draw = () => {
    clear(body);
    pathInput.value = v.path;
    body.append(
      h("div.stack.tight",
        h("strong", "1. The file you exported"),
        h("p.small.muted", "In Scopus, Web of Science, IEEE Xplore or your library's database: run your search, select all results, choose Export, and pick RIS, BibTeX or CSV with abstracts included. PubMed's “Citation manager” file works too."),
        h("div.row", pathInput,
          h("button.btn", { onclick: async () => { const picked = await tryCall("choose_import_file"); if (picked) { v.path = picked; v.info = null; await read(); } } }, "Browse…"),
          h("button.btn", { onclick: read }, "Read file"))));
    if (!v.info) return;
    const i = v.info, pct = (n) => Math.round((100 * n) / i.n_records) + "%";
    body.append(
      h("div.banner" + (i.n_abstract < i.n_records / 2 ? ".warn" : ".good"), h("div",
        h("strong", fmt.n(i.n_records) + " record(s) found in " + i.name + " (" + i.format + "). "),
        pct(i.n_abstract) + " have an abstract, " + pct(i.n_doi) + " a DOI, " + pct(i.n_year) + " a year." +
        (i.n_no_title ? " " + i.n_no_title + " entr" + (i.n_no_title > 1 ? "ies" : "y") + " without a title will be left out." : ""),
        i.n_abstract < i.n_records / 2 ? h("div.small", "Most records have no abstract, so they can only be screened on the title. If the database offers it, export again with abstracts included.") : null)),
      h("ol.import-sample", i.sample.map((t) => h("li", t))),
      h("div.stack.tight", h("strong", "2. Where these records came from"),
        h("div.grid2",
          field("Database", h("input", { type: "text", value: v.source, list: "import-sources", placeholder: "For example Scopus", oninput: (e) => { v.source = e.target.value; } }),
            "Reported in your PRISMA flow and methods."),
          field("Date you searched it", h("input", { type: "text", value: v.searched_on, placeholder: "YYYY-MM-DD", oninput: (e) => { v.searched_on = e.target.value; } }),
            "Reported as the date last searched.")),
        h("datalist", { id: "import-sources" }, IMPORT_SOURCES.map((sname) => h("option", { value: sname }))),
        field("Search string you used there", h("textarea", { rows: 3, value: v.query, placeholder: "Paste the exact search from the database, for example TITLE-ABS-KEY(\"intrusion detection\" AND \"machine learning\")", oninput: (e) => { v.query = e.target.value; } }),
          "Optional now, but PRISMA item 7 asks you to report it. It goes into the search-strategy table.")));
  };
  draw();
  modal({ title: "Import records into phase " + p.phase, wide: true, body,
    sub: "Adds records from a file to this phase. Duplicates of records you already have are removed, and decisions you have made are kept.",
    actions: [{ label: "Cancel" }, { label: "Import", primary: true, onclick: async () => {
      if (!v.info) { await read(); return false; }
      if (!v.source.trim()) { toast("Say which database the file came from.", true); return false; }
      startJob("start_import", [run.run_id, p.phase, v.path, v.source, v.query, v.searched_on], (job) => {
        const r = job.result || {};
        if (job.status === "done") toast(fmt.n(r.n_records) + " imported: " + fmt.n(r.n_new) + " new to screen, " + fmt.n(r.n_duplicates) + " already in the review.");
      });
    } }] });
}

/* ---- search ---- */
S.views["run:search"] = async (el) => {
  const run = S.run;
  el.append(runHead("Search", "Queries every selected source, removes duplicates, and builds the screening sheet. Records exported from a database website can be added from a file.",
    h("button.btn", { onclick: () => editSettings() }, "Search settings")));
  const p = phasePicker(el, "search");
  if (!p.unlocked) { el.append(lockedNote(p)); return; }
  const st = p.stages, cnt = (s, k) => (st[s] && st[s].counts ? st[s].counts[k] : undefined);
  const decided = p.n_rows - p.n_undecided;

  const launch = async (rerun) => {
    if (rerun) {
      const msg = "This overwrites phase " + p.phase + "'s search results, duplicates and screening sheet." +
        (cnt("search", "n_imported") ? " Records you imported from files are kept." : "") +
        (decided ? " " + decided + " recorded title/abstract decision(s) for this phase will be LOST." : "");
      if (!(await confirmBox("Re-run the search?", msg, "Re-run search", true))) return;
    }
    startJob("start_search", [run.run_id, p.phase, rerun]);
  };
  el.append(h("div.card.stack", { style: "margin-bottom:16px" },
    h("div", h("div.small.muted", "Search string for phase " + p.phase), h("div.query-preview", p.query || "(no keywords set)")),
    h("div.row.wrap",
      h("span.chip", run.config.year_from + "–" + run.config.year_to), h("span.chip", "max " + run.config.max_per_source + " per source"),
      run.config.sources.map((s) => h("span.chip" , s)),
      h("span.grow"),
      h("button.btn", { disabled: jobRunning(), onclick: () => importRecords(run, p) }, "Import from a file"),
      p.searched ? h("button.btn", { disabled: jobRunning(), onclick: () => launch(true) }, cnt("search", "imported_only") ? "Search the online sources too" : "Re-run search")
        : h("button.btn.primary", { disabled: jobRunning(), onclick: () => launch(false) }, st.search ? "Continue (dedup + sheet)" : "Run search"))));

  if (!st.search) {
    el.append(h("div.card.empty", h("h2", "Not searched yet"),
      h("p", "Run the search to fetch records for this phase. Progress appears in the log at the bottom."),
      h("p.small.muted", { style: "margin-top:10px;max-width:60ch;margin-left:auto;margin-right:auto" },
        "No API key for Scopus, Web of Science or IEEE Xplore? Search there in your browser, export the results as a file, and use “Import from a file”.")));
    return;
  }
  el.append(h("div.grid4", { style: "margin-bottom:16px" },
    h("div.pbox", h("span", "Records found"), h("b", fmt.n(cnt("search", "n_hits")))),
    h("div.pbox.side", h("span", "Duplicates"), h("b", fmt.n(cnt("dedup", "n_dupes")))),
    h("div.pbox", h("span", "Unique records"), h("b", fmt.n(cnt("dedup", "n_out")))),
    h("div.pbox", h("span", "On screening sheet"), h("b", fmt.n(cnt("prescreen", "n_rows"))))));
  if (cnt("search", "n_hits") === 0) {
    el.append(h("div.banner.warn", { style: "margin-bottom:16px" }, h("div", h("strong", "The search returned nothing. "),
      "Check the per-source status below, then widen the years or sources in Search settings and re-run.")));
  }
  const rows = await call("search_strategy", run.run_id, p.phase);
  if (rows.length) {
    el.append(h("h2", { style: "margin-bottom:8px" }, "Per-source search record"), h("div.table-wrap", h("table",
      h("thead", h("tr", h("th", "Source"), h("th", "Status"), h("th.n", "Retrieved"), h("th.n", "Available"), h("th", "Query sent"))),
      h("tbody", rows.map((r) => h("tr",
        h("td", h("strong", r.source)),
        h("td", h("span.badge" + (r.status === "ok" ? ".inc" : r.status === "imported" ? ".acc" : /^skipped/.test(r.status) ? "" : ".exc"), String(r.status || "ok").replace(/_/g, " ")),
          String(r.truncated).toLowerCase() === "true" ? h("span.badge.may", { style: "margin-left:6px", title: "Capped by max per source: this is a top-ranked sample" }, "truncated") : null,
          r.detail ? h("div.small.muted", String(r.detail).slice(0, 160)) : null),
        h("td.n", fmt.n(r.n_retrieved)), h("td.n", fmt.n(r.total_available)),
        h("td.mono", { style: "max-width:420px;overflow-wrap:anywhere" }, r.query_sent)))))),
      h("p.small.faint", { style: "margin-top:8px" }, "Saved as search_strategy.csv in the phase folder and reused for the methods report."));
  }
  if (p.searched) el.append(h("p", { style: "margin-top:16px" },
    h("button.btn.primary", { onclick: () => go(runPath(run.run_id, "screening", String(p.phase))) }, "Go to screening")));
};

/* ---- review gate + query expansion ---- */
const gateState = {};
S.views["run:gate"] = async (el) => {
  const run = S.run;
  el.append(runHead("Review gate", "Your decision gate for this phase. Flip anything you disagree with, in either direction, then confirm."));
  const p = phasePicker(el, "gate");
  if (!p.unlocked) { el.append(lockedNote(p)); return; }
  const key = run.run_id + ":" + p.phase;
  const gs = gateState[key] || (gateState[key] = { toExclude: new Set(), toInclude: new Set(), show: "sample" });
  const data = await call("get_gate", run.run_id, p.phase);
  if (data.empty) {
    el.append(h("div.card.empty", h("h2", "Nothing to review yet"), h("p", "Run this phase's search and screen its records first."))); return;
  }
  if (data.n_undecided) {
    el.append(h("div.banner.warn", { style: "margin-bottom:16px" }, h("div.grow", h("strong", data.n_undecided + " record(s) have no decision yet. "),
      "The gate cannot be completed while records are unaccounted for."),
      h("button.btn.sm", { onclick: () => go(runPath(run.run_id, "screening", String(p.phase))) }, "Finish screening")));
  } else if (data.done) {
    el.append(h("div.banner.good", { style: "margin-bottom:16px" }, h("div", h("strong", "Gate confirmed. "),
      data.included.length + " record(s) proceed to full text. You can still flip records and confirm again.")));
  }

  const table = (rows, flipSet, flipLabel, cls) => h("div.table-wrap", h("table",
    h("thead", h("tr", h("th", "Id"), h("th", "Record"), h("th", "Decision"), h("th", ""))),
    h("tbody", rows.length ? rows.map((r) => {
      const flipped = flipSet.has(r.id);
      return h("tr" + (flipped ? ".sel" : ""),
        h("td.num.muted", r.id),
        h("td", h("div", { style: "font-weight:550" }, r.title),
          h("div.small.muted", [fmt.year(r.year), r.venue].filter(Boolean).join(" · ")),
          r.ta_reason ? h("div.small", { style: "margin-top:3px" }, "Reason: " + r.ta_reason) : null),
        h("td", tag(r.ta_decision), r.ai ? h("div.small.faint", "AI suggested") : h("div.small.faint", r.reviewer || ""),
          r.ai && r.ta_decision === "exclude" && r.title_only ? h("div", h("span.badge.may", { title: "This record has no abstract, so the AI judged it on the title alone" }, "title only")) : null),
        h("td", h("button.btn.sm." + cls + (flipped ? ".on" : ""), { "aria-pressed": flipped,
          onclick: () => { flipped ? flipSet.delete(r.id) : flipSet.add(r.id); render(); } }, flipped ? "Will " + flipLabel.toLowerCase() : flipLabel)));
    }) : h("tr", h("td", { colspan: 4, class: "muted" }, "None.")))));

  let shown = data.excluded;
  if (gs.show === "sample") shown = data.excluded.filter((r) => r.in_sample);
  else if (gs.show === "ai") shown = data.excluded.filter((r) => r.ai);
  else if (gs.show === "title") shown = data.excluded.filter((r) => r.ai && r.title_only);
  // What the sample says about the AI exclusions nobody looked at.
  const sampled = data.excluded.filter((r) => r.in_sample), rescued = sampled.filter((r) => gs.toInclude.has(r.id));
  const unchecked = data.n_ai_excluded - sampled.length;
  const estimate = sampled.length ? Math.round((rescued.length / sampled.length) * unchecked) : 0;
  el.append(h("div.stack",
    h("div", h("div.row", { style: "margin-bottom:8px" }, h("h2", "Proceeding to full text"), h("span.badge.inc", String(data.included.length)),
      h("span.small.muted", "include and maybe both proceed")), table(data.included, gs.toExclude, "Exclude", "exc")),
    h("div", h("div.row.wrap", { style: "margin-bottom:8px" }, h("h2", "Excluded"), h("span.badge.exc", String(data.excluded.length)),
      h("span.grow"),
      seg([["sample", "AI sample (" + data.sample_size + ")"], ["ai", "All AI (" + data.n_ai_excluded + ")"]]
        .concat(data.n_ai_title_only ? [["title", "Title only (" + data.n_ai_title_only + ")"]] : []).concat([["all", "All"]]), gs.show, (v) => { gs.show = v; render(); })),
      rescued.length && unchecked > 0 ? h("div.banner.warn", { style: "margin-bottom:8px" }, h("div.grow",
        h("strong", "You rescued " + rescued.length + " of the " + sampled.length + " sampled AI exclusions. "),
        "At that rate about " + Math.max(estimate, 1) + " of the " + unchecked + " AI exclusions you have not looked at are also wrong. Check all of them before you confirm."),
        h("button.btn.sm", { onclick: () => { gs.show = "ai"; render(); } }, "Show all AI exclusions")) : null,
      data.n_ai_title_only && gs.show !== "title" ? h("p.small.muted", { style: "margin-bottom:8px" },
        data.n_ai_title_only + " AI exclusion(s) were made on the title alone because the record has no abstract. They are the ones most worth a second look.") : null,
      data.n_ai_excluded ? h("p.small.muted", { style: "margin-bottom:8px" },
        "A false exclusion is invisible unless you look. The sample is fixed for this phase, so re-opening the gate shows the same records.") : null,
      table(shown, gs.toInclude, "Rescue", "inc"))));

  const nFlip = gs.toExclude.size + gs.toInclude.size;
  el.append(h("div.card.row", { style: "margin-top:16px;position:sticky;bottom:0" },
    h("div.grow", nFlip ? h("span", h("strong", nFlip + " flip(s) pending: "), gs.toExclude.size + " to exclude, " + gs.toInclude.size + " rescued")
      : h("span.muted", "No changes. Confirming accepts the decisions as they are.")),
    nFlip ? h("button.btn.ghost", { onclick: () => { gs.toExclude.clear(); gs.toInclude.clear(); render(); } }, "Clear") : null,
    h("button.btn.primary", { disabled: jobRunning(), onclick: async () => {
      const out = await tryCall("apply_gate", run.run_id, p.phase, [...gs.toExclude], [...gs.toInclude]);
      if (!out) return;
      gs.toExclude.clear(); gs.toInclude.clear();
      toast(out.complete ? "Gate confirmed: " + out.n_included + " record(s) proceed." : "Flips saved. " + out.n_undecided + " record(s) still need a decision.", !out.complete);
      render();
    } }, data.done ? "Confirm again" : "Confirm gate")));

  if (p.gated && !p.is_last) await expansionCard(el, run, p);
  else if (p.gated && p.is_last) el.append(h("p", { style: "margin-top:16px" },
    h("button.btn.primary", { onclick: () => go(runPath(run.run_id, "fulltext")) }, "Go to full-text screening")));
};

async function expansionCard(el, run, p) {
  const data = await call("expansion_terms", run.run_id, p.phase);
  const picked = new Set();
  const custom = h("input", { type: "text", placeholder: "Your own terms, comma-separated (optional)" });
  const chips = h("div.row.wrap");
  const draw = () => { clear(chips); for (const t of data.terms) chips.append(h("button.chip" + (picked.has(t) ? ".on" : ""),
    { "aria-pressed": picked.has(t), onclick: () => { picked.has(t) ? picked.delete(t) : picked.add(t); draw(); } }, t)); };
  draw();
  el.append(h("div.card.stack", { style: "margin-top:16px" },
    h("div", h("h2", p.expanded ? "Query expansion (done)" : "Query expansion for phase " + (p.phase + 1)),
      h("p.muted", "Terms frequent in the titles you kept. This follows words, not references; citation snowballing is a separate action under Outputs.")),
    data.terms.length ? chips : h("p.muted", "No candidate terms found in this phase's included titles."),
    custom,
    h("div.row", h("span.small.muted.grow", "Your base keywords are always kept. Each added term becomes a new AND concept."),
      h("button.btn.primary", { onclick: async () => {
        const extra = custom.value.split(",").map((s) => s.trim()).filter(Boolean);
        if (p.expanded && !(await confirmBox("Change the next query?", "Phase " + (p.phase + 1) + "'s query will be replaced. A search already run for it is not re-run automatically.", "Replace"))) return;
        const out = await tryCall("apply_expansion", run.run_id, p.phase, [...picked, ...extra]);
        if (out) { toast("Phase " + (p.phase + 1) + " is open."); go(runPath(run.run_id, "search", String(p.phase + 1))); }
      } }, p.expanded ? "Update phase " + (p.phase + 1) + " query" : "Open phase " + (p.phase + 1)))));
}

/* ---- activity (audit trail) ---- */
S.views["run:activity"] = async (el) => {
  const run = S.run, events = await call("provenance_events", run.run_id);
  el.append(runHead("Activity", "The append-only audit trail (provenance.jsonl), newest first. PRISMA counts and the methods draft are generated from this record.",
    h("button.btn", { onclick: () => openPath(run.run_dir + "/provenance.jsonl") }, "Open log file")));
  if (!events.length) { el.append(h("div.card.empty", "No events yet.")); return; }
  const list = h("div.card.timeline");
  for (const e of events) {
    const { ts, event, ...rest } = e;
    const detail = Object.entries(rest).map(([k, v]) => k + ": " + (typeof v === "object" ? JSON.stringify(v) : v)).join("  ·  ");
    list.append(h("div.tl-item", h("span.muted.num", fmt.time(ts)), h("span.ev", String(event).replace(/_/g, " ")),
      h("span.muted", { style: "overflow-wrap:anywhere" }, detail.slice(0, 400))));
  }
  el.append(list);
};
