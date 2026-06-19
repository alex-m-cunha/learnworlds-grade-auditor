"""Assessment submissions: fetch + flatten into audit rows.

Endpoint (confirmed): GET /v2/assessments/{assessment_id}/responses
Shape: { data: [ submission, ... ], meta: {page, totalItems, totalPages, itemsPerPage} }

Each submission has an `answers[]` array (one item per question/block). We emit
ONE output row per answer block, repeating the submission-level fields.

No validation, no grade recomputation. The only derived field is
`derived_score_status`, computed solely from points / blockMaxScore and clearly
named as derived.
"""

from __future__ import annotations

from .client import LearnWorldsClient

# Output columns — fixed order (23 columns). See plan / README.
SUBMISSION_COLUMNS = [
    "assessment_id",
    "submission_id",
    "user_id",
    "username",
    "email",
    "grade",
    "passed",
    "created",
    "modified",
    "submittedTimestamp",
    "blockId",
    "blockType",
    "description",
    "answer",
    "points",
    "blockMaxScore",
    "answerData",
    "downloads",
    "feedback",
    "generalFeedback",
    "submission_generalFeedback",
    "derived_score_status",
    "source_endpoint",
    "extraction_timestamp",
]

# Submission-level keys copied (under their output names) onto every row.
_SUBMISSION_LEVEL = {
    "submission_id": "id",
    "user_id": "user_id",
    "email": "email",
    "grade": "grade",
    "passed": "passed",
    "created": "created",
    "modified": "modified",
    "submittedTimestamp": "submittedTimestamp",
}

# Block-level keys taken verbatim from each answers[] item.
_BLOCK_LEVEL = [
    "blockId",
    "blockType",
    "description",
    "answer",
    "points",
    "blockMaxScore",
    "answerData",
    "downloads",
    "feedback",
    "generalFeedback",
]


def get_assessment_responses(client: LearnWorldsClient, assessment_id: str):
    """Fetch all response pages for an assessment.

    Returns (combined_data, raw_pages, source_endpoint).
    """
    path = f"/v2/assessments/{assessment_id}/responses"
    combined_data, raw_pages = client.get_paginated(path)
    source_endpoint = f"{client.api_url}{path}"
    return combined_data, raw_pages, source_endpoint


def _to_number(value):
    """Coerce to int/float when possible; return None otherwise (no exceptions)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        number = float(str(value).strip().replace(",", "."))
        return int(number) if number.is_integer() else number
    except (ValueError, TypeError):
        return None


def derive_score_status(points, block_max_score) -> str:
    """Derived (not official) classification from points vs blockMaxScore.

    full_score | partial_score | zero_score | score_unavailable
    """
    earned = _to_number(points)
    maximum = _to_number(block_max_score)
    if earned is None or maximum is None or maximum <= 0:
        return "score_unavailable"
    if earned == 0:
        return "zero_score"
    if earned == maximum:
        return "full_score"
    if 0 < earned < maximum:
        return "partial_score"
    # points > max (unexpected) — don't pretend it's a clean full score.
    return "score_unavailable"


def flatten_submissions(
    data: list,
    assessment_id: str,
    source_endpoint: str,
    extraction_timestamp: str,
    username_map: dict | None = None,
) -> tuple[list[dict], dict]:
    """Flatten submissions -> one row per answer block.

    `username_map` (optional) maps user_id -> username for enrichment.
    Returns (rows, stats) where stats carries counts and warnings for the report.
    """
    username_map = username_map or {}
    rows: list[dict] = []
    warnings: list[str] = []
    submissions_count = 0
    answer_blocks_count = 0
    submissions_without_answers = 0

    for submission in data:
        if not isinstance(submission, dict):
            warnings.append("Skipped a non-object item in data[].")
            continue
        submissions_count += 1

        base = {"assessment_id": assessment_id}
        for out_key, src_key in _SUBMISSION_LEVEL.items():
            base[out_key] = submission.get(src_key)
        base["username"] = username_map.get(submission.get("user_id"), "")
        base["submission_generalFeedback"] = submission.get("generalFeedback")
        base["source_endpoint"] = source_endpoint
        base["extraction_timestamp"] = extraction_timestamp

        answers = submission.get("answers")
        if not isinstance(answers, list) or not answers:
            submissions_without_answers += 1
            continue

        for block in answers:
            if not isinstance(block, dict):
                warnings.append(
                    f"Skipped a non-object answer block in submission "
                    f"{submission.get('id')}."
                )
                continue
            answer_blocks_count += 1
            row = dict(base)
            for key in _BLOCK_LEVEL:
                row[key] = block.get(key)
            row["derived_score_status"] = derive_score_status(
                block.get("points"), block.get("blockMaxScore")
            )
            rows.append(row)

    if submissions_without_answers:
        warnings.append(
            f"{submissions_without_answers} submission(s) had no answers[] and "
            "produced no rows."
        )

    stats = {
        "submissions_count": submissions_count,
        "answer_blocks_count": answer_blocks_count,
        "rows_written": len(rows),
        "submissions_without_answers": submissions_without_answers,
        "warnings": warnings,
    }
    return rows, stats
