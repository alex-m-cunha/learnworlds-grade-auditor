#!/usr/bin/env python3
"""
LearnWorlds Assessment Responses Exporter
=========================================

Internal tool for the Nova SBE Executive Education LMS team.

Pipeline (modular, raw-response-first):
    load_config()
    fetch_raw_responses_live()      (live mode only)
    load_raw_responses_from_file()  (offline mode only)
    save_raw_response()
    normalize_responses()
    export_csv_xlsx()
    main()

Two modes, selected by EXPORT_MODE in .env:
    offline  -> read a local JSON sample, run the full pipeline (no API call)
    live     -> call GET {API_URL}/v2/assessments/{ASSESSMENT_ID}/responses

Token policy: this tool NEVER creates, refreshes, revokes, or generates access
tokens, and never calls an auth endpoint. Live mode only works when a valid
existing token is manually placed in .env.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths are always resolved relative to THIS script's folder, never the current
# working directory. This keeps the tool correct even when launched by double
# click from a OneDrive folder whose path contains spaces and accented chars.
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "output"

# Output column order (one row = one learner answer to one question).
COLUMNS = [
    "learner_email",
    "final_score",
    "question_text",
    "submitted_answer",
    "points_earned",
    "max_points",
    "status",
]

# Candidate keys for each output field (first present, non-empty key wins).
FIELD_KEYS = {
    "learner_email": ["email", "learner_email", "userEmail"],
    "final_score": ["grade", "final_score", "score"],
    "question_text": ["description", "question_text", "question", "title"],
    "submitted_answer": ["answer", "submitted_answer", "response"],
    "points_earned": ["points", "points_earned", "score"],
    "max_points": ["blockMaxScore", "max_points", "maxScore"],
}

# Nested-path candidates for the learner email (e.g. user.email / learner.email).
EMAIL_NESTED_PATHS = [
    ("user", "email"),
    ("learner", "email"),
    ("student", "email"),
]

# Keys whose value (a list) holds the per-question / per-block records.
BLOCK_KEYS = ["blocks", "answers", "questions", "responses", "questionBlocks"]

# Common wrapper keys around the list of learner/answer records.
CONTAINER_KEYS = ["data", "results", "items", "responses"]

# Common pagination keys.
MAX_PAGES = 500


class ExporterError(Exception):
    """User-facing error: printed as a clean message, no traceback (unless DEBUG)."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _env_bool(value: str | None) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _first_value(record: dict, keys: list[str]):
    """Return the first present, non-empty value among `keys` in `record`."""
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def _nested_value(record: dict, paths: list[tuple]):
    """Return the first present value for a list of nested key paths."""
    for path in paths:
        node = record
        ok = True
        for key in path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                ok = False
                break
        if ok and node not in (None, ""):
            return node
    return None


def _to_number(value):
    """Coerce to int/float when possible; return None on failure or missing."""
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        text = str(value).strip().replace(",", ".")
        number = float(text)
        return int(number) if number.is_integer() else number
    except (ValueError, TypeError):
        return None


def _compute_status(points_earned, max_points) -> str:
    """correct if points == max, incorrect if points == 0, else partial.

    Returns "" when the numbers are not usable.
    """
    earned = _to_number(points_earned)
    maximum = _to_number(max_points)
    if earned is None or maximum is None:
        return ""
    if earned == 0:
        return "incorrect"
    if earned == maximum:
        return "correct"
    return "partial"


# ---------------------------------------------------------------------------
# 1. load_config
# ---------------------------------------------------------------------------
def load_config() -> dict:
    """Load and validate configuration from .env (resolved next to this script)."""
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise ExporterError(
            "Missing dependency 'python-dotenv'. Install requirements first:\n"
            "    pip install -r requirements.txt"
        ) from exc

    import os

    env_path = SCRIPT_DIR / ".env"
    if not env_path.exists():
        raise ExporterError(
            f"No .env file found at: {env_path}\n"
            "Copy .env.example to .env and fill in the values "
            "(EXPORT_MODE=offline works out of the box)."
        )
    load_dotenv(env_path)

    mode = (os.getenv("EXPORT_MODE") or "offline").strip().lower()
    if mode not in {"offline", "live"}:
        raise ExporterError(
            f"EXPORT_MODE must be 'offline' or 'live' (got: '{mode}')."
        )

    api_url = (os.getenv("LEARNWORLDS_API_URL") or "").strip().rstrip("/")

    config = {
        "mode": mode,
        "debug": _env_bool(os.getenv("DEBUG")),
        "api_url": api_url,
        "school_id": (os.getenv("LEARNWORLDS_SCHOOL_ID") or "").strip(),
        "access_token": (os.getenv("LEARNWORLDS_ACCESS_TOKEN") or "").strip(),
        "assessment_id": (os.getenv("ASSESSMENT_ID") or "").strip(),
        "input_json_path": (
            os.getenv("INPUT_JSON_PATH") or "input/sample_response.json"
        ).strip(),
    }

    if mode == "offline":
        # Only the input path is required; never require token/school in offline.
        input_path = Path(config["input_json_path"])
        if not input_path.is_absolute():
            input_path = SCRIPT_DIR / input_path
        config["input_path_resolved"] = input_path
    else:  # live
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
            raise ExporterError(
                "Live mode is missing required .env values: "
                + ", ".join(missing)
                + "\n\nLive mode requires a VALID LearnWorlds Access Token added "
                "manually to .env. This tool does not create or refresh tokens.\n"
                "If no valid token is available, set EXPORT_MODE=offline."
            )

    return config


# ---------------------------------------------------------------------------
# 2. fetch_raw_responses_live
# ---------------------------------------------------------------------------
def _detect_next_request(page: dict, base_url: str):
    """Inspect a raw page for common pagination markers.

    Returns either an absolute URL (str) for the next page, or None when there
    is no further page. Defensive: unknown shapes are treated as single-page.
    """
    if not isinstance(page, dict):
        return None

    # Direct "next"-style keys holding a URL.
    for key in ("next", "nextPage", "next_page"):
        value = page.get(key)
        if isinstance(value, str) and value:
            return value

    # links.next / meta.next nested under common containers.
    for container in ("links", "meta", "pagination", "_links"):
        node = page.get(container)
        if isinstance(node, dict):
            nxt = node.get("next") or node.get("nextPage") or node.get("next_page")
            if isinstance(nxt, str) and nxt:
                return nxt
            if isinstance(nxt, dict) and isinstance(nxt.get("href"), str):
                return nxt["href"]

    # page / total_pages numeric style.
    meta = page.get("meta") if isinstance(page.get("meta"), dict) else page
    current = _to_number(meta.get("page"))
    total = _to_number(meta.get("total_pages") or meta.get("totalPages"))
    if current is not None and total is not None and current < total:
        sep = "&" if "?" in base_url else "?"
        return f"{base_url}{sep}page={int(current) + 1}"

    return None


def _extract_records(payload):
    """Return the list of records inside a payload, unwrapping known containers."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in CONTAINER_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def fetch_raw_responses_live(config: dict):
    """Call the LearnWorlds endpoint and follow pagination defensively.

    Returns (combined_data, raw_pages):
        combined_data : list of records merged across all pages (for normalize)
        raw_pages     : list of every raw page response received (for raw save)
    """
    try:
        import requests
    except ImportError as exc:
        raise ExporterError(
            "Missing dependency 'requests'. Install requirements first:\n"
            "    pip install -r requirements.txt"
        ) from exc

    endpoint = (
        f"{config['api_url']}/v2/assessments/"
        f"{config['assessment_id']}/responses"
    )
    headers = {
        "Authorization": f"Bearer {config['access_token']}",
        "Lw-Client": config["school_id"],
        "Accept": "application/json",
    }

    raw_pages: list = []
    combined_data: list = []
    url = endpoint
    print(f"Calling LearnWorlds API: {endpoint}")

    for page_number in range(1, MAX_PAGES + 1):
        try:
            response = requests.get(url, headers=headers, timeout=60)
        except requests.RequestException as exc:
            raise ExporterError(
                f"Network error contacting LearnWorlds API:\n    {exc}"
            ) from exc

        if response.status_code in (401, 403):
            raise ExporterError(
                "Authentication failed (HTTP "
                f"{response.status_code}). The LearnWorlds Access Token is "
                "missing, expired, or invalid.\n"
                "A VALID LearnWorlds Access Token must be added manually to "
                ".env. This tool does not create or refresh tokens.\n"
                "If no valid token is available, set EXPORT_MODE=offline."
            )
        if response.status_code >= 400:
            snippet = response.text[:500] if response.text else "(no body)"
            raise ExporterError(
                f"LearnWorlds API returned HTTP {response.status_code}.\n"
                f"Response: {snippet}"
            )

        try:
            page = response.json()
        except ValueError as exc:
            raise ExporterError(
                "LearnWorlds API did not return valid JSON.\n"
                f"First 500 chars: {response.text[:500]}"
            ) from exc

        raw_pages.append(page)
        combined_data.extend(_extract_records(page))

        next_url = _detect_next_request(page, endpoint)
        if not next_url:
            break
        if page_number == MAX_PAGES:
            print(
                f"WARNING: reached the {MAX_PAGES}-page safety limit; "
                "stopping pagination."
            )
            break
        url = next_url

    print(f"Received {len(raw_pages)} page(s), {len(combined_data)} record(s).")
    return combined_data, raw_pages


# ---------------------------------------------------------------------------
# 3. load_raw_responses_from_file
# ---------------------------------------------------------------------------
def load_raw_responses_from_file(config: dict):
    """Read INPUT_JSON_PATH and return the loaded JSON UNCHANGED (offline mode)."""
    input_path: Path = config["input_path_resolved"]
    if not input_path.exists():
        raise ExporterError(
            f"Input JSON file not found: {input_path}\n"
            "Set INPUT_JSON_PATH in .env or place a sample at that location."
        )
    try:
        with input_path.open("r", encoding="utf-8") as handle:
            raw_json = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ExporterError(
            f"Could not read/parse input JSON ({input_path}):\n    {exc}"
        ) from exc

    print(f"Loaded offline sample: {input_path}")
    return raw_json


# ---------------------------------------------------------------------------
# 4. save_raw_response
# ---------------------------------------------------------------------------
def save_raw_response(raw, output_dir: Path) -> Path:
    """Always persist the raw response before normalizing (audit trail)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / f"raw_response_{_timestamp()}.json"
    with raw_path.open("w", encoding="utf-8") as handle:
        json.dump(raw, handle, ensure_ascii=False, indent=2)
    print(f"Saved raw response: {raw_path}")
    return raw_path


# ---------------------------------------------------------------------------
# 5. normalize_responses
# ---------------------------------------------------------------------------
def _row_from_mapping(source: dict, learner_email=None, final_score=None) -> dict:
    """Build one output row from a record using FIELD_KEYS, with overrides."""
    email = learner_email
    if email is None:
        email = _first_value(source, FIELD_KEYS["learner_email"])
    if email is None:
        email = _nested_value(source, EMAIL_NESTED_PATHS)

    score = final_score
    if score is None:
        score = _first_value(source, FIELD_KEYS["final_score"])

    points_earned = _first_value(source, FIELD_KEYS["points_earned"])
    max_points = _first_value(source, FIELD_KEYS["max_points"])

    return {
        "learner_email": email if email is not None else "",
        "final_score": score if score is not None else "",
        "question_text": _first_value(source, FIELD_KEYS["question_text"]) or "",
        "submitted_answer": _first_value(source, FIELD_KEYS["submitted_answer"]) or "",
        "points_earned": points_earned if points_earned is not None else "",
        "max_points": max_points if max_points is not None else "",
        "status": _compute_status(points_earned, max_points),
    }


def _is_useful_row(row: dict) -> bool:
    """A row is worth keeping if it carries identifying or answer content."""
    meaningful = (
        row["learner_email"],
        row["question_text"],
        row["submitted_answer"],
        row["points_earned"],
    )
    return any(value not in (None, "") for value in meaningful)


def _find_blocks(record: dict):
    """Return the first nested list of per-question blocks, if any."""
    for key in BLOCK_KEYS:
        value = record.get(key)
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
    return None


def normalize_responses(raw) -> list[dict]:
    """Flatten a raw response into output rows. Supports nested and flat shapes."""
    records = _extract_records(raw)
    if not records and isinstance(raw, dict):
        # A single learner/answer object provided directly.
        records = [raw]

    if not records:
        print(
            "WARNING: could not find a list of records in the response. "
            "The parser may need adjusting against a real API sample.\n"
            "         (The raw JSON has been saved for inspection.)"
        )
        return []

    rows: list[dict] = []
    skipped = 0

    for record in records:
        if not isinstance(record, dict):
            skipped += 1
            continue

        blocks = _find_blocks(record)
        if blocks is not None:
            # Shape A: learner-level record with nested question blocks.
            learner_email = _first_value(record, FIELD_KEYS["learner_email"])
            if learner_email is None:
                learner_email = _nested_value(record, EMAIL_NESTED_PATHS)
            final_score = _first_value(record, FIELD_KEYS["final_score"])
            for block in blocks:
                if not isinstance(block, dict):
                    skipped += 1
                    continue
                row = _row_from_mapping(block, learner_email, final_score)
                if _is_useful_row(row):
                    rows.append(row)
                else:
                    skipped += 1
        else:
            # Shape B: flat record already representing one answer/question.
            row = _row_from_mapping(record)
            if _is_useful_row(row):
                rows.append(row)
            else:
                skipped += 1

    if not rows:
        print(
            "WARNING: no usable rows could be extracted from the response.\n"
            "         The expected fields were not found. The parser likely "
            "needs adjusting\n         once a real API response sample is "
            "available. Raw JSON has been saved."
        )
    elif skipped:
        print(f"Note: skipped {skipped} record(s) without usable fields.")

    return rows


# ---------------------------------------------------------------------------
# 6. export_csv_xlsx
# ---------------------------------------------------------------------------
def export_csv_xlsx(rows: list[dict], output_dir: Path):
    """Write timestamped CSV + XLSX (never overwriting) from normalized rows."""
    if not rows:
        print(
            "No normalized rows were extracted — skipping CSV/XLSX export.\n"
            "The raw response was still saved for later parser adjustment."
        )
        return None, None

    try:
        import pandas as pd
    except ImportError as exc:
        raise ExporterError(
            "Missing dependency 'pandas'. Install requirements first:\n"
            "    pip install -r requirements.txt"
        ) from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows, columns=COLUMNS)

    stamp = _timestamp()
    csv_path = output_dir / f"assessment_responses_{stamp}.csv"
    xlsx_path = output_dir / f"assessment_responses_{stamp}.xlsx"

    frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
    try:
        frame.to_excel(xlsx_path, index=False, engine="openpyxl")
    except ImportError as exc:
        raise ExporterError(
            "Missing dependency 'openpyxl' (needed for XLSX). Install "
            "requirements first:\n    pip install -r requirements.txt"
        ) from exc

    print(f"Exported {len(frame)} row(s).")
    print(f"  CSV : {csv_path}")
    print(f"  XLSX: {xlsx_path}")
    return csv_path, xlsx_path


# ---------------------------------------------------------------------------
# 7. main
# ---------------------------------------------------------------------------
def main() -> int:
    config = load_config()
    print(f"Mode: {config['mode']}")

    if config["mode"] == "live":
        combined_data, raw_pages = fetch_raw_responses_live(config)
        save_raw_response({"mode": "live", "pages": raw_pages}, OUTPUT_DIR)
        # Normalize the combined payload, not the {"mode","pages"} wrapper.
        to_normalize = combined_data if combined_data else raw_pages
        rows = normalize_responses(to_normalize)
    else:
        raw_json = load_raw_responses_from_file(config)
        save_raw_response(raw_json, OUTPUT_DIR)
        rows = normalize_responses(raw_json)

    export_csv_xlsx(rows, OUTPUT_DIR)
    print("Done.")
    return 0


def _run() -> int:
    """Entry point with friendly error handling (no traceback unless DEBUG)."""
    debug = False
    try:
        # Peek at DEBUG early so even load_config errors can show a traceback.
        import os

        from dotenv import load_dotenv  # noqa: F401  (best-effort)

        env_path = SCRIPT_DIR / ".env"
        if env_path.exists():
            from dotenv import load_dotenv as _ld

            _ld(env_path)
        debug = _env_bool(os.getenv("DEBUG"))
    except Exception:
        debug = False

    try:
        return main()
    except ExporterError as exc:
        print(f"\nERROR: {exc}\n", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        if debug:
            raise
        print(
            f"\nUNEXPECTED ERROR: {exc}\n"
            "Set DEBUG=true in .env to see the full traceback.\n",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(_run())
