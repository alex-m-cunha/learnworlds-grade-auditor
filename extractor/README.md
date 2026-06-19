# LearnWorlds Extractor (`extractor/`) — V1

Modular, **audit-grade** extraction of assessment submissions from the LearnWorlds
API into a complete CSV. Built for the Nova SBE Executive Education LMS team to
prepare clean files for later external validation.

It **coexists** with the legacy `export_assessment_responses.py` (the 7-column
"teaching view"), which is left untouched. This package produces the **full,
loss-less** extract instead.

> **Scope:** API → CSV only. **No** answer validation, **no** grade recomputation,
> **no** semantic/pedagogical logic, **no** "correction" of data.
> Token policy unchanged: this tool never creates, refreshes, or revokes tokens.

---

## End-to-end process (condensed)

Full walkthrough: **[`../PROCESSO.md`](../PROCESSO.md)**. In short, produce three
datasets (each as **CSV + XLSX**):

1. **Submissions** (API) — `python -m extractor.run_extract --assessment-id <id> --label "<title>"`
2. **Course grades** (API) — `python -m extractor.run_grades --course-id <slug>`
3. **Exam config / answer key** (manual UI export → import) —
   `python -m extractor.run_exam_config --xlsx "<export.xlsx>" --assessment-id <id> --label "<title>"`

`--label` names the output folder by the **activity title** (instead of the id).
Use the same `--label` across tools to co-locate one assessment's outputs. Then hand
the files to the **separate** validation phase (it joins them by `user` + question
text). This project does **not** compare or validate.

---

## Modules

| Module | Responsibility |
|--------|----------------|
| `config.py` | Reads `.env` from the project root; validates live credentials. |
| `client.py` | Base HTTP: Bearer + `Lw-Client` headers, generic GET, `meta` pagination, retries/backoff, typed errors. **No** assessment logic. **Never logs the token.** |
| `submissions.py` | `get_assessment_responses()` + `flatten_submissions()` (one row per answer block) + `derive_score_status()`. |
| `users.py` | `resolve_usernames()` — `user_id → username` via `GET /v2/users/{id}` (cached). |
| `writers.py` | Raw JSON save; CSV writer that serializes arrays/objects as **JSON strings**. |
| `report.py` | Builds `extraction_report.json` (counts, warnings, limitations). **No credentials.** |
| `run_extract.py` | Submissions orchestrator (assessment_id → submissions_export.csv). |
| `grades.py` | `get_course_grades()` + `flatten_grades()` (one row per learner per assessment unit). |
| `run_grades.py` | Course-grades orchestrator (course_id → course_grades.csv). |
| `exam_config.py` | `parse_exam_config_xlsx()` — reads the manual UI export (answer key) the API can't provide. |
| `run_exam_config.py` | Exam-config orchestrator (XLSX → exam_config_as_is.csv). |

---

## Usage

From the project root, with the virtual environment active:

```bash
# Submissions — live, using ASSESSMENT_ID from .env:
python -m extractor.run_extract

# Submissions — for a specific assessment:
python -m extractor.run_extract --assessment-id <assessment_id>

# Submissions — offline regression: replay a saved raw_response file (no API call):
python -m extractor.run_extract --from-raw output/<id>/raw_response_<ts>.json --assessment-id <id>

# Course grades — live, using COURSE_ID from .env (or --course-id):
python -m extractor.run_grades --course-id <course_slug>

# Exam config — from a manual UI export (NO API call; provides the answer key):
python -m extractor.run_exam_config --xlsx "/path/to/export.xlsx" \
    --assessment-id <id> --course-id <slug>
```

The API tools need the live values in `.env`: `LEARNWORLDS_API_URL`,
`LEARNWORLDS_SCHOOL_ID`, `LEARNWORLDS_ACCESS_TOKEN`. Submissions also need
`ASSESSMENT_ID`; course grades need a course slug. **`run_exam_config` makes no API
call** — it only reads the XLSX (credentials not required for it).

---

## Outputs

Written to `output/<label>/` (the activity title when `--label` is given, else the
assessment id), always timestamped, never overwritten:

- `raw_response_<ts>.json` — full raw payload (all pages), saved **before** any transformation.
- `submissions_export_<ts>.csv` **and** `submissions_export_<ts>.xlsx` — one row per answer block (same data).
- `extraction_report_<ts>.json` — run metadata, counts, warnings, limitations.

Every dataset below is written as both **CSV and XLSX** (the XLSX is the same data;
arrays/objects are serialized as JSON strings, with Excel-illegal chars stripped and
cells over 32 767 chars truncated — the CSV stays the loss-less canonical copy).

### `submissions_export.csv` columns (24, fixed order)

`assessment_id, submission_id, user_id, username, email, grade, passed, created,
modified, submittedTimestamp, blockId, blockType, description, answer, points,
blockMaxScore, answerData, downloads, feedback, generalFeedback,
submission_generalFeedback, derived_score_status, source_endpoint,
extraction_timestamp`

Notes:
- `username` is resolved via `GET /v2/users/{user_id}` (not present in the responses
  payload). Skip it with `--no-usernames`; it stays blank in offline replay.
- Submission-level fields are repeated on every row of that submission.
- `generalFeedback` is the **block-level** field; `submission_generalFeedback` is
  the **submission-level** field — kept as separate columns so neither is lost.
- `answerData` and `downloads` are preserved as **JSON strings** when they are
  arrays/objects (UTF-8 preserved). Scalars/null pass through; null → empty cell.
- `source_endpoint` and `extraction_timestamp` are added by the extractor for audit.

### `derived_score_status` — **derived locally, not an official API field**

There is **no** correctness field in the API. This column is computed **only** from
`points` and `blockMaxScore`:

| Condition | Value |
|-----------|-------|
| `points == blockMaxScore` (both valid, `blockMaxScore > 0`) | `full_score` |
| `0 < points < blockMaxScore` | `partial_score` |
| `points == 0` | `zero_score` |
| `points`/`blockMaxScore` null/non-numeric, or `blockMaxScore <= 0`, or `points > max` | `score_unavailable` |

It is intentionally **not** named `status`, `correctness_status`, `is_correct`, or
`is_partially_correct`, to avoid implying an official/pedagogical judgement.

### Course grades (`run_grades`)

Written to `output/course_<course_slug>/`: `raw_grades_<ts>.json`,
`course_grades_<ts>.csv`, `extraction_report_<ts>.json`.

`course_grades.csv` columns (15, fixed order):

`course_id, grade_record_id, user_id, username, email, grade, created,
submittedTimestamp, modified, learningUnit_id, learningUnit_type,
learningUnit_subtitle, learningUnit_raw, source_endpoint, extraction_timestamp`

- One row per learner per assessment unit. `grade` is the **official recorded**
  course-level grade (not recomputed).
- **`learningUnit_id` == `assessment_id`** → joins to `submissions_export.csv` by
  `(learningUnit_id, user_id/email)`. The full `learningUnit` object is also kept
  as a JSON string in `learningUnit_raw` (loss-less).
- This is a **complementary reconciliation source** only. The extractor does **not**
  compare the two CSVs or recompute anything — that is left to the (separate)
  validation phase.

### Exam config (`run_exam_config`) — from a manual UI export

The answer key (configured correct answers, options, feedback) is **not in the API**
(see Endpoint classification). It is exported manually from the LearnWorlds UI as an
XLSX with a `Questions` sheet, then imported here. **No API call is made.**

Written to `output/<title-slug>/` (the activity title by default, or `--label`):
`raw_exam_config_<ts>.json`, `exam_config_as_is_<ts>.csv` + `.xlsx`, `extraction_report_<ts>.json`.

`exam_config_as_is.csv` columns (19, fixed order):

`assessment_id, assessment_title, course_id, unit_uid, blockId, group,
question_number, blockType, description, options_raw,
configured_correct_answer_raw, configured_accepted_answers_raw, configured_score,
feedback_correct, feedback_incorrect, settings_raw, row_raw, source,
extraction_timestamp`

- `blockType` keeps the export's raw `Type` (`TTF`/`TMC`/`TMCMA`/`TST`/…).
- `options_raw` / `configured_accepted_answers_raw` are **JSON strings**; `row_raw`
  preserves the entire raw row (loss-less).
- **Blank by design** (not present in the export): `unit_uid`, `blockId`,
  `configured_score`, `settings_raw`. `assessment_id`/`course_id` are blank unless
  passed via `--assessment-id` / `--course-id`.
- **Positional-overflow handling:** when a question references >5 options, the option
  list overflows past `Answer5` and reuses the feedback columns as answer slots —
  detected by max answer index, so `feedback_*` are left blank for those rows and the
  options are read positionally. (Verified on the `TST` 30-variant fill-in.)
- **Join to submissions/grades:** by **question text** (`description` ≈ submissions
  `description`) — the export carries no shared id. No comparison is done here.

---

## Endpoint classification

Sources: live read-only probing (2026-06-19) **and** the official documented
endpoint list (learnworlds.dev Stoplight reference, confirmed via screenshots).

The official docs list the **complete** `Assessments` section as only three
endpoints — there is **no** get-assessment / get-questions / get-config endpoint:

| Assessments section (official, complete) | Method | Use here |
|------------------------------------------|--------|----------|
| Get assessment responses (`/v2/assessments/{id}/responses`) | GET | ✅ used (submissions source) |
| Get form responses (`/v2/forms/{id}/responses`) | GET | survey/form responses (`newSurvey` units), not assessments |
| Review the submission of a … | POST | **write** (grade/review) — out of scope |

Confirmed working / relevant:

| Endpoint | Status | Notes |
|----------|--------|-------|
| `GET /v2/assessments/{id}/responses` | ✅ **Confirmed** | `data[]` + `meta{page,totalItems,totalPages,itemsPerPage}`. |
| `GET /v2/users/{id}` (Get a user) | ✅ **Confirmed & used** | Returns `username, email, first_name, last_name, …`. Used to fill the `username` column (the responses/grades payloads only carry `user_id`/`email`). |
| `GET /v2/courses` | ✅ **Confirmed** | Paginated (`itemsPerPage:50`). Course `id` is a **slug**, not hex. |
| `GET /v2/courses/{id}` · `/contents` | ✅ **Confirmed** | `contents.sections[].learningUnits[]` → units with `id, type, title`. |
| learningUnit `type == "assessmentV2"` | ✅ **Confirmed** | Its `id` **is** usable as `assessment_id` for `/responses` (V2 discovery path). |
| `GET /v2/courses/{id}/grades` (Get course grades) | ✅ **Confirmed** | Paginated (`itemsPerPage:20`). Records: `id, user_id, email, grade, created, submittedTimestamp, modified, learningUnit{id,type,subtitle,icon}`. **`learningUnit.id` == `assessment_id`** (joins cleanly to the submissions extract by user). Gives the official recorded grade per learner per assessment — complementary reconciliation source. |
| `GET .../units/{uid}/analytics` (Get analytics for a learning activity) | ✅ **Confirmed** | Aggregates only (`avg_score_rate`, `users_completed`, …) — no question config. |
| `GET /v2/assessments/{id}` · `/questions` · `/config` · `/blocks` · `/settings` | ❌ **404 + not in docs** | No assessment-config endpoint exists. |

### Key limitation — `exam_config_as_is.csv` (definitive)

The assessment **configuration** (configured correct answers, options, accepted
answers, per-question scores, feedback templates) is **not exposed by any API
endpoint** — confirmed both by 404 probing and by the official documented endpoint
list (the entire `Assessments` section is responses + form responses + review).
Therefore:

> **`exam_config_as_is.csv` cannot be produced from the API.** It is imported from a
> **manual LearnWorlds UI export** via `run_exam_config` (see *Exam config* above).

---

## Roadmap

- **V2 — course discovery** (not yet implemented): `course_id` → `/contents` →
  `learningUnits[type=="assessmentV2"]` → run submissions per discovered assessment,
  mapping `course_id, unit_id, assessment_id`. Path is **confirmed viable** (and
  `run_grades` already returns the assessment-unit ids for a course as a shortcut).
- **V3 — exam config**: ✅ **implemented** (`exam_config.py` / `run_exam_config.py`),
  importing the manual UI export.
- **Validation phase** (separate, out of scope here): join `submissions_export`,
  `course_grades`, and `exam_config_as_is` by user + question text to reconcile
  official grades vs extracted points vs the configured answer key.

---

## Acceptance criteria met (V1)

Runs with an `assessment_id`; produces `submissions_export.csv` with one row per
`answers[]` item; respects `meta.totalPages` pagination; preserves `points`/
`blockMaxScore`; serializes arrays/objects as JSON strings; writes a report with
counts and warnings; never exposes the token in logs/report; performs **no** answer
validation and **no** grade recomputation; documents API limitations.
