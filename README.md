# LearnWorlds Assessment Responses Exporter

Internal tool for the **Nova SBE Executive Education LMS team**.

> **Three components live here:**
> 1. **This exporter** (`export_assessment_responses.py`) — the simple **7-column
>    "teaching view"** (offline/live), documented below.
> 2. **The extractor** (`extractor/`) — a modular, **audit-grade extractor** that
>    produces the complete submissions CSV/XLSX (24 columns, JSON-preserved nested
>    data, `derived_score_status`, username enrichment), plus **course grades** and
>    the **exam-config** answer key imported from a manual UI export.
>    See **[`extractor/README.md`](extractor/README.md)**. Run: `python -m extractor.run_extract`.
> 3. **The reconciler** (`reconcile/`) — a **deterministic** validation phase (no API,
>    no LLM) that joins the three sources and **flags** discrepancies: grade
>    reconciliation, answer-key contradictions, cross-student scoring inconsistencies,
>    and a deduplicated manual-review queue. Run: `python -m reconcile.run_reconcile --label <folder>`.
>
> **End-to-end audit process:** see **[`PROCESSO.md`](PROCESSO.md)** for the full
> step-by-step workflow (extract → reconcile → human review). The components coexist;
> this exporter is unchanged.

---

## 1. What this tool does

It exports learner **assessment responses** from LearnWorlds into **CSV** and **XLSX**.

It:

1. Gets the assessment responses (from the LearnWorlds API, or from a local JSON sample).
2. Saves a **timestamped raw copy** of the response to `output/` (audit trail).
3. **Normalizes** the data into one row per learner answer per question.
4. Exports timestamped **CSV** and **XLSX** files to `output/`.

Each output row contains: `learner_email`, `final_score`, `question_text`,
`submitted_answer`, `points_earned`, `max_points`, `status`.

---

## 2. Current API access limitation

> **We do not currently have a valid active LearnWorlds Access Token.**
> All existing tokens appear to be expired, and new ones cannot be created yet.

Because of this, the tool runs in two modes:

- **`offline`** — works today, with no token. Reads a local JSON sample and runs the full
  pipeline. Use this for development, testing, and validating the structure.
- **`live`** — requires a **valid** LearnWorlds Access Token added manually to `.env`.
  Use this once token access is restored.

Until a valid token exists, keep `EXPORT_MODE=offline`.

---

## 3. Where the tool is stored

This tool lives in the **restricted LMS team OneDrive folder**:

```
04. LearnWorlds / Avaliações / learnworlds-assessment-exporter
```

Access is limited to the LMS team. The real `.env` file may be stored here for operational
simplicity, **because access is restricted**. The scripts and launchers are written to handle
OneDrive paths that contain spaces and accented characters.

---

## 4. Required inputs

| Input | Offline mode | Live mode |
|-------|--------------|-----------|
| `.env` file | ✅ required | ✅ required |
| `INPUT_JSON_PATH` (local sample) | ✅ required | — |
| `LEARNWORLDS_API_URL` | — | ✅ required |
| `LEARNWORLDS_SCHOOL_ID` | — | ✅ required |
| `LEARNWORLDS_ACCESS_TOKEN` (valid) | — | ✅ required |
| `ASSESSMENT_ID` | — | ✅ required |

---

## 5. `EXPORT_MODE=offline` vs `EXPORT_MODE=live`

**`offline`**
- Does **not** call the LearnWorlds API.
- Does **not** require a token or school id.
- Reads the JSON at `INPUT_JSON_PATH`.
- Saves a timestamped raw copy, normalizes, and exports CSV/XLSX if the shape can be parsed.
- If parsing fails, prints a clear message (the raw JSON is still saved).

**`live`**
- Requires `LEARNWORLDS_API_URL`, `LEARNWORLDS_SCHOOL_ID`, `LEARNWORLDS_ACCESS_TOKEN`,
  `ASSESSMENT_ID`.
- Calls `GET {LEARNWORLDS_API_URL}/v2/assessments/{ASSESSMENT_ID}/responses` with headers
  `Authorization: Bearer <token>` and `Lw-Client: <school_id>`.
- Follows pagination defensively (up to 500 pages), saves the full raw page collection,
  normalizes, and exports CSV/XLSX.

---

## 6. How to create and fill the `.env` file

1. Duplicate **`.env.example`** and rename the copy to **`.env`**.
2. Leave `EXPORT_MODE=offline` for now.
3. Confirm `INPUT_JSON_PATH=input/sample_response.json`.
4. When token access is restored (live mode), set:
   - `EXPORT_MODE=live`
   - `LEARNWORLDS_SCHOOL_ID=<your school / client id>`
   - `LEARNWORLDS_ACCESS_TOKEN=<a valid existing token>`
   - `ASSESSMENT_ID=<the assessment id>`
   - `LEARNWORLDS_API_URL=https://online.executiveducation.novasbe.pt/admin/api`
     (no trailing slash; the script strips one if present)

`LEARNWORLDS_CLIENT_ID` / `LEARNWORLDS_CLIENT_SECRET` are kept in `.env.example` only as
optional reference fields. **They are not used in v1.**

---

## 7. Security warning — the `.env` file

- The `.env` file may contain **sensitive LearnWorlds API credentials** (access token, school id).
- **Do not share the `.env` file outside the LMS team.**
- Do not commit it to any repository (it is already in `.gitignore`).
- Do not paste its contents into chats, tickets, or emails.

---

## 8. Security warning — exported learner data

- The exported CSV/XLSX files may contain **learner personal data** (emails) and **assessment
  results**.
- Keep all output **inside the restricted LMS team folder**.
- Do **not** create public links to this folder or to output files.
- Do **not** email exported CSV/XLSX files unless strictly necessary.

---

## 9. Token management note

- This tool **does not create** new access tokens.
- This tool **does not refresh** access tokens.
- This tool **does not call any authentication or token-generation endpoint**, and does not use
  a client-credentials flow.
- Live mode only uses a **valid LearnWorlds Access Token manually added to `.env`**.
- If no valid token exists, **use offline mode** until token access is restored.

---

## 10. How to run manually via terminal

**macOS / Linux**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python export_assessment_responses.py
```

**Windows**

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python export_assessment_responses.py
```

---

## 11. How to run on macOS (`run_export.command`)

1. Open the folder.
2. Duplicate `.env.example` and rename it to `.env`.
3. Set `EXPORT_MODE=offline` until a valid LearnWorlds Access Token is available.
4. Double-click **`run_export.command`**.
5. If macOS blocks execution, open Terminal in this folder and run:
   ```bash
   chmod +x run_export.command
   ```
   Then double-click again.

The launcher creates a local `.venv`, installs dependencies, runs the exporter, and keeps the
Terminal window open so you can read the result.

---

## 12. How to run on Windows (`run_export.bat`)

1. Open the folder.
2. Duplicate `.env.example` and rename it to `.env`.
3. Set `EXPORT_MODE=offline` until a valid LearnWorlds Access Token is available.
4. Double-click **`run_export.bat`**.

The launcher creates a local `.venv`, installs dependencies, runs the exporter, and keeps the
Command Prompt window open so you can read the result.

---

## 13. Where output files are saved

Everything is written to the **`output/`** folder, with timestamped names so nothing is ever
overwritten:

```
output/raw_response_2026-06-17_143000.json
output/assessment_responses_2026-06-17_143000.csv
output/assessment_responses_2026-06-17_143000.xlsx
```

---

## 14. Exported fields

| Column | Source (LearnWorlds support guidance) |
|--------|----------------------------------------|
| `learner_email` | learner email field (`email` / `user.email` / `learner.email`) |
| `final_score` | final score / `grade` |
| `question_text` | `description` |
| `submitted_answer` | `answer` |
| `points_earned` | `points` |
| `max_points` | `blockMaxScore` |
| `status` | **calculated** (see below) |

The parser is defensive: it also accepts alternative key names (e.g. `final_score`, `score`,
`question_text`, `maxScore`) and both a nested learner-with-blocks shape and a flat
one-row-per-answer shape.

---

## 15. How `status` is calculated

There is no direct correct/incorrect field, so it is computed from the points:

- **`correct`** — `points_earned` equals `max_points`
- **`incorrect`** — `points_earned` equals `0`
- **`partial`** — anything in between

If the points are missing or not numeric, `status` is left blank.

---

## 16. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `No .env file found` | Copy `.env.example` to `.env`. |
| `Live mode is missing required .env values` | Fill the live-mode variables, or set `EXPORT_MODE=offline`. |
| `Authentication failed (HTTP 401/403)` | The token is missing, expired, or invalid. Add a valid token to `.env`, or use offline mode. This tool will not create/refresh tokens. |
| `Input JSON file not found` | Check `INPUT_JSON_PATH`, or place a sample at `input/sample_response.json`. |
| `no usable rows could be extracted` | The response shape differs from expectations. The raw JSON was still saved — share it so the parser can be adjusted. |
| `Missing dependency ...` | Run `pip install -r requirements.txt`, or just use the launcher. |
| macOS blocks the launcher | `chmod +x run_export.command`, then double-click again. |
| Want full tracebacks | Set `DEBUG=true` in `.env`. |

---

## 17. First-run validation checklist

- [ ] `.env` exists (copied from `.env.example`), `EXPORT_MODE=offline`.
- [ ] `input/sample_response.json` exists.
- [ ] Run the tool (terminal or launcher).
- [ ] A `raw_response_<timestamp>.json` appears in `output/`.
- [ ] A CSV **and** an XLSX appear in `output/` with the 7 expected columns.
- [ ] `status` values look right (correct / incorrect / partial) for the sample.
- [ ] Re-running produces **new** timestamped files (nothing overwritten).

---

## 18. Next step once API token access is restored

1. Set `EXPORT_MODE=live` in `.env`.
2. Add a valid `LEARNWORLDS_ACCESS_TOKEN` and `LEARNWORLDS_SCHOOL_ID`.
3. Set `ASSESSMENT_ID`.
4. Run the tool — it calls `GET /v2/assessments/{ASSESSMENT_ID}/responses` and saves the real
   raw JSON to `output/`.
5. If the real response shape differs, **adjust the parser** in `normalize_responses()` using the
   saved raw JSON.
6. **Validate** the export against the native LearnWorlds **PDF feedback report** for a small
   sample of learners, checking:
   - learner email
   - final score
   - question text
   - submitted answer
   - points earned
   - max points
   - calculated correct / incorrect / partial status
