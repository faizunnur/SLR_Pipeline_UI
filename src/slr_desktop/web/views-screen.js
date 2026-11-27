/* views-screen.js -- title/abstract screening (by hand and AI-assist), full-text
   screening, and the extraction sheet. */
"use strict";

const wsState = {};      // per-sheet: selected record, filter, search text, undo stack
const assistState = {};  // per run+phase: skipped ids, running totals, current batch

/* ---- highlighted words ----
   Three groups, marked differently by more than colour: the review's own search
   keywords, words the reviewer says point to "include", and words that point to
   "exclude". In a term, * stands for any ending, and a plain word also matches
   its plural. Text is split into nodes; nothing is ever written as HTML. */
function termPattern(term) {
  const parts = String(term).trim().replace(/^"|"$/g, "").split("*")
    .map((part) => part.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+"));
  if (!parts.join("")) return null;
  const body = parts.join("[\\p{L}\\p{N}]*") + (term.includes("*") ? "" : "(?:s|es)?");
  return "(?<![\\p{L}\\p{N}])" + body + "(?![\\p{L}\\p{N}])";
}
function highlighter(aids) {
  const groups = [];
  if (aids) {
    for (const [cls, terms] of [["exc", aids.exclude], ["inc", aids.include], ["key", aids.use_keywords ? aids.keywords : []]]) {
      const patterns = (terms || []).map(termPattern).filter(Boolean);
      // an engine without lookbehind (old WebKit) throws here: then that group is simply not marked
      try { if (patterns.length) groups.push([cls, new RegExp(patterns.join("|"), "giu")]); } catch (e) { /* no highlight */ }
    }
  }
  return (text) => {
    text = String(text || "");
    if (!groups.length || !text) return text;
    const taken = [];
    // groups are in priority order: where two overlap, the earlier one keeps the words
    for (const [cls, re] of groups) {
      re.lastIndex = 0;
      for (let m = re.exec(text); m; m = re.exec(text)) {
        if (!m[0]) { re.lastIndex++; continue; }
        const a = m.index, b = a + m[0].length;
        if (!taken.some(([s, e]) => a < e && b > s)) taken.push([a, b, cls]);
      }
    }
    if (!taken.length) return text;
    taken.sort((x, y) => x[0] - y[0]);
    const nodes = [];
    let pos = 0;
    for (const [a, b, cls] of taken) {
      if (a > pos) nodes.push(text.slice(pos, a));
      nodes.push(h("mark.hl." + cls, text.slice(a, b)));
      pos = b;
    }
    if (pos < text.length) nodes.push(text.slice(pos));
    return nodes;
  };
}
/* Highlighted words and the one-click reason list, edited in one place. */
function editAids(aids) {
  const v = { use_keywords: aids.use_keywords, include: aids.include.join(", "), exclude: aids.exclude.join(", "), reasons: aids.reasons.join("\n") };
  const area = (key, rows, placeholder) => h("textarea", { rows, placeholder, value: v[key], oninput: (e) => { v[key] = e.target.value; } });
  modal({ title: "Highlighted words and quick reasons",
    sub: "Reading aids for this review. They change how records look and how fast you can record a reason, never a decision.",
    body: h("div.stack",
      h("label.check", h("input", { type: "checkbox", checked: v.use_keywords, onchange: (e) => { v.use_keywords = e.target.checked; } }),
        h("span", "Mark my search keywords ", h("mark.hl.key", aids.keywords.slice(0, 3).join(", ") || "none set"))),
      field(h("span", "Words that suggest ", h("mark.hl.inc", "include")), area("include", 2, "randomized, cohort, empirical, evaluat*"), "Separate with commas. Use * for any ending."),
      field(h("span", "Words that suggest ", h("mark.hl.exc", "exclude")), area("exclude", 2, "editorial, protocol, animal, survey of"), "Separate with commas. Use * for any ending."),
      field("Quick exclusion reasons", area("reasons", 8, "One reason per line"), "One per line, up to 9. Press the number shown on a reason to exclude with it in one key.")),
    actions: [{ label: "Cancel" }, { label: "Save", primary: true, onclick: async () => {
      const out = await tryCall("set_screening_aids", S.run.run_id, { use_keywords: v.use_keywords, include: v.include, exclude: v.exclude, reasons: v.reasons.split("\n") });
      if (!out) return false;
      toast("Saved."); render();
    } }] });
}

/* A two-pane review workspace: record list on the left, one record on the right.
   opts: { key, rows, decisionOf(r), reasonOf(r), options: [[decision, label, cls, key]], reasonRequired: [..],
           decide(r, decision, reason) -> Promise, restore(r, before) -> Promise, aids,
           meta(r) -> nodes, criteria, emptyText } */
function reviewWorkspace(host, opts) {
  const st = wsState[opts.key] || (wsState[opts.key] = { sel: null, filter: "undecided", q: "", undo: [] });
  const rows = opts.rows;
  if (!rows.length) { host.append(h("div.card.empty", h("h2", "Nothing here yet"), h("p", opts.emptyText || ""))); return; }
  if (st.filter === "undecided" && !rows.some((r) => !opts.decisionOf(r))) st.filter = "all";
  const aids = opts.aids || { keywords: [], include: [], exclude: [], reasons: [], use_keywords: false };
  const mark = highlighter(aids);
  const reasons = aids.reasons.slice(0, 9);
  const canExclude = opts.options.some((o) => o[0] === "exclude");

  const visible = () => rows.filter((r) => {
    const d = opts.decisionOf(r);
    if (st.filter === "undecided" ? d : st.filter !== "all" && d !== st.filter) return false;
    if (!st.q) return true;
    const q = st.q.toLowerCase();
    return String(r.title).toLowerCase().includes(q) || String(r.abstract || "").toLowerCase().includes(q) || String(r.id) === q;
  });
  const current = () => rows.find((r) => r.id === st.sel);
  const reasonInput = h("input", { type: "text", placeholder: "Reason", "aria-label": "Reason for the decision" });
  const wrap = h("div.screen");
  host.append(wrap);

  const move = (step) => {
    const list = visible(), i = list.findIndex((r) => r.id === st.sel);
    const next = list[Math.min(Math.max(i + step, 0), list.length - 1)];
    if (next) { st.sel = next.id; draw(); }
  };
  let busy = false;   // one save at a time, so a held key cannot decide two records
  const decide = async (decision, quickReason) => {
    const r = current();
    if (!r || busy) return;
    const reason = quickReason !== undefined ? quickReason : reasonInput.value.trim();
    if ((opts.reasonRequired || []).includes(decision) && !reason) {
      reasonInput.focus(); toast("Give a reason for this exclusion first: type one, or pick a quick reason (PRISMA item 16b).", true); return;
    }
    const list = visible(), i = list.findIndex((x) => x.id === r.id);
    const after = list[i + 1] || list[i - 1];
    const before = { id: r.id, decision: opts.decisionOf(r) || "", reason: opts.reasonOf(r) || "", reviewer: r.reviewer || "" };
    busy = true;
    try { await opts.decide(r, decision, reason); } catch (e) { toast(e.message, true); return; } finally { busy = false; }
    st.undo.push(before);
    if (st.undo.length > 100) st.undo.shift();
    if (st.filter !== "all" && after) st.sel = after.id;
    else if (st.filter === "all" && list[i + 1]) st.sel = list[i + 1].id;
    draw();
    if (opts.afterDecide) opts.afterDecide();
  };
  const undo = async () => {
    if (busy) return;
    const before = st.undo[st.undo.length - 1];
    if (!before) { toast("Nothing to undo."); return; }
    const r = rows.find((x) => x.id === before.id);
    if (!r) { st.undo.pop(); return; }
    busy = true;
    try { await opts.restore(r, before); } catch (e) { toast(e.message, true); return; } finally { busy = false; }
    st.undo.pop();
    st.sel = r.id;
    if (!visible().some((x) => x.id === r.id)) st.filter = before.decision ? "all" : "undecided";
    if (!visible().some((x) => x.id === r.id)) { st.filter = "all"; st.q = ""; }
    toast("Undone: #" + r.id + (before.decision ? " is back to " + before.decision + "." : " has no decision again."));
    draw();
    if (opts.afterDecide) opts.afterDecide();
  };

  function draw() {
    const list = visible();
    if (!list.some((r) => r.id === st.sel)) st.sel = list.length ? list[0].id : null;
    const done = rows.filter((r) => opts.decisionOf(r)).length;
    const count = (f) => rows.filter((r) => (f === "undecided" ? !opts.decisionOf(r) : opts.decisionOf(r) === f)).length;
    const filters = [["undecided", "To do " + count("undecided")], ["all", "All " + rows.length]]
      .concat(opts.options.map(([d, label]) => [d, label + " " + count(d)]));

    const items = h("div.rec-items", { role: "listbox", "aria-label": "Records" });
    for (const r of list.slice(0, 600)) {
      const d = opts.decisionOf(r);
      items.append(h("button.rec-item" + (r.id === st.sel ? ".sel" : ""), { role: "option", "aria-selected": r.id === st.sel,
        onclick: () => { st.sel = r.id; draw(); } },
        h("span.dot" + (d ? "." + d : ""), { title: d || "undecided" }),
        h("span.grow", h("span.ti", r.title || "(no title)"), h("span.small.faint", "#" + r.id + (r.year !== "" ? " · " + fmt.year(r.year) : "")))));
    }
    if (list.length > 600) items.append(h("div.empty.small", "Showing the first 600 of " + list.length + ". Type in the filter box to narrow the list."));
    if (!list.length) items.append(h("div.empty.small", st.filter === "undecided" ? "Every record has a decision." : "No records match."));
    const search = h("input", { type: "text", placeholder: "Filter by title, abstract or id", value: st.q, "aria-label": "Filter records",
      oninput: (e) => { st.q = e.target.value; const pos = e.target.selectionStart; draw(); const s = wrap.querySelector(".rec-list-head input"); s.focus(); s.setSelectionRange(pos, pos); } });
    const left = h("div.rec-list",
      h("div.rec-list-head",
        h("div.row.small", h("strong", done + " of " + rows.length + " decided"), h("span.right.muted", Math.round((100 * done) / rows.length) + "%")),
        progress(done, rows.length), search,
        h("div.row.wrap", filters.map(([f, label]) => h("button.chip" + (st.filter === f ? ".on" : ""), { onclick: () => { st.filter = f; draw(); } }, label)))),
      items);

    const r = current();
    let right;
    if (!r) {
      right = h("div.rec-detail", h("div.rec-body.empty", h("h2", "All done here"), h("p", opts.doneText || "Every record in this view has a decision."),
        st.undo.length ? h("p", { style: "margin-top:14px" }, h("button.btn", { onclick: undo }, "Undo last decision", h("kbd", "U"))) : null));
    } else {
      const d = opts.decisionOf(r), reasonNow = opts.reasonOf(r) || "";
      reasonInput.value = reasonNow;
      const crit = opts.criteria && (opts.criteria.inclusion || opts.criteria.exclusion)
        ? h("details.criteria", h("summary", "Eligibility criteria"),
            opts.criteria.inclusion ? h("div", h("strong", "Include if all: "), opts.criteria.inclusion) : null,
            opts.criteria.exclusion ? h("div", h("strong", "Exclude if any: "), opts.criteria.exclusion) : null) : null;
      const anyMarks = (aids.use_keywords && aids.keywords.length) || aids.include.length || aids.exclude.length;
      right = h("div.rec-detail",
        h("div.rec-body",
          h("div.row.wrap.small.muted", { style: "margin-bottom:8px" }, h("span.num", "#" + r.id), tag(d),
            r.earlier_decision ? h("span.badge.acc", { title: "The same record was judged in another phase" }, "earlier phase: " + r.earlier_decision) : null,
            opts.byline ? opts.byline(r) : null,
            h("span.grow"),
            h("button.btn.ghost.sm", { onclick: () => move(-1), title: "Previous record" }, "Previous", h("kbd", "K")),
            h("button.btn.ghost.sm", { onclick: () => move(1), title: "Next record" }, "Next", h("kbd", "J"))),
          h("h2", r.title ? mark(r.title) : "(no title)"),
          h("div.muted", { style: "margin-top:6px" }, [r.authors, fmt.year(r.year), r.venue].filter(Boolean).join(" · ")),
          h("div.row.wrap.small", { style: "margin-top:8px" },
            r.doi ? h("a", { onclick: () => tryCall("open_url", "https://doi.org/" + r.doi) }, "doi:" + r.doi) : null,
            r.url && String(r.url).startsWith("http") ? h("a", { onclick: () => tryCall("open_url", r.url) }, "Open record") : null,
            opts.meta ? opts.meta(r) : null),
          h("div.rec-abstract", r.abstract ? mark(r.abstract) : h("span.faint", "No abstract available for this record.")),
          crit),
        canExclude && opts.aids ? h("div.rec-reasons", { role: "group", "aria-label": "Exclude with a reason" },
          h("span.small.muted", "Exclude:"),
          reasons.map((text, i) => h("button.chip.reason" + (d === "exclude" && reasonNow === text ? ".on" : ""),
            { onclick: () => decide("exclude", text), title: "Exclude with this reason (key " + (i + 1) + ")" }, h("kbd", String(i + 1)), text)),
          h("button.help.plain", { type: "button", onclick: () => editAids(aids) }, reasons.length ? "Edit list" : "Add quick reasons")) : null,
        h("div.rec-actions", reasonInput,
          opts.options.map(([dec, label, cls, key]) => h("button.btn." + cls + (d === dec ? ".on" : ""), { onclick: () => decide(dec) }, label, h("kbd", key))),
          h("span.grow"),
          opts.aids ? h("button.help.plain", { type: "button", onclick: () => editAids(aids), title: "Choose the words that are marked in the text" },
            anyMarks ? "Highlighted words" : "Highlight words") : null,
          opts.restore ? h("button.btn.ghost.sm", { disabled: !st.undo.length, onclick: undo, title: "Take back your last decision (U or Ctrl+Z)" }, "Undo", h("kbd", "U")) : null));
    }
    clear(wrap).append(left, right);
    const selEl = wrap.querySelector(".rec-item.sel");
    if (selEl) selEl.scrollIntoView({ block: "nearest" });
  }
  draw();
  setKeys((e, typing, withCtrl) => {
    const k = e.key.toLowerCase();
    if (withCtrl) { if (k === "z" && !typing && opts.restore) { e.preventDefault(); undo(); } return; }
    if (typing) { if (e.key === "Escape") e.target.blur(); return; }
    const opt = opts.options.find((o) => o[3].toLowerCase() === k);
    if (opt) { e.preventDefault(); decide(opt[0]); }
    else if (canExclude && opts.aids && /^[1-9]$/.test(e.key) && reasons[Number(e.key) - 1]) { e.preventDefault(); decide("exclude", reasons[Number(e.key) - 1]); }
    else if (k === "u" && opts.restore) { e.preventDefault(); undo(); }
    else if (k === "j" || e.key === "ArrowDown") { e.preventDefault(); move(1); }
    else if (k === "k" || e.key === "ArrowUp") { e.preventDefault(); move(-1); }
    else if (k === "r") { e.preventDefault(); reasonInput.focus(); }
  });
}
const TA_OPTIONS = [["include", "Include", "inc", "I"], ["maybe", "Maybe", "may", "M"], ["exclude", "Exclude", "exc", "E"]];
async function quietRunRefresh() {
  try { S.run = await call("get_run", S.run.run_id); renderSidebar(); } catch (e) { /* keep the stale sidebar */ }
}

/* ---- title/abstract screening ---- */
S.views["run:screening"] = async (el) => {
  const run = S.run;
  el.append(runHead("Title and abstract screening",
    "Decide each record against your criteria. Include and maybe both go on to full-text reading."));
  const p = phasePicker(el, "screening", run.has_snowball);
  if (p === "snowball") {
    const data = await call("get_snowball_screening", run.run_id);
    el.append(h("p.small.muted", { style: "margin-bottom:10px" }, "Candidates found by citation snowballing. When you are done, use “Merge snowball results” under Outputs."));
    reviewWorkspace(el, { key: run.run_id + ":sb", rows: data.rows, criteria: data.criteria, aids: data.aids, options: TA_OPTIONS,
      decisionOf: (r) => r.ta_decision, reasonOf: (r) => r.ta_reason,
      decide: async (r, d, reason) => { await call("set_snowball_decision", run.run_id, r.id, d, reason); r.ta_decision = d; r.ta_reason = reason; r.reviewer = data.reviewer || "human-review"; },
      restore: async (r, b) => { await call("restore_decision", run.run_id, "snowball", 0, r.id, b.decision, b.reason, b.reviewer); r.ta_decision = b.decision; r.ta_reason = b.reason; r.reviewer = b.reviewer; } });
    return;
  }
  if (!p.unlocked) { el.append(lockedNote(p)); return; }
  if (!p.searched) {
    el.append(h("div.card.empty", h("h2", "No screening sheet yet"), h("p", "Run this phase's search first."),
      h("p", { style: "margin-top:14px" }, h("button.btn.primary", { onclick: () => go(runPath(run.run_id, "search", String(p.phase))) }, "Go to search")))); return;
  }
  const akey = run.run_id + ":" + p.phase;
  const as = assistState[akey] || (assistState[akey] = { skipped: [], totals: {}, batch: null, last: null });
  as.mode = S.route.arg2 === "assist" ? "assist" : "hand";
  const setMode = (m) => go(runPath(run.run_id, "screening", String(p.phase), m === "assist" ? "assist" : ""));
  el.append(h("div.row", { style: "margin-bottom:14px" },
    seg([["hand", "Screen by hand"], ["assist", "AI assist (copy and paste)"]], as.mode, setMode),
    h("span.grow"),
    !p.n_undecided ? h("button.btn.primary", { onclick: () => go(runPath(run.run_id, "gate", String(p.phase))) }, "Go to review gate") : null));

  if (as.mode === "assist") { await assistPanel(el, run, p, as); return; }
  const data = await call("get_screening", run.run_id, p.phase);
  reviewWorkspace(el, { key: akey, rows: data.rows, criteria: data.criteria, aids: data.aids, options: TA_OPTIONS,
    restore: async (r, b) => { await call("restore_decision", run.run_id, "ta", p.phase, r.id, b.decision, b.reason, b.reviewer); r.ta_decision = b.decision; r.ta_reason = b.reason; r.reviewer = b.reviewer; },
    decisionOf: (r) => r.ta_decision, reasonOf: (r) => r.ta_reason,
    byline: (r) => (r.reviewer ? h("span", String(r.reviewer) === "ai-assisted" ? "AI suggested" : "by " + r.reviewer) : null),
    doneText: "Every record in this phase has a decision. Continue to the review gate.",
    decide: async (r, d, reason) => { await call("set_ta_decision", run.run_id, p.phase, r.id, d, reason); r.ta_decision = d; r.ta_reason = reason; r.reviewer = data.reviewer || "human-review"; },
    afterDecide: quietRunRefresh });
};

/* AI assist is a loop of three moves: copy the prompt, send it to a chatbot,
   paste the answer back. The page always marks the ONE control to use next (a
   pulsing outline and the keyboard focus), so nobody has to work out the order:
   Copy prompt -> the reply box -> Apply decisions -> Copy prompt of the next batch. */
const CHATBOTS = [[/chatgpt|openai|gpt/i, "ChatGPT", "https://chatgpt.com/"], [/claude/i, "Claude", "https://claude.ai/new"],
  [/gemini|bard/i, "Gemini", "https://gemini.google.com/app"], [/copilot/i, "Copilot", "https://copilot.microsoft.com/"],
  [/deepseek/i, "DeepSeek", "https://chat.deepseek.com/"]];
async function assistPanel(el, run, p, as) {
  if (!as.batch) as.batch = await call("assist_batch", run.run_id, p.phase, as.skipped);
  const b = as.batch;
  if (!as.step) as.step = 1;
  if (as.last) {
    const c = as.last.counts, left = as.last.n_left || 0;
    el.append(h("div.banner" + (as.last.problems.length ? ".warn" : ".good"), { style: "margin-bottom:14px" }, h("div.grow",
      h("strong", "Last batch: " + as.last.n_applied + " decision(s) saved. "),
      Object.entries(c).map(([k, v]) => v + " " + k).join(", "),
      left ? h("div", { style: "margin-top:4px" }, h("strong", left + " study/studies got no usable answer and are still undecided. "),
        b.done ? "Screen them by hand." : "They are in the next batch below, so you do not need to do anything special.") : null,
      as.last.problems.map((x) => h("div.small.muted", x))),
      left && as.last.fix_request ? h("button.btn.sm", { title: "A short message for the same chat, asking only for the studies that were not answered",
        onclick: () => copyText(as.last.fix_request, "Request") }, "Copy a request for just these") : null));
  }
  if (b.done) {
    const fin = await call("assist_finish", run.run_id, p.phase, as.skipped, as.totals);
    el.append(h("div.card.empty", h("h2", fin.complete ? "AI-assist screening is complete" : "This AI-assist session is finished"),
      h("p", fin.complete ? "Every record in this phase has a decision. Nothing an AI suggested is final until you pass the review gate."
        : fin.remaining + " record(s) still have no decision. Screen them by hand, or start another AI-assist batch."),
      h("p.row", { style: "margin-top:14px;justify-content:center" },
        fin.complete ? h("button.btn.primary", { onclick: () => go(runPath(run.run_id, "gate", String(p.phase))) }, "Go to review gate")
          : h("button.btn.primary", { onclick: () => { as.batch = null; go(runPath(run.run_id, "screening", String(p.phase))); } }, "Screen the rest by hand"),
        as.skipped.length ? h("button.btn", { onclick: () => { as.skipped = []; as.batch = null; render(); } }, "Retry skipped records with AI") : null)));
    return;
  }
  const bot = CHATBOTS.find(([re]) => re.test(b.tool));
  const botName = bot ? bot[1] : b.tool;
  const state = { ack: !b.criteria_missing };
  const total = p.n_rows || b.n_undecided, doneCount = Math.max(total - b.n_undecided, 0);
  const body = h("div.stack");

  const drawBody = () => {
    clear(body);
    if (b.criteria_missing) {
      body.append(h("div.banner.warn", h("div.grow", h("strong", "This review has no eligibility criteria. "),
        "The prompt can only ask “is this plausibly relevant?”, which is not your protocol's criteria and not defensible screening for a systematic review."),
        h("label.check", h("input", { type: "checkbox", checked: state.ack, onchange: (e) => { state.ack = e.target.checked; drawBody(); } }), "Use the generic prompt anyway")));
      if (!state.ack) return;
    }
    const stepEls = [["Copy the prompt", "one click"], ["Send it to " + botName, "paste, then wait for the answer"], ["Paste the answer here", "then apply the decisions"]]
      .map(([label, sub], i) => h("li", h("span.n", String(i + 1)), h("span", h("strong", label), h("span.small.muted", sub))));
    const copyBtn = h("button.btn.primary", "Copy prompt");
    const applyBtn = h("button.btn.primary", "Apply decisions", h("kbd", "Ctrl Enter"));
    const reply = h("textarea.prompt-box", { placeholder: "Paste the chatbot's whole answer here.\nOne line per study, for example:\n12 | INCLUDE | on-topic empirical study", "aria-label": "Chatbot reply" });
    const hint = h("p.assist-hint", { role: "status" });
    const openBot = bot ? h("button.btn", { onclick: () => tryCall("open_url", bot[2]) }, "Open " + bot[1]) : null;

    const show = (focus) => {
      const step = as.step;
      stepEls.forEach((li, i) => { li.className = i + 1 < step ? "done" : i + 1 === step ? "now" : ""; });
      copyBtn.classList.toggle("cue", step === 1);
      reply.classList.toggle("cue", step === 2);
      applyBtn.classList.toggle("cue", step === 3);
      if (openBot) openBot.classList.toggle("primary", step === 2);
      clear(hint).append(step === 1 ? "Start here: press Copy prompt."
        : step === 2 ? "Copied. Now go to " + botName + ", paste the prompt into a new chat and send it. When it has answered, copy its whole answer and paste it into the box marked below."
        : "Answer pasted. Press Apply decisions. The next batch then appears and you start again at Copy prompt.");
      if (!focus) return;
      const target = step === 1 ? copyBtn : step === 2 ? reply : applyBtn;
      if (step === 2) reply.scrollIntoView({ block: "center", behavior: "smooth" });
      target.focus({ preventScroll: step === 2 });
    };
    const apply = async () => {
      if (!reply.value.trim()) { as.step = 2; show(true); toast("Paste the chatbot's answer into the box first.", true); return; }
      const out = await tryCall("assist_apply", run.run_id, p.phase, b.start, b.batch_ids, reply.value);
      if (!out) return;
      for (const [k, v] of Object.entries(out.counts)) as.totals[k] = (as.totals[k] || 0) + v;
      as.last = out; as.batch = null; as.step = 1;
      await quietRunRefresh();
      await render();
      $("#view").scrollTop = 0;
    };
    copyBtn.addEventListener("click", async () => {
      as.step = reply.value.trim() ? 3 : 2; show(false);
      // move the focus only after the copy is over: the copy fallback borrows the focus for a moment
      try { await copyText(b.prompt, "Prompt"); } catch (e) { /* copyText reports its own failure */ }
      show(true);
    });
    reply.addEventListener("input", () => { const next = reply.value.trim() ? 3 : 2; if (next !== as.step) { as.step = next; show(false); } });
    reply.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); apply(); } });
    applyBtn.addEventListener("click", apply);

    body.append(
      h("div.card.stack",
        h("div.row.wrap", h("strong", "Batch of " + b.batch_ids.length + " records"),
          h("span.small.muted", doneCount + " of " + total + " decided · " + b.n_batches + " batch" + (b.n_batches > 1 ? "es" : "") + " to go"), h("span.grow")),
        progress(doneCount, total),
        h("ol.steps", stepEls), hint),
      h("div.card.stack",
        h("div.row.wrap", h("h2.grow", "1. The prompt for " + botName), copyBtn, openBot),
        h("p.small.muted", "It holds your criteria and this batch's " + b.batch_ids.length + " records. Also saved as " + b.prompt_file.split(/[\\/]/).pop() + " in the review folder."),
        h("details", h("summary.small", { style: "cursor:pointer" }, "Show the prompt"),
          h("textarea.prompt-box", { readonly: true, "aria-label": "Screening prompt", style: "margin-top:8px" }, b.prompt))),
      h("div.card.stack",
        h("h2", "2. The chatbot's answer"), reply,
        h("div.row.wrap", applyBtn,
          h("button.btn", { onclick: () => { as.skipped.push(...b.batch_ids); as.batch = null; as.last = null; as.step = 1; toast("Batch left for manual screening."); render(); } }, "Skip this batch"),
          h("span.grow"),
          h("span.small.muted", { style: "max-width:52ch" }, "Checked against this batch: an invented id is refused, an unclear or contradictory line is left undecided, and a decision you already made is never overwritten."))));
    setTimeout(() => { if (document.contains(copyBtn)) show(as.step === 1); }, 80);
    show(false);
  };
  drawBody();
  el.append(body);
}

/* ---- full-text screening ---- */
S.views["run:fulltext"] = async (el) => {
  const run = S.run;
  const data = await call("get_fulltext", run.run_id);
  const hasDecisions = data.rows.some((r) => r.ft_decision);
  el.append(runHead("Full-text screening",
    "Read each full text, then record include or exclude. An exclusion needs a reason: PRISMA item 16b asks you to cite the reports you excluded and say why.",
    h("button.btn", { disabled: jobRunning() || !data.rows.length, onclick: () => startJob("start_download", [run.run_id]) }, "Download open-access PDFs"),
    h("button.btn", { disabled: jobRunning(), title: "Rebuild the set from the phase screening sheets", onclick: async () => {
      if (hasDecisions && !(await confirmBox("Rebuild the merged set?", "The set is rebuilt from the screening sheets. Full-text decisions are kept for studies that are still in the set.", "Rebuild"))) return;
      const out = await tryCall("merge", run.run_id); if (out) { toast(out.n_merged + " study/studies in the merged set."); render(); } } }, "Rebuild set")));
  if (!run.phases.some((p) => p.gated)) {
    el.append(h("div.banner.warn", { style: "margin-bottom:14px" }, h("div", h("strong", "No phase has passed its review gate yet. "),
      "The list below reflects title/abstract decisions that are not confirmed.")));
  }
  if (data.rows.some((r) => !r.pdf)) el.append(h("p.small.muted", { style: "margin-bottom:12px;max-width:86ch" },
    "“Download open-access PDFs” fetches only what is free to read. For the rest, connect to your university network or VPN, use “Open at publisher” on the study, save the PDF, and add it with “Attach PDF”."));
  if (data.done) el.append(h("div.banner.good", { style: "margin-bottom:14px" }, h("div.grow", h("strong", "Full-text screening is complete. "),
    data.rows.filter((r) => r.ft_decision === "include").length + " study/studies included."),
    h("button.btn.sm", { onclick: () => go(runPath(run.run_id, "extraction")) }, "Go to extraction")));
  reviewWorkspace(el, {
    key: run.run_id + ":ft", rows: data.rows, criteria: data.criteria, aids: data.aids,
    restore: async (r, b) => {
      await call("restore_decision", run.run_id, "ft", 0, r.id, b.decision, b.reason, "");
      r.ft_decision = b.decision; r.ft_reason = b.reason;
      if (data.done && !b.decision) render();   // screening is no longer complete: redraw the page banner
    },
    options: [["include", "Include", "inc", "I"], ["exclude", "Exclude", "exc", "E"]], reasonRequired: ["exclude"],
    decisionOf: (r) => r.ft_decision, reasonOf: (r) => r.ft_reason,
    emptyText: "No study has passed title/abstract screening yet.",
    doneText: "Every study has a full-text decision.",
    byline: (r) => h("span", "passed title/abstract as " + (r.ta_decision || "include") + (r.phase !== "" ? " · phase " + r.phase : "")),
    meta: (r) => (r.pdf ? h("button.btn.sm", { onclick: () => openPath(r.pdf) }, "Open PDF") : [
      h("span.faint", "No PDF yet"),
      r.doi || (r.url && String(r.url).startsWith("http")) ? h("button.btn.sm", { title: "Opens the article in your browser. On your university network or VPN the full text is usually available there.",
        onclick: () => tryCall("open_url", r.doi ? "https://doi.org/" + r.doi : r.url) }, "Open at publisher") : null,
      h("button.btn.sm", { title: "Keep a PDF you saved yourself as this study's full text", onclick: async () => {
        const picked = await tryCall("choose_pdf");
        if (!picked) return;
        const out = await tryCall("attach_pdf", run.run_id, r.id, picked);
        if (out) { r.pdf = out.pdf; toast("PDF attached to study " + r.id + "."); render(); }
      } }, "Attach PDF…")]),
    decide: async (r, d, reason) => {
      const out = await call("set_ft_decision", run.run_id, r.id, d, reason);
      r.ft_decision = d; r.ft_reason = reason;
      if (out.complete) { toast("Full-text screening complete: " + out.n_included + " included, " + out.n_excluded + " excluded."); render(); }
    },
    afterDecide: quietRunRefresh });
};

/* ---- extraction ---- */
const extState = {};
S.views["run:extraction"] = async (el) => {
  const run = S.run;
  const data = await call("get_extraction", run.run_id);
  el.append(runHead("Data extraction and quality appraisal",
    "One row per included study. R/A/T/C set the quality tier by a fixed formula; changing the tier by hand is logged as an override.",
    h("button.btn" + (data.rows.length ? "" : ".primary"), { disabled: jobRunning(), onclick: async () => {
      if (data.rows.length && !(await confirmBox("Rebuild the extraction sheet?", "The sheet is rebuilt for the current included set. Values you already entered are kept for studies that are still in it.", "Rebuild"))) return;
      startJob("start_extract", [run.run_id]); } }, data.rows.length ? "Rebuild sheet" : "Build extraction sheet"),
    data.rows.length ? h("button.btn", { onclick: () => openPath(run.run_dir + "/extraction.csv") }, "Open CSV") : null));
  if (!data.rows.length) {
    el.append(h("div.card.empty", h("h2", "No extraction sheet yet"),
      h("p", run.fulltext_done ? "Build the sheet to start coding the included studies." : "Finish full-text screening first, then build the sheet for the included studies.")));
    return;
  }
  const st = extState[run.run_id] || (extState[run.run_id] = { sel: data.rows[0].id });
  if (!data.rows.some((r) => r.id === st.sel)) st.sel = data.rows[0].id;
  const save = async (r, fieldName, value) => {
    const out = await tryCall("update_extraction", run.run_id, r.id, fieldName, value);
    if (!out) return false;
    Object.assign(r, out.row);
    if (out.note) toast(out.note);
    return out.changed;
  };
  const sel = (r, f) => h("select", { "aria-label": f + " for study " + r.id, onclick: (e) => e.stopPropagation(),
    onchange: async (e) => { await save(r, f, e.target.value); render(); } },
    h("option", { value: "" }, "–"), data.enums[f].map((o) => h("option", { value: o, selected: String(r[f]) === o }, o)));
  const coded = data.rows.filter((r) => r.quality_tier).length;
  const table = h("div.table-wrap", h("table",
    h("thead", h("tr", h("th", "Id"), h("th", "Study"), h("th", "Venue"), h("th", "R"), h("th", "A"), h("th", "T"), h("th", "C"), h("th", "Tier"))),
    h("tbody", data.rows.map((r) => h("tr.click" + (r.id === st.sel ? ".sel" : ""), { onclick: () => { st.sel = r.id; render(); } },
      h("td.num.muted", r.id), h("td", h("div.study-title", { title: r.title }, r.title), h("div.small.muted", fmt.year(r.year))),
      h("td", sel(r, "venue_tier")), h("td", sel(r, "R")), h("td", sel(r, "A")), h("td", sel(r, "T")), h("td", sel(r, "C")), h("td", sel(r, "quality_tier")))))));

  const r = data.rows.find((x) => x.id === st.sel);
  const textFields = data.fields.filter((f) => !data.enums[f]);
  const fixed = new Set(["id", "title", "authors", "year", "venue", "doi", "url", "extraction_reviewer", "extraction_date", ...data.fields]);
  const extra = data.columns.filter((c) => !fixed.has(c));
  const editor = (f) => field(f.replace(/_/g, " "), h("textarea", { rows: f === "notes" || f === "key_findings" ? 3 : 2, value: r[f] === undefined ? "" : String(r[f]),
    onchange: async (e) => { if (await save(r, f, e.target.value)) toast("Saved " + f.replace(/_/g, " ") + "."); } }));
  const panel = h("div.card.stack.ext-panel",
    h("div", h("div.small.muted", "Study " + r.id), h("h2", r.title),
      h("div.small.muted", [r.authors, fmt.year(r.year)].filter(Boolean).join(" · ")),
      r.extraction_reviewer ? h("div.small.faint", "Last edited by " + r.extraction_reviewer + " on " + r.extraction_date) : null),
    textFields.map(editor),
    extra.length ? h("details", h("summary", { style: "cursor:pointer;font-weight:600" }, "Appraisal checklist (" + extra.length + " items)"),
      h("div.stack", { style: "margin-top:10px" }, extra.map(editor))) : null,
    h("p.small.faint", "A field is saved when you leave it. Every change is written to the audit trail."));
  el.append(h("div.row.small.muted", { style: "margin-bottom:10px" }, h("strong", coded + " of " + data.rows.length + " studies have a quality tier")),
    h("div.ext", table, panel));
};
