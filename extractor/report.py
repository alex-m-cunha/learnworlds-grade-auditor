"""Extraction report builder.

Produces a plain dict (serialized by writers.write_report) describing what was
extracted. NEVER contains the access token, school id, or any credential.
"""

from __future__ import annotations

EXAM_CONFIG_LIMITATION = (
    "exam_config (assessment configuration / questions / configured correct "
    "answers) is NOT available from the API. It is imported separately from a "
    "manual LearnWorlds UI export via `python -m extractor.run_exam_config`."
)


def _breakdown(rows: list[dict]) -> dict:
    counts = {
        "full_score": 0,
        "partial_score": 0,
        "zero_score": 0,
        "score_unavailable": 0,
    }
    for row in rows:
        key = row.get("derived_score_status")
        if key in counts:
            counts[key] += 1
    return counts


def build_report(
    *,
    run_timestamp: str,
    assessment_id: str,
    source_endpoint: str,
    pages_fetched: int,
    rows: list[dict],
    stats: dict,
    output_files: dict,
    extra_warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> dict:
    """Assemble the extraction report. No credentials included."""
    warnings = list(stats.get("warnings", []))
    if extra_warnings:
        warnings.extend(extra_warnings)
    if pages_fetched >= 500:
        warnings.append(
            "Reached the 500-page safety limit; pagination may be incomplete."
        )

    return {
        "tool": "learnworlds-extractor",
        "version": "v1",
        "run_timestamp": run_timestamp,
        "mode": "live",
        "assessment_id": assessment_id,
        "source_endpoint": source_endpoint,
        "pages_fetched": pages_fetched,
        "submissions_count": stats.get("submissions_count", 0),
        "answer_blocks_count": stats.get("answer_blocks_count", 0),
        "rows_written": stats.get("rows_written", len(rows)),
        "submissions_without_answers": stats.get("submissions_without_answers", 0),
        "derived_score_status_breakdown": _breakdown(rows),
        "derived_fields_note": (
            "derived_score_status is computed LOCALLY from points and "
            "blockMaxScore only. It is NOT an official LearnWorlds field."
        ),
        "output_files": output_files,
        "warnings": warnings,
        "limitations": [EXAM_CONFIG_LIMITATION],
        "errors": errors or [],
    }


def build_grades_report(
    *,
    run_timestamp: str,
    course_id: str,
    source_endpoint: str,
    pages_fetched: int,
    stats: dict,
    output_files: dict,
    errors: list[str] | None = None,
) -> dict:
    """Assemble the course-grades extraction report. No credentials included."""
    warnings = list(stats.get("warnings", []))
    if pages_fetched >= 500:
        warnings.append(
            "Reached the 500-page safety limit; pagination may be incomplete."
        )
    return {
        "tool": "learnworlds-extractor",
        "version": "v1",
        "run_timestamp": run_timestamp,
        "mode": "live",
        "dataset": "course_grades",
        "course_id": course_id,
        "source_endpoint": source_endpoint,
        "pages_fetched": pages_fetched,
        "grades_count": stats.get("grades_count", 0),
        "rows_written": stats.get("rows_written", 0),
        "distinct_assessment_units": stats.get("distinct_assessment_units", 0),
        "assessment_unit_ids": stats.get("assessment_unit_ids", []),
        "note": (
            "grade is the OFFICIAL recorded grade per learner per assessment unit. "
            "learningUnit_id == assessment_id (joins to submissions_export by "
            "user). No recomputation, no cross-CSV comparison performed here."
        ),
        "output_files": output_files,
        "warnings": warnings,
        "errors": errors or [],
    }


def build_exam_config_report(
    *,
    run_timestamp: str,
    source_file: str,
    assessment_id: str,
    assessment_title: str,
    course_id: str,
    stats: dict,
    output_files: dict,
    errors: list[str] | None = None,
) -> dict:
    """Assemble the exam-config import report. No API call, no credentials."""
    return {
        "tool": "learnworlds-extractor",
        "version": "v1",
        "run_timestamp": run_timestamp,
        "mode": "manual_ui_export",
        "dataset": "exam_config_as_is",
        "source_file": source_file,
        "assessment_id": assessment_id or None,
        "assessment_title": assessment_title,
        "course_id": course_id or None,
        "questions_count": stats.get("questions_count", 0),
        "type_breakdown": stats.get("type_breakdown", {}),
        "overflow_questions": stats.get("overflow_questions", 0),
        "blank_fields_note": stats.get("blank_fields_note", ""),
        "configured_answers_note": (
            "configured_correct_answer_raw / configured_accepted_answers_raw come "
            "from the manual UI export (the answer key). The API does not expose "
            "these. No answer validation or cross-CSV comparison is performed here."
        ),
        "output_files": output_files,
        "warnings": stats.get("warnings", []),
        "limitations": [
            "Join to submissions_export is by question text (no shared id in the "
            "export). assessment_id/course_id are stamped only if passed as args."
        ],
        "errors": errors or [],
    }
