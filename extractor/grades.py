"""Course grades: fetch + flatten into audit rows.

Endpoint (confirmed): GET /v2/courses/{course_id}/grades
Shape: { data: [ grade_record, ... ], meta: {page, totalItems, totalPages, itemsPerPage} }

Each grade record is the OFFICIAL recorded grade for one learner on one assessment
unit, e.g.:
    { id, user_id, email, grade, created, submittedTimestamp, modified,
      learningUnit: { id, type, subtitle, icon } }

`learningUnit.id` == the assessment_id used by /v2/assessments/{id}/responses, so
this is a clean complementary/reconciliation source (one row per learner+unit).

No validation, no grade recomputation, no cross-CSV comparison. API → CSV only.
"""

from __future__ import annotations

from .client import LearnWorldsClient

# Output columns — fixed order. learningUnit_raw preserves the full nested object
# (serialized as JSON string by writers.write_csv) so nothing is lost.
GRADE_COLUMNS = [
    "course_id",
    "grade_record_id",
    "user_id",
    "username",
    "email",
    "grade",
    "created",
    "submittedTimestamp",
    "modified",
    "learningUnit_id",
    "learningUnit_type",
    "learningUnit_subtitle",
    "learningUnit_raw",
    "source_endpoint",
    "extraction_timestamp",
]


def get_course_grades(client: LearnWorldsClient, course_id: str):
    """Fetch all grade pages for a course.

    Returns (combined_data, raw_pages, source_endpoint).
    """
    path = f"/v2/courses/{course_id}/grades"
    combined_data, raw_pages = client.get_paginated(path)
    source_endpoint = f"{client.api_url}{path}"
    return combined_data, raw_pages, source_endpoint


def flatten_grades(
    data: list,
    course_id: str,
    source_endpoint: str,
    extraction_timestamp: str,
    username_map: dict | None = None,
) -> tuple[list[dict], dict]:
    """Flatten grade records -> one row per (learner, assessment unit).

    `username_map` (optional) maps user_id -> username for enrichment.
    Returns (rows, stats) for the report.
    """
    username_map = username_map or {}
    rows: list[dict] = []
    warnings: list[str] = []
    grades_count = 0
    distinct_units: set = set()

    for record in data:
        if not isinstance(record, dict):
            warnings.append("Skipped a non-object item in grades data[].")
            continue
        grades_count += 1

        unit = record.get("learningUnit")
        if not isinstance(unit, dict):
            unit = {}
        unit_id = unit.get("id")
        if unit_id:
            distinct_units.add(unit_id)

        rows.append(
            {
                "course_id": course_id,
                "grade_record_id": record.get("id"),
                "user_id": record.get("user_id"),
                "username": username_map.get(record.get("user_id"), ""),
                "email": record.get("email"),
                "grade": record.get("grade"),
                "created": record.get("created"),
                "submittedTimestamp": record.get("submittedTimestamp"),
                "modified": record.get("modified"),
                "learningUnit_id": unit_id,
                "learningUnit_type": unit.get("type"),
                "learningUnit_subtitle": unit.get("subtitle"),
                # Full nested object preserved (write_csv serializes dict -> JSON).
                "learningUnit_raw": unit if unit else None,
                "source_endpoint": source_endpoint,
                "extraction_timestamp": extraction_timestamp,
            }
        )

    stats = {
        "grades_count": grades_count,
        "rows_written": len(rows),
        "distinct_assessment_units": len(distinct_units),
        "assessment_unit_ids": sorted(distinct_units),
        "warnings": warnings,
    }
    return rows, stats
