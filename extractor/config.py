"""Configuration loading for the LearnWorlds extractor.

Priority for non-sensitive settings (ASSESSMENT_ID, COURSE_ID, LABEL):
    assessment.cfg  →  .env  →  CLI flag

assessment.cfg lives at the project root, is committed to git, and is safe
to share — it contains only identifiers, never credentials.

Credentials (API_URL, SCHOOL_ID, ACCESS_TOKEN, OPENAI_API_KEY) live only in
.env, which is gitignored.

Paths are resolved relative to the project root (never cwd) — safe for
OneDrive folders with spaces / accented characters.
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


def resolve_step_dir(
    run_dir: str | None,
    step_name: str,
    run_ts: str,
    config: dict,
    label: str = "",
    assessment_id: str = "",
) -> "Path":
    """Return the output directory for one pipeline step.

    With --run-dir (unified launcher): <run-dir>/<step_name>/
    Without (standalone CLI): output/<program>/<label>/<run-ts>/<step_name>/
    """
    if run_dir:
        return Path(run_dir) / step_name
    program = config.get("program", "")
    folder = slugify(label or config.get("label", "")) or assessment_id or "unknown"
    base = OUTPUT_DIR / slugify(program) / folder if program else OUTPUT_DIR / folder
    return base / run_ts / step_name


def _env_bool(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _load_cfg_file(path: Path) -> dict[str, str]:
    """Parse a key=value file (dotenv format), ignoring comments and blanks."""
    try:
        from dotenv import dotenv_values
        return {k: (v or "") for k, v in dotenv_values(path).items()}
    except ImportError:
        return {}
    except Exception:
        return {}


def load_config(require_live: bool = True) -> dict:
    """Load and validate configuration.

    Reading order for each key (first non-empty value wins):
        assessment.cfg  →  .env

    assessment.cfg holds non-sensitive identifiers (ASSESSMENT_ID, COURSE_ID, LABEL).
    .env holds credentials and everything else. `require_live=False` is used by
    offline regression replays that don't need live API credentials.
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

    # assessment.cfg overrides .env for the non-sensitive keys
    cfg = _load_cfg_file(PROJECT_ROOT / "assessment.cfg")

    def _get(key: str) -> str:
        """assessment.cfg first, then os.environ (.env already loaded into it)."""
        return (cfg.get(key) or os.getenv(key) or "").strip()

    config = {
        "debug": _env_bool(os.getenv("DEBUG")),
        "api_url": (os.getenv("LEARNWORLDS_API_URL") or "").strip().rstrip("/"),
        "school_id": (os.getenv("LEARNWORLDS_SCHOOL_ID") or "").strip(),
        "access_token": (os.getenv("LEARNWORLDS_ACCESS_TOKEN") or "").strip(),
        "assessment_id": _get("ASSESSMENT_ID"),
        "course_id": _get("COURSE_ID"),
        "label": _get("LABEL"),
        "program": _get("PROGRAM"),
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
