/* core.js -- DOM builder, Python bridge, router, modal/toast, background-task dock.

   Record titles and abstracts come from external scholarly APIs, so nothing in
   this app is ever written with innerHTML: every node is built with h(), which
   only sets text content and attributes. */
"use strict";

const S = { info: null, route: null, run: null, job: null, views: {}, keyHandler: null };

/* ---- DOM ---- */
function h(spec, attrs, ...kids) {
  const [tag, ...classes] = spec.split(".");
  const el = document.createElement(tag || "div");
  if (classes.length) el.className = classes.join(" ");
  if (attrs && (typeof attrs !== "object" || attrs instanceof Node || Array.isArray(attrs))) {
    kids.unshift(attrs); attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className += " " + v;
    else if (k === "value") el.value = v;
    else if (k === "checked" || k === "disabled" || k === "selected" || k === "open") el[k] = !!v;
    else if (k === "style") el.style.cssText = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  add(el, kids);
  return el;
}
function add(el, kids) {
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
function clear(el) { el.replaceChildren(); return el; }
const $ = (sel) => document.querySelector(sel);

/* ---- formatting ---- */
const fmt = {
  n: (v) => (v === null || v === undefined || v === "" ? "–" : Number(v).toLocaleString()),
  date: (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    return isNaN(d) ? String(iso) : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  },
  time: (iso) => {
    const d = new Date(iso);
    return isNaN(d) ? String(iso || "") : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  },
  size: (b) => (b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : b > 1024 ? Math.round(b / 1024) + " KB" : b + " B"),
  year: (v) => (v === "" || v === null || v === undefined ? "" : String(v).replace(/\.0$/, "")),
};

/* ---- Python bridge ----
   In the desktop window calls go through pywebview's in-process bridge. In
   browser mode (`slr-desktop --browser`) the same calls go to the local
   server on 127.0.0.1 with the session token it was started with. */
const TOKEN = new URLSearchParams(location.search).get("t") || "";
async function call(name, ...args) {
  let res;
  if (window.pywebview && window.pywebview.api && window.pywebview.api[name]) {
    res = await window.pywebview.api[name](...args);
  } else {
    const r = await fetch("/api/" + name, {
      method: "POST", headers: { "Content-Type": "application/json", "X-SLR-Token": TOKEN },
      body: JSON.stringify({ args }),
    });
    res = await r.json();
  }
  if (!res || !res.ok) throw new Error((res && res.error) || "The app did not answer.");
  return res.data;
}
/* call + show a failure as a toast; resolves to undefined on failure */
async function tryCall(name, ...args) {
  try { return await call(name, ...args); } catch (e) { toast(e.message, true); return undefined; }
}

/* ---- toast + modal ---- */
function toast(msg, bad) {
  const el = h("div.toast" + (bad ? ".bad" : ""), { role: bad ? "alert" : "status" }, msg);
  $("#toasts").append(el);
  setTimeout(() => el.remove(), bad ? 9000 : 4200);
}
function closeModal() { clear($("#modal-root")); }
function modal({ title, body, actions, wide, sub }) {
  const foot = h("div.modal-foot");
  let overlay = null;
  for (const a of actions || [{ label: "Close" }]) {
    foot.append(h("button.btn" + (a.primary ? ".primary" : "") + (a.danger ? ".danger" : ""), {
      // close only this dialog: the action may have opened another one meanwhile
      onclick: async () => {
        const keep = a.onclick && (await a.onclick()) === false;
        if (!keep && $("#modal-root").firstChild === overlay) closeModal();
      },
    }, a.label));
  }
  const box = h("div.modal" + (wide ? ".wide" : ""), { role: "dialog", "aria-modal": "true", "aria-label": title },
    h("div.modal-head", h("h2", title), sub ? h("p.muted.small", sub) : null),
    h("div.modal-body", body), foot);
  overlay = h("div.overlay", { onmousedown: (e) => { if (e.target === overlay) closeModal(); } }, box);
  clear($("#modal-root")).append(overlay);
  const first = box.querySelector("input, textarea, select, button.primary");
  if (first) first.focus();
  return box;
}
function confirmBox(title, text, okLabel, danger) {
  return new Promise((resolve) => {
    modal({
      title, body: h("p", text),
      actions: [{ label: "Cancel", onclick: () => resolve(false) },
                { label: okLabel || "Continue", primary: !danger, danger, onclick: () => resolve(true) }],
    });
  });
}

/* ---- small UI pieces ---- */
function field(label, input, hint) {
  return h("label.field", label, hint ? h("span.hint", hint) : null, input);
}
function seg(options, value, onpick) {
  const el = h("div.seg", { role: "tablist" });
  for (const [val, label] of options) {
    el.append(h("button" + (val === value ? ".on" : ""), { role: "tab", "aria-selected": val === value, onclick: () => onpick(val) }, label));
  }
  return el;
}
function tag(decision) {
  return decision ? h("span.tag." + decision, decision) : h("span.tag", "undecided");
}
function progress(done, total) {
  const pct = total ? Math.round((100 * done) / total) : 0;
  return h("div.progress", { role: "progressbar", "aria-valuenow": pct, "aria-valuemin": 0, "aria-valuemax": 100 },
    h("i", { style: `width:${pct}%` }));
}
function copyText(text, what) {
  const done = () => toast((what || "Text") + " copied.");
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text).then(done, () => legacyCopy(text, done));
  legacyCopy(text, done);
}
function legacyCopy(text, done) {
  const ta = h("textarea", { style: "position:fixed;opacity:0" });
  ta.value = text; document.body.append(ta); ta.select();
  try { document.execCommand("copy"); done(); } catch (e) { toast("Could not copy; select the text and press Ctrl+C.", true); }
  ta.remove();
}
function openPath(path) { return tryCall("open_path", path); }

/* ---- help popover ----
   A small panel that explains something next to its trigger. It opens when the
   pointer rests on the trigger, when the trigger gets keyboard focus, and on
   click (which also moves focus into it, so a link inside can be reached with
   the keyboard). It stays open while the pointer is inside it. Esc closes it.
   It is attached to <body> with fixed positioning so a scrolling table cannot
   clip it. */
let popEl = null, popFor = null, popTimer = null, popQuiet = false;
function hidePop() {
  clearTimeout(popTimer);
  if (popEl) popEl.remove();
  if (popFor) popFor.setAttribute("aria-expanded", "false");
  popEl = popFor = null;
}
/* beside the trigger when there is room (so it never covers the row it explains), else under or over it */
function placePop() {
  const r = popFor.getBoundingClientRect(), p = popEl.getBoundingClientRect();
  const W = window.innerWidth, H = window.innerHeight, clamp = (v, max) => Math.max(12, Math.min(v, max));
  if (r.right + 12 + p.width <= W - 12) {
    popEl.style.left = r.right + 12 + "px";
    popEl.style.top = clamp(r.top - 14, H - p.height - 12) + "px";
  } else {
    popEl.style.left = clamp(r.left, W - p.width - 12) + "px";
    popEl.style.top = (r.bottom + 6 + p.height <= H - 12 ? r.bottom + 6 : clamp(r.top - p.height - 6, H)) + "px";
  }
}
function helpTip(label, build) {
  const btn = h("button.help", { type: "button", "aria-expanded": "false" }, label);
  const later = () => { clearTimeout(popTimer); popTimer = setTimeout(hidePop, 220); };
  const show = () => {
    clearTimeout(popTimer);
    if (popFor === btn || popQuiet) return;
    hidePop();
    popEl = h("div.pop", { role: "dialog", "aria-label": label, tabindex: "-1",
      onmouseenter: () => clearTimeout(popTimer), onmouseleave: later,
      onfocusin: () => clearTimeout(popTimer), onfocusout: later }, build());
    document.body.append(popEl);
    popFor = btn; btn.setAttribute("aria-expanded", "true");
    placePop();
  };
  btn.addEventListener("mouseenter", show);
  btn.addEventListener("mouseleave", later);
  btn.addEventListener("focus", show);
  btn.addEventListener("blur", later);
  btn.addEventListener("click", () => { show(); if (popEl) (popEl.querySelector("button, a") || popEl).focus(); });
  return btn;
}
/* The page scrolled (for one, because Tab brought the trigger into view): follow
   the trigger, or close once it has left the screen. */
document.addEventListener("scroll", (e) => {
  if (!popEl || popEl.contains(e.target)) return;
  const r = popFor.getBoundingClientRect();
  if (r.bottom < 0 || r.top > window.innerHeight) hidePop(); else placePop();
}, true);
window.addEventListener("resize", hidePop);

/* ---- router ---- */
function go(path) { if (location.hash === "#" + path) render(); else location.hash = path; }
function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] === "run" && parts[1]) return { name: "run", runId: parts[1], tab: parts[2] || "overview", arg: parts[3] || "", arg2: parts[4] || "" };
  return { name: parts[0] || "home", arg: parts[1] || "" };
}
function runPath(runId, tab, arg, arg2) {
  return "/run/" + encodeURIComponent(runId) + "/" + (tab || "overview") + (arg ? "/" + arg : "") + (arg && arg2 ? "/" + arg2 : "");
}
let renderSeq = 0;
async function render() {
  const seq = ++renderSeq;
  S.route = parseRoute();
  setKeys(null);
  hidePop();
  const view = $("#view");
  const scroll = view.scrollTop;
  const sameRoute = view.dataset.route === location.hash;
  try {
    if (S.route.name === "run") {
      S.run = await call("get_run", S.route.runId);
    } else {
      S.run = null;
    }
    if (seq !== renderSeq) return;
    renderSidebar();
    const fn = S.route.name === "run" ? S.views["run:" + S.route.tab] : S.views[S.route.name];
    const inner = h("div.view-inner");
    await (fn || S.views.home)(inner, S.route);
    if (seq !== renderSeq) return;
    clear(view).append(inner);
    view.dataset.route = location.hash;
    view.scrollTop = sameRoute ? scroll : 0;
  } catch (e) {
    if (seq !== renderSeq) return;
    clear(view).append(h("div.view-inner", h("div.banner.bad", h("div", h("strong", "Something went wrong. "), e.message)),
      h("p", { style: "margin-top:14px" }, h("a", { onclick: () => go("/") }, "Back to your reviews"))));
  }
}
/* per-view keyboard shortcuts; cleared on every render */
function setKeys(handler) { S.keyHandler = handler; }
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && popEl) {
    // give focus back to the trigger if it was inside the panel, without reopening it
    const from = popFor, inside = popEl.contains(document.activeElement);
    hidePop();
    if (inside) { popQuiet = true; from.focus(); popQuiet = false; }
    return;
  }
  if (e.key === "Escape" && $("#modal-root").firstChild) { closeModal(); return; }
  if (!S.keyHandler || $("#modal-root").firstChild) return;
  const t = e.target;
  const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT");
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  S.keyHandler(e, typing);
});

/* ---- background tasks ---- */
async function startJob(apiName, args, onDone) {
  if (S.job && S.job.status === "running") { toast("Another task is still running.", true); return; }
  let started;
  try { started = await call(apiName, ...(args || [])); } catch (e) { toast(e.message, true); return; }
  S.job = { id: started.job_id, title: started.title, status: "running", lines: [], next: 0, open: true, onDone };
  renderDock();
  pollJob();
}
async function pollJob() {
  const job = S.job;
  if (!job) return;
  let st;
  try { st = await call("job_status", job.id, job.next); } catch (e) { setTimeout(pollJob, 1200); return; }
  if (S.job !== job) return;
  job.lines.push(...st.lines); job.next = st.next; job.status = st.status; job.result = st.result; job.error = st.error;
  renderDock();
  if (st.status === "running") { setTimeout(pollJob, 450); return; }
  if (st.status === "done") toast(job.title + " finished.");
  else if (st.status === "failed") toast(job.title + " failed: " + (st.error || "see the log"), true);
  if (job.onDone) { try { await job.onDone(st); } catch (e) { /* view already gone */ } }
  render();
}
function renderDock() {
  const dock = $("#dock");
  const job = S.job;
  if (!job) { dock.classList.add("hidden"); return; }
  dock.classList.remove("hidden");
  const running = job.status === "running";
  const state = { running: "Running", done: "Finished", failed: "Failed", cancelled: "Cancelled" }[job.status] || job.status;
  const head = h("div.dock-head",
    running ? h("span.spinner", { "aria-hidden": "true" }) : h("span.badge" + (job.status === "done" ? ".inc" : ".exc"), state),
    h("strong", job.title),
    h("span.muted.small.grow", running ? (job.lines[job.lines.length - 1] || "") .slice(0, 110) : ""),
    h("button.btn.sm.ghost", { onclick: () => { job.open = !job.open; renderDock(); } }, job.open ? "Hide log" : "Show log"),
    running ? h("button.btn.sm.danger", { onclick: () => tryCall("job_cancel", job.id) }, "Cancel")
            : h("button.btn.sm.ghost", { onclick: () => { S.job = null; renderDock(); } }, "Dismiss"));
  clear(dock).append(head);
  if (job.open) {
    const pre = h("pre", { "aria-live": "polite" }, job.lines.join("\n") || "Starting…");
    dock.append(pre);
    pre.scrollTop = pre.scrollHeight;
  }
}
const jobRunning = () => !!(S.job && S.job.status === "running");

/* ---- theme ---- */
function applyTheme(theme) {
  const dark = theme === "dark" || (theme !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}
