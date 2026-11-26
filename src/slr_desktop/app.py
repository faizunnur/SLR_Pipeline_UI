"""
app.py -- starts the desktop app.

    slr-desktop            (after `pip install slr-pipeline-ui`)
    python -m slr_desktop  (from a source checkout, with src/ on the path)

The window is a native OS window (pywebview: Edge WebView2 on Windows, WebKit
on macOS, GTK/Qt on Linux) showing the bundled HTML/CSS/JS in
`slr_desktop/web/`. The page talks to Python through the in-process bridge in
api.py. `--browser` shows the same page in the default browser instead, for
systems with no web view installed (see server.py).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent / "web"


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="slr-ui",
        description="Desktop app for the SLR pipeline: search, dedup, screening, full-text "
                    "assessment, extraction, PRISMA figures and reports in one window.")
    ap.add_argument("--workspace", default=None,
                    help="Folder holding your reviews (runs/) and API keys (.env). "
                         "Default: the folder last used, else Documents/SLR Pipeline.")
    ap.add_argument("--browser", action="store_true",
                    help="Show the app in your default browser instead of a native window "
                         "(for systems with no web view installed). Local only: 127.0.0.1.")
    ap.add_argument("--shortcut", action="store_true",
                    help="Create a desktop shortcut (and a Start-menu entry on Windows), then exit.")
    ap.add_argument("--debug", action="store_true", help="Open the web inspector.")
    return ap


def _workspace(args) -> Path:
    from slr_desktop import settings

    workspace = Path(args.workspace) if args.workspace else settings.workspace()
    if args.workspace:
        data = settings.load()
        data["workspace"] = str(workspace.expanduser().resolve())
        settings.save(data)
    return workspace


def run_in_browser(args) -> int:
    import time
    import webbrowser

    from slr_desktop.api import Api
    from slr_desktop.server import serve_in_thread
    from slr_desktop.service import APP_NAME, ReviewService

    httpd, url = serve_in_thread(Api(ReviewService(_workspace(args))))
    print(f"{APP_NAME} is running in browser mode.\n  {url}\nPress Ctrl+C here to stop it.")
    webbrowser.open(url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        httpd.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.shortcut:
        from slr_desktop import settings, shortcut
        try:
            paths = shortcut.create()
        except (OSError, subprocess.SubprocessError) as e:
            print(f"Could not create the shortcut: {e}", file=sys.stderr)
            return 1
        data = settings.load()
        data["shortcut_done"] = True
        settings.save(data)
        print("Shortcut created:\n  " + "\n  ".join(str(p) for p in paths))
        return 0
    if args.browser:
        return run_in_browser(args)
    try:
        import webview
    except ImportError:
        print("The desktop window needs pywebview (pip install pywebview). "
              "Or run: slr-desktop --browser", file=sys.stderr)
        return 1

    from slr_desktop.api import Api
    from slr_desktop.service import APP_NAME, ReviewService

    api = Api(ReviewService(_workspace(args)))
    window = webview.create_window(
        APP_NAME, url=str(WEB_DIR / "index.html"), js_api=api,
        width=1320, height=860, min_size=(1040, 680), background_color="#F5F6F8",
        text_select=True,
    )
    api._bind(window)
    webview.start(debug=args.debug, http_server=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
