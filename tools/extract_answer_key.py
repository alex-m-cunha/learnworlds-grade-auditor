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
    question_number, blockType, lw_question_text, question_text, lw_correct_answer, is_gap,
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
    "lw_question_text",       # original LW description (reference)
    "question_text",          # question text as it appears in the Word doc
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

INFERRED_COLUMNS = [
    "blockType",
    "doc_question_number",
    "question_text",
    "doc_correct_answer",
    "confidence",
    "notes",
    "source_doc",
]

INFERRED_SYSTEM_PROMPT = """\
You are an answer key extraction assistant for questions that do NOT appear in the LMS configuration.
You will receive:
1. A JSON list of questions (fill-in-the-blank and matching types) found only in student submissions.
2. The extracted text of a professor's Word document containing the answer key.

These questions have NO predefined answer options in the LMS — you must extract the correct answer
directly from the document text.

Rules:
- Do NOT invent answers. Only extract answers explicitly present in the document.
- If you cannot find the question or no answer is marked, set confidence to "unmatched".
- Return the answer exactly as it appears in the document (no reformatting).
- Use the question text as the primary match signal (numbers may differ between LMS and document).

For FILL-IN-THE-BLANK questions (question text contains [] for each blank):
- Look for labels like "Variações aceites (espaço 1):", "Espaço 1:", "Resposta espaço 1:",
  or similar numbering ("Espaço N:").
- Multiple accepted variants for the same blank are separated by ";" or "," or newlines.
- Return all blanks joined with " | " as separator; variants within each blank separated by "; ".
  Example: "Custo Médio Ponderado de Capital; custo médio ponderado de capital | capital; Capital"
- If the document gives only one answer per blank, return that single answer per blank.
- The document text uses [BOLD], [STYLE:...], [COLOR:#...], [TABLE]/[/TABLE] markers.
  "Variações aceites" labels are often bold or in a custom style.

For MATCH questions (question text asks students to match Column A with Column B):
- Look for colour-coded rows (same colour = same pair), explicit pair labels
  ("Par 1 — Coluna A / Coluna B"), or a two-column table where each row is a pair.
  Colour markers appear as [COLOR:#RRGGBB] in the text.
- Return each pair as "Column A text → Column B text"; pairs separated by "; ".
  Example: "Forward cambial → Fixa a taxa de câmbio; Swap de taxa de juro → Troca taxa variável"

Confidence rubric:
- "high": question clearly found, answer is unambiguously marked.
- "medium": question found with minor uncertainty, or answer marking requires interpretation.
- "low": question found but answer is ambiguous or unclear.
- "unmatched": question not found in document, or found but no answer is present.

Return a single JSON object with a "results" key:
{"results": [
  {
    "question_index": 0,
    "doc_question_number": "7",
    "doc_correct_answer": "Custo Médio Ponderado de Capital; custo médio ponderado de capital | capital",
    "confidence": "high",
    "notes": "Found fill-in-blank with Variações aceites labels for 2 blanks."
  },
  ...
]}

"doc_question_number" is the question number from the Word document. Each question in the
document is preceded by a [QUESTION:N] marker — use that N directly (e.g. [QUESTION:5] → "5").
"""

SYSTEM_PROMPT = """\
You are an answer key extraction assistant. You will receive:
1. A JSON list of LMS question texts — search references only, no answer options.
2. The extracted text of a professor's Word document containing the answer key.

Your task: for each LMS question, find the matching question in the Word document and return:
- "doc_question_text": the question text EXACTLY AS IT APPEARS IN THE DOCUMENT (not the LMS version).
- "doc_correct_answer": the correct answer EXACTLY AS IT APPEARS IN THE DOCUMENT.

Rules:
- Do NOT invent answers. Only extract answers that are explicitly marked in the document.
- If you cannot find the question or no answer is clearly marked, set confidence to "unmatched".
- Return both the question text and the answer text verbatim from the document — do NOT normalise
  or paraphrase to match any LMS wording. Minor whitespace cleanup is acceptable.
- Match primarily by question text; use question numbers only as a secondary hint.

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
Some questions in the document are preceded by a [QUESTION:N] marker — when present, use that
N as "doc_question_number". When absent (e.g. paragraph-style questions), infer the number from
the question text prefix ("1.", "2.", etc.) or leave it empty if unclear.

For True/False questions, identify which of Verdadeiro/Verdade/True or Falso/False is marked correct.
For TMCMA (multiple correct answers), return all correct options separated by "; ".

Confidence rubric:
- "high": question text matches clearly, correct answer marking is unambiguous
- "medium": question text matches with minor wording differences, or marking is clear but match has \
minor uncertainty
- "low": uncertain question match OR ambiguous/conflicting answer marking
- "unmatched": question not found in document, or found but no answer is marked

Return a single JSON object with a "results" key, one entry per input question. Each entry MUST
echo the "qid" from the input so results can be matched back regardless of order:

{"results": [
  {
    "qid": "a",
    "doc_question_number": "3",
    "doc_question_text": "Qual é a capital de Portugal?",
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


_OPTION_PREFIX_RE = re.compile(r"^[a-zA-Z][.)]\s*", re.UNICODE)
_TTF_NORMALISE = {
    "verdadeira": "Verdadeiro", "verdade": "Verdadeiro", "true": "Verdadeiro",
    "falsa": "Falso", "false": "Falso",
}


def _clean_doc_answer(answer: str, block_type: str) -> str:
    """Strip option letter prefix and normalise TTF gender variants."""
    cleaned = _OPTION_PREFIX_RE.sub("", answer.strip())
    if (block_type or "").upper() == "TTF":
        cleaned = _TTF_NORMALISE.get(cleaned.strip().lower(), cleaned)
    return cleaned


def _answers_match(lw: str, doc: str) -> str:
    def _as_key_set(v: str) -> frozenset:
        parts = [_join_key(p) for p in v.split(";")]
        return frozenset(p for p in parts if p)

    lw_s = _as_key_set(lw)
    doc_s = _as_key_set(doc)
    if not lw_s and not doc_s:
        return ""
    if not doc_s:
        return "lw_only"
    if not lw_s:
        return "doc_only"
    return "yes" if lw_s == doc_s else "no"


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
    try:
        from docx.text.run import Run
    except ImportError:
        return False
    runs = [Run(e, para) for t, e in _iter_inline_items(para._p) if t == "r"]
    runs = [r for r in runs if r.text]
    if not runs:
        return False
    bold_chars = sum(len(r.text) for r in runs if r.bold)
    total_chars = sum(len(r.text) for r in runs)
    return total_chars > 0 and (bold_chars / total_chars) > 0.5


def _para_to_line(para) -> str:
    try:
        from docx.text.run import Run
    except ImportError:
        Run = None

    style_name = (para.style.name if para.style else "") or ""
    is_normal_style = style_name.lower() in (
        "normal", "default paragraph style", "", "body text", "no spacing",
    )

    parts: list[str] = []
    for tag, child in _iter_inline_items(para._p):
        if tag != "r":
            continue
        run = Run(child, para) if Run else None
        if run is None:
            continue
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


_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _sdt_content(elem):
    """Return the <w:sdtContent> child of an <w:sdt> element, or None."""
    return elem.find(f"{{{_NS_W}}}sdtContent")


def _iter_block_items(parent):
    """Yield (tag, element) for block-level children, recursively unwrapping <w:sdt>."""
    for child in parent:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "sdt":
            content = _sdt_content(child)
            if content is not None:
                yield from _iter_block_items(content)
        else:
            yield tag, child


def _iter_paras(elem):
    """Yield <w:p> elements from elem, recursively unwrapping <w:sdt> containers."""
    for child in elem:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "sdt":
            content = _sdt_content(child)
            if content is not None:
                yield from _iter_paras(content)
        elif tag == "p":
            yield child


def _iter_inline_items(p_elem):
    """Yield (tag, element) for inline children of <w:p>, recursively unwrapping inline <w:sdt>."""
    for child in p_elem:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "sdt":
            content = _sdt_content(child)
            if content is not None:
                yield from _iter_inline_items(content)
        else:
            yield tag, child


def _render_table_rows(tbl_elem, doc) -> list[str]:
    """Return table content as a list of row strings, recursively handling nested tables.

    Cells with nested tables: the nested rows are appended immediately after the
    containing row so the LLM sees a flat list of rows in document order.
    """
    from docx.text.paragraph import Paragraph

    rows: list[str] = []
    for tag_row, tr_elem in _iter_block_items(tbl_elem):
        if tag_row != "tr":
            continue
        row_cells: list[str] = []
        seen: set[int] = set()
        nested_after: list[list[str]] = []

        for tag_c, tc_elem in _iter_block_items(tr_elem):
            if tag_c != "tc":
                continue
            cid = id(tc_elem)
            if cid in seen:
                continue
            seen.add(cid)

            para_parts: list[str] = []
            for btag, bchild in _iter_block_items(tc_elem):
                if btag == "p":
                    line = _para_to_line(Paragraph(bchild, doc))
                    if line:
                        para_parts.append(line)
                elif btag == "tbl":
                    nested_after.append(_render_table_rows(bchild, doc))

            row_cells.append(" | ".join(para_parts))

        row_text = " | ".join(c for c in row_cells if c)
        if row_text:
            rows.append(row_text)
        for nested in nested_after:
            rows.extend(nested)

    return rows


_NS_W_URI = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_EXPLICIT_Q_NUM_RE = re.compile(
    r"(?:^|(?<=\s))(\d+)[.)]\s"          # "7. " or "7) "
    r"|(?:Pergunta|Questão|Question)\s*[nN]?[°º.]?\s*(\d+)",  # "Pergunta 7" / "Question 7"
    re.IGNORECASE,
)


def _first_row_numpr(tbl_elem) -> tuple | None:
    """Return (numId, ilvl) for the first auto-numbered paragraph in the table's first row, or None."""
    for tag_row, tr_elem in _iter_block_items(tbl_elem):
        if tag_row != "tr":
            continue
        for tag_c, tc_elem in _iter_block_items(tr_elem):
            if tag_c != "tc":
                continue
            for btag, bchild in _iter_block_items(tc_elem):
                if btag != "p":
                    continue
                nid_el = bchild.find(f".//{{{_NS_W_URI}}}numId")
                ilvl_el = bchild.find(f".//{{{_NS_W_URI}}}ilvl")
                if nid_el is not None:
                    nid = nid_el.get(f"{{{_NS_W_URI}}}val", "0")
                    ilvl = ilvl_el.get(f"{{{_NS_W_URI}}}val", "0") if ilvl_el is not None else "0"
                    return (nid, ilvl)
            return None  # only inspect first cell
        return None      # only inspect first row
    return None


def extract_doc_text(path: Path) -> str:
    try:
        from docx import Document
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency 'python-docx'. Run: pip install python-docx"
        ) from exc

    doc = Document(path)
    sections: list[str] = []
    tbl_n = 0
    numpr_counters: dict = {}  # (numId, ilvl) -> running count

    for tag, block in _iter_block_items(doc.element.body):
        if tag == "p":
            para = Paragraph(block, doc)
            line = _para_to_line(para)
            if line:
                sections.append(line)

        elif tag == "tbl":
            tbl_n += 1
            inner = _render_table_rows(block, doc)

            # Priority 1: explicit number in first-row text ("7." / "Pergunta 7")
            q_num: str | None = None
            if inner:
                m = _EXPLICIT_Q_NUM_RE.search(inner[0])
                if m:
                    q_num = m.group(1) or m.group(2)

            # Priority 2: auto-numbering via numPr (always increment counter)
            numpr = _first_row_numpr(block)
            if numpr:
                numpr_counters[numpr] = numpr_counters.get(numpr, 0) + 1
                if q_num is None:
                    q_num = str(numpr_counters[numpr])

            # Determine if this table looks like a question table.
            # Data tables (e.g. balance sheets, income statements) should NOT get
            # a [QUESTION:N] marker — it misleads the LLM into treating them as
            # questions and confuses matching for questions that follow as paragraphs.
            _QUESTION_TABLE_KEYWORDS = (
                "pergunta", "enunciado", "par ", "par\t", "opção", "opcao",
                "resposta", "question", "statement",
            )
            first_cell_text = inner[0].lower() if inner else ""
            is_question_table = (
                q_num is not None  # has explicit number or list numbering
                or any(kw in first_cell_text for kw in _QUESTION_TABLE_KEYWORDS)
            )

            # [QUESTION:N] lets the LLM read the question number directly;
            # only emit it for actual question tables.
            if is_question_table:
                if q_num is None:
                    q_num = str(tbl_n)
                sections.append(f"[QUESTION:{q_num}]\n[TABLE]\n" + "\n".join(inner) + "\n[/TABLE]")
            else:
                sections.append(f"[TABLE]\n" + "\n".join(inner) + "\n[/TABLE]")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# OpenAI call
# ---------------------------------------------------------------------------

# Alphabetic batch IDs — single letters a–z then aa, ab, … (never look like question numbers)
def _batch_id(i: int) -> str:
    letters = "abcdefghijklmnopqrstuvwxyz"
    if i < 26:
        return letters[i]
    return letters[i // 26 - 1] + letters[i % 26]


def _build_questions_payload(rows: list[dict]) -> list[dict]:
    """Build LLM payload. Each question gets an opaque alphabetic 'qid' (never a number)
    so the LLM can echo it back for stable mapping without anchoring on question numbers."""
    return [
        {
            "qid": _batch_id(i),
            "blockType": row.get("blockType", ""),
            "question_text": row.get("description", ""),
            "_lw_question_number": row.get("question_number", ""),  # internal only, stripped below
        }
        for i, row in enumerate(rows)
    ]


_QUESTION_BATCH_SIZE = 15  # questions per LLM call to avoid context overflow


def _call_openai_batch(client, model: str, questions: list[dict], doc_text: str) -> list[dict]:
    """Single LLM call for one batch of questions against one doc."""
    # Strip internal fields before sending — only blockType and question_text go to the LLM.
    llm_questions = [
        {k: v for k, v in q.items() if not k.startswith("_")}
        for q in questions
    ]
    user_msg = (
        "LMS question texts (search references only — no answer options):\n"
        + json.dumps(llm_questions, ensure_ascii=False, indent=2)
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


def call_openai(client, model: str, questions: list[dict], doc_text: str) -> list[dict]:
    """Call LLM in batches of _QUESTION_BATCH_SIZE to avoid context overflow."""
    all_results: list[dict] = []
    for i in range(0, len(questions), _QUESTION_BATCH_SIZE):
        batch = questions[i : i + _QUESTION_BATCH_SIZE]
        batch_num = i // _QUESTION_BATCH_SIZE + 1
        total_batches = (len(questions) + _QUESTION_BATCH_SIZE - 1) // _QUESTION_BATCH_SIZE
        if total_batches > 1:
            print(f"    batch {batch_num}/{total_batches} ({len(batch)} questions)...")
        batch_results = _call_openai_batch(client, model, batch, doc_text)
        # Map by qid — stable regardless of the order the LLM returns results.
        qid_to_qn = {q["qid"]: q["_lw_question_number"] for q in batch}
        for item in batch_results:
            item["lw_question_number"] = qid_to_qn.get(item.get("qid", ""), "")
        all_results.extend(batch_results)
    return all_results


# ---------------------------------------------------------------------------
# Inferred extraction (Phase 2: orphan questions not in exam config)
# ---------------------------------------------------------------------------

def _find_orphan_questions(sub_path: Path, exam_config_rows: list[dict]) -> list[dict]:
    """Find unique fillInTheBlank/match questions in submissions not in exam config."""
    config_keys = {_join_key(r.get("description", "")) for r in exam_config_rows}
    orphan_types = {"fillintheblankblock", "match"}
    seen: set[str] = set()
    orphans: list[dict] = []
    with sub_path.open(encoding="utf-8-sig", newline="") as fh:
        import csv as _csv
        for row in _csv.DictReader(fh):
            bt = (row.get("blockType") or "").strip().lower()
            if bt not in orphan_types:
                continue
            desc = row.get("description", "").strip()
            key = _join_key(desc)
            if not key or key in config_keys or key in seen:
                continue
            seen.add(key)
            orphans.append({"blockType": row.get("blockType", ""), "question_text": desc})
    return orphans


def _call_openai_inferred(client, model: str, questions: list[dict], doc_text: str) -> list[dict]:
    user_msg = (
        "Questions without LMS options (fill-in-the-blank and match types):\n"
        + json.dumps(questions, ensure_ascii=False, indent=2)
        + "\n\nDocument text:\n"
        + doc_text
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": INFERRED_SYSTEM_PROMPT},
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
    raise ValueError(f"Unexpected inferred LLM response: keys={list(parsed.keys())}")


def _extract_inferred(
    client,
    model: str,
    orphan_questions: list[dict],
    docs: list[str],
) -> list[dict]:
    """Send orphan questions to LLM with INFERRED_SYSTEM_PROMPT; aggregate by best confidence."""
    payload = [
        {"question_index": i, "blockType": q["blockType"], "question_text": q["question_text"]}
        for i, q in enumerate(orphan_questions)
    ]
    best: dict[int, dict] = {}

    for doc_str in docs:
        doc_path = Path(doc_str).expanduser()
        if not doc_path.is_absolute():
            doc_path = PROJECT_ROOT / doc_path
        if not doc_path.exists():
            continue
        doc_text = extract_doc_text(doc_path)
        try:
            results = _call_openai_inferred(client, model, payload, doc_text)
        except Exception as exc:
            print(f"  ERRO inferência [{doc_path.name}]: {exc}", file=sys.stderr)
            continue
        for item in results:
            qi = item.get("question_index")
            if qi is None:
                continue
            qi = int(qi)
            conf = item.get("confidence", "unmatched")
            prev = best.get(qi)
            if prev is None or CONFIDENCE_RANK.get(conf, 0) > CONFIDENCE_RANK.get(
                prev.get("confidence", "unmatched"), 0
            ):
                best[qi] = {**item, "source_doc": doc_path.name}

    rows = []
    for i, q in enumerate(orphan_questions):
        m = best.get(i, {})
        rows.append({
            "blockType": q["blockType"],
            "doc_question_number": str(m.get("doc_question_number") or ""),
            "question_text": q["question_text"],
            "doc_correct_answer": str(m.get("doc_correct_answer") or ""),
            "confidence": m.get("confidence", "unmatched"),
            "notes": str(m.get("notes") or ""),
            "source_doc": m.get("source_doc", ""),
        })
    return rows


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=ANSWER_KEY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_inferred_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=INFERRED_COLUMNS, extrasaction="ignore")
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
        doc_answer = _clean_doc_answer(
            str(match_data.get("doc_correct_answer") or ""),
            row.get("blockType", ""),
        )
        confidence = match_data.get("confidence", "unmatched")

        lw_desc = row.get("description", "")
        # Use Word doc question text when LLM found the question; fall back to LW description.
        doc_question_text = str(match_data.get("doc_question_text") or "") or lw_desc

        am = _answers_match(lw_answer, doc_answer)
        needs_review = confidence in ("low", "unmatched") or am == "no"

        output_rows.append({
            "question_number": qn,
            "blockType": row.get("blockType", ""),
            "lw_question_text": lw_desc,
            "question_text": doc_question_text,
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

    # Phase 2: infer answers for orphan questions (fillInTheBlank, match)
    # Only runs when --run-dir is provided (submissions path is knowable).
    if run_dir:
        sub_path = Path(run_dir) / "submissions" / "submissions_export.csv"
        if sub_path.exists():
            orphans = _find_orphan_questions(sub_path, rows)
            if orphans:
                print(
                    f"\nFase 2: {len(orphans)} pergunta(s) não encontrada(s) no gabarito LW "
                    f"(preenchimento de lacunas / correspondência) — a inferir via Word..."
                )
                inferred_rows = _extract_inferred(client, effective_model, orphans, docs)
                inferred_path = Path(run_dir) / "answer_key" / "inferred_answer_key.csv"
                _write_inferred_csv(inferred_rows, inferred_path)
                inf_conf = Counter(r["confidence"] for r in inferred_rows)
                print(
                    f"  Inferidas: high={inf_conf.get('high', 0)}, "
                    f"medium={inf_conf.get('medium', 0)}, "
                    f"low={inf_conf.get('low', 0)}, "
                    f"unmatched={inf_conf.get('unmatched', 0)}\n"
                    f"  Respostas inferidas escritas em: {inferred_path}"
                )
            else:
                print("\nFase 2: não há perguntas orphan — todas têm gabarito LW.")
        else:
            print(f"\nFase 2: submissions_export.csv não encontrado em {sub_path} — ignorado.")

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
