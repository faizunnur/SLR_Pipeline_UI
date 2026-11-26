/* views-outputs.js -- evidence checks, figures, exports, reports and the file list. */
"use strict";

S.views["run:outputs"] = async (el) => {
  const run = S.run, id = run.run_id;
  const [out, figs] = await Promise.all([call("outputs", id), call("figures", id)]);
  const busy = jobRunning();
  el.append(runHead("Outputs", "Evidence checks, figures, exports and reports. Each one is generated from this review's own records.",
    h("button.btn", { onclick: () => openPath(out.run_dir) }, "Open review folder")));

  const action = (title, text, button, extra) => h("div.card.action", h("h3", title), h("p.muted.small", text), h("div.row.wrap", button, extra || null));
  const btn = (label, onclick, primary) => h("button.btn.sm" + (primary ? ".primary" : ""), { disabled: busy, onclick }, label);
  const simple = (apiName, done) => async () => { const r = await tryCall(apiName, id); if (r) { toast(done(r)); render(); } };
  const has = (name) => out.files.some((f) => f.name === name && f.exists);
  const view = (name) => h("button.btn.sm.ghost", { onclick: () => previewFile(id, name) }, "View");

  /* evidence */
  el.append(h("h2", { style: "margin-bottom:10px" }, "Evidence"), h("div.action-grid", { style: "margin-bottom:22px" },
    action("Download PDFs", "Finds open-access copies through Unpaywall and direct links. Zero downloads usually means the set is closed-access, not a failure.",
      btn("Download", () => startJob("start_download", [id])),
      out.n_pdfs ? h("button.btn.sm.ghost", { onclick: () => openPath(out.pdf_dir) }, out.n_pdfs + " PDF(s)") : null),
    action("Verify citations", "Resolves every DOI on Crossref and compares the title it returns with the title you hold, to catch fabricated or mismatched references.",
      btn("Verify", () => startJob("start_verify", [id], () => showVerification(id))),
      has("citation_verification.csv") ? h("button.btn.sm.ghost", { onclick: () => showVerification(id) }, "Results") : null),
    action("Citation snowballing", "Follows the references and citations of your included studies (Wohlin 2014). A separate identification method from query expansion.",
      btn("Snowball…", () => snowballDialog(id)),
      run.has_snowball ? [h("button.btn.sm.ghost", { onclick: () => go(runPath(id, "screening", "snowball")) }, "Screen results"),
        btn("Merge results", async () => { const r = await tryCall("merge_snowball", id); if (r) { toast(r.n_added + " study/studies added, " + r.n_duplicate + " already present."); render(); } })] : null),
    action("Inter-rater agreement", "Cohen's kappa between two reviewers' independent screening sheets, computed before you reconcile.",
      btn("Compare two sheets…", () => kappaDialog(id))),
    action("Diagnose this review", "Per-phase funnel of stage counts. Flags the first stage that returned zero and any stage marked done whose file is missing.",
      btn("Diagnose", () => showDiagnose(id)))));

  /* figures */
  el.append(h("div.row", { style: "margin-bottom:10px" }, h("h2.grow", "Figures"),
    btn(figs.length ? "Regenerate" : "Generate PRISMA and tier figures", async () => {
      const chk = await tryCall("figures_check", id);
      if (!chk) return;
      if (chk.warnings.length) {
        const ok = await new Promise((resolve) => modal({ title: "The PRISMA numbers do not balance",
          body: h("div.stack.tight", chk.warnings.map((w) => h("p", "⚠ " + w)), h("p.muted", "Generate the figure anyway?")),
          actions: [{ label: "Not now", onclick: () => resolve(false) }, { label: "Generate anyway", primary: true, onclick: () => resolve(true) }] }));
        if (!ok) { await tryCall("figures_skip", id, chk.warnings); return; }
      }
      startJob("start_figures", [id]);
    }, !figs.length)));
  el.append(figs.length ? h("div.fig-grid", { style: "margin-bottom:22px" }, figs.map((f) => h("div.fig", { onclick: () => openPath(f.pdf || f.path), title: "Open " + (f.pdf ? "PDF" : "image") },
      h("img", { src: f.src, alt: f.name + " figure" }), h("div", f.name))))
    : h("div.card.empty", { style: "margin-bottom:22px" }, "No figures yet. The PRISMA flow diagram is drawn from the counts on the Overview page."));

  /* exports and reports */
  el.append(h("h2", { style: "margin-bottom:10px" }, "Exports and reports"), h("div.action-grid", { style: "margin-bottom:22px" },
    action("References", "BibTeX and RIS of the included studies, for your reference manager.",
      btn("Export", simple("export_references", (r) => r.n_records + " reference(s) written." + (r.note ? " " + r.note : ""))),
      has("references.bib") ? view("references.bib") : null),
    action("Full-text exclusions", "The PRISMA 16b table: every study excluded at full text, with its reason.",
      btn("Export", simple("export_exclusions", (r) => r.n_excluded + " exclusion(s) written." + (r.n_missing_reason ? " " + r.n_missing_reason + " have no reason." : ""))),
      has("excluded_full_text.csv") ? h("button.btn.sm.ghost", { onclick: () => openPath(out.run_dir + "/excluded_full_text.csv") }, "Open") : null),
    action("Methods paragraph", "A draft search-methods paragraph and per-source strategy table, written from the recorded counts. Read it before you submit.",
      btn("Draft", async () => { const r = await tryCall("write_methods", id); if (r) { render(); previewFile(id, "search_methods.md"); } }),
      has("search_methods.md") ? view("search_methods.md") : null),
    action("Provenance report", "PROVENANCE.md: every stage's counts, the PRISMA numbers and the AI-assistance disclosure, as a reproducibility appendix.",
      btn("Write", async () => { const r = await tryCall("write_provenance", id); if (r) { r.warnings.forEach((w) => toast("PRISMA check: " + w, true)); render(); previewFile(id, "PROVENANCE.md"); } }),
      has("PROVENANCE.md") ? view("PROVENANCE.md") : null),
    action("Review self-appraisal", "A fillable checklist for appraising the review itself (AMSTAR 2, ROBIS, DARE or MECCIR).",
      btn("Write checklist…", () => appraisalDialog(id)))));

  /* files */
  const present = out.files.filter((f) => f.exists);
  el.append(h("h2", { style: "margin-bottom:10px" }, "Files in this review"),
    present.length ? h("div.table-wrap", h("table", h("thead", h("tr", h("th", "File"), h("th", "What it is"), h("th.n", "Size"), h("th", ""))),
      h("tbody", present.map((f) => h("tr", h("td.mono", f.name), h("td.muted", f.desc), h("td.n.muted", fmt.size(f.size)),
        h("td", h("button.btn.sm.ghost", { onclick: () => openPath(f.path) }, "Open")))))))
      : h("div.card.empty", "No output files yet."));
};

async function previewFile(runId, name) {
  const text = await tryCall("read_text", runId, name);
  if (text === undefined) return;
  modal({ title: name, wide: true, body: h("pre.doc", text),
    actions: [{ label: "Copy", onclick: () => { copyText(text, name); return false; } },
      { label: "Open file", onclick: () => { openPath(S.run.run_dir + "/" + name); return false; } }, { label: "Close", primary: true }] });
}

async function showVerification(runId) {
  const rows = await tryCall("verification_results", runId);
  if (!rows) return;
  const status = (r) => String(r.status || "").toUpperCase();
  const failed = rows.filter((r) => status(r) === "FAIL").length;
  const cols = rows.length ? Object.keys(rows[0]).slice(0, 7) : [];
  const cell = (r, c) => {
    if (c !== "status") return h("td", String(r[c]).slice(0, 140));
    const cls = status(r) === "FAIL" ? ".exc" : status(r) === "PASS" || status(r) === "OK" ? ".inc" : "";
    return h("td", h("span.badge" + cls, r[c]));
  };
  let banner = null;
  if (failed) {
    banner = h("div.banner.bad", h("div", h("strong", failed + " citation(s) failed. "),
      "They do not match Crossref and must not enter your manuscript unchecked."));
  } else if (rows.length) {
    banner = h("div.banner.good", h("div", "Every checked citation matched Crossref."));
  }
  const head = h("thead", h("tr", cols.map((c) => h("th", c.replace(/_/g, " ")))));
  const bodyRows = h("tbody", rows.map((r) => h("tr", cols.map((c) => cell(r, c)))));
  modal({ title: "Citation verification", wide: true,
    sub: rows.length ? rows.length + " checked, " + failed + " failed" : "No results yet.",
    body: h("div.stack", banner, rows.length ? h("div.table-wrap", h("table", head, bodyRows)) : null) });
}

function snowballDialog(runId) {
  const v = { direction: "both", max: 50 };
  const dir = h("select", { onchange: (e) => { v.direction = e.target.value; } },
    [["both", "Both directions"], ["backward", "Backward (their references)"], ["forward", "Forward (papers citing them)"]].map(([k, l]) => h("option", { value: k }, l)));
  modal({ title: "Citation snowballing", sub: "Seeds are the studies currently in your included set.",
    body: h("div.grid2", field("Direction", dir), field("Max per seed study", h("input", { type: "number", min: 1, value: 50, oninput: (e) => { v.max = e.target.value; } }))),
    actions: [{ label: "Cancel" }, { label: "Start", primary: true, onclick: () => {
      startJob("start_snowball", [runId, v.direction, parseInt(v.max, 10) || 50], (st) => {
        if (st.status === "done" && st.result && st.result.n_unique) go(runPath(runId, "screening", "snowball"));
        else if (st.status === "done") toast("No new citation candidates found.");
      }); } }] });
}

function kappaDialog(runId) {
  const v = { a: S.run.run_dir + "/phase_1/screening.csv", b: "", stage: "ta_decision" };
  const result = h("div");
  const pathRow = (key, label) => {
    const input = h("input", { type: "text", value: v[key], placeholder: "Path to a screening CSV", oninput: (e) => { v[key] = e.target.value; } });
    return field(label, h("div.row", input, h("button.btn.sm", { onclick: async () => { const p = await tryCall("choose_csv"); if (p) { v[key] = p; input.value = p; } } }, "Browse…")));
  };
  modal({ title: "Inter-rater agreement (Cohen's kappa)", wide: true,
    sub: "Two reviewers screen the same records without seeing each other's calls. To make the second sheet, copy screening.csv and have reviewer B fill in the decisions independently.",
    body: h("div.stack", pathRow("a", "Reviewer A's sheet"), pathRow("b", "Reviewer B's sheet"),
      field("Stage", h("select", { onchange: (e) => { v.stage = e.target.value; } }, h("option", { value: "ta_decision" }, "Title/abstract"), h("option", { value: "ft_decision" }, "Full text"))), result),
    actions: [{ label: "Close" }, { label: "Compare", primary: true, onclick: async () => {
      const r = await tryCall("kappa", runId, v.a, v.b, v.stage);
      if (!r) return false;
      const headline = r.kappa === null ? "Kappa not defined. " : "κ = " + Number(r.kappa).toFixed(3) + ". ";
      const parts = [h("div.banner" + (r.kappa === null ? ".warn" : ""),
        h("div", h("strong", headline), r.summary, r.note ? " " + r.note : ""))];
      if (r.labels.length) {
        const mHead = h("thead", h("tr", h("th", "A vs B"), r.labels.map((l) => h("th.n", l))));
        const mBody = h("tbody", r.labels.map((a) => h("tr", h("td", h("strong", a)),
          r.labels.map((b) => h("td.n", String(r.matrix[a][b]))))));
        parts.push(h("div.table-wrap", h("table", mHead, mBody)));
      }
      if (r.conflicts.length) {
        const fileName = r.conflicts_path.split(/[\\/]/).pop();
        const cBody = h("tbody", r.conflicts.slice(0, 60).map((c) => h("tr", h("td.num", String(c.id)),
          h("td", String(c.title || "").slice(0, 110)), h("td", tag(c.reviewer_a)), h("td", tag(c.reviewer_b)))));
        parts.push(h("div",
          h("p", h("strong", r.conflicts.length + " conflict(s) to reconcile"), " – saved to ",
            h("a", { onclick: () => openPath(r.conflicts_path) }, fileName),
            ". Resolve them by discussion or a third reviewer, and say how in your methods."),
          h("div.table-wrap", { style: "margin-top:8px" }, h("table",
            h("thead", h("tr", h("th", "Id"), h("th", "Title"), h("th", "A"), h("th", "B"))), cBody))));
      }
      clear(result).append(h("div.stack", parts));
      return false;
    } }] });
}

async function showDiagnose(runId) {
  const d = await tryCall("diagnose", runId);
  if (!d) return;
  const countCell = (r) => {
    if (r.count === null || r.count === undefined) return h("td.n", "–");
    const style = r.first_zero ? "color:var(--exc);font-weight:650" : "";
    return h("td.n", h("span", { style }, r.count + (r.first_zero ? "  ← first zero" : "")));
  };
  const fileCell = (r) => {
    if (!r.file) return h("td.mono", "–");
    return h("td.mono", r.exists ? r.file : h("span", { style: "color:var(--exc)" }, r.file + " missing"));
  };
  const phaseTable = (p) => {
    const rows = h("tbody", p.rows.map((r) => h("tr", h("td", r.label),
      h("td", h("span.badge" + (r.status === "done" ? ".inc" : ""), r.status)), countCell(r), fileCell(r))));
    return h("div", h("h3", { style: "margin-bottom:6px" }, "Phase " + p.phase),
      h("div.table-wrap", h("table",
        h("thead", h("tr", h("th", "Stage"), h("th", "Status"), h("th.n", "Count"), h("th", "Output file"))), rows)));
  };
  modal({ title: "Diagnose this review", wide: true,
    body: h("div.stack",
      d.phases.length ? null : h("p.muted", "No stage has run in this review yet."),
      d.mismatches.map((m) => h("div.banner.bad", h("div", m))),
      d.phases.map(phaseTable)) });
}

async function appraisalDialog(runId) {
  const opts = await tryCall("wizard_options");
  if (!opts) return;
  const current = S.run.config.review_level_instrument || opts.review_level_instruments[0].key;
  const pick = h("select", opts.review_level_instruments.map((i) => h("option", { value: i.key, selected: i.key === current }, i.name)));
  modal({ title: "Review self-appraisal checklist", sub: "Appraises the review itself, once. Fill in the ratings by hand against the finished manuscript.",
    body: field("Instrument", pick),
    actions: [{ label: "Cancel" }, { label: "Write checklist", primary: true, onclick: async () => {
      const r = await tryCall("write_self_appraisal", runId, pick.value);
      if (!r) return false;
      toast(r.instrument + " checklist written."); render(); previewFile(runId, r.path.split(/[\\/]/).pop());
    } }] });
}
