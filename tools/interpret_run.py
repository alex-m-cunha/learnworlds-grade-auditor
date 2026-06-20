#!/usr/bin/env python3
"""Generate a Portuguese AI interpretation of a completed reconciliation run.

Reads reconciliation_summary.json + supporting CSVs from a run folder, calls
OpenAI, and writes audit_interpretation.md at the run root.

Usage:
    python tools/interpret_run.py --run-dir "output/pggf2/uc1/2026-06-20_020222"
    python tools/interpret_run.py --run-dir "..." [--model gpt-4.5] [--output path]

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

SYSTEM_PROMPT = """\
És um analista de auditoria académica da Nova SBE Executive Education.
Receberás dados estruturados de uma corrida de reconciliação de avaliação do LearnWorlds
e deves produzir um relatório de interpretação sintético, concreto e accionável, em Português
de Portugal, no formato Markdown especificado abaixo.

REGRAS:
- Escreve sempre em Português de Portugal (não Brasil).
- Sê concreto: menciona nomes de alunos, números de pergunta, respostas exactas quando disponíveis.
- Não inventes dados. Só inclui afirmações que estejam suportadas pelos dados fornecidos.
- Distingue claramente o que é erro técnico do que é limitação de auditabilidade.
- Flags `no_config_match` significam que o LW não exporta o gabarito desses tipos de pergunta
  (fillInTheBlank, match) — NÃO é um erro de configuração, é uma limitação conhecida.
- Flags `answer_accepted_but_zero` significam que a resposta do aluno é aceite pelo gabarito
  mas foi atribuída 0 pontos — ISTO É um problema real que precisa de verificação.
- Para a tabela de acções: 🔴 urgente (impacta notas actuais), 🟡 médio (boas práticas),
  🔵 baixo (informativo/preventivo).

FORMATO DE OUTPUT (Markdown exacto — não desviar da estrutura):

# Interpretação da auditoria — [label-legível] ([programa])

**Run:** [timestamp]
**Alunos:** [n] | **Perguntas auditadas:** [q] | **Linhas de submissão:** [rows]

---

## Resumo executivo

[2-3 parágrafos com o essencial: o que correu bem, o que precisa de atenção,
qual o nível de confiança geral na auditoria]

---

## ✅ O que correu bem

[lista com marcadores — cada item numa linha começada por "- "]

---

## ⚠️ Problemas a corrigir

[Para cada problema real: um sub-título H3, descrição, alunos afectados, resposta concreta,
e o que fazer. Se não houver problemas, escrever apenas "Nenhum problema identificado."]

---

## ℹ️ Informação relevante

[lista com marcadores — limitações conhecidas, contexto metodológico, notas para o futuro]

---

## Tabela de acções

| Prioridade | Acção | Responsável | Prazo sugerido |
|------------|-------|-------------|---------------|
[linhas da tabela — pelo menos uma linha por cada problema ⚠️ e uma por cada item ℹ️ accionável]

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


def _build_context(run_dir: Path) -> dict:
    """Assemble all run data into a dict for the prompt."""
    # Core summary
    summary_path = run_dir / "reconcile" / "reconciliation_summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"reconciliation_summary.json not found at {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    # Flagged rows (answer_accepted_but_zero, etc.)
    report_path = run_dir / "reconcile" / "reconciliation_report" / "reconciliation_report.csv"
    report_rows = _read_csv(report_path)
    flagged = [
        {
            "question_number": r.get("question_number", ""),
            "description": r.get("description", "")[:200],
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

    # Manual review queue
    queue_path = run_dir / "reconcile" / "manual_review_queue" / "manual_review_queue.csv"
    queue_rows = _read_csv(queue_path)
    review_queue = [
        {
            "blockType": r.get("blockType", ""),
            "question_number": r.get("question_number", ""),
            "description": r.get("description", "")[:300],
            "reason": r.get("reason", ""),
            "n_students": r.get("n_students", ""),
            "note": r.get("note", ""),
        }
        for r in queue_rows
    ]

    # Answer key discrepancies
    ak_path = run_dir / "answer_key" / "manual_answer_key.csv"
    ak_rows = _read_csv(ak_path)
    ak_issues = [
        {
            "question_number": r.get("question_number", ""),
            "question_text": r.get("question_text", "")[:200],
            "lw_correct_answer": r.get("lw_correct_answer", ""),
            "doc_correct_answer": r.get("doc_correct_answer", ""),
            "answers_match": r.get("answers_match", ""),
            "confidence": r.get("confidence", ""),
            "needs_review": r.get("needs_review", ""),
            "notes": r.get("notes", ""),
            "source_doc": r.get("source_doc", ""),
        }
        for r in ak_rows
        if r.get("needs_review", "").lower() == "true"
    ]

    # Grade reconciliation
    grade_path = run_dir / "reconcile" / "grade_reconciliation" / "grade_reconciliation.csv"
    grade_rows = _read_csv(grade_path)
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

    # Derive label + program from path (run_dir = output/<prog>/<label>/<ts>)
    parts = run_dir.parts
    try:
        label = parts[-2]
        program = parts[-3]
        timestamp = parts[-1]
    except IndexError:
        label, program, timestamp = "—", "—", "—"

    return {
        "program": program,
        "label": label,
        "run_timestamp": summary.get("run_timestamp", timestamp),
        "summary": summary,
        "flagged_rows": flagged,
        "manual_review_queue": review_queue,
        "answer_key_issues": ak_issues,
        "grade_mismatches": grade_mismatches,
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

    n_flags = len(context["flagged_rows"])
    n_queue = len(context["manual_review_queue"])
    n_ak = len(context["answer_key_issues"])
    print(
        f"  Flags: {n_flags}  |  Fila de revisão: {n_queue}  |  Discrepâncias gabarito: {n_ak}"
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
