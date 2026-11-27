"""
api.py -- the bridge the front end calls (`window.pywebview.api.<name>(...)`).

Every method returns {"ok": True, "data": ...} or {"ok": False, "error": "..."},
so the JavaScript side has one way to handle a result and never sees a Python
traceback as an unhandled promise rejection. The pipeline logic itself lives in
service.py; this file only adapts calls, opens native dialogs, and hands files
to the operating system.
"""
from __future__ import annotations

import functools
import os
import subprocess
import sys
import traceback
import webbrowser
from pathlib import Path

from slr_desktop import settings
from slr_desktop.service import ReviewService, ServiceError


def _safe(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return {"ok": True, "data": fn(self, *args, **kwargs)}
        except ServiceError as e:
            return {"ok": False, "error": str(e)}
        except RuntimeError as e:  # JobManager: a task is already running
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001 -- report, never crash the window
            traceback.print_exc()
            return {"ok": False, "error": f"{e.__class__.__name__}: {e}"}
    return wrapper


def _job(job) -> dict:
    return {"job_id": job.id, "title": job.title}


class Api:
    """Public methods only -- pywebview exposes every non-underscore attribute."""

    def __init__(self, service: ReviewService):
        self._svc = service
        self._window = None

    def _bind(self, window) -> None:
        self._window = window

    # --- app ---------------------------------------------------------------
    @_safe
    def app_info(self):
        info = self._svc.app_info()
        stored = settings.load()
        info["theme"] = stored.get("theme", "system")
        info["shortcut_done"] = bool(stored.get("shortcut_done"))
        info["shortcut_dismissed"] = bool(stored.get("shortcut_dismissed"))
        return info

    @_safe
    def create_shortcut(self):
        from slr_desktop import shortcut
        try:
            paths = shortcut.create()
        except (OSError, subprocess.SubprocessError) as e:
            raise ServiceError(f"Could not create the shortcut: {e}")
        data = settings.load()
        data["shortcut_done"] = True
        settings.save(data)
        return [str(p) for p in paths]

    @_safe
    def dismiss_shortcut_tip(self):
        data = settings.load()
        data["shortcut_dismissed"] = True
        settings.save(data)
        return True

    @_safe
    def start_example(self):
        job, run_id = self._svc.start_example()
        return {"job_id": job.id, "title": job.title, "run_id": run_id}

    @_safe
    def set_theme(self, theme):
        data = settings.load()
        data["theme"] = theme if theme in ("light", "dark", "system") else "system"
        settings.save(data)
        return data["theme"]

    @_safe
    def choose_workspace(self):
        if self._window is None:
            raise ServiceError("In browser mode, start the app with --workspace <folder> to "
                               "change the folder.")
        import webview
        kind = getattr(getattr(webview, "FileDialog", None), "FOLDER", None) or webview.FOLDER_DIALOG
        picked = self._window.create_file_dialog(kind, directory=str(self._svc.workspace))
        if not picked:
            return None
        folder = picked[0] if isinstance(picked, (list, tuple)) else picked
        if self._svc.jobs.busy():
            raise ServiceError("A task is running; wait for it to finish before changing the folder.")
        self._svc.set_workspace(Path(folder))
        data = settings.load()
        data["workspace"] = str(self._svc.workspace)
        settings.save(data)
        return self._svc.app_info()

    @_safe
    def choose_csv(self):
        if self._window is None:
            raise ServiceError("In browser mode, type or paste the file's path instead.")
        import webview
        kind = getattr(getattr(webview, "FileDialog", None), "OPEN", None) or webview.OPEN_DIALOG
        picked = self._window.create_file_dialog(
            kind, directory=str(self._svc.runs_dir),
            file_types=("CSV files (*.csv)", "All files (*.*)"))
        if not picked:
            return None
        return picked[0] if isinstance(picked, (list, tuple)) else picked

    @_safe
    def choose_import_file(self):
        """Pick a file exported from a database website (RIS, BibTeX, CSV, PubMed)."""
        if self._window is None:
            raise ServiceError("In browser mode, type or paste the file's path instead.")
        import webview
        kind = getattr(getattr(webview, "FileDialog", None), "OPEN", None) or webview.OPEN_DIALOG
        picked = self._window.create_file_dialog(
            kind, directory=str(Path.home() / "Downloads"),
            file_types=("Reference exports (*.ris;*.bib;*.csv;*.nbib;*.txt;*.tsv)", "All files (*.*)"))
        if not picked:
            return None
        return picked[0] if isinstance(picked, (list, tuple)) else picked

    @_safe
    def start_import(self, run_id, phase, path, source, query="", searched_on=""):
        return _job(self._svc.start_import(run_id, phase, path, source, query, searched_on))

    @_safe
    def open_path(self, path):
        """Open a file or folder of the workspace with the system's default app."""
        p = Path(path).resolve()
        root = self._svc.workspace.resolve()
        if p != root and root not in p.parents:
            raise ServiceError("Only files inside the workspace can be opened from here.")
        if not p.exists():
            raise ServiceError(f"{p.name} does not exist yet.")
        if sys.platform == "win32":
            os.startfile(str(p))  # noqa: S606 -- a path inside the user's own workspace
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
        return True

    @_safe
    def open_url(self, url):
        if not str(url).startswith(("https://", "http://")):
            raise ServiceError("Not a web link.")
        webbrowser.open(url)
        return True

    @_safe
    def check_update(self):
        from srp.update_check import is_newer, latest_release_version
        from srp import __version__ as version
        if version.startswith("0.0.0-dev"):
            return {"outcome": "dev_build", "current": version, "latest": None}
        latest = latest_release_version()
        if latest is None:
            return {"outcome": "check_failed", "current": version, "latest": None}
        outcome = "outdated" if is_newer(latest, version) else "up_to_date"
        return {"outcome": outcome, "current": version, "latest": latest}

    # --- jobs ----------------------------------------------------------------
    @_safe
    def job_status(self, job_id, since=0):
        return self._svc.jobs.status(job_id, int(since or 0))

    @_safe
    def job_cancel(self, job_id):
        return self._svc.jobs.cancel(job_id)

    @_safe
    def job_busy(self):
        job = self._svc.jobs.busy()
        return {"job_id": job.id, "title": job.title, "run_id": job.run_id} if job else None

    @_safe
    def start_search(self, run_id, phase, rerun=False):
        return _job(self._svc.start_search(run_id, phase, bool(rerun)))

    @_safe
    def start_download(self, run_id):
        return _job(self._svc.start_download(run_id))

    @_safe
    def start_verify(self, run_id):
        return _job(self._svc.start_verify(run_id))

    @_safe
    def start_extract(self, run_id):
        return _job(self._svc.start_extract(run_id))

    @_safe
    def start_figures(self, run_id):
        return _job(self._svc.start_figures(run_id))

    @_safe
    def start_snowball(self, run_id, direction="both", max_per_seed=50):
        return _job(self._svc.start_snowball(run_id, direction, max_per_seed))


def _forward(name: str):
    """Expose a ReviewService method unchanged, wrapped in the ok/error envelope."""
    @_safe
    def method(self, *args):
        return getattr(self._svc, name)(*args)
    method.__name__ = name
    return method


for _name in (
    "wizard_options", "list_runs", "create_run", "preview_query", "get_run",
    "update_search_settings", "search_strategy", "get_screening", "set_ta_decision",
    "assist_batch", "assist_apply", "assist_finish", "get_gate", "apply_gate",
    "expansion_terms", "apply_expansion", "merge", "get_fulltext", "set_ft_decision",
    "finish_fulltext", "verification_results", "figures_check", "figures_skip", "figures",
    "merge_snowball", "get_snowball_screening", "set_snowball_decision", "get_extraction",
    "update_extraction", "export_references", "export_exclusions", "write_provenance",
    "write_methods", "write_self_appraisal", "kappa", "diagnose", "provenance_events",
    "outputs", "read_text", "api_keys", "set_api_key", "delete_api_key", "delete_run",
    "preview_import", "list_imports", "set_screening_aids", "restore_decision",
):
    setattr(Api, _name, _forward(_name))
