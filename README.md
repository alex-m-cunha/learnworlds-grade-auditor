# LearnWorlds Grade Auditor

[🇵🇹 Versão em Português](README.pt.md)

Internal tool of the LMS team at **Nova SBE Executive Education**.

Extracts data from LearnWorlds assessments (API + UI export + professor Word documents) and reconciles them deterministically to support grade audits — flagging contradictions between what the system scored and what the answer key (LW or professor) indicates as correct.

**Detailed operation guide → [`PROCESS.md`](PROCESS.md)**

---

## How it works

When students take a test on LearnWorlds, their answers are stored digitally on the platform. This tool does three things:

1. **Extracts** student submissions (via API), the answer key configured in LearnWorlds (via UI export), and — if available — the professor's answer key from a Word document (via LLM).

2. **Reconciles** all three sources deterministically: compares each answer against the answer key and flags contradictions — students who answered correctly but received 0 points, answers accepted by the system but contradicted by the professor's key, inconsistent scoring for the same answer across students, and more.

3. **Interprets** the results via LLM and produces a natural-language report in Portuguese, listing issues by priority with suggested next steps.

The system never makes decisions — a human always reviews the flagged cases and decides whether any corrections are needed.

---

## How to use

### 1. Initial setup (once)

**a) Fill in `.env`** — copy `.env.example` to `.env` and fill in:

```
LEARNWORLDS_API_URL=https://your-school.learnworlds.com/admin/api
LEARNWORLDS_SCHOOL_ID=<school id>
LEARNWORLDS_ACCESS_TOKEN=<valid token>
OPENAI_API_KEY=<OpenAI key — needed for Word answer keys and interpretation>
OPENAI_MODEL=gpt-4o
```

**b) Fill in `assessment.cfg`** — file at the project root, for the current assessment:

```
PROGRAM=program1
LABEL=uc5-fintech
LABEL_DISPLAY=UC5: Fintech and Financial Innovation
ASSESSMENT_ID=000000000000000000000000
EPOCA=Normal
```

### 2. Launch

Double-click **`run_audit_gui.py`** (or run `python run_audit_gui.py`).

Opens a window with two tabs: **New Run** (full pipeline) and **Re-run** (reconcile / interpret an existing run).

> Terminal launchers `run_audit.command` (macOS) and `run_audit.bat` (Windows) remain available for those who prefer the command line.

---

## What the GUI does

### "New Run" tab

Form pre-filled with `assessment.cfg` values. Four steps activate in sequence:

| Step | Action | What runs |
|------|--------|-----------|
| **[1/4]** LW Answer Key | Select the XLSX exported from the UI | Submission extraction (API) + answer key import (XLSX) |
| **[2/4]** Word docs *(optional)* | Select one or more `.docx` files | Answer extraction via OpenAI (Phase 1: questions with LW options → `manual_answer_key.csv`; Phase 2: fill-in-the-blank / matching → `inferred_answer_key.csv`) |
| **[3/4]** Reconcile | Button | `run_reconcile` — no API, no LLM |
| **[4/4]** Interpret | Button | `interpret_run` — generates `audit_interpretation.md` via OpenAI |

### "Re-run" tab

Select an existing run folder and run Reconcile and/or Interpret — useful after manually correcting an `inferred_answer_key.csv` or `manual_answer_key.csv`.

---

## File reference

### Configuration

| File | What it is |
|------|-----------|
| `assessment.cfg` | Current assessment: program, label, ID, period. **No credentials.** Update before each assessment. |
| `.env` | LearnWorlds API credentials and OpenAI key. **Never share.** Not in git. |
| `.env.example` | `.env` template with placeholders. |
| `requirements.txt` | Python dependencies. |

### Launchers / interface

| File | What it is |
|------|-----------|
| `run_audit_gui.py` | Main graphical interface (tkinter). Double-click to open. |
| `run_audit.command` | Alternative macOS launcher (terminal, osascript dialogs). |
| `run_audit.bat` | Alternative Windows launcher. |

### Code

| Folder / file | What it is |
|---------------|-----------|
| `extractor/` | Data extractors (API + XLSX). |
| `extractor/run_extract.py` | Submissions via API (`GET /v2/assessments/{id}/responses`). |
| `extractor/run_exam_config.py` | LW answer key from the manually exported XLSX. |
| `extractor/run_grades.py` | Official course grades via API (advanced use). |
| `extractor/config.py` | Reads `assessment.cfg` + `.env`. |
| `reconcile/` | Deterministic reconciler (no network I/O). |
| `reconcile/run_reconcile.py` | Joins submissions + answer keys, applies rules, generates reports. |
| `reconcile/core.py` | Business logic: `check_answer()`, `join_key()`, flags. |
| `tools/extract_answer_key.py` | Extracts answers from Word files via OpenAI → `manual_answer_key.csv` + `inferred_answer_key.csv`. |
| `tools/interpret_run.py` | Generates `audit_interpretation.md` — Portuguese-language audit interpretation via OpenAI. |

### Data folders (not in git)

| Folder | What it is |
|--------|-----------|
| `input/<program>/exam_configs/` | Copies of the XLSX exports from the LW UI. |
| `input/<program>/word_docs/` | Copies of professor Word documents. |
| `output/<program>/<label>/<timestamp>/` | Run results. Never overwritten. |

---

## Output structure

```
output/
  <program>/                          e.g. program1
    <label>/                          e.g. uc5-fintech
      <YYYY-MM-DD_HHmmss>/            one folder per run, never overwritten
        submissions/
          raw/
            raw_response.json         raw API response
            extraction_report.json    counts and warnings
          submissions_export.csv      1 row per answer block per student
          submissions_export.xlsx
        exam_config/
          raw/
            raw_exam_config.json
            extraction_report.json
          exam_config_as_is.csv       LW answer key: correct answers + options
          exam_config_as_is.xlsx
        answer_key/                   only if the Word step was run
          manual_answer_key.csv       professor's key (questions with LW options)
          inferred_answer_key.csv     inferred answers (fill-in-the-blank / matching)
        question_index.csv            unified index of all active questions
        reconcile/                    only if reconciliation was run
          reconciliation_report/
            reconciliation_report.csv     1 row per (student × question)
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

## Reconciliation flags

| Flag | Meaning |
|------|---------|
| `answer_correct_per_doc_but_zero` | ⚠️ Answer correct per professor's key, but LW gave 0 points — LW parametrisation error |
| `answer_accepted_but_zero` | Answer accepted by LW answer key, but 0 points awarded |
| `answer_not_accepted_but_full` | Answer not accepted by the key, but full marks awarded |
| `mcma_wrong_not_penalized` | MCMA: student selected all correct options + wrong ones and received full marks — LW did not penalise the wrong picks |
| `answer_accepted_but_partial` | Partial credit configured (informational) |
| `inconsistent_scoring` | Same question + same answer → different points across students |

---

## Security

- **`.env`** — sensitive credentials. Never commit, never share outside the LMS team.
- **`output/`** — student personal data. Keep in the restricted OneDrive folder. Do not create public links.
- **`input/`** — copies of answer keys. Same restrictions.
- All gitignored. `assessment.cfg` is the only configuration file in git (no credentials).

---

## Quick troubleshooting

| Symptom | Solution |
|---------|---------|
| `HTTP 401/403` | Expired LW token — paste new token in `.env` |
| `HTTP 404 Unit not found` | Wrong `ASSESSMENT_ID` in `assessment.cfg` |
| `No .env file found` | Copy `.env.example` to `.env` and fill in |
| `OPENAI_API_KEY not set` | Add `OPENAI_API_KEY=sk-...` to `.env` |
| `429 insufficient_quota` | OpenAI account out of credits — top up at platform.openai.com |
| macOS blocks the launcher | Run `chmod +x run_audit.command` in Terminal |
| Full tracebacks | Add `DEBUG=true` to `.env` |
