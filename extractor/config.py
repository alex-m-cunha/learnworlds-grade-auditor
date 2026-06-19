"""Configuration loading for the LearnWorlds extractor (V1).

Reads .env from the PROJECT ROOT (the folder above this package), so it shares
the same .env as the legacy exporter. Paths are resolved relative to the project
root, never the current working directory — safe for OneDrive folders with
spaces / accented characters.
"""

from __future__ import annotations

import os
import re
from pathlib import Path


def slugify(text: str) -> str:
    """Make a filesystem-safe folder/file name (keeps unicode letters/accents)."""
    text = re.sub(r"[\s/\\]+", "-", str(text).strip())
    text = re.sub(r"[^\w\-]+", "", text, flags=re.UNICODE)
    text = re.sub(r"-+", "-", text).strip("-").lower()
    return text or "untitled"

# This file lives in <project>/extractor/config.py → project root is parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
OUTPUT_DIR = PROJECT_ROOT / "output"


class ExtractorError(Exception):
    """User-facing error: printed as a clean message (no traceback unless DEBUG)."""


def _env_bool(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load_config(require_live: bool = True) -> dict:
    """Load and validate configuration from .env.

    The extractor always talks to the live API (its purpose is a fresh, auditable
    pull), so by default it requires the live credentials. `require_live=False`
    is used by offline regression tests that replay a saved raw_response file.
    """
    try:
        from dotenv import load_dotenv
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise ExtractorError(
            "Missing dependency 'python-dotenv'. Install requirements first:\n"
            "    pip install -r requirements.txt"
        ) from exc

    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        raise ExtractorError(
            f"No .env file found at: {env_path}\n"
            "Copy .env.example to .env and fill in the live credentials."
        )
    load_dotenv(env_path)

    config = {
        "debug": _env_bool(os.getenv("DEBUG")),
        "api_url": (os.getenv("LEARNWORLDS_API_URL") or "").strip().rstrip("/"),
        "school_id": (os.getenv("LEARNWORLDS_SCHOOL_ID") or "").strip(),
        "access_token": (os.getenv("LEARNWORLDS_ACCESS_TOKEN") or "").strip(),
        "assessment_id": (os.getenv("ASSESSMENT_ID") or "").strip(),
        # Reserved for V2 (course discovery); read but not used in V1.
        "course_id": (os.getenv("COURSE_ID") or "").strip(),
    }

    if require_live:
        missing = [
            name
            for name, key in (
                ("LEARNWORLDS_API_URL", "api_url"),
                ("LEARNWORLDS_SCHOOL_ID", "school_id"),
                ("LEARNWORLDS_ACCESS_TOKEN", "access_token"),
                ("ASSESSMENT_ID", "assessment_id"),
            )
            if not config[key]
        ]
        if missing:
            raise ExtractorError(
                "The extractor needs these .env values: "
                + ", ".join(missing)
                + "\n\nA VALID LearnWorlds Access Token must be added manually to "
                ".env. This tool never creates or refreshes tokens."
            )

    return config
