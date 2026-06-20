#!/usr/bin/env python3
"""Extract correct answers from professor Word docs via OpenAI.

Reads an exam_config_as_is.csv (from run_exam_config.py), sends verifiable questions
plus enriched Word doc text to an OpenAI model, and writes manual_answer_key.csv.

Usage:
    python tools/extract_answer_key.py \\
        --exam-config "output/uc5/2026-06-19_123456/exam_config_as_is.csv" \\
        --docs "input/docs/5.3 Fintech.docx" "input/docs/6.2 Exame.docx" \\
        [--model gpt-4o] \\
        [--output "output/uc5/2026-06-19_123456/manual_answer_key.csv"]

Output columns (manual_answer_key.csv):
    question_number, blockType, question_text, lw_correct_answer, is_gap,
    doc_question_number, doc_correct_answer, answers_match, confidence,
    needs_review, notes, source_doc

Security: OPENAI_API_KEY is read from .env only — never logged or written to any file.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXCLUDE_TYPES = {"TP", "TFU"}

ANSWER_KEY_COLUMNS = [
    "question_number",
    "blockType",
    "question_text",
    "lw_correct_answer",
    "is_gap",
    "doc_question_number",
    "doc_correct_answer",
    "answers_match",
    "confidence",
    "needs_review",
    "notes",
    "source_doc",
]

CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1, "unmatched": 0}

SYSTEM_PROMPT = """\
You are an answer key extraction assistant. You will receive:
1. A JSON list of questions from a Learning Management System (LMS), each with its answer options.
2. The extracted text of a professor's Word document containing the answer key.

Your task: for each LMS question, find the matching question in the document and identify the \
correct answer text.

Rules:
- Do NOT invent answers. Only extract answers that are explicitly marked in the document.
- If you cannot find the question or no answer is clearly marked, set confidence to "unmatched".
- Return the correct answer text as it appears in the LMS answer options (not as in the document), \
so it can be matched against the LMS configuration. If the option text differs slightly between \
the document and LMS, use the LMS option text.
- Use question text as the primary match signal; question numbers as secondary (they may differ).

Correct answer marking conventions (look for any of these):
1. BOLD TEXT — the correct answer paragraph or run is wrapped in [BOLD]...[/BOLD]
2. CUSTOM WORD STYLE — the paragraph has [STYLE:Correct Answer] or similar non-Normal style
3. ASTERISK PREFIX — the option text starts with "*"
4. EXPLICIT TEXT LABEL — "Resposta: B)", "Resposta Correta: C)", "Correct answer: ..." near question
5. TABLE CELL LABEL — in a [TABLE], the option row/cell label contains "Opção correta", "Correct"
6. COLOUR — the correct option is wrapped in [COLOR:#RRGGBB]...[/COLOR] with a non-black colour
7. CHECKMARK OR SYMBOL — the option is preceded by ✓, ✔, or a similar tick character
8. UNDERLINE — the correct option is explicitly underlined while others are not
9. STRIKETHROUGH ON WRONG — wrong options have strikethrough; correct one is unmarked

The document text uses [BOLD], [STYLE:...], [COLOR:#...], [TABLE] / [/TABLE] markers inserted \
during extraction to preserve Word formatting that would otherwise be invisible in plain text.

For True/False questions, identify which of Verdadeiro/Verdade/True or Falso/False is marked correct.
For TMCMA (multiple correct answers), return all correct options separated by "; ".

Confidence rubric:
- "high": question text matches clearly, correct answer marking is unambiguous
- "medium": question text matches with minor wording differences, or marking is clear but match has \
minor uncertainty
- "low": uncertain question match OR ambiguous/conflicting answer marking
- "unmatched": question not found in document, or found but no answer is marked

Return a single JSON object with a "results" key containing one entry per LMS question, in the \
same order as the input:

{"results": [
  {
    "lw_question_number": 1,
    "doc_question_number": "3",
    "doc_correct_answer": "Lisboa",
    "confidence": "high",
    "notes": "Question matched by text; correct answer marked [BOLD] in document."
  },
  ...
]}
"""


# ---------------------------------------------------------------------------
# Text normalization (mirrors reconcile/core.py — kept local to avoid import)
# ---------------------------------------------------------------------------

def _norm(value: object) -> str:
    s = unicodedata.normalize("NFKC", "" if value is None else str(value)).lower()
    return re.sub(r"\s+", " ", s).strip()


def _join_key(value: object) -> str:
    return re.sub(r"\W+", "", _norm(value), flags=re.UNICODE)


def _answers_match(lw: str, doc: str) -> str:
    lw_k = _join_key(lw)
    doc_k = _join_key(doc)
    if not lw_k and not doc_k:
        return ""
    if not doc_k:
        return "lw_only"
    if not lw_k:
        return "doc_only"
    return "yes" if lw_k == doc_k else "no"


# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------

def _load_env() -> dict:
    try:
        from dotenv import dotenv_values
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency 'python-dotenv'. Run: pip install -r requirements.txt"
        ) from exc

    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        raise SystemExit(f".env file not found at {env_path}. Copy .env.example and fill it in.")

    values = dotenv_values(env_path)
    api_key = (values.get("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise SystemExit(
            "OPENAI_API_KEY is not set in .env.\n"
            "Add it to .env (never commit the key)."
        )
    return {
        "api_key": api_key,
        "model": (values.get("OPENAI_MODEL") or "gpt-4o").strip() or "gpt-4o",
    }


# ---------------------------------------------------------------------------
# Exam config loading
# ---------------------------------------------------------------------------

def _parse_json_or_str(raw: str) -> str:
    """Deserialise a JSON-array cell from the CSV back to a readable string."""
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return "; ".join(str(a) for a in parsed)
        return str(parsed)
    except (json.JSONDecodeError, TypeError):
        return raw


def load_exam_config(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"exam_config_as_is.csv not found: {path}")

    rows = []
    with path.open(encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            bt = (row.get("blockType") or "").strip().upper()
            if not bt or bt in EXCLUDE_TYPES:
                continue
            rows.append(row)

    if not rows:
        raise SystemExit(
            "No verifiable questions found in exam_config "
            "(all rows were TP/TFU or had no blockType)."
        )
    return rows


# ---------------------------------------------------------------------------
# Word document text extraction (enriched with formatting markers)
# ---------------------------------------------------------------------------

def _run_color_hex(run) -> str | None:
    try:
        color = run.font.color
        if color and color.type is not None and color.rgb is not None:
            rgb = str(color.rgb).upper().strip()
            if rgb and rgb not in ("000000", "AUTO"):
                return rgb
    except Exception:
        pass
    return None


def _is_para_bold(para) -> bool:
    runs = [r for r in para.runs if r.text]
    if not runs:
        return False
    bold_chars = sum(len(r.text) for r in runs if r.bold)
    total_chars = sum(len(r.text) for r in runs)
    return total_chars > 0 and (bold_chars / total_chars) > 0.5


def _para_to_line(para) -> str:
    style_name = (para.style.name if para.style else "") or ""
    is_normal_style = style_name.lower() in (
        "normal", "default paragraph style", "", "body text", "no spacing",
    )

    parts: list[str] = []
    for run in para.runs:
        text = run.text
        if not text:
            continue
        color = _run_color_hex(run)
        if run.bold:
            text = f"[BOLD]{text}[/BOLD]"
        if color:
            text = f"[COLOR:#{color}]{text}[/COLOR]"
        parts.append(text)

    line = "".join(parts).strip()
    if not line:
        return ""

    if not is_normal_style and style_name:
        line = f"[STYLE:{style_name}] {line}"

    return line


def extract_doc_text(path: Path) -> str:
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency 'python-docx'. Run: pip install python-docx"
        ) from exc

    doc = Document(path)
    sections: list[str] = []

    for block in doc.element.body:
        tag = block.tag.split("}")[-1] if "}" in block.tag else block.tag

        if tag == "p":
            para = Paragraph(block, doc)
            line = _para_to_line(para)
            if line:
                sections.append(line)

        elif tag == "tbl":
            tbl = Table(block, doc)
            table_lines = ["[TABLE]"]
            for row in tbl.rows:
                cells: list[str] = []
                seen: set[int] = set()
                for cell in row.cells:
                    cid = id(cell._element)
                    if cid in seen:
                        continue
                    seen.add(cid)
                    cell_parts = [_para_to_line(p) for p in cell.paragraphs]
                    cell_text = " | ".join(p for p in cell_parts if p)
                    cells.append(cell_text)
                table_lines.append(" | ".join(cells))
            table_lines.append("[/TABLE]")
            sections.append("\n".join(table_lines))

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# OpenAI call
# ---------------------------------------------------------------------------

def _build_questions_payload(rows: list[dict]) -> list[dict]:
    payload = []
    for row in rows:
        options_raw = row.get("options_raw") or ""
        try:
            options = json.loads(options_raw)
            if not isinstance(options, list):
                options = [options_raw] if options_raw else []
        except (json.JSONDecodeError, TypeError):
            options = [options_raw] if options_raw else []

        lw_answer = _parse_json_or_str(row.get("configured_accepted_answers_raw") or "")

        payload.append({
            "question_number": row.get("question_number", ""),
            "blockType": row.get("blockType", ""),
            "question_text": row.get("description", ""),
            "options": [str(o) for o in options if o is not None],
            "lw_correct_answer": lw_answer,
        })
    return payload


def call_openai(client, model: str, questions: list[dict], doc_text: str) -> list[dict]:
    user_msg = (
        "LMS questions:\n"
        + json.dumps(questions, ensure_ascii=False, indent=2)
        + "\n\nDocument text:\n"
        + doc_text
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )

    content = response.choices[0].message.content
    parsed = json.loads(content)

    if isinstance(parsed, list):
        return parsed
    for key in ("results", "answers", "questions", "data", "items"):
        if isinstance(parsed.get(key), list):
            return parsed[key]

    raise ValueError(
        f"Unexpected LLM response structure — got keys: {list(parsed.keys())}.\n"
        f"Full response: {content[:500]}"
    )


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=ANSWER_KEY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def run(
    exam_config: str,
    docs: list[str],
    model: str = "",
    output: str = "",
    run_dir: str = "",
) -> int:
    env = _load_env()
    effective_model = model or env["model"]

    # When --run-dir is provided, derive defaults for exam-config and output.
    if run_dir:
        rd = Path(run_dir)
        if not exam_config:
            exam_config = str(rd / "exam_config" / "exam_config_as_is.csv")
        if not output:
            output = str(rd / "answer_key" / "manual_answer_key.csv")

    exam_config_path = Path(exam_config).expanduser()
    if not exam_config_path.is_absolute():
        exam_config_path = PROJECT_ROOT / exam_config_path

    output_path = (
        Path(output).expanduser()
        if output
        else exam_config_path.parent / "manual_answer_key.csv"
    )

    rows = load_exam_config(exam_config_path)
    gaps = sum(1 for r in rows if not (r.get("configured_accepted_answers_raw") or "").strip())
    print(f"Loaded {len(rows)} verifiable question(s) from exam_config ({gaps} without LW answer).")

    questions_payload = _build_questions_payload(rows)

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency 'openai'. Run: pip install openai"
        ) from exc

    client = OpenAI(api_key=env["api_key"])

    best: dict[str, dict] = {}

    for doc_str in docs:
        doc_path = Path(doc_str).expanduser()
        if not doc_path.is_absolute():
            doc_path = PROJECT_ROOT / doc_path
        if not doc_path.exists():
            print(f"WARNING: doc not found, skipping: {doc_path}", file=sys.stderr)
            continue

        print(f"Processing: {doc_path.name}")
        doc_text = extract_doc_text(doc_path)
        print(f"  {len(doc_text):,} chars of enriched text extracted.")

        try:
            llm_results = call_openai(client, effective_model, questions_payload, doc_text)
        except Exception as exc:
            print(f"  ERROR calling OpenAI for {doc_path.name}: {exc}", file=sys.stderr)
            continue

        for item in llm_results:
            qn = str(item.get("lw_question_number", ""))
            conf = item.get("confidence", "unmatched")
            prev = best.get(qn)
            if prev is None or CONFIDENCE_RANK.get(conf, 0) > CONFIDENCE_RANK.get(
                prev.get("confidence", "unmatched"), 0
            ):
                best[qn] = {**item, "source_doc": doc_path.name}

    output_rows: list[dict] = []
    for row in rows:
        qn = str(row.get("question_number", ""))
        lw_answer = _parse_json_or_str(row.get("configured_accepted_answers_raw") or "")
        is_gap = not lw_answer.strip()

        match_data = best.get(qn, {})
        doc_answer = str(match_data.get("doc_correct_answer") or "")
        confidence = match_data.get("confidence", "unmatched")

        am = _answers_match(lw_answer, doc_answer)
        needs_review = confidence in ("low", "unmatched") or am == "no"

        output_rows.append({
            "question_number": qn,
            "blockType": row.get("blockType", ""),
            "question_text": row.get("description", ""),
            "lw_correct_answer": lw_answer,
            "is_gap": "true" if is_gap else "false",
            "doc_question_number": str(match_data.get("doc_question_number") or ""),
            "doc_correct_answer": doc_answer,
            "answers_match": am,
            "confidence": confidence,
            "needs_review": "true" if needs_review else "false",
            "notes": str(match_data.get("notes") or ""),
            "source_doc": match_data.get("source_doc", ""),
        })

    _write_csv(output_rows, output_path)

    conf_counts = Counter(r["confidence"] for r in output_rows)
    discrepancies = sum(1 for r in output_rows if r["answers_match"] == "no")
    needs_review_n = sum(1 for r in output_rows if r["needs_review"] == "true")

    print(
        f"\nResults: {len(output_rows)} question(s) processed\n"
        f"  Confidence — high: {conf_counts.get('high', 0)}, "
        f"medium: {conf_counts.get('medium', 0)}, "
        f"low: {conf_counts.get('low', 0)}, "
        f"unmatched: {conf_counts.get('unmatched', 0)}\n"
        f"  Answer discrepancies (LW ≠ Word doc): {discrepancies}\n"
        f"  Needs human review: {needs_review_n}\n"
        f"\nOutput: {output_path}"
    )
    print("Done.")
    return 0


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Extract correct answers from professor Word docs via OpenAI."
    )
    parser.add_argument(
        "--exam-config",
        default="",
        help="Path to exam_config_as_is.csv (from run_exam_config.py). "
        "Auto-inferred from --run-dir when not provided.",
    )
    parser.add_argument(
        "--docs",
        nargs="+",
        default=[],
        help="One or more professor Word documents (.docx).",
    )
    parser.add_argument(
        "--model",
        default="",
        help="OpenAI model to use (default: OPENAI_MODEL in .env, fallback gpt-4o).",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output CSV path (default: manual_answer_key.csv alongside --exam-config).",
    )
    parser.add_argument(
        "--run-dir",
        default="",
        help="Shared run folder from the unified launcher. When set, exam-config is read "
        "from <run-dir>/exam_config/ and output goes to <run-dir>/answer_key/.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if not args.exam_config and not args.run_dir:
        print("ERROR: --exam-config is required unless --run-dir is provided.", file=sys.stderr)
        return 1
    if not args.docs:
        print("ERROR: --docs is required (one or more .docx files).", file=sys.stderr)
        return 1
    try:
        return run(
            exam_config=args.exam_config,
            docs=args.docs,
            model=args.model,
            output=args.output,
            run_dir=args.run_dir,
        )
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"\nUNEXPECTED ERROR: {exc}", file=sys.stderr)
        env_path = PROJECT_ROOT / ".env"
        try:
            from dotenv import dotenv_values
            if dotenv_values(env_path).get("DEBUG", "").lower() == "true":
                raise
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
