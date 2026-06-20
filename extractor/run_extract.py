#!/usr/bin/env python3
"""V1 orchestrator: assessment_id -> submissions_export.csv + extraction_report.json.

Usage:
    python -m extractor.run_extract                 # live, uses ASSESSMENT_ID from .env
    python -m extractor.run_extract --assessment-id <id>
    python -m extractor.run_extract --from-raw output/<label>/<ts>/raw/raw_response.json
                                                    # offline regression replay

Output layout (one timestamped folder per run):
    output/<label>/
      <YYYY-MM-DD_HHmmss>/
        raw/
          raw_response.json
          extraction_report.json
        submissions_export.csv
        submissions_export.xlsx

This module orchestrates only. The HTTP/auth/pagination lives in client.py, the
assessment knowledge in submissions.py, serialization in writers.py, and the run
report in report.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .client import LearnWorldsClient
from .config import OUTPUT_DIR, ExtractorError, load_config, resolve_step_dir, slugify
from .report import build_report
from .submissions import (
    SUBMISSION_COLUMNS,
    flatten_submissions,
    get_assessment_responses,
)
from .users import resolve_usernames
from .writers import save_raw_response, write_csv, write_report, write_xlsx


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def _records_from_raw(raw):
    """Extract submission records from a saved raw_response file.

    Supports both the live wrapper {"mode":..,"pages":[...]} and a bare page or
    list, so regression replays of older raw files still work.
    """
    pages = raw.get("pages") if isinstance(raw, dict) and "pages" in raw else [raw]
    records: list = []
    for page in pages:
        if isinstance(page, dict) and isinstance(page.get("data"), list):
            records.extend(page["data"])
        elif isinstance(page, list):
            records.extend(page)
    return records, len(pages)


def run(
    assessment_id: str | None = None,
    from_raw: str | None = None,
    label: str | None = None,
    resolve_users: bool = True,
    run_dir: str | None = None,
) -> int:
    run_ts = _timestamp()
    extraction_iso = datetime.now().isoformat(timespec="seconds")
    username_map: dict = {}
    username_missing: list = []

    if from_raw:
        # Offline regression: replay a saved raw response, no API call.
        config = load_config(require_live=False)
        assessment_id = assessment_id or config.get("assessment_id") or "unknown"
        raw_path = Path(from_raw)
        if not raw_path.is_absolute():
            raw_path = config_project_path(raw_path)
        if not raw_path.exists():
            raise ExtractorError(f"Raw file not found: {raw_path}")
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        data, pages_fetched = _records_from_raw(raw)
        source_endpoint = "(offline replay) " + str(raw_path.name)
        raw_to_save = raw
        print(f"Offline replay from: {raw_path}")
        # Usernames need a live API call; not available in offline replay.
    else:
        config = load_config(require_live=True)
        assessment_id = assessment_id or config["assessment_id"]
        client = LearnWorldsClient(
            api_url=config["api_url"],
            school_id=config["school_id"],
            access_token=config["access_token"],
        )
        print(
            f"Calling: {config['api_url']}/v2/assessments/"
            f"{assessment_id}/responses"
        )
        data, raw_pages, source_endpoint = get_assessment_responses(
            client, assessment_id
        )
        pages_fetched = len(raw_pages)
        raw_to_save = {"mode": "live", "pages": raw_pages}
        print(f"Received {pages_fetched} page(s), {len(data)} submission(s).")
        if resolve_users:
            username_map, username_missing = resolve_usernames(
                client, [s.get("user_id") for s in data if isinstance(s, dict)]
            )

    # Step folder: <run-dir>/submissions/ or output/<program>/<label>/<ts>/submissions/
    out_dir = resolve_step_dir(run_dir, "submissions", run_ts, config, label or "", assessment_id or "")
    raw_dir = out_dir / "raw"

    # Raw-first: always persist the raw payload before transforming.
    raw_file = save_raw_response(raw_to_save, raw_dir)
    print(f"Saved raw response: {raw_file}")

    rows, stats = flatten_submissions(
        data, assessment_id, source_endpoint, extraction_iso, username_map
    )
    if username_missing:
        stats["warnings"].append(
            f"{len(username_missing)} user(s) had no resolvable username "
            "(username left blank)."
        )

    output_files = {"raw_response": str(raw_file)}
    if rows:
        csv_file = write_csv(rows, SUBMISSION_COLUMNS, out_dir, "submissions_export")
        xlsx_file = write_xlsx(rows, SUBMISSION_COLUMNS, out_dir, "submissions_export")
        output_files["submissions_export_csv"] = str(csv_file)
        output_files["submissions_export_xlsx"] = str(xlsx_file)
        print(f"Wrote CSV : {csv_file}  ({len(rows)} rows)")
        print(f"Wrote XLSX: {xlsx_file}")
    else:
        print(
            "No rows extracted (no answer blocks found). CSV/XLSX skipped; raw + "
            "report still written."
        )

    report = build_report(
        run_timestamp=extraction_iso,
        assessment_id=assessment_id,
        source_endpoint=source_endpoint,
        pages_fetched=pages_fetched,
        rows=rows,
        stats=stats,
        output_files=output_files,
    )
    report_file = write_report(report, raw_dir)
    output_files["extraction_report"] = str(report_file)
    print(f"Wrote report: {report_file}")

    print(
        "Summary: "
        f"{stats['submissions_count']} submissions, "
        f"{stats['answer_blocks_count']} answer blocks, "
        f"{stats['rows_written']} rows."
    )
    if stats.get("warnings"):
        print(f"Warnings: {len(stats['warnings'])} (see report).")
    print(f"Output folder: {out_dir}")
    print("Done.")
    return 0


def config_project_path(rel: Path) -> Path:
    """Resolve a relative path against the project root."""
    from .config import PROJECT_ROOT

    return PROJECT_ROOT / rel


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="LearnWorlds extractor V1 (submissions -> CSV + report)."
    )
    parser.add_argument(
        "--assessment-id",
        help="Assessment id to extract (defaults to ASSESSMENT_ID in .env).",
    )
    parser.add_argument(
        "--from-raw",
        help="Replay a saved raw_response.json instead of calling the API "
        "(offline regression).",
    )
    parser.add_argument(
        "--label",
        help="Activity title/label used to name the output folder "
        "(default: the assessment id). Use the same --label across tools to "
        "co-locate outputs for one assessment.",
    )
    parser.add_argument(
        "--no-usernames",
        action="store_true",
        help="Skip the per-user lookup that fills the 'username' column "
        "(faster; username stays blank).",
    )
    parser.add_argument(
        "--run-dir",
        help="Shared run folder created by the unified launcher. When set, "
        "output goes to <run-dir>/submissions/ and no new timestamp is created.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    # Resolve DEBUG early for traceback control.
    debug = False
    try:
        cfg = load_config(require_live=False)
        debug = cfg.get("debug", False)
    except Exception:
        debug = False

    try:
        return run(
            assessment_id=args.assessment_id,
            from_raw=args.from_raw,
            label=args.label,
            resolve_users=not args.no_usernames,
            run_dir=args.run_dir,
        )
    except ExtractorError as exc:
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
    sys.exit(main())
