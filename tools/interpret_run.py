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
- Nunca escrever "documento Word" — usar sempre "gabarito ID / Docente".

REGRAS SOBRE OS DADOS:
- `answer_accepted_but_zero`: resposta correcta (gabarito LW) mas 0 pontos — PROBLEMA REAL, correcção urgente.
- `answer_correct_per_doc_but_zero`: aluno respondeu o que o docente pretendia (gabarito ID /
  Docente) mas o LW não aceitou e deu 0 pontos — ERRO DE PARAMETRIZAÇÃO no LW, o mais grave de
  todos. Usar linguagem de urgência máxima.
- `answer_not_accepted_but_full` (em `not_accepted_but_full_flags`): o gabarito não reconhece a
  resposta como correcta MAS o LW atribuiu pontuação completa — possível falha no gabarito ou
  resposta alternativa válida que o gabarito não prevê. NÃO confundir com "resposta certa, 0 pontos":
  aqui o aluno TEM pontos. Descrever como "resposta não reconhecida pelo gabarito mas com pontuação
  completa atribuída — requer verificação". Urgência 🟡.
- `fill_in_blank_over_answered` (em `over_answered_flags`): pergunta de preenchimento de lacunas
  onde o aluno escreveu a resposta correcta mas com texto adicional. O LearnWorlds exige
  correspondência exacta e rejeitou. Descrever como "resposta correcta com texto a mais — o sistema
  de avaliação não aceitou". Agrupar por pergunta e listar cada aluno afectado com a resposta.
  Grau de urgência 🔴 — impacta notas.
- `answer_key_real_discrepancies`: gabarito LW e gabarito ID / Docente divergem — o gabarito ID /
  Docente detectou uma possível parametrização errada no LW. PROBLEMA REAL — o LW pode estar a
  penalizar respostas correctas. Listar com urgência 🔴.
- `answer_key_doc_not_found`: pergunta presente no gabarito LW mas não encontrada no gabarito ID /
  Docente — sem validação cruzada para esta pergunta.
- `inferred_questions_detail`: perguntas de preenchimento de lacunas ou correspondência cujo
  gabarito NÃO existe no LW — foi inferido automaticamente a partir do gabarito ID / Docente e
  USADO na reconciliação. Não é um gabarito oficial: requer confirmação humana.
- `unverifiable_questions`: perguntas SEM gabarito em QUALQUER fonte — não verificadas de todo.
- Next steps: 🔴 urgente (impacta notas actuais), 🟡 médio (boas práticas),
  🔵 baixo (informativo/preventivo).

FORMATO DE OUTPUT (Markdown exacto — respeitar secções e ordem):

# Interpretação da auditoria — [label_display] ([program_display])

**Tipo:** Teste de Avaliação
**Época:** [epoca]
**Run:** [run_timestamp]
**Alunos:** [n_students] | **Perguntas do teste:** [active_questions_count] | **Linhas de submissão:** [submission_rows]

---

## Resumo

[1 frase de abertura: "Este relatório refere-se à auditoria de um Teste de Avaliação da unidade
curricular [label_display] no âmbito do programa [program_display]."]

[Bullets separados por linha em branco entre si. NÃO agrupar em parágrafo.]

- O teste contém um total de [active_questions_count] perguntas, detetadas nas submissões dos alunos.

- O gabarito LearnWorlds tem resposta correcta definida para [exam_config_exportable_count] perguntas. O gabarito ID / Docente [se answer_key_ran: tem resposta correcta definida para [answer_key_matched_count + len(inferred_questions_detail)] perguntas — [answer_key_confidence_breakdown.high] com correspondência exacta[, se inferred: [N] inferidas automaticamente (preenchimento de lacunas / correspondência)][, se answer_key_doc_not_found: [N] não encontradas]][senão: não foi executado].

- Foram validadas [verifiable_count + inferred_rows_count] respostas cruzando com o gabarito ID / Docente[se inferred_rows_count > 0: , das quais [inferred_rows_count] com base em [len(inferred_questions_detail)] pergunta(s) inferida(s) automaticamente — a confirmação humana dessas inferências é necessária antes de qualquer decisão sobre notas].

[Uma linha em branco antes de cada ocorrência. Só incluir os bullets abaixo se existirem dados:]

- [N] aluno(s) com respostas correctas mas 0 pontos atribuídos.

- [N] resposta(s) não reconhecidas pelo gabarito mas com pontuação completa atribuída.

- [N] aluno(s) com resposta correcta rejeitada por conter texto adicional.

[Se unverifiable_questions não vazio:] - [N] pergunta(s) sem resposta correcta definida em qualquer gabarito.

NÃO incluir sub-listas de perguntas nesta secção — o detalhe fica nas secções abaixo.

---

## Auditoria

### Extração de submissões
[1-2 linhas: estado + alunos + total de linhas de resposta]

### Importação do gabarito LearnWorlds
[1-2 linhas: quantas perguntas com gabarito verificável; quantas sem gabarito no sistema LW.]

### Gabarito ID / Docente (extracção automática)
[Se não correu: "Não executado nesta corrida."]
[Se correu — 1 linha de cobertura seguida de sub-bullets:]
Cobertura: [expected_answer_key_count] perguntas verificáveis + [len(inferred_questions_detail)] perguntas de preenchimento de lacunas / correspondência.
- [answer_key_matched_count] perguntas com correspondência exacta (confiança exacta).
[Se inferred_questions_detail não vazio:] - [N] perguntas inferidas automaticamente[: listar confiança por nível se não for tudo "high"]; usadas na reconciliação mas requerem confirmação humana.
[Se answer_key_doc_not_found não vazio:] - [N] perguntas não encontradas no gabarito ID / Docente — sem validação cruzada para estas perguntas:
  [listar cada uma como "- Q[número]: [80 chars do enunciado]"]

### Reconciliação de respostas
- Verificadas por gabarito LearnWorlds (directo): [verifiable_count] ([exam_config_exportable_count] perguntas × [n_students] alunos, tipos compatíveis).
[Se inferred_rows_count > 0:] - Verificadas por gabarito inferido: [inferred_rows_count] ([len(inferred_questions_detail)] pergunta(s) × [n_students] alunos, confiança alta).
[Se inferred_low_conf_rows_count > 0:] - Aguardam confirmação (inferência com confiança insuficiente): [inferred_low_conf_rows_count].
- Sem gabarito disponível: [unverifiable_count].
[Se existem ocorrências — listar cada uma em linha separada, precedida de "Ocorrências detectadas:" se houver mais de 1:]
[Se flagged_rows com answer_accepted_but_zero ou answer_correct_per_doc_but_zero:] - [N] resposta(s) correctas com 0 pontos.
[Se not_accepted_but_full_flags:] - [N] resposta(s) não reconhecidas pelo gabarito mas com pontuação completa.
[Se over_answered_flags:] - [N] resposta(s) correctas rejeitadas por texto adicional.
[Se answer_key_real_discrepancies:] - [N] divergência(s) entre gabarito LearnWorlds e gabarito ID / Docente — possível parametrização errada.

---

## ⚠️ Problemas a corrigir

[OBRIGATÓRIO: verifica individualmente cada uma das 5 condições abaixo. "Nenhum problema
identificado." só pode aparecer se AS 5 listas estiverem TODAS vazias. Cada condição é
independente — não inferir a partir de outras. Usar `problems_checklist` no contexto como guia.]

1. Se `answer_key_real_discrepancies` não vazio → "Divergência detectada entre gabaritos —
   possível parametrização errada no LearnWorlds": explicar que o gabarito ID / Docente indica
   uma resposta diferente da que está configurada no LearnWorlds. Isto pode significar que o LW
   tem a opção errada configurada, penalizando respostas correctas. Listar pergunta (Q[número] +
   enunciado) com resposta LW vs resposta do docente. Urgência 🔴.

2. Se `doc_override_flags` não vazio → "Erro de parametrização confirmado — resposta correcta
   rejeitada pelo LearnWorlds": o aluno respondeu conforme a intenção do docente mas levou 0
   pontos. Listar pergunta e alunos (nome + resposta + pontos). Máxima urgência 🔴.

3. Se `over_answered_flags` não vazio → "Resposta correcta com texto a mais — rejeitada pelo
   LearnWorlds": agrupar por pergunta; para cada aluno: nome, resposta submetida, pontos. 🔴.

4. Se `flagged_rows` contém flag="answer_accepted_but_zero" → "Resposta certa mas pontuação
   zero": listar pergunta e alunos. Só flag exactamente "answer_accepted_but_zero". 🔴.

5. Se `not_accepted_but_full_flags` não vazio → escrever (substituindo os placeholders pelos dados reais):

   Resposta não reconhecida pelo gabarito mas com pontuação completa:
   - **Q[question_number]**: "[question_text]"
     - [student_name]: "[submitted_answer]" ([points] pontos)
   [repetir para cada item em not_accepted_but_full_flags]

   Nota: estes alunos TÊM pontos — não é problema de pontuação zero. Urgência 🟡.

---

## [Se inferred_reviewed=false: "🔍 Perguntas inferidas — confirmação necessária"][Se inferred_reviewed=true: "✅ Perguntas inferidas — revistas manualmente"]

[Se `inferred_questions_detail` vazio: omitir esta secção inteira.]
[Se não vazio e inferred_reviewed=false:]
Estas perguntas não têm gabarito no LearnWorlds. A resposta correcta foi inferida
automaticamente a partir do gabarito ID / Docente e usada na reconciliação. Requerem
confirmação humana antes de qualquer decisão sobre notas.
[Se não vazio e inferred_reviewed=true:]
As respostas inferidas foram confirmadas manualmente. Registo para referência:

[Para cada entrada em `inferred_questions_detail`:]
- **(sem número atribuído)**: "[question_text primeiros 150 chars]"
  - **Resposta inferida:** "[doc_correct_answer]"
  - **Confiança:** [confidence]
  - **Acção:** Confirmar que a resposta inferida corresponde à intenção do docente. Se incorrecta, corrigir manualmente na tabela de extracção.

---

## ℹ️ Perguntas sem gabarito disponível

[Se não há `unverifiable_questions` E não há `answer_key_doc_not_found`: omitir esta secção.]

[Se `unverifiable_questions` não vazio:]
**Sem resposta em nenhum gabarito:**
[Uma linha por pergunta — sem gabarito em QUALQUER fonte:]
- **(sem número atribuído)**: "[primeiros 80 chars]" — Não foi possível verificar automaticamente a resposta correcta. Recomenda-se verificação manual.

[Se `answer_key_doc_not_found` não vazio:]
**Presentes no gabarito LearnWorlds mas não encontradas no gabarito ID / Docente:**
[Uma linha por pergunta:]
- **Q[número]**: "[primeiros 80 chars]" — Presente no gabarito LearnWorlds (resposta: "[lw_correct_answer curta]"), mas não encontrada no gabarito ID / Docente para validação cruzada.

---

## Next steps

| Prioridade | Acção |
|------------|-------|
[Gerar linhas APENAS para as situações que existem nos dados. Seguir esta lógica de prioridade:]
[🔴 Se answer_key_real_discrepancies:] | 🔴 | Verificar divergências detectadas entre gabaritos e corrigir a parametrização no LearnWorlds se confirmado o erro |
[🔴 Se doc_override_flags:] | 🔴 | Corrigir parametrização no LearnWorlds para as perguntas onde o gabarito ID / Docente confirma erro |
[🔴 Se over_answered_flags:] | 🔴 | Corrigir parametrização no LearnWorlds para aceitar as variantes de resposta correcta rejeitadas por texto adicional |
[🔴 Se flagged_rows com zero-score:] | 🔴 | Investigar e corrigir pontuação zero para as respostas correctas identificadas |
[🟡 Se not_accepted_but_full_flags:] | 🟡 | Verificar se as respostas com pontuação completa mas não reconhecidas pelo gabarito são válidas; se sim, actualizar o gabarito |
[🔵 Se inferred_questions_detail e inferred_reviewed=false:] | 🔵 | Confirmar manualmente as [N] perguntas inferidas: comparar a resposta inferida com o gabarito ID / Docente original |
[🔵 Se inferred_questions_detail e inferred_reviewed=true:] | 🔵 | Inferências já confirmadas — cruzar os resultados da reconciliação com a tabela de revisão para validar notas |
[🔵 Se answer_key_doc_not_found:] | 🔵 | Verificar as [N] perguntas presentes no gabarito LearnWorlds mas não encontradas no gabarito ID / Docente |

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
    inferred_rows = _read_csv(run_dir / "answer_key" / "inferred_answer_key.csv")
    report_rows = _read_csv(run_dir / "reconcile" / "reconciliation_report" / "reconciliation_report.csv")
    queue_rows = _read_csv(run_dir / "reconcile" / "manual_review_queue" / "manual_review_queue.csv")
    grade_rows = _read_csv(run_dir / "reconcile" / "grade_reconciliation" / "grade_reconciliation.csv")

    # Active questions: named (from reconciliation_report) + unnamed (no question_number in LW)
    active_qns = sorted(
        {r.get("question_number", "").strip() for r in report_rows if r.get("question_number", "").strip()},
        key=lambda x: int(x) if x.isdigit() else 999,
    )
    # Unnamed = questions with no LW question_number: queue (no_config_match) + inferred
    inferred_unnamed = len({
        r.get("description", "")
        for r in report_rows
        if r.get("verifiable", "") == "inferred" and r.get("description", "")
    })
    unnamed_count = len(queue_rows) + inferred_unnamed
    active_count = len(active_qns) + unnamed_count

    # Build cross-referenced question index
    questions_index = _build_question_index(config_rows, ak_rows, report_rows)

    # Write question_index.csv
    _write_question_index(run_dir, questions_index)

    # Flagged rows with student detail — split by flag type
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

    # Parametrization error: student followed teacher's intent but LW rejected it
    doc_override_flags = [
        {
            "question_number": r.get("question_number", ""),
            "question_text": r.get("description", "")[:150],
            "student_name": r.get("username", ""),
            "submitted_answer": r.get("submitted_answer", ""),
            "points": r.get("points", ""),
        }
        for r in report_rows
        if r.get("flag", "").strip() == "answer_correct_per_doc_but_zero"
    ]

    # Wrong answer per gabarito but LW gave full points
    not_accepted_but_full_flags = [
        {
            "question_number": r.get("question_number", "") or "(sem número no LW)",
            "question_text": r.get("description", "")[:150],
            "student_name": r.get("username", ""),
            "submitted_answer": r.get("submitted_answer", ""),
            "points": r.get("points", ""),
            "max_points": r.get("max_points", ""),
            "verifiable": r.get("verifiable", ""),
        }
        for r in report_rows
        if r.get("flag", "").strip() == "answer_not_accepted_but_full"
    ]

    # Over-answered fill-in-blank: student wrote correct answer with extra text; LW rejected
    over_answered_flags = [
        {
            "question_number": r.get("question_number", "") or "(sem número no LW)",
            "question_text": r.get("description", "")[:150],
            "student_name": r.get("username", ""),
            "submitted_answer": r.get("submitted_answer", ""),
            "points": r.get("points", ""),
            "max_points": r.get("max_points", ""),
        }
        for r in report_rows
        if r.get("flag", "").strip() == "fill_in_blank_over_answered"
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

    # Human-readable label — prefer LABEL_DISPLAY from run_meta.cfg (written at extraction
    # time) so the label is always correct even when assessment.cfg has since changed.
    # Fall back to path-slug derivation for older runs without run_meta.cfg.
    _cfg_display = ""
    epoca = "Normal"
    inferred_reviewed = False
    run_meta_path = run_dir / "run_meta.cfg"
    if run_meta_path.exists():
        for _line in run_meta_path.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line.startswith("LABEL_DISPLAY="):
                _cfg_display = _line.partition("=")[2].strip().strip('"').strip("'")
            elif _line.startswith("EPOCA="):
                epoca = _line.partition("=")[2].strip().strip('"').strip("'") or "Normal"
            elif _line.startswith("INFERRED_REVIEWED="):
                inferred_reviewed = _line.partition("=")[2].strip().lower() == "true"
                break
    if _cfg_display:
        label_display = _cfg_display
    else:
        # Derive from path slug ("uc1-mercados-e-economia-financeira" → "UC1 Mercados e Economia Financeira")
        import re as _re
        _words = label.split("-")
        _display_words = []
        for w in _words:
            if _re.match(r"^uc\d+$", w, _re.IGNORECASE):
                _display_words.append(w.upper())
            elif len(w) <= 2 and w.lower() in ("e", "de", "da", "do", "a", "o", "em"):
                _display_words.append(w.lower())
            else:
                _display_words.append(w.capitalize())
        label_display = " ".join(_display_words)

    # Answer key summary stats
    ak_summary = summary.get("answer_key", {})

    # Inferred rows counts
    inferred_rows_count = sum(1 for r in report_rows if r.get("verifiable") == "inferred")
    inferred_low_conf_rows_count = sum(1 for r in report_rows if r.get("verifiable") == "inferred_low_confidence")

    # Inferred questions: orphan questions resolved by LLM from Word doc
    inferred_summary = ak_summary.get("inferred", {})
    inferred_questions_detail = [
        {
            "blockType": r.get("blockType", ""),
            "question_text": r.get("question_text", "")[:200],
            "doc_correct_answer": r.get("doc_correct_answer", ""),
            "confidence": r.get("confidence", ""),
            "notes": r.get("notes", ""),
            "source_doc": r.get("source_doc", ""),
        }
        for r in inferred_rows
    ]
    # Unique inferred questions that were actually reconciled (verifiable="inferred")
    inferred_reconciled_questions: list[dict] = []
    _seen_inferred: set[str] = set()
    for r in report_rows:
        if r.get("verifiable", "") == "inferred":
            desc = r.get("description", "")
            if desc not in _seen_inferred:
                _seen_inferred.add(desc)
                inferred_reconciled_questions.append({
                    "question_text": desc[:150],
                    "blockType": r.get("blockType", ""),
                })

    # Explicit checklist so the LLM never skips a problem type even when flagged_rows is empty
    problems_checklist = {
        "answer_key_real_discrepancies_count": len(ak_real_discrepancies),
        "doc_override_flags_count": len(doc_override_flags),
        "over_answered_flags_count": len(over_answered_flags),
        "answer_accepted_but_zero_count": sum(
            1 for r in flagged if r["flag"] == "answer_accepted_but_zero"
        ),
        "not_accepted_but_full_count": len(not_accepted_but_full_flags),
        "has_any_problem": bool(
            ak_real_discrepancies
            or doc_override_flags
            or over_answered_flags
            or any(r["flag"] == "answer_accepted_but_zero" for r in flagged)
            or not_accepted_but_full_flags
        ),
    }

    return {
        "assessment_type": "Teste de Avaliação",
        "program": program,
        "program_display": program.upper(),
        "label": label,
        "label_display": label_display,
        "epoca": epoca,
        "inferred_reviewed": inferred_reviewed,
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
        # Flags — flagged_rows only carries zero-score anomalies (others have dedicated lists)
        "flagged_rows": [
            r for r in flagged
            if r["flag"] in ("answer_accepted_but_zero", "answer_correct_per_doc_but_zero")
        ],
        "doc_override_flags": doc_override_flags,
        "over_answered_flags": over_answered_flags,
        "not_accepted_but_full_flags": not_accepted_but_full_flags,
        "problems_checklist": problems_checklist,
        "flag_counts": summary.get("flag_counts", {}),
        # Consistency (same answer, different points across students)
        "inconsistent_scoring_questions": summary.get("inconsistent_scoring_questions", 0),
        # Answer key (docente Word → LLM)
        "answer_key_ran": bool(ak_rows),
        "expected_answer_key_count": expected_answer_key_count,
        "answer_key_confidence_breakdown": dict(ak_conf),
        "answer_key_matched_count": ak_matched_count,
        "answer_key_real_discrepancies": ak_real_discrepancies,
        "answer_key_doc_not_found": [
            {**r, "question_text": r["question_text"][:80]} for r in ak_doc_not_found
        ],
        # Inferred answers (fillInTheBlank / match — not in LW export)
        "inferred_ran": bool(inferred_rows),
        "inferred_summary": inferred_summary,
        "inferred_questions_detail": inferred_questions_detail,
        "inferred_reconciled_questions": inferred_reconciled_questions,
        "inferred_rows_count": inferred_rows_count,
        "inferred_low_conf_rows_count": inferred_low_conf_rows_count,
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
        f"Inferidas: {len(context['inferred_questions_detail'])}  |  "
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
