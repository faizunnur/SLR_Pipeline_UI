"""
release.py -- make a release with one command.

    python tools/release.py patch      # 1.0.1 -> 1.0.2   (bug fixes)
    python tools/release.py minor      # 1.0.1 -> 1.1.0   (new features)
    python tools/release.py major      # 1.0.1 -> 2.0.0   (breaking changes)
    python tools/release.py 1.4.0      # an exact version

It does the three edits a release needs, so none of them can be forgotten:

  1. sets the version in pyproject.toml,
  2. sets the version and date in CITATION.cff,
  3. turns the "Unreleased" section of CHANGELOG.md into that version's section,

then commits and pushes. The push is what releases: the `publish` workflow sees
a version with no tag yet, runs the tests, uploads to PyPI and creates the
GitHub release with the changelog section as its notes.

Day to day, write what you changed under "## [Unreleased]" in CHANGELOG.md.

Options:  --no-push    commit but do not push
          --no-commit  only edit the files
          --allow-empty  release even with nothing under "Unreleased"
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT, CITATION, CHANGELOG = ROOT / "pyproject.toml", ROOT / "CITATION.cff", ROOT / "CHANGELOG.md"
UNRELEASED = "## [Unreleased]"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def current_version() -> str:
    m = re.search(r'^version = "(\d+\.\d+\.\d+)"', PYPROJECT.read_text(encoding="utf-8"), re.M)
    if not m:
        sys.exit("Could not find `version = \"X.Y.Z\"` in pyproject.toml.")
    return m.group(1)


def next_version(current: str, bump: str) -> str:
    if re.fullmatch(r"\d+\.\d+\.\d+", bump):
        return bump
    major, minor, patch = (int(p) for p in current.split("."))
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "major":
        return f"{major + 1}.0.0"
    sys.exit("Say patch, minor, major, or an exact version such as 1.4.0.")


def replace_once(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(pattern, replacement, text, count=1, flags=re.M)
    if n != 1:
        sys.exit(f"Could not update {path.name}: pattern {pattern!r} not found.")
    path.write_text(new, encoding="utf-8", newline="\n")


def roll_changelog(version: str, today: str, allow_empty: bool) -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    if UNRELEASED not in text:
        sys.exit(f'CHANGELOG.md has no "{UNRELEASED}" section.')
    head, rest = text.split(UNRELEASED, 1)
    nxt = re.search(r"^## \[", rest, re.M)
    body, tail = (rest[:nxt.start()], rest[nxt.start():]) if nxt else (rest, "")
    if not body.strip() and not allow_empty:
        sys.exit(f'Nothing is written under "{UNRELEASED}" in CHANGELOG.md. Say what changed '
                 f"there first (or pass --allow-empty).")
    body = body.strip() or "- Maintenance release."
    CHANGELOG.write_text(f"{head}{UNRELEASED}\n\n## [{version}] - {today}\n\n{body}\n\n{tail}",
                         encoding="utf-8", newline="\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Bump the version, roll the changelog, commit and push.")
    ap.add_argument("bump", help="patch, minor, major, or an exact version like 1.4.0")
    ap.add_argument("--no-push", action="store_true")
    ap.add_argument("--no-commit", action="store_true")
    ap.add_argument("--allow-empty", action="store_true")
    args = ap.parse_args()

    old = current_version()
    new = next_version(old, args.bump)
    if tuple(map(int, new.split("."))) <= tuple(map(int, old.split("."))):
        sys.exit(f"{new} is not newer than the current version {old}.")
    if git("tag", "-l", f"v{new}"):
        sys.exit(f"Tag v{new} already exists.")
    will_commit = not args.no_commit
    if will_commit:
        if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
            sys.exit("Releases are made from the main branch. Switch to main first.")
        dirty = [line for line in git("status", "--porcelain").splitlines()
                 if line[3:] != "CHANGELOG.md"]
        if dirty:
            sys.exit("Commit or stash your other changes first, so the release commit contains "
                     "only the version change:\n  " + "\n  ".join(dirty))

    today = date.today().isoformat()
    roll_changelog(new, today, args.allow_empty)
    replace_once(PYPROJECT, r'^version = "\d+\.\d+\.\d+"', f'version = "{new}"')
    replace_once(CITATION, r'^version: "[^"]*"', f'version: "{new}"')
    replace_once(CITATION, r'^date-released: "[^"]*"', f'date-released: "{today}"')
    print(f"{old} -> {new}: updated pyproject.toml, CITATION.cff and CHANGELOG.md")

    if not will_commit:
        print("Not committed (--no-commit). Commit and push to main to release.")
        return 0
    git("add", "pyproject.toml", "CITATION.cff", "CHANGELOG.md")
    git("commit", "-m", f"Release v{new}")
    if args.no_push:
        print(f"Committed. Run `git push` to release v{new}.")
        return 0
    git("push", "origin", "main")
    print(f"Pushed. GitHub is now testing and releasing v{new}; watch the Actions tab.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
