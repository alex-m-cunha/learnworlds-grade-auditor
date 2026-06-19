#!/usr/bin/env python3
"""Course grades extractor: course_id -> course_grades.csv + report.

Usage:
    python -m extractor.run_grades --course-id <course_slug>
    python -m extractor.run_grades                 # uses COURSE_ID from .env

Output layout:
    output/<label>/
      <YYYY-MM-DD_HHmmss>/
        raw/
          raw_grades.json
          extraction_report.json
        course_grades.csv
        course_grades.xlsx

Complementary to run_extract.py. Provides the official recorded grade per learner
per assessment unit (learningUnit_id == assessment_id), as a reconciliation source.
API -> CSV only: no validation, no recomputation, no cross-CSV comparison.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from .client import LearnWorldsClient
from .config import OUTPUT_DIR, ExtractorError, load_config, slugify
from .grades import GRADE_COLUMNS, flatten_grades, get_course_grades
from .report import build_grades_report
from .users import resolve_usernames
from .writers import save_raw_response, write_csv, write_report, write_xlsx


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def run(
    course_id: str | None = None,
    label: str | None = None,
    resolve_users: bool = True,
) -> int:
    run_ts = _timestamp()
    extraction_iso = datetime.now().isoformat(timespec="seconds")

    # Course grades only needs API_URL + credentials, not ASSESSMENT_ID.
    config = load_config(require_live=False)
    missing = [
        name
        for name, key in (
            ("LEARNWORLDS_API_URL", "api_url"),
            ("LEARNWORLDS_SCHOOL_ID", "school_id"),
            ("LEARNWORLDS_ACCESS_TOKEN", "access_token"),
        )
        if not config.get(key)
    ]
    if missing:
        raise ExtractorError(
            "Course grades extraction needs these .env values: "
            + ", ".join(missing)
            + "\nAdd a VALID token to .env (this tool never creates/refreshes tokens)."
        )

    course_id = course_id or config.get("course_id")
    if not course_id:
        raise ExtractorError(
            "No course id provided. Pass --course-id <course_slug> or set "
            "COURSE_ID in .env. (It is a course slug, e.g. 'my-course-name'.)"
        )

    client = LearnWorldsClient(
        api_url=config["api_url"],
        school_id=config["school_id"],
        access_token=config["access_token"],
    )
    print(f"Calling: {config['api_url']}/v2/courses/{course_id}/grades")
    data, raw_pages, source_endpoint = get_course_grades(client, course_id)
    pages_fetched = len(raw_pages)
    print(f"Received {pages_fetched} page(s), {len(data)} grade record(s).")

    username_map: dict = {}
    username_missing: list = []
    if resolve_users:
        username_map, username_missing = resolve_usernames(
            client, [r.get("user_id") for r in data if isinstance(r, dict)]
        )

    folder = slugify(label) if label else f"course_{slugify(course_id)}"
    out_dir = OUTPUT_DIR / folder / run_ts
    raw_dir = out_dir / "raw"

    raw_file = save_raw_response(
        {"mode": "live", "pages": raw_pages}, raw_dir, prefix="raw_grades"
    )
    print(f"Saved raw response: {raw_file}")

    rows, stats = flatten_grades(
        data, course_id, source_endpoint, extraction_iso, username_map
    )
    if username_missing:
        stats["warnings"].append(
            f"{len(username_missing)} user(s) had no resolvable username "
            "(username left blank)."
        )

    output_files = {"raw_grades": str(raw_file)}
    if rows:
        csv_file = write_csv(rows, GRADE_COLUMNS, out_dir, "course_grades")
        xlsx_file = write_xlsx(rows, GRADE_COLUMNS, out_dir, "course_grades")
        output_files["course_grades_csv"] = str(csv_file)
        output_files["course_grades_xlsx"] = str(xlsx_file)
        print(f"Wrote CSV : {csv_file}  ({len(rows)} rows)")
        print(f"Wrote XLSX: {xlsx_file}")
    else:
        print("No grade records found. CSV/XLSX skipped; raw + report still written.")

    report = build_grades_report(
        run_timestamp=extraction_iso,
        course_id=course_id,
        source_endpoint=source_endpoint,
        pages_fetched=pages_fetched,
        stats=stats,
        output_files=output_files,
    )
    report_file = write_report(report, raw_dir)
    output_files["extraction_report"] = str(report_file)
    print(f"Wrote report: {report_file}")

    print(
        "Summary: "
        f"{stats['grades_count']} grade records, "
        f"{stats['rows_written']} rows, "
        f"{stats['distinct_assessment_units']} distinct assessment unit(s)."
    )
    print(f"Output folder: {out_dir}")
    print("Done.")
    return 0


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="LearnWorlds course-grades extractor (grades -> CSV + report)."
    )
    parser.add_argument(
        "--course-id",
        help="Course slug to extract grades for (defaults to COURSE_ID in .env).",
    )
    parser.add_argument(
        "--label",
        help="Label used to name the output folder (default: course_<slug>).",
    )
    parser.add_argument(
        "--no-usernames",
        action="store_true",
        help="Skip the per-user lookup that fills the 'username' column.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    debug = False
    try:
        debug = load_config(require_live=False).get("debug", False)
    except Exception:
        debug = False

    try:
        return run(
            course_id=args.course_id,
            label=args.label,
            resolve_users=not args.no_usernames,
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
