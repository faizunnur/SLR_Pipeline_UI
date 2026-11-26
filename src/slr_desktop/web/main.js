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

if (window.pywebview) boot();
else if (new URLSearchParams(location.search).has("t")) boot();          // browser mode
else window.addEventListener("pywebviewready", boot, { once: true });    // desktop window
