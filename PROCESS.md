# Assessment Audit Process — end-to-end

[🇵🇹 Versão em Português](PROCESSO.md)

---

## What this does (for non-technical readers)

When students take a test on LearnWorlds, their answers are stored digitally on the platform. This tool does three things automatically:

1. **Extracts** all student submissions, the official answer key configured in LearnWorlds, and — if available — the professor's answer key from a Word document.

2. **Compares** each student's answer against the answer key using exact, reproducible rules. It does not interpret or guess — it applies the rules and flags cases that don't fit.

3. **Produces a report** in Portuguese that lists, by priority, the issues found: students who answered correctly but received 0 points, answers the system accepted as correct but that contradict the professor's key, inconsistent scores for the same answer, and more.

**The system never decides anything.** A human always reviews the flagged cases and decides whether any corrections are needed. The tool's job is to ensure no case goes unnoticed.

> Scope: data extraction + deterministic reconciliation + discrepancy flagging. Out of scope: any pedagogical judgement or automatic grade correction.

---

## Pipeline architecture

```text
┌─────────────────────┐   ┌──────────────────────┐   ┌──────────────────────────┐
│   LearnWorlds API   │   │  LearnWorlds UI       │   │   Professor Word docs    │
│                     │   │  Export (XLSX)        │   │                          │
└──────────┬──────────┘   └──────────┬────────────┘   └────────────┬─────────────┘
           │  [Step 1]               │  [Step 1]                   │  [Step 2]
           ▼                         ▼                              ▼
  submissions_export.csv   exam_config_as_is.csv             LLM (OpenAI)
  (what each student        (LW answer key — options               │
   answered, points,         and correct answers,                  ├─ Phase 1: questions with
   blockType)                question types)                       │  LW options (MCQ, T/F…)
                                                                   │  → manual_answer_key.csv
                                                                   │
                                                                   └─ Phase 2: questions WITHOUT
                                                                      LW options
                                                                      (fill-in-the-blank / matching)
                                                                      → inferred_answer_key.csv

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
               [Step 3]  RECONCILER  (no API, no LLM)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  In-memory merge of three sources:
    LW answer key  +  professor's key (Phase 1)  +  inferred answers (Phase 2)
           │
           ├─ verifiable = "yes"               verified against LW answer key
           ├─ verifiable = "inferred"           verified against Word-inferred answer
           ├─ verifiable = "unverifiable_mcma"  ambiguous MCMA → manual review
           └─ verifiable = "no_config_match"    no answer key in any source

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                              OUTPUTS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  reconciliation_report.csv    grade_reconciliation.csv    manual_review_queue.csv
  reconciliation_summary.md    question_index.csv
  audit_interpretation.md   ←  [Step 4 — LLM]
```

---

## Prerequisites (one-time setup)

### 1. Python 3.10+

Check with `python3 --version`. Install from [python.org](https://www.python.org) if needed.

The virtual environment (`.venv`) and dependencies are installed **automatically** on first run. No manual setup needed.

### 2. `.env` file

Copy `.env.example` to `.env` (at the project root) and fill in:

```
LEARNWORLDS_API_URL=https://your-school.learnworlds.com/admin/api
LEARNWORLDS_SCHOOL_ID=<your school id>
LEARNWORLDS_ACCESS_TOKEN=<LearnWorlds access token — see below>
OPENAI_API_KEY=<OpenAI API key — needed for Word answer keys and interpretation>
OPENAI_MODEL=gpt-4o
```

> **LearnWorlds token:** generated manually in the LW admin, has limited validity. This tool **never** creates or refreshes tokens. Expired token → clear HTTP 401 error. Paste the new token into `.env`.

> **Security:** `.env` contains sensitive credentials. It is gitignored and must never be shared or emailed outside the LMS team.

### 3. `assessment.cfg` file

File at the project root, already included in the repository. Update before each assessment:

```
PROGRAM=program1          ← program code + edition (e.g. mba3, pggf2)
LABEL=uc5-fintech         ← used as the output folder name
LABEL_DISPLAY=UC5: Fintech and Financial Innovation   ← shown in reports
ASSESSMENT_ID=000000000000000000000000               ← 24-char ID from the LW admin URL
EPOCA=Normal              ← Normal | Extraordinária
```

The `ASSESSMENT_ID` is found at the end of the activity URL in the LW admin:
`https://.../admin/assessments/**000000000000000000000000**/edit`

> `assessment.cfg` has no credentials — safe to commit and share.

---

## Running — graphical interface (recommended)

Double-click **`run_audit_gui.py`** (or `python run_audit_gui.py`).

The window has two tabs:

---

### "New Run" tab

For running a complete audit from scratch.

Form fields are pre-filled from `assessment.cfg`. Update if needed, then follow the 4 steps in sequence:

---

#### [1/4] LW Answer Key

Click **"Select LW Answer Key (XLSX)"** and choose the file exported from the LearnWorlds UI.

**How to export the XLSX:**
1. LearnWorlds admin → course → **Course Outline**
2. Click the activity → **Edit questions**
3. **Export → Export as .xls**

After selecting:
- The file is copied to `input/<program>/exam_configs/`
- Runs automatically: submission extraction (API) + answer key import (XLSX)

> ⚠️ Export the XLSX from the **same version** of the test that students took. Edits made after submissions break the matching.

---

#### [2/4] Word docs *(optional)*

Click **"Select Word docs"** and choose the professor's `.docx` files (Cmd+click / Ctrl+click for multiple).

Or click **"Skip Word docs"** to proceed without this step.

If selected, runs `tools/extract_answer_key.py` via OpenAI in two automatic phases:

**Phase 1 — Questions with LW answer key** (MCQ, MCMA, T/F, Dropdown, Short Text):

The LLM cross-references the LW answer key with the Word document and produces `answer_key/manual_answer_key.csv`:

- `lw_correct_answer` vs `doc_correct_answer` — answer in LW and in the Word doc
- `answers_match` — `yes / no / lw_only / doc_only`
- `confidence` — `high / medium / low / unmatched`
- `needs_review` — `true` when there is a discrepancy or low confidence

The LLM recognises answers marked by: bold text, Word styles, asterisk, checkmark, colour, underline, explicit label ("Answer: B)"), table cell, or strikethrough on wrong options.

Questions are sent to the LLM **per document** — if the assessment has 3 Word docs (15+15+15 questions), each doc receives only its own questions. This prevents the LLM from associating questions with the wrong doc.

**Phase 2 — Questions without LW answer key** (fill-in-the-blank, matching):

LearnWorlds **does not export** these questions in the XLSX. The LLM locates them in the Word doc and infers the correct answers:

- **Fill-in-the-blank:** labels like "Accepted variations (blank N)", variants separated by `; `, blanks by ` | `
- **Matching:** pairs with identical colour or labels like "Pair N"

Produces `answer_key/inferred_answer_key.csv`. High/medium confidence answers enter reconciliation; low/unmatched go to the manual review queue.

> ⚠️ Phase 2 answers are automatic inferences. Always verify before making decisions about grades.

---

#### [3/4] Reconcile

Click **"Reconcile"**.

Runs the deterministic reconciler (no API, no LLM). Joins all three sources and produces reports in `reconcile/`.

What it detects:

| Flag | Meaning |
|------|---------|
| `answer_correct_per_doc_but_zero` | ⚠️ Student answered correctly per the professor's key but LW gave 0 points — **LW parametrisation error** |
| `answer_accepted_but_zero` | Answer accepted by LW answer key, but 0 points awarded |
| `answer_not_accepted_but_full` | Answer not accepted by the key, but full marks awarded |
| `mcma_wrong_not_penalized` | MCMA: student selected all correct options + wrong ones and received full marks — LW did not penalise the wrong picks |
| `answer_accepted_but_partial` | Partial credit configured (informational) |

Reconciliation also reports the **verification source** (`answer_matched_source`):
- `lw` — verified against the LW answer key
- `doc` — verified against the professor's key (Word), when LW had a wrong configuration
- `inferred` — verified against LLM inference (fill-in-the-blank / matching)

Also reconciles the official `grade` (API field) with `round(Σpoints/Σmax × 100)` computed in `Decimal`.

The canonical question number is derived from **submission order** (not the XLSX or Word), ensuring all sources use the same index.

---

#### [4/4] Interpret

Click **"Interpret"**.

Calls OpenAI and produces `audit_interpretation.md` in the run folder.

Includes:
- **Executive summary** — 2-3 paragraph overview
- **✅ What went well**
- **⚠️ Issues to fix** — flags with student names, concrete answers, suggested action
- **ℹ️ Relevant information** — limitations, methodological context
- **Next steps** — priority (🔴🟡🔵) and action to take

---

### "Re-run" tab

Select an existing run folder and run Reconcile and/or Interpret — useful after manually correcting a `manual_answer_key.csv` or `inferred_answer_key.csv`.

1. Click **"Select run folder"** and choose the timestamped folder
2. Click **"Reconcile"** and/or **"Interpret"**
3. **"Open in Finder"** — opens the run folder directly

---

## Alternative: terminal launchers

For those who prefer the command line:

- **macOS:** double-click `run_audit.command` (run `chmod +x run_audit.command` the first time)
- **Windows:** double-click `run_audit.bat`

---

## Output structure

Each run creates a timestamped folder — **never overwrites** previous runs:

```
output/
  <program>/                              e.g. program1
    <label>/                              e.g. uc5-fintech
      <YYYY-MM-DD_HHmmss>/                one folder per run
        submissions/
          raw/
            raw_response.json             raw API response (all pages)
            extraction_report.json        counts, warnings, endpoint called
          submissions_export.csv          1 row per answer block per student
          submissions_export.xlsx
        exam_config/
          raw/
            raw_exam_config.json
            extraction_report.json
          exam_config_as_is.csv           LW answer key: correct answers + options
          exam_config_as_is.xlsx
        answer_key/                       only if the Word step was run
          manual_answer_key.csv           professor's key (Phase 1 — questions with LW options)
          inferred_answer_key.csv         inferred answers (Phase 2 — fill-in-the-blank/matching)
        question_index.csv                unified index of all active questions
        reconcile/                        only if reconciliation was run
          reconciliation_report/
            reconciliation_report.csv     1 row per (student × question), with flag
            reconciliation_report.xlsx
          grade_reconciliation/
            grade_reconciliation.csv      official vs recalculated grade per student
            grade_reconciliation.xlsx
          consistency_report/
            consistency_report.csv        same answer scored differently across students
            consistency_report.xlsx
          manual_review_queue/
            manual_review_queue.csv       questions requiring human review
            manual_review_queue.xlsx
          reconciliation_summary.json
          reconciliation_summary.md       human-readable summary with counts and flags
        audit_interpretation.md           AI interpretation in Portuguese
```

---

## Data sources and what they contain

| File | Contents | Origin |
|------|----------|--------|
| `submissions_export.csv` | 1 row per answer block per student: `blockType`, `answer`, `points`, `blockMaxScore`, `user_id`, `username`, `email`, `grade` | API `GET /v2/assessments/{id}/responses` |
| `exam_config_as_is.csv` | LW answer key: configured correct answers, available options, feedback, `blockType`. **Does not include** fill-in-the-blank or matching questions. | Manual UI export |
| `manual_answer_key.csv` | Professor's key (Phase 1): correct answer per question extracted from Word via LLM, with `confidence` and `answers_match`. | Word docs → OpenAI |
| `inferred_answer_key.csv` | Inferred answers (Phase 2): fill-in-the-blank and matching not exported by LW, extracted from Word via LLM. `confidence` indicates reliability. | Word docs → OpenAI |
| `question_index.csv` | Unified index of all active questions, with canonical number, source (LW/inferred), and answer keys from all sources. | Local processing |
| `reconciliation_report.csv` | 1 row per (student × question): `verifiable`, `is_correct`, `flag`, `answer_matched_source`. | Local processing |
| `grade_reconciliation.csv` | Official grade (API) vs recalculated grade per student. | Local processing |
| `manual_review_queue.csv` | Questions without an answer key, ambiguous MCMA, or unresolvable inferences. 1 row per question. | Local processing |

---

## Final review (human)

Reconciliation **flags**, it doesn't decide. After running:

1. Open `audit_interpretation.md` — main entry point; lists all issues by severity with next steps
2. For each flagged issue: check `reconciliation_report.csv` or `manual_review_queue.csv`
3. If the Word step was run: review `manual_answer_key.csv` filtering `needs_review=true`

### Confirming inferred answers (fill-in-the-blank and matching)

Fill-in-the-blank and matching questions have no answer key in LW — the answer was inferred automatically from the Word doc.

1. In `audit_interpretation.md`, read the **"Inferred questions"** section
2. For each question, compare with what the professor wrote in the Word doc
3. **If correct** → no action needed
4. **If a valid variant is missing:**
   - Open `answer_key/inferred_answer_key.csv` in the run folder
   - Add the missing variant to the `doc_correct_answer` field (separate by `"; "` within a blank, `" | "` between blanks)
   - Re-run via the "Re-run" tab (or `python -m reconcile.run_reconcile --run-dir <folder>`)

### LLM hallucinations in the extracted answer key

The LLM that extracts `manual_answer_key.csv` may occasionally produce text slightly different from the Word document (synonyms, translation, formatting). When `needs_review=true` due to `answers_match=no`, always verify whether the divergence is real (genuinely different answer keys) or a hallucination (doc and LW say the same thing in different words).

If it's a hallucination: edit `doc_correct_answer` in the CSV to the correct text and re-reconcile.

---

## Advanced use — direct CLI

All scripts accept `--help`. Examples:

```bash
# Submissions only
python -m extractor.run_extract --assessment-id <id> --label "uc5"

# LW answer key
python -m extractor.run_exam_config --xlsx "/path/export.xlsx" --assessment-id <id> --label "uc5"

# Professor's Word answer key
python tools/extract_answer_key.py \
    --exam-config "output/program1/uc5/2026-06-20_120000/exam_config/exam_config_as_is.csv" \
    --docs "input/program1/word_docs/uc5_answer_key.docx"

# Reconcile
python -m reconcile.run_reconcile \
    --run-dir "output/program1/uc5/2026-06-20_120000"

# Interpret
python tools/interpret_run.py \
    --run-dir "output/program1/uc5/2026-06-20_120000"
```

---

## Technical notes

- **LW token:** never created or refreshed by the tool. Expired token → clear HTTP 401 error.
- **`grade` is a percentage** — `round(Σpoints/Σmax × 100)`, not raw points.
- **`derived_score_status`** in `submissions_export.csv` is computed locally from `points`/`blockMaxScore` (not an official API field).
- **Robust matching:** the API and UI export differ in punctuation/spacing. Comparison uses `join_key()` (alphanumeric normalisation) to avoid false positives.
- **MCMA:** answers are concatenated without a delimiter. The reconciler reconstructs the selected set by containment of known options; if ambiguous → `unverifiable_mcma`.
- **MCMA doc override:** when the professor's Word key uses different wording than the LW option texts (not just formatting), the reconciler cannot map them automatically and marks `unverifiable_mcma` for human review.
- **Canonical numbering:** each question's number is derived from its first appearance in submissions, not from the XLSX or Word. Ensures consistency across all sources.
- **Doc-scoped extraction:** when there are multiple Word docs, each doc receives only its own questions (determined by submission order position). Prevents the LLM from associating questions with the wrong doc.
- Column and endpoint technical detail: [`extractor/README.md`](extractor/README.md)
