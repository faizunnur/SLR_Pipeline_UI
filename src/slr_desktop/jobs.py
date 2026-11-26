"""
jobs.py -- background task runner for the desktop app.

Long stages (search, PDF download, citation verification, figures) run the
same stage scripts the terminal app runs, as child processes. The window must
stay responsive while they work, so each task runs on a worker thread and
collects its output line by line; the front end polls `status()` for new
lines instead of blocking on the call.

Only one task runs at a time. Every stage writes to the run folder, and two
stages writing the same CSV at once is exactly the kind of silent corruption
the pipeline's checkpoints exist to prevent.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Callable

# Values passed on a command line that must never be echoed into the log pane.
_SECRET_FLAGS = ("--scopus-insttoken",)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


class JobCancelled(Exception):
    """Raised inside a task when the user pressed Cancel."""


def redact_cmd(cmd: list) -> str:
    """The command as shown in the log pane, with secret flag values masked."""
    out = []
    hide_next = False
    for part in cmd:
        part = str(part)
        if hide_next:
            out.append("****")
            hide_next = False
            continue
        out.append(f'"{part}"' if " " in part else part)
        if part in _SECRET_FLAGS:
            hide_next = True
    return " ".join(out)


@dataclass
class Job:
    id: str
    title: str
    run_id: str | None
    status: str = "running"          # running | done | failed | cancelled
    lines: list = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    _proc: subprocess.Popen | None = None
    _cancel: threading.Event = field(default_factory=threading.Event)


class JobContext:
    """What a task function receives: a logger and a child-process runner."""

    def __init__(self, job: Job, cwd: str | None):
        self._job = job
        self._cwd = cwd

    @property
    def cancelled(self) -> bool:
        return self._job._cancel.is_set()

    def check_cancel(self) -> None:
        if self.cancelled:
            raise JobCancelled()

    def log(self, line: str = "") -> None:
        for part in str(line).splitlines() or [""]:
            self._job.lines.append(part)

    def run(self, cmd: list, description: str, success_codes=(0,),
            secret_env: dict | None = None) -> bool:
        """Run one stage script; stream its output; True on a success code.

        secret_env goes into the child's environment, never its argv, matching
        the terminal app's rule that API keys stay out of the process table.
        """
        self.check_cancel()
        env = {**os.environ, **(secret_env or {}),
               "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        self.log(f"▶ {description}")
        self.log(f"$ {redact_cmd(cmd)}")
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
                cwd=self._cwd, creationflags=_NO_WINDOW,
            )
        except OSError as e:
            self.log(f"✖ could not start: {e}")
            return False
        self._job._proc = proc
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                self.log(_strip_ansi(line.rstrip("\n")))
            proc.wait()
        finally:
            self._job._proc = None
        if self.cancelled:
            raise JobCancelled()
        if proc.returncode in success_codes:
            self.log(f"✔ {description}")
            return True
        self.log(f"✖ {description} failed (exit {proc.returncode})")
        return False


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


class JobManager:
    def __init__(self, cwd: str | None = None):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self.cwd = cwd

    def busy(self) -> Job | None:
        with self._lock:
            for job in self._jobs.values():
                if job.status == "running":
                    return job
        return None

    def start(self, title: str, fn: Callable[[JobContext], dict | None],
              run_id: str | None = None) -> Job:
        with self._lock:
            for job in self._jobs.values():
                if job.status == "running":
                    raise RuntimeError(f"'{job.title}' is still running. Wait for it to "
                                       f"finish, or cancel it first.")
            job = Job(id=uuid.uuid4().hex[:12], title=title, run_id=run_id)
            self._jobs[job.id] = job

        def _worker():
            ctx = JobContext(job, self.cwd)
            try:
                job.result = fn(ctx) or {}
                if job.status == "running":
                    job.status = "done"
            except JobCancelled:
                job.status = "cancelled"
                ctx.log("■ cancelled")
            except Exception as e:  # noqa: BLE001 -- surface every failure in the UI
                job.status = "failed"
                job.error = str(e) or e.__class__.__name__
                ctx.log(f"✖ {job.error}")
                ctx.log(traceback.format_exc())

        threading.Thread(target=_worker, name=f"job-{job.id}", daemon=True).start()
        return job

    def status(self, job_id: str, since: int = 0) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            return {"id": job_id, "status": "unknown", "lines": [], "next": since}
        lines = job.lines[since:]
        return {
            "id": job.id, "title": job.title, "run_id": job.run_id, "status": job.status,
            "lines": lines, "next": since + len(lines), "result": job.result,
            "error": job.error,
        }

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job is None or job.status != "running":
            return False
        job._cancel.set()
        proc = job._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
        return True
