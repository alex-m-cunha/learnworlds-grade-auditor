#!/usr/bin/env python3
"""Generate a Portuguese AI interpretation of a completed reconciliation run.

Reads reconciliation_summary.json + all supporting CSVs, builds a cross-referenced
question index, calls OpenAI, and writes:
  <run-dir>/audit_interpretation.md  — AI interpretation
  <run-dir>/question_index.csv       — per-question reference table (all sources)

Usage:
    python tools/interpret_run.py --run-dir "output/pggf2/uc1/2026-06-20_020222"
    python tools/interpret_run.py --run-dir "..." [--model gpt-4o] [--output path]

Security: OPENAI_API_KEY is read from .env only — never logged or written to any file.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_MODEL = "gpt-4o"

QUESTION_INDEX_COLUMNS = [
    "question_number",
    "blockType",
    "question_text",
    "lw_correct_answer",
    "doc_correct_answer",
    "answers_match",
    "doc_confidence",
    "needs_review",
    "flag",
    "n_flagged_students",
    "flagged_student_names",
]

SYSTEM_PROMPT = """\
És um analista de auditoria académica da Nova SBE Executive Education.
Receberás dados estruturados de uma corrida de reconciliação de um Teste de Avaliação
LearnWorlds e deves produzir um relatório de interpretação em Português de Portugal.

REGRAS GERAIS:
- Português de Portugal (não Brasil).
- Linguagem acessível para vários stakeholders (docentes, coordenadores, pessoal administrativo).
  Sem jargão técnico: nunca usar "export", "flag", "blockType", "fillInTheBlankBlock", "match",
  "API", "CSV", "JSON", "LLM", "config", "unmatched". Traduzir sempre para linguagem natural.
- Sê concreto e sintético: menciona nomes, números de pergunta e enunciados; evita generalidades.
- Não inventes dados. Só incluis afirmações suportadas pelos dados.
- Usa sempre números arábicos (42, não "quarenta e dois"). Nunca mistures algarismos com
  palavras por extenso no mesmo contexto.
- O programa deve aparecer sempre em maiúsculas (ex: pggf2 → PGGF2, mba3 → MBA3).
- "correspondência exacta" = confidence high no gabarito ID/Docente. Não usar "alta".
- Nunca escrever "documento Word" — usar sempre "Gabarito ID / Docente".

REGRAS SOBRE OS DADOS:
- As perguntas em `unverifiable_questions` (sem gabarito no LW) também NÃO foram processadas
  pelo Gabarito ID / Docente — portanto NÃO têm resposta correcta em NENHUMA fonte disponível.
  Dizer isso claramente: "X perguntas sem resposta correcta definida em qualquer gabarito".
- `answer_accepted_but_zero`: resposta correcta mas 0 pontos — PROBLEMA REAL, correcção urgente.
- `answer_key_real_discrepancies`: Gabarito LW e Gabarito ID / Docente divergem — PROBLEMA REAL.
- `answer_key_doc_not_found`: pergunta não encontrada no Gabarito ID / Docente — o Gabarito LW
  está correcto, mas não há validação cruzada do docente para esta pergunta.
- Tabela de acções: 🔴 urgente (impacta notas actuais), 🟡 médio (boas práticas),
  🔵 baixo (informativo/preventivo).

FORMATO DE OUTPUT (Markdown exacto — respeitar secções e ordem):

# Interpretação da auditoria — [label_display] ([program_display])

**Tipo:** Teste de Avaliação
**Run:** [run_timestamp]
**Alunos:** [n_students] | **Perguntas do teste:** [active_questions_count] | **Linhas de submissão:** [submission_rows]

---

## Resumo

[1 frase de abertura SEM label — ex: "Este relatório refere-se à auditoria de um Teste de
Avaliação da unidade curricular [label_display] no âmbito do programa [program_display]."]

[Bullets imediatamente a seguir — SEM "Dados:", SEM "Pontos de atenção:", SEM "Conclusão:":]

- O teste contém um total de [active_questions_count] perguntas.
- O Gabarito LW tem resposta correcta definida para [exam_config_exportable_count] perguntas.
- O Gabarito ID / Docente tem resposta correcta definida para [answer_key_matched_count]
  perguntas[, e tem N pergunta(s) não encontrada(s) — se answer_key_doc_not_found não for vazio]:
    - Pergunta [Q número]: "[primeiros 80 chars do enunciado]"
    [uma sub-linha por cada item de answer_key_doc_not_found]
- Foram validadas [answer_key_matched_count] perguntas cruzando com o Gabarito ID / Docente.
- [Se unverifiable_questions não for vazio:] Há [N] pergunta(s) que não foram detectadas em
  nenhum gabarito:
    - [Para cada item de unverifiable_questions: "(sem número atribuído): [primeiros 80 chars]"]
- [Se flagged_rows não vazio:] [N] aluno(s) têm respostas correctas com 0 pontos atribuídos.

---

## Auditoria

### Extração de submissões (API)
[1-2 linhas: estado + alunos + total de respostas]

### Importação do Gabarito LW (XLSX)
[1-2 linhas: quantas perguntas com gabarito verificável; quantas sem gabarito no sistema LW.]

### Gabarito ID / Docente
[Se não correu: "Não executado nesta corrida."
Se correu:
- Cobertura: N perguntas esperadas, N com correspondência exacta, N não encontradas.
- Não encontradas EXPLICITAMENTE: Q[número]: "[100 chars]" para cada item de answer_key_doc_not_found.
- ⚠️ ADVERTÊNCIA se answer_key_matched_count < expected_answer_key_count.]

### Reconciliação de respostas
[2-3 linhas:
- Verificáveis: N (perguntas com gabarito LW × alunos, tipos compatíveis).
- Não verificáveis: N — sem resposta em qualquer gabarito.
- Flags: tipo e contagem.]

---

## ⚠️ Problemas a corrigir

[H3 por cada problema, em linguagem natural sem jargão.
- Respostas correctas com 0 pontos → título "Resposta certa mas pontuação zero": listar pergunta
  (número + início do enunciado), alunos (nome + resposta enviada + pontos atribuídos).
- Divergências entre Gabarito LW e Gabarito ID / Docente → listar pergunta, resposta LW vs
  resposta do Gabarito ID / Docente.
Se não houver: "Nenhum problema identificado."]

---

## ℹ️ Perguntas sem gabarito disponível

ATENÇÃO: existem 2 situações DISTINTAS — não misturar. NÃO escrever os nomes dos campos
("unverifiable_questions", "answer_key_doc_not_found") no output — usar apenas os títulos:

**Sem resposta em nenhum gabarito:**
[Perguntas de `unverifiable_questions` — sem gabarito em NENHUMA fonte. Uma linha por pergunta:]
- **(sem número atribuído)**: "[primeiros 80 chars]" — Não foi possível verificar
  automaticamente a resposta correcta desta pergunta. Recomenda-se verificação manual.

**Presentes no Gabarito LW mas não encontradas no Gabarito ID / Docente:**
[Perguntas de `answer_key_doc_not_found` — TÊM resposta no Gabarito LW. Uma linha por pergunta:]
- **Q[número]**: "[primeiros 80 chars]" — Presente no Gabarito LW (resposta:
  "[lw_correct_answer curta]"), mas não encontrada no Gabarito ID / Docente para validação.

Sem jargão técnico em nenhuma das entradas.]

### Sugestões de melhoria
[2-4 bullets em linguagem acessível para docentes e coordenadores. PROIBIDO usar: "export",
"blockType", "fillInTheBlankBlock", "match", "API", "CSV", "flag", "config", "LLM".
Exemplos de linguagem correcta: "perguntas de preenchimento de espaço", "perguntas de
correspondência", "plataforma LearnWorlds", "Gabarito ID / Docente".]

---

## Tabela de acções

| Prioridade | Acção | Responsável | Prazo sugerido |
|------------|-------|-------------|---------------|
[mínimo 1 linha por problema ⚠️; 1 linha por limitação accionável]

---

> *Interpretação gerada automaticamente por IA a partir dos dados da reconciliação determinística.
> Verificar antes de tomar decisões sobre notas.*
"""


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return env
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _build_question_index(
    config_rows: list[dict],
    ak_rows: list[dict],
    report_rows: list[dict],
) -> dict[str, dict]:
    """Build a per-question cross-reference from all data sources."""
    questions: dict[str, dict] = {}

    # Seed from exam_config (authoritative for question text and LW answer)
    for r in config_rows:
        qn = r.get("question_number", "").strip()
        if not qn:
            continue
        questions[qn] = {
            "question_number": qn,
            "blockType": r.get("blockType", ""),
            "question_text": r.get("description", "")[:200],
            "lw_correct_answer": r.get("configured_accepted_answers_raw", "")[:120],
            "doc_correct_answer": "",
            "answers_match": "",
            "doc_confidence": "",
            "needs_review": "",
            "flag": "",
            "n_flagged_students": 0,
            "flagged_student_names": "",
        }

    # Enrich from answer_key (docente cross-check)
    for r in ak_rows:
        qn = r.get("question_number", "").strip()
        if qn in questions:
            questions[qn]["doc_correct_answer"] = r.get("doc_correct_answer", "")
            questions[qn]["answers_match"] = r.get("answers_match", "")
            questions[qn]["doc_confidence"] = r.get("confidence", "")
            questions[qn]["needs_review"] = r.get("needs_review", "")

    # Enrich from reconciliation_report (flags + affected students)
    flag_data: dict[str, dict] = {}
    for r in report_rows:
        qn = r.get("question_number", "").strip()
        flag = r.get("flag", "").strip()
        if not qn or not flag:
            continue
        if qn not in flag_data:
            flag_data[qn] = {"flag": flag, "students": []}
        name = r.get("username", r.get("email", ""))
        answer = r.get("submitted_answer", "")
        points = r.get("points", "")
        flag_data[qn]["students"].append(f"{name} (resp: {answer!r}, pts: {points})")

    for qn, data in flag_data.items():
        if qn in questions:
            questions[qn]["flag"] = data["flag"]
            questions[qn]["n_flagged_students"] = len(data["students"])
            questions[qn]["flagged_student_names"] = "; ".join(data["students"])

    return questions


def _write_question_index(run_dir: Path, questions: dict[str, dict]) -> Path:
    out_path = run_dir / "question_index.csv"
    sorted_qs = sorted(questions.values(), key=lambda r: int(r["question_number"]) if r["question_number"].isdigit() else 999)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=QUESTION_INDEX_COLUMNS)
        w.writeheader()
        w.writerows(sorted_qs)
    return out_path


def _build_context(run_dir: Path) -> dict:
    summary_path = run_dir / "reconcile" / "reconciliation_summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"reconciliation_summary.json not found at {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    # Load all CSVs
    config_rows = _read_csv(run_dir / "exam_config" / "exam_config_as_is.csv")
    ak_rows = _read_csv(run_dir / "answer_key" / "manual_answer_key.csv")
    report_rows = _read_csv(run_dir / "reconcile" / "reconciliation_report" / "reconciliation_report.csv")
    queue_rows = _read_csv(run_dir / "reconcile" / "manual_review_queue" / "manual_review_queue.csv")
    grade_rows = _read_csv(run_dir / "reconcile" / "grade_reconciliation" / "grade_reconciliation.csv")

    # Active questions: named (from reconciliation_report) + unnamed (no_config_match from queue)
    active_qns = sorted(
        {r.get("question_number", "").strip() for r in report_rows if r.get("question_number", "").strip()},
        key=lambda x: int(x) if x.isdigit() else 999,
    )
    unnamed_count = len(queue_rows)  # fillInTheBlankBlock/match — no question_number in export
    active_count = len(active_qns) + unnamed_count

    # Build cross-referenced question index
    questions_index = _build_question_index(config_rows, ak_rows, report_rows)

    # Write question_index.csv
    _write_question_index(run_dir, questions_index)

    # Flagged rows with student detail
    flagged = [
        {
            "question_number": r.get("question_number", ""),
            "question_text": r.get("description", "")[:150],
            "student_name": r.get("username", ""),
            "student_email": r.get("email", ""),
            "submitted_answer": r.get("submitted_answer", ""),
            "points": r.get("points", ""),
            "max_points": r.get("max_points", ""),
            "flag": r.get("flag", ""),
        }
        for r in report_rows
        if r.get("flag", "").strip()
    ]

    # Manual review queue — unverifiable questions (no question_number in LW export)
    review_queue = [
        {
            "question_number": r.get("question_number", "") or "(sem número no LW)",
            "blockType": r.get("blockType", ""),
            "description": r.get("description", "")[:200],
            "reason": r.get("reason", ""),
            "n_students": r.get("n_students", ""),
            "note": r.get("note", ""),
        }
        for r in queue_rows
    ]

    # Verifiability counts
    no_config = summary.get("verifiable_breakdown", {}).get("no_config_match", 0)
    verifiable = summary.get("verifiable_breakdown", {}).get("yes", 0)
    total_rows = summary.get("submission_rows", 0)
    n_students = len({r.get("user_id", r.get("email", "")) for r in report_rows})
    unverifiable_types = sorted({r.get("blockType", "") for r in queue_rows if r.get("blockType")})
    unverifiable_explanation = (
        f"{no_config} linhas não verificáveis = {len(review_queue)} pergunta(s) × {n_students} alunos. "
        f"Tipos: {', '.join(unverifiable_types)}. "
        f"O LearnWorlds não exporta o gabarito destes tipos — limitação da plataforma, não erro de configuração."
    )
    # Verifiable by blockType (to confirm compatible typology)
    from collections import Counter
    verif_bt = Counter(
        r.get("blockType", "") for r in report_rows if r.get("verifiable", "") == "yes"
    )
    verifiable_by_type = dict(verif_bt)

    # Answer key coverage stats
    expected_answer_key_count = len(config_rows)  # exam_config questions the Word extraction covers
    ak_conf = Counter(r.get("confidence", "") for r in ak_rows)
    ak_matched_count = ak_conf.get("high", 0) + ak_conf.get("medium", 0) + ak_conf.get("low", 0)

    def _norm(s: str) -> str:
        import re, unicodedata
        s = unicodedata.normalize("NFKD", s.lower())
        return re.sub(r"[^a-z0-9]", "", s)

    def _doc_in_lw_variants(lw: str, doc: str) -> bool:
        """True if doc answer is one of the semicolon-separated LW accepted variants."""
        if ";" not in lw:
            return False
        doc_norm = _norm(doc)
        return any(_norm(v.strip()) == doc_norm for v in lw.split(";"))

    # Answer key: split real discrepancies from doc-not-found
    ak_real_discrepancies = []
    ak_doc_not_found = []
    for r in ak_rows:
        if r.get("needs_review", "").lower() != "true":
            continue
        if r.get("confidence", "") == "unmatched":
            ak_doc_not_found.append({
                "question_number": r.get("question_number", ""),
                "question_text": r.get("question_text", "")[:120],
                "lw_correct_answer": r.get("lw_correct_answer", ""),
                "note": "Pergunta não encontrada no documento Word pelo LLM — gabarito LW correcto.",
                "source_doc": r.get("source_doc", ""),
            })
        else:
            lw_ans = r.get("lw_correct_answer", "")
            doc_ans = r.get("doc_correct_answer", "")
            # Skip false positive: doc answer is one of the LW accepted variants
            if _doc_in_lw_variants(lw_ans, doc_ans):
                continue
            ak_real_discrepancies.append({
                "question_number": r.get("question_number", ""),
                "question_text": r.get("question_text", "")[:120],
                "lw_correct_answer": lw_ans,
                "doc_correct_answer": doc_ans,
                "answers_match": r.get("answers_match", ""),
                "confidence": r.get("confidence", ""),
                "notes": r.get("notes", ""),
                "source_doc": r.get("source_doc", ""),
            })

    # Grade reconciliation
    grade_mismatches = [
        {
            "student": r.get("username", r.get("email", "")),
            "official_grade": r.get("grade", ""),
            "derived_grade": r.get("derived_pct", ""),
            "status": r.get("grade_status", ""),
        }
        for r in grade_rows
        if r.get("grade_status", "") not in ("match", "")
    ]

    # Derive label + program from path
    parts = run_dir.parts
    try:
        label = parts[-2]
        program = parts[-3]
        timestamp = parts[-1]
    except IndexError:
        label, program, timestamp = "—", "—", "—"

    # Human-readable label — prefer LABEL_DISPLAY from assessment.cfg if present
    _cfg_display = ""
    cfg_path = PROJECT_ROOT / "assessment.cfg"
    if cfg_path.exists():
        for _line in cfg_path.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line.startswith("LABEL_DISPLAY="):
                _cfg_display = _line.partition("=")[2].strip().strip('"').strip("'")
                break
    if _cfg_display:
        label_display = _cfg_display
    else:
        # Fallback: derive from slug ("uc1-mercados-economia-financeira" → "UC1 Mercados Economia Financeira")
        import re as _re
        _words = label.split("-")
        _display_words = []
        for w in _words:
            if _re.match(r"^uc\d+$", w, _re.IGNORECASE):
                _display_words.append(w.upper())
            else:
                _display_words.append(w.capitalize())
        label_display = " ".join(_display_words)

    # Answer key summary stats
    ak_summary = summary.get("answer_key", {})

    return {
        "assessment_type": "Teste de Avaliação",
        "program": program,
        "program_display": program.upper(),
        "label": label,
        "label_display": label_display,
        "run_timestamp": summary.get("run_timestamp", timestamp),
        # Counts
        "submission_rows": total_rows,
        "n_students": n_students,
        "active_questions_count": active_count,
        "active_questions_named": active_qns,        # with question_number (from exam_config)
        "active_questions_unnamed_count": unnamed_count,  # fillInTheBlankBlock/match (no number)
        "exam_config_exportable_count": len(config_rows),  # questions LW exported with answer key
        "numbering_note": (
            "Os números de pergunta vêm das submissões dos alunos. "
            "O export XLSX pode diferir da numeração visual no LW — identificar sempre pelo enunciado."
        ),
        # Verifiability
        "verifiable_count": verifiable,
        "verifiable_by_type": verifiable_by_type,
        "unverifiable_count": no_config,
        "unverifiable_explanation": unverifiable_explanation,
        "unverifiable_questions": review_queue,
        # Flags
        "flagged_rows": flagged,
        "flag_counts": summary.get("flag_counts", {}),
        # Consistency (same answer, different points across students)
        "inconsistent_scoring_questions": summary.get("inconsistent_scoring_questions", 0),
        # Answer key (docente Word → LLM)
        "answer_key_ran": bool(ak_rows),
        "expected_answer_key_count": expected_answer_key_count,
        "answer_key_confidence_breakdown": dict(ak_conf),
        "answer_key_matched_count": ak_matched_count,
        "answer_key_real_discrepancies": ak_real_discrepancies,
        "answer_key_doc_not_found": ak_doc_not_found,
        # Full question index (consistent numbering for LLM)
        "questions_index": {
            qn: {k: v for k, v in q.items() if k != "question_number"}
            for qn, q in questions_index.items()
        },
    }


def _call_openai(api_key: str, model: str, context: dict) -> str:
    try:
        from openai import OpenAI  # type: ignore
    except ImportError:
        raise ImportError("openai package not installed — run: pip install openai>=1.0")

    client = OpenAI(api_key=api_key)

    user_msg = (
        "Dados da corrida de auditoria (JSON):\n\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
        + "\n\nProduz o relatório de interpretação no formato especificado."
    )

    response = client.chat.completions.create(
        model=model,
        temperature=0.2,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )
    return response.choices[0].message.content or ""


def run(run_dir_str: str, model: str, output: str | None = None) -> None:
    run_dir = Path(run_dir_str)
    if not run_dir.is_dir():
        print(f"ERRO: pasta de run não encontrada: {run_dir}", file=sys.stderr)
        sys.exit(1)

    env = _load_env()
    api_key = env.get("OPENAI_API_KEY", "")
    if not api_key:
        print(
            "AVISO: OPENAI_API_KEY não está configurada em .env — interpretação ignorada.",
            file=sys.stderr,
        )
        sys.exit(0)

    effective_model = model or env.get("OPENAI_MODEL", DEFAULT_MODEL)

    print(f"A carregar dados de: {run_dir}")
    try:
        context = _build_context(run_dir)
    except FileNotFoundError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        sys.exit(1)

    qi_path = run_dir / "question_index.csv"
    print(f"  Índice de perguntas escrito: {qi_path}")
    print(
        f"  Perguntas activas: {context['active_questions_count']}  |  "
        f"Flags: {len(context['flagged_rows'])}  |  "
        f"Não verificáveis: {context['unverifiable_count']} linhas  |  "
        f"Discrepâncias gabarito: {len(context['answer_key_real_discrepancies'])}"
    )
    print(f"A chamar OpenAI ({effective_model})...")

    try:
        md_text = _call_openai(api_key, effective_model, context)
    except Exception as exc:
        print(f"ERRO OpenAI: {exc}", file=sys.stderr)
        sys.exit(1)

    out_path = Path(output) if output else run_dir / "audit_interpretation.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md_text, encoding="utf-8")
    print(f"Interpretação escrita em: {out_path}")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate AI interpretation of a LearnWorlds reconciliation run."
    )
    p.add_argument("--run-dir", required=True, help="Path to the run folder (timestamped)")
    p.add_argument(
        "--model",
        default="",
        help=f"OpenAI model (default: OPENAI_MODEL from .env, else {DEFAULT_MODEL})",
    )
    p.add_argument(
        "--output",
        default="",
        help="Output path for the .md file (default: <run-dir>/audit_interpretation.md)",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    run(args.run_dir, args.model, args.output or None)


if __name__ == "__main__":
    main()
