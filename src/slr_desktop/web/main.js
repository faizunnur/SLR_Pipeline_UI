/* main.js -- start-up: wait for the Python bridge, load app info, render. */
"use strict";

async function boot() {
  try {
    S.info = await call("app_info");
  } catch (e) {
    clear($("#view")).append(h("div.view-inner", h("div.banner.bad", h("div", h("strong", "The app could not start. "), e.message))));
    return;
  }
  applyTheme(S.info.theme);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyTheme(S.info.theme));
  // A task may still be running if the page was reloaded: pick it up again.
  const busy = await tryCall("job_busy");
  if (busy) { S.job = { id: busy.job_id, title: busy.title, status: "running", lines: [], next: 0, open: false }; pollJob(); }
  window.addEventListener("hashchange", render);
  render();
}

/* The desktop window starts when pywebview says its bridge is ready. If that
   never happens the page would stay blank with no hint, so after a wait it
   says what to do instead. A late bridge still starts the app. */
const BRIDGE_WAIT_MS = 12000;
function bridgeMissing() {
  clear($("#view")).append(h("div.view-inner", h("div.banner.bad", h("div",
    h("strong", "The window could not connect to the app. "),
    "Close it and start the app in your browser instead: open a terminal and run ",
    h("span.mono", "slr-ui --browser"), ". Your reviews are not affected."))));
}

if (new URLSearchParams(location.search).has("t")) boot();               // browser mode
else if (window.pywebview && window.pywebview.api && window.pywebview.api.app_info) boot();
else {                                                                    // desktop window
  const waiting = setTimeout(bridgeMissing, BRIDGE_WAIT_MS);
  window.addEventListener("pywebviewready", () => { clearTimeout(waiting); boot(); }, { once: true });
}
