"""Core deterministic logic: text normalization + the reconciliation rules.

No I/O here. Everything is exact and reproducible. Numeric work uses Decimal so
fractional points (e.g. 0.44) never produce false mismatches.
"""

from __future__ import annotations

import json
import re
import unicodedata
from decimal import Decimal, InvalidOperation

# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------
def norm(value) -> str:
    """Lowercase, NFKC, collapse whitespace. For comparing answer/option text."""
    s = unicodedata.normalize("NFKC", "" if value is None else str(value)).lower()
    return re.sub(r"\s+", " ", s).strip()


def join_key(value) -> str:
    """Alphanumeric-only key for matching question text across sources.

    Robust to micro-formatting differences (spaces around symbols, punctuation).
    """
    return re.sub(r"\W+", "", norm(value), flags=re.UNICODE)


def to_decimal(value):
    """Parse to Decimal, or None when missing/non-numeric."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _as_list(raw):
    """Parse a JSON-string list cell (options_raw / accepted_answers) -> list."""
    if isinstance(raw, list):
        return raw
    if raw in (None, "", "[]"):
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


# ---------------------------------------------------------------------------
# Answer normalisation helpers for cross-source comparison
# ---------------------------------------------------------------------------

_OPTION_PREFIX_RE = re.compile(r"^[a-zA-Z][.)]\s*", re.UNICODE)

_TTF_CANONICAL: dict[str, str] = {
    "verdadeiro": "verdadeiro", "verdadeira": "verdadeiro",
    "verdade": "verdadeiro", "true": "verdadeiro", "v": "verdadeiro",
    "falso": "falso", "falsa": "falso", "false": "falso", "f": "falso",
}


def _strip_option_prefix(s: str) -> str:
    """Remove leading option label like 'e. ', 'b) ', 'a. ' from an answer string."""
    return _OPTION_PREFIX_RE.sub("", s.strip())


def _answers_truly_diverge(lw: str, doc: str, block_type: str) -> bool:
    """True only when LW and doc answers are genuinely different.

    Handles two common false-positive patterns produced by LLM extraction:
    - Option letter prefix in doc ("e. Todas as opções..." vs "Todas as opções...")
    - TTF gender variant ("Verdadeira" vs "Verdadeiro", "Falsa" vs "Falso")
    """
    if not lw or not doc:
        return bool(doc)  # doc_only case counts as divergence

    doc_clean = _strip_option_prefix(doc)
    bt = (block_type or "").lower()

    if bt == "ttf":
        lw_k = _TTF_CANONICAL.get(join_key(lw), join_key(lw))
        doc_k = _TTF_CANONICAL.get(join_key(doc_clean), join_key(doc_clean))
        return lw_k != doc_k

    # For all other types: set comparison on semicolon-split parts (handles MCMA and TST variants)
    lw_keys = frozenset(join_key(p) for p in lw.split(";") if join_key(p))
    doc_keys = frozenset(join_key(p) for p in doc_clean.split(";") if join_key(p))
    return lw_keys != doc_keys


# ---------------------------------------------------------------------------
# mcma (multi-select) reconstruction by containment
# ---------------------------------------------------------------------------
def reconstruct_mcma(answer, options):
    """Reconstruct the selected option set from a delimiter-less mcma answer.

    Comparison uses the alphanumeric join_key (punctuation/space-insensitive) so
    formatting differences between API and export don't break matching.

    Returns (selected_keys, ambiguous):
        selected_keys : option join_keys found inside the answer's join_key
        ambiguous     : True if one option's key is a substring of another's
                        (containment then unreliable -> caller marks unverifiable)
    """
    na = join_key(answer)
    keys = [join_key(o) for o in options if join_key(o)]
    ambiguous = any(a != b and a in b for a in keys for b in keys)
    selected = {k for k in keys if k and k in na}
    return selected, ambiguous


# ---------------------------------------------------------------------------
# Per-answer key check
# ---------------------------------------------------------------------------
def build_config_index(exam_config_rows) -> dict:
    """Map join_key(description) -> config entry. Last write wins on collision."""
    index = {}
    for row in exam_config_rows:
        index[join_key(row.get("description"))] = {
            "blockType": row.get("blockType"),
            "question_number": row.get("question_number"),
            "options": _as_list(row.get("options_raw")),
            "accepted": _as_list(row.get("configured_accepted_answers_raw")),
            "correct_raw": row.get("configured_correct_answer_raw"),
            "description": row.get("description"),
            "doc_correct_answer": None,
            "doc_confidence": None,
            "doc_overrides_lw": False,
        }
    return index


def build_merged_index(exam_config_rows, ak_rows) -> dict:
    """Build config index enriched with teacher answer key (gabarito ID / Docente).

    For questions where the teacher's doc disagrees with LW (answers_match=no)
    and confidence is high or medium, sets doc_overrides_lw=True so check_answer()
    uses the teacher's intended answer exclusively.

    Falls back to build_config_index() behaviour when ak_rows is empty.
    """
    index = build_config_index(exam_config_rows)
    if not ak_rows:
        return index

    ak_by_key = {}
    for row in ak_rows:
        k = join_key(row.get("question_text", ""))
        if k:
            ak_by_key[k] = row

    for key, entry in index.items():
        ak = ak_by_key.get(key)
        if ak is None:
            continue

        confidence = ak.get("confidence", "")
        answers_match = ak.get("answers_match", "")
        block_type = (entry.get("blockType") or "").lower()
        doc_answer = ak.get("doc_correct_answer", "")
        lw_answer = ak.get("lw_correct_answer", "")

        entry["doc_correct_answer"] = doc_answer or None
        entry["doc_confidence"] = confidence

        # Override when doc has a concrete answer, genuinely disagrees with LW
        # (re-verified with lenient comparison to avoid false positives from option
        # letter prefixes and TTF gender variants), and confidence is actionable.
        if (
            doc_answer
            and answers_match in ("no", "doc_only")
            and confidence in ("high", "medium")
            and _answers_truly_diverge(lw_answer, doc_answer, block_type)
        ):
            entry["doc_overrides_lw"] = True

    return index


def check_answer(block_type, answer, points, max_points, cfg_entry):
    """Apply the contradiction rule to one (learner, question) block.

    Returns dict: verifiable, is_correct, flag, configured_correct, accepted,
    answer_matched_source.
    - verifiable: "yes" | "no_config_match" | "unverifiable_mcma" | "no_answer_key"
    - flag: None | "answer_accepted_but_zero" | "answer_correct_per_doc_but_zero"
            | "answer_not_accepted_but_full" | "answer_accepted_but_partial"
            | "mcma_wrong_not_penalized"
    - answer_matched_source: "lw" | "doc" | ""
    """
    result = {
        "verifiable": "yes",
        "is_correct": None,
        "flag": None,
        "configured_correct": "",
        "accepted": [],
        "answer_matched_source": "",
    }
    if cfg_entry is None:
        result["verifiable"] = "no_config_match"
        return result

    result["configured_correct"] = cfg_entry.get("correct_raw") or ""
    result["accepted"] = cfg_entry["accepted"]

    # When LW and doc disagree (or LW has no answer), the teacher's doc takes full
    # precedence — verifying against a misconfigured LW answer would produce wrong results.
    if cfg_entry.get("doc_overrides_lw") and cfg_entry.get("doc_correct_answer"):
        doc_answer_raw = cfg_entry["doc_correct_answer"]
        bt = (block_type or "").lower()

        if bt == "mcma":
            doc_accepted_keys = {join_key(o) for o in doc_answer_raw.split(";") if join_key(o)}
            if doc_accepted_keys:
                selected, ambiguous = reconstruct_mcma(answer, cfg_entry["options"])
                if ambiguous:
                    result["verifiable"] = "unverifiable_mcma"
                    return result
                is_correct = selected == doc_accepted_keys
                result["is_correct"] = is_correct
                result["answer_matched_source"] = "doc" if is_correct else ""
                pts = to_decimal(points)
                mx = to_decimal(max_points)
                if pts is not None and mx is not None:
                    if is_correct and pts == 0:
                        result["flag"] = "answer_correct_per_doc_but_zero"
                    elif not is_correct and mx > 0 and pts == mx:
                        # All correct options selected plus extra wrong ones, yet full
                        # marks → LW did not penalise the wrong picks (not "not accepted").
                        if doc_accepted_keys <= selected:
                            result["flag"] = "mcma_wrong_not_penalized"
                        else:
                            result["flag"] = "answer_not_accepted_but_full"
                return result
            # Empty doc set — fall through to LW checking
        else:
            doc_key = join_key(doc_answer_raw)
            if doc_key:
                is_correct = join_key(answer) == doc_key
                result["is_correct"] = is_correct
                result["answer_matched_source"] = "doc" if is_correct else ""
                pts = to_decimal(points)
                mx = to_decimal(max_points)
                if pts is not None and mx is not None:
                    if is_correct and pts == 0:
                        result["flag"] = "answer_correct_per_doc_but_zero"
                    elif not is_correct and mx > 0 and pts == mx:
                        result["flag"] = "answer_not_accepted_but_full"
                return result

    accepted = cfg_entry["accepted"]
    if not accepted:
        result["verifiable"] = "no_answer_key"
        return result

    # Compare with the alphanumeric join_key so punctuation/whitespace
    # differences between the API answer and the exported option text
    # (e.g. comma vs dash) don't produce false mismatches.
    accepted_keys = {join_key(a) for a in accepted}

    is_mcma = (block_type or "").lower() == "mcma"
    selected: set = set()
    if is_mcma:
        selected, ambiguous = reconstruct_mcma(answer, cfg_entry["options"])
        if ambiguous:
            result["verifiable"] = "unverifiable_mcma"
            return result
        is_correct = selected == accepted_keys
        answer_matched_source = "lw" if is_correct else ""
    else:
        is_correct = join_key(answer) in accepted_keys
        answer_matched_source = "lw" if is_correct else ""

    result["is_correct"] = is_correct
    result["answer_matched_source"] = answer_matched_source

    pts = to_decimal(points)
    mx = to_decimal(max_points)
    if pts is None or mx is None:
        return result  # can't compare scoring; correctness still reported

    if is_correct and pts == 0:
        # Parametrization error: student followed teacher's intent but LW rejected it
        if answer_matched_source == "doc":
            result["flag"] = "answer_correct_per_doc_but_zero"
        else:
            result["flag"] = "answer_accepted_but_zero"
    elif (not is_correct) and mx > 0 and pts == mx:
        # MCMA with partial credit: when the student selected ALL accepted options
        # plus extra (wrong) ones and still scored full, LW simply did not penalise
        # the wrong picks — not an "answer not accepted" case. Flag it distinctly so
        # the report can say so accurately. The genuinely anomalous case (full marks
        # WITHOUT all correct options) keeps answer_not_accepted_but_full.
        if is_mcma and accepted_keys and accepted_keys <= selected:
            result["flag"] = "mcma_wrong_not_penalized"
        else:
            result["flag"] = "answer_not_accepted_but_full"
    elif is_correct and 0 < pts < mx:
        result["flag"] = "answer_accepted_but_partial"  # informational
    return result


# ---------------------------------------------------------------------------
# Inferred answer checking (fillInTheBlank / match orphan questions)
# ---------------------------------------------------------------------------

def build_inferred_index(inferred_rows) -> dict:
    """Map join_key(question_text) → inferred entry for orphan questions."""
    index = {}
    for row in inferred_rows:
        k = join_key(row.get("question_text", ""))
        if k:
            index[k] = {
                "blockType": row.get("blockType", ""),
                "question_text": row.get("question_text", ""),
                "inferred_correct_answer": row.get("doc_correct_answer", ""),
                "confidence": row.get("confidence", "unmatched"),
                "source_doc": row.get("source_doc", ""),
                "doc_question_number": row.get("doc_question_number", ""),
            }
    return index


_MIN_VARIANT_KEY_LEN = 15  # variants shorter than this are too generic for containment check


def _check_fill_in_blank(student_answer: str, inferred_answer: str) -> tuple[bool, str | None]:
    """Check fillInTheBlank: each blank against its accepted variants.

    Inferred format: "var1; var2 | var3; var4" (blanks by " | ", variants by "; ")
    Student format:  "answer1 | answer2" (blanks by " | ")

    Returns (is_correct, flag) where flag may be "fill_in_blank_over_answered" when
    a student wrote a correct variant but with extra surrounding text (LW rejects
    because it expects an exact match). Only triggered for variants ≥ _MIN_VARIANT_KEY_LEN
    chars to avoid false positives with short generic words like "capital".
    """
    inferred_blanks = [b.strip() for b in inferred_answer.split(" | ")]
    student_blanks = [b.strip() for b in student_answer.split(" | ")]
    if len(student_blanks) != len(inferred_blanks):
        return False, None
    all_correct = True
    any_over_answered = False
    for s_blank, i_blank in zip(student_blanks, inferred_blanks):
        s_key = join_key(s_blank)
        variants = [join_key(v.strip()) for v in i_blank.split(";")]
        if s_key in variants:
            continue
        all_correct = False
        if any(len(v) >= _MIN_VARIANT_KEY_LEN and v in s_key for v in variants):
            any_over_answered = True
    flag = "fill_in_blank_over_answered" if any_over_answered else None
    return all_correct, flag


def _check_match(student_answer: str, inferred_answer: str) -> bool:
    """Check match question: verify each gabarito A→B pair appears in sequence
    in the student answer, using join_key for normalisation.

    Inferred format: "A → B; C → D" (pairs by "; ", within pair by first " → ")
    Student format (LW API): "A / B, C / D" (pairs by ", ", within pair by " / ")

    Substring matching on join_key output is used instead of positional splitting
    so that Column A values containing "/" (e.g. "Senior Developer / Gatekeeper")
    do not cause incorrect splits.
    """
    inferred_pairs: list[tuple[str, str]] = []
    for pair in inferred_answer.split(";"):
        pair = pair.strip()
        if "→" in pair:
            a, _, b = pair.partition("→")
            ak, bk = join_key(a), join_key(b)
            if ak and bk:
                inferred_pairs.append((ak, bk))
    if not inferred_pairs:
        return False

    student_key = join_key(student_answer)
    if not student_key:
        return False

    return all(ak + bk in student_key for ak, bk in inferred_pairs)


def check_inferred_answer(block_type: str, answer: str, points, max_points, inferred_entry: dict) -> dict:
    """Check a student answer against an LLM-inferred correct answer.

    For orphan questions (fillInTheBlank, match) not in LW exam config.
    Returns same dict structure as check_answer().
    verifiable: "inferred" (answer found, human-validated pre-reconcile)
              | "inferred_unresolved" (no answer extracted or confidence=unmatched)

    Confidence level is informational metadata only — the quality gate is
    human validation via the Revisão Manual tab (INFERRED_REVIEWED in run_meta.cfg),
    not the LLM confidence score.
    """
    result = {
        "verifiable": "inferred",
        "is_correct": None,
        "flag": None,
        "configured_correct": inferred_entry.get("inferred_correct_answer", ""),
        "accepted": [],
        "answer_matched_source": "inferred",
    }

    confidence = inferred_entry.get("confidence", "unmatched")
    inferred_answer = inferred_entry.get("inferred_correct_answer", "")

    if not inferred_answer or confidence == "unmatched":
        result["verifiable"] = "inferred_unresolved"
        result["answer_matched_source"] = ""
        return result

    bt = (block_type or "").lower()
    blank_flag: str | None = None
    if bt == "fillintheblankblock":
        is_correct, blank_flag = _check_fill_in_blank(answer or "", inferred_answer)
    elif bt == "match":
        is_correct = _check_match(answer or "", inferred_answer)
    else:
        is_correct = join_key(answer or "") == join_key(inferred_answer)

    result["is_correct"] = is_correct

    pts = to_decimal(points)
    mx = to_decimal(max_points)
    if pts is not None and mx is not None:
        if is_correct and pts == 0:
            result["flag"] = "answer_correct_per_doc_but_zero"
        elif not is_correct and mx > 0 and pts == mx:
            result["flag"] = "answer_not_accepted_but_full"
        elif blank_flag:
            result["flag"] = blank_flag

    return result


# ---------------------------------------------------------------------------
# Grade reconciliation (Σpoints/Σmax*100 vs official grade)
# ---------------------------------------------------------------------------
def reconcile_grade(sum_points: Decimal, sum_max: Decimal, official_grade):
    """Compare official grade to the derived percentage.

    Returns dict: sum_points, sum_max, derived_pct, derived_pct_rounded,
    official_grade, status ("match" | "mismatch" | "grade_unavailable").
    """
    derived = None
    derived_rounded = None
    if sum_max and sum_max != 0:
        derived = (sum_points / sum_max * 100)
        derived_rounded = int(derived.to_integral_value(rounding="ROUND_HALF_UP"))

    g = to_decimal(official_grade)
    if g is None:
        status = "grade_unavailable"
    elif derived_rounded is None:
        status = "grade_unavailable"
    else:
        status = "match" if int(g) == derived_rounded else "mismatch"

    return {
        "sum_points": str(sum_points),
        "sum_max": str(sum_max),
        "derived_pct": (f"{derived:.4f}" if derived is not None else ""),
        "derived_pct_rounded": (derived_rounded if derived_rounded is not None else ""),
        "official_grade": (official_grade if official_grade not in (None, "") else ""),
        "status": status,
    }
