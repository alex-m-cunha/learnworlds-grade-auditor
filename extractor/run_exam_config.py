#!/usr/bin/env python3
"""Exam-config importer (V3): UI-exported XLSX -> exam_config_as_is.csv + report.

Usage:
    python -m extractor.run_exam_config --xlsx "/path/to/export.xlsx"
    python -m extractor.run_exam_config --xlsx export.xlsx \
        --assessment-id <id> --course-id <slug> --assessment-title "Nice title"

Output layout:
    output/<label>/
      <YYYY-MM-DD_HHmmss>/
        raw/
          raw_exam_config.json
          extraction_report.json
        exam_config_as_is.csv
        exam_config_as_is.xlsx

This reads the answer key that the LearnWorlds API does NOT expose, from a manual
UI export. It performs NO API call, NO answer validation, and NO comparison with
submissions — it only normalizes the export into a clean, auditable CSV.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from .config import OUTPUT_DIR, ExtractorError, load_config, slugify
from .exam_config import EXAM_CONFIG_COLUMNS, parse_exam_config_xlsx, title_from_filename
from .report import build_exam_config_report
from .writers import save_raw_response, write_csv, write_report, write_xlsx


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M%S")


def run(
    xlsx: str,
    assessment_id: str = "",
    assessment_title: str = "",
    course_id: str = "",
    label: str = "",
) -> int:
    run_ts = _timestamp()
    extraction_iso = datetime.now().isoformat(timespec="seconds")

    xlsx_path = Path(xlsx).expanduser()
    if not xlsx_path.is_absolute():
        from .config import PROJECT_ROOT

        candidate = PROJECT_ROOT / xlsx_path
        xlsx_path = candidate if candidate.exists() else xlsx_path
    if not xlsx_path.exists():
        raise ExtractorError(f"Exam-config XLSX not found: {xlsx_path}")

    title = assessment_title or title_from_filename(xlsx_path)
    print(f"Reading exam-config export: {xlsx_path}")

    rows, raw_rows, stats = parse_exam_config_xlsx(
        xlsx_path,
        assessment_id=assessment_id,
        assessment_title=title,
        course_id=course_id,
        extraction_timestamp=extraction_iso,
    )

    # Output folder: output/<label>/<timestamp>/
    folder = slugify(label) if label else slugify(title)
    out_dir = OUTPUT_DIR / folder / run_ts
    raw_dir = out_dir / "raw"

    # Raw-first: persist the faithful parsed rows before the normalized CSV.
    raw_file = save_raw_response(
        {"source_file": xlsx_path.name, "rows": raw_rows},
        raw_dir,
        prefix="raw_exam_config",
    )
    print(f"Saved raw copy: {raw_file}")

    output_files = {"raw_exam_config": str(raw_file)}
    if rows:
        csv_file = write_csv(rows, EXAM_CONFIG_COLUMNS, out_dir, "exam_config_as_is")
        xlsx_file = write_xlsx(rows, EXAM_CONFIG_COLUMNS, out_dir, "exam_config_as_is")
        output_files["exam_config_as_is_csv"] = str(csv_file)
        output_files["exam_config_as_is_xlsx"] = str(xlsx_file)
        print(f"Wrote CSV : {csv_file}  ({len(rows)} questions)")
        print(f"Wrote XLSX: {xlsx_file}")
    else:
        print("No questions parsed. CSV/XLSX skipped; raw + report still written.")

    report = build_exam_config_report(
        run_timestamp=extraction_iso,
        source_file=xlsx_path.name,
        assessment_id=assessment_id,
        assessment_title=title,
        course_id=course_id,
        stats=stats,
        output_files=output_files,
    )
    report_file = write_report(report, raw_dir)
    output_files["extraction_report"] = str(report_file)
    print(f"Wrote report: {report_file}")

    print(
        "Summary: "
        f"{stats['questions_count']} questions, "
        f"types={stats['type_breakdown']}, "
        f"{stats['overflow_questions']} overflow."
    )
    if stats.get("warnings"):
        print(f"Warnings: {len(stats['warnings'])} (see report).")
    print(f"Output folder: {out_dir}")
    print("Done.")
    return 0


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Import a UI-exported assessment XLSX into exam_config_as_is.csv."
    )
    parser.add_argument("--xlsx", required=True, help="Path to the UI-exported XLSX.")
    parser.add_argument("--assessment-id", default="", help="Stamp this assessment id.")
    parser.add_argument("--course-id", default="", help="Stamp this course id/slug.")
    parser.add_argument(
        "--assessment-title", default="", help="Override the assessment title."
    )
    parser.add_argument(
        "--label",
        default="",
        help="Override the output folder name (default: a slug of the title). "
        "Use the same --label as run_extract to co-locate outputs.",
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
            xlsx=args.xlsx,
            assessment_id=args.assessment_id,
            assessment_title=args.assessment_title,
            course_id=args.course_id,
            label=args.label,
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
