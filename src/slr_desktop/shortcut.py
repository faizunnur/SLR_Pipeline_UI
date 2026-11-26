"""
shortcut.py -- put the app on the Desktop (and in the Start menu on Windows),
so a researcher installs once from a terminal and never needs one again.

    slr-ui --shortcut      or the button under Settings & about

Nothing here is required to run the app; it only creates a launcher that
starts `slr-desktop` (the windowed entry point pip installed).
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

NAME = "SLR Pipeline UI"


def launch_command() -> list[str]:
    """How to start the app without a console: the installed `slr-desktop`
    launcher if it can be found, else this interpreter with `-m slr_desktop`."""
    exe = Path(sys.executable)
    suffix = ".exe" if sys.platform == "win32" else ""
    for folder in (exe.parent, exe.parent / "Scripts", exe.parent / "bin"):
        candidate = folder / f"slr-desktop{suffix}"
        if candidate.is_file():
            return [str(candidate)]
    found = shutil.which("slr-desktop")
    if found:
        return [found]
    if sys.platform == "win32":
        pythonw = exe.with_name("pythonw.exe")
        return [str(pythonw if pythonw.is_file() else exe), "-m", "slr_desktop"]
    return [str(exe), "-m", "slr_desktop"]


def _windows(folders: list[Path] | None) -> list[Path]:
    cmd = launch_command()
    # Paths go through the environment, not the script text, so a folder name
    # with quotes or spaces cannot change what PowerShell runs.
    script = (
        "$ErrorActionPreference = 'Stop';"
        "$dirs = if ($env:SLR_SC_DIRS) { $env:SLR_SC_DIRS -split '\\|' } else {"
        " @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs')) };"
        "$shell = New-Object -ComObject WScript.Shell;"
        "foreach ($d in $dirs) { if (-not $d) { continue };"
        " $path = Join-Path $d ($env:SLR_SC_NAME + '.lnk');"
        " $s = $shell.CreateShortcut($path);"
        " $s.TargetPath = $env:SLR_SC_TARGET; $s.Arguments = $env:SLR_SC_ARGS;"
        " $s.Description = 'Systematic literature review desktop app';"
        " $s.Save(); Write-Output $path }"
    )
    env = {**os.environ, "SLR_SC_NAME": NAME, "SLR_SC_TARGET": cmd[0],
           "SLR_SC_ARGS": " ".join(cmd[1:]),
           "SLR_SC_DIRS": "|".join(str(f) for f in folders) if folders else ""}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    done = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, env=env, creationflags=flags, timeout=60)
    if done.returncode != 0:
        raise OSError((done.stderr or "PowerShell could not create the shortcut").strip()[:300])
    return [Path(line.strip()) for line in done.stdout.splitlines() if line.strip()]


def _quote(part: str) -> str:
    return '"' + part.replace('"', '\\"') + '"'


_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>{name}</string>
  <key>CFBundleDisplayName</key><string>{name}</string>
  <key>CFBundleIdentifier</key><string>io.github.faizunnur.slr-pipeline-ui</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
"""


def _mac(folders: list[Path] | None) -> list[Path]:
    """A small .app bundle: it shows in Launchpad/Spotlight and opens with a
    double-click, with no Terminal window. It only starts the installed app."""
    if folders is None:
        folders = [Path.home() / "Applications", Path.home() / "Desktop"]
    out = []
    for folder in folders:
        app = folder / f"{NAME}.app"
        macos = app / "Contents" / "MacOS"
        macos.mkdir(parents=True, exist_ok=True)
        (app / "Contents" / "Info.plist").write_text(_PLIST.format(name=NAME), encoding="utf-8")
        launcher = macos / "launcher"
        launcher.write_text("#!/bin/sh\nexec " + " ".join(_quote(p) for p in launch_command()) + "\n",
                            encoding="utf-8")
        launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        out.append(app)
    return out


def _linux(folders: list[Path] | None) -> list[Path]:
    if folders is None:
        folders = [Path.home() / ".local" / "share" / "applications"]
        if (Path.home() / "Desktop").is_dir():
            folders.append(Path.home() / "Desktop")
    out = []
    body = ("[Desktop Entry]\nType=Application\nName=" + NAME + "\n"
            "Comment=Systematic literature review desktop app\n"
            "Exec=" + " ".join(_quote(p) for p in launch_command()) + "\n"
            "Terminal=false\nCategories=Science;Education;\n")
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "slr-pipeline-ui.desktop"
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        out.append(path)
    return out


def create(folders: list[Path] | None = None) -> list[Path]:
    """Create the launcher(s); returns the files written. `folders` overrides
    the default locations (used by the tests)."""
    if sys.platform == "win32":
        return _windows(folders)
    if sys.platform == "darwin":
        return _mac(folders)
    return _linux(folders)
