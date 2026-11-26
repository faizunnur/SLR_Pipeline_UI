"""
settings.py -- the few things the desktop app remembers between launches
(workspace folder, theme). Review data never goes here: it lives in the
workspace, so a review folder stays self-contained and shareable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def settings_path() -> Path:
    override = os.environ.get("SLR_UI_SETTINGS")
    if override:
        return Path(override)
    return Path.home() / ".slr-pipeline-ui" / "settings.json"


def default_workspace() -> Path:
    override = os.environ.get("SLR_UI_WORKSPACE")
    if override:
        return Path(override)
    documents = Path.home() / "Documents"
    return (documents if documents.is_dir() else Path.home()) / "SLR Pipeline"


def load() -> dict:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(data: dict) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def workspace() -> Path:
    stored = load().get("workspace")
    # SLR_UI_WORKSPACE wins so a test or a portable install can pin the folder.
    if os.environ.get("SLR_UI_WORKSPACE"):
        return default_workspace()
    return Path(stored) if stored else default_workspace()
