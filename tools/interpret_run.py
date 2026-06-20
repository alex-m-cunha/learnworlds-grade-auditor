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

REGRAS:
- Escreve sempre em Português de Portugal (não Brasil).
- Sê concreto e sintético: menciona nomes de alunos, números de pergunta e enunciados quando
  disponíveis; evita generalidades.
- Não inventes dados. Só incluis afirmações suportadas pelos dados.
- `no_config_match`: tipos fillInTheBlankBlock e match — o LW NÃO exporta o gabarito destas
  perguntas. É uma limitação da plataforma, NÃO um erro de configuração.
- `answer_accepted_but_zero`: resposta aceite pelo gabarito mas 0 pontos atribuídos — PROBLEMA
  REAL que requer correcção imediata.
- `answer_key_real_discrepancies`: ambas as fontes têm resposta mas diferem — PROBLEMA REAL.
- `answer_key_doc_not_found`: o LLM não encontrou a questão no documento Word — o gabarito LW
  está provavelmente correcto; apenas não existe cross-check docente.
- Para a tabela de acções: 🔴 urgente (impacta notas actuais), 🟡 médio (boas práticas),
  🔵 baixo (informativo/preventivo).
- Usa `active_questions_count` para referir o número de perguntas do teste, não
  `exam_config_count`.

FORMATO DE OUTPUT (Markdown exacto):

# Interpretação da auditoria — [label legível] ([programa])

**Tipo:** Teste de Avaliação
**Run:** [run_timestamp]
**Alunos:** [n] | **Perguntas do teste:** [active_questions_count] | **Linhas de submissão:** [submission_rows]

---

## Resumo executivo

[2-3 parágrafos: o que é este teste, o que a auditoria fez, nível de confiança geral]

---

## Pipeline de auditoria

### Extração de submissões (API)
[estado + contagens: submissions, alunos, linhas]

### Importação do gabarito LW (XLSX)
[estado + contagens: perguntas exportadas, tipos presentes]

### Gabarito docente (Word → LLM)
[estado + contagens de confiança. Se não correu, dizer "Não executado nesta corrida."]

### Reconciliação de respostas
[verificáveis vs não verificáveis — SER EXPLÍCITO sobre o que não foi verificável e porquê,
em 2-3 linhas sintéticas. Flags detectados.]

### Reconciliação de notas
[match/mismatch/unavailable por aluno]

### Coerência entre alunos
[inconsistências de pontuação — se 0, dizer explicitamente "Nenhuma inconsistência detectada."]

---

## ⚠️ Problemas a corrigir

[Para cada problema real: H3 com título, descrição, alunos afectados com nome e resposta
exacta, acção necessária. Se não houver problemas, escrever "Nenhum problema identificado."]

---

## ℹ️ Limitações de auditabilidade

[Lista sintética das perguntas não auditáveis e porquê — mencionar tipos e enunciados concretos]

---

## Tabela de acções

| Prioridade | Acção | Responsável | Prazo sugerido |
|------------|-------|-------------|---------------|
[mínimo uma linha por problema ⚠️; uma linha por limitação relevante]

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

    # Active questions from submissions (canonical list — what students actually answered)
    active_qns = sorted(
        {r.get("question_number", "").strip() for r in report_rows if r.get("question_number", "").strip()},
        key=lambda x: int(x) if x.isdigit() else 999,
    )
    active_count = len(active_qns)

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

    # Manual review queue — unverifiable questions
    review_queue = [
        {
            "blockType": r.get("blockType", ""),
            "description": r.get("description", "")[:200],
            "reason": r.get("reason", ""),
            "n_students": r.get("n_students", ""),
            "note": r.get("note", ""),
        }
        for r in queue_rows
    ]

    # Unverifiable explanation (explicit counts)
    no_config = summary.get("verifiable_breakdown", {}).get("no_config_match", 0)
    verifiable = summary.get("verifiable_breakdown", {}).get("yes", 0)
    total_rows = summary.get("submission_rows", 0)
    n_students = len({r.get("user_id", r.get("email", "")) for r in report_rows})
    unverifiable_types = sorted({r.get("blockType", "") for r in queue_rows if r.get("blockType")})
    unverifiable_explanation = (
        f"{no_config} linhas não verificáveis ({len(review_queue)} pergunta(s) × {n_students} alunos). "
        f"Tipos: {', '.join(unverifiable_types)}. "
        f"O LearnWorlds não exporta o gabarito destes tipos — limitação da plataforma."
    )

    # Answer key: split real discrepancies from doc-not-found
    ak_real_discrepancies = []
    ak_doc_not_found = []
    for r in ak_rows:
        if r.get("needs_review", "").lower() != "true":
            continue
        if r.get("confidence", "") == "unmatched":
            ak_doc_not_found.append({
                "question_number": r.get("question_number", ""),
                "question_text": r.get("question_text", "")[:150],
                "lw_correct_answer": r.get("lw_correct_answer", ""),
                "note": "Não encontrada no documento Word — gabarito LW está correcto.",
                "source_doc": r.get("source_doc", ""),
            })
        else:
            ak_real_discrepancies.append({
                "question_number": r.get("question_number", ""),
                "question_text": r.get("question_text", "")[:150],
                "lw_correct_answer": r.get("lw_correct_answer", ""),
                "doc_correct_answer": r.get("doc_correct_answer", ""),
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

    # Answer key summary stats
    ak_summary = summary.get("answer_key", {})

    return {
        "assessment_type": "Teste de Avaliação",
        "program": program,
        "label": label,
        "run_timestamp": summary.get("run_timestamp", timestamp),
        # Counts
        "submission_rows": total_rows,
        "n_students": n_students,
        "exam_config_count": len(config_rows),
        "active_questions_count": active_count,
        "active_questions": active_qns,
        "numbering_note": (
            "A numeração em 'active_questions' vem das submissões (o que os alunos responderam). "
            "O export XLSX pode conter linhas extra não apresentadas aos alunos. "
            "Referenciar sempre perguntas pelo enunciado além do número."
        ),
        # Verifiability
        "verifiable_count": verifiable,
        "unverifiable_count": no_config,
        "unverifiable_explanation": unverifiable_explanation,
        "unverifiable_questions": review_queue,
        # Flags
        "flagged_rows": flagged,
        "flag_counts": summary.get("flag_counts", {}),
        # Grade reconciliation
        "grade_status_counts": summary.get("grade_status_counts", {}),
        "grade_mismatches": grade_mismatches,
        # Consistency
        "inconsistent_scoring_questions": summary.get("inconsistent_scoring_questions", 0),
        # Answer key (docente)
        "answer_key_ran": bool(ak_rows),
        "answer_key_summary": ak_summary,
        "answer_key_real_discrepancies": ak_real_discrepancies,
        "answer_key_doc_not_found": ak_doc_not_found,
        # Full question index (for LLM to use consistent numbering)
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
