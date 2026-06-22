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
- Para o gabarito ID/Docente (manual_answer_key): confidence high = "correspondência exacta". Nunca usar "alta" para estas perguntas.
- Para perguntas inferidas (inferred_answer_key): usar "confiança alta/média/baixa" conforme o nível. Nunca mencionar o tipo de pergunta (preenchimento de lacunas, correspondência, etc.) — apenas o nível de confiança.
- Perguntas inferidas com confiança exacta ou alta NÃO requerem confirmação humana. Apenas média/baixa requerem.
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
- `inferred_questions_detail`: perguntas cujo gabarito NÃO existe no LW — foi inferido automaticamente
  a partir do gabarito ID / Docente e USADO na reconciliação. Usar `inferred_conf_breakdown` para
  mostrar a distribuição de confiança. Só requerem confirmação humana as de confiança média ou baixa
  (`inferred_conf_breakdown.medium > 0` ou `.low > 0`). As de confiança alta são consideradas validadas.
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

- O gabarito LearnWorlds tem resposta correcta definida para [exam_config_exportable_count] perguntas. O gabarito ID / Docente [se answer_key_ran: tem resposta correcta definida para [answer_key_matched_count + len(inferred_questions_detail)] perguntas — [answer_key_confidence_breakdown.high] com correspondência exacta[, se inferred: [N] inferidas automaticamente (confiança: [breakdown de inferred_conf_breakdown, ex: "alta" se só high, ou "alta: X, média: Y" se misto)][, se answer_key_doc_not_found: [N] não encontradas]][senão: não foi executado].

- Foram validadas [verifiable_count + inferred_rows_count] respostas cruzando com o gabarito ID / Docente[se inferred_rows_count > 0 E (inferred_conf_breakdown.medium > 0 OU inferred_conf_breakdown.low > 0): , das quais [inferred_rows_count] com base em [N] pergunta(s) inferida(s) com confiança média ou baixa — a confirmação humana dessas inferências é necessária antes de qualquer decisão sobre notas][se inferred_rows_count > 0 E só confiança alta: , das quais [inferred_rows_count] com base em [N] pergunta(s) inferida(s) com confiança alta].

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
Cobertura: [expected_answer_key_count] perguntas verificáveis + [len(inferred_questions_detail)] inferidas.
- [answer_key_matched_count] perguntas com correspondência exacta (confiança exacta).
[Se inferred_questions_detail não vazio:] - [N] pergunta(s) inferida(s) automaticamente (confiança: [breakdown de inferred_conf_breakdown])[se inferred_conf_breakdown.medium > 0 ou .low > 0: ; requerem confirmação humana][se só high: ; sem necessidade de confirmação humana].
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

[OBRIGATÓRIO: verifica individualmente cada uma das 5 condições abaixo, usando `problems_checklist`
no contexto como guia. Cada condição é independente — não inferir a partir de outras.

REGRA DE OUTPUT (importante):
- Mostra APENAS as condições cuja lista NÃO está vazia. Omite por completo — sem cabeçalho, sem
  qualquer texto — todas as condições vazias. NUNCA escrevas "Nenhum problema identificado" por
  baixo de uma condição individual. A frase "Nenhum problema identificado." só pode aparecer UMA
  vez, sozinha, e apenas quando AS 5 listas estiverem TODAS vazias.
- Os números 1–5 abaixo são guia interno — NÃO os uses como numeração no relatório. Apresenta cada
  problema encontrado com o seu próprio título em negrito (o título indicado a seguir à seta "→").
- NÃO copies para o relatório as frases de instrução (ex.: "Listar pergunta e alunos (nome + resposta
  + pontos)"). Escreve só o título, uma frase de contexto, e a lista de perguntas/alunos com os dados
  reais. O emoji de urgência (🔴/🟡) vai no fim do título.]

1. Se `answer_key_real_discrepancies` não vazio → "Divergência detectada entre gabaritos —
   possível parametrização errada no LearnWorlds": explicar que o gabarito ID / Docente indica
   uma resposta diferente da que está configurada no LearnWorlds. Isto pode significar que o LW
   tem a opção errada configurada, penalizando respostas correctas. Para cada entrada em
   `answer_key_real_discrepancies`, escrever obrigatoriamente:
   - **Q[question_number]**: "[question_text]"
     - LW: [lw_correct_answer]
     - Docente: [doc_correct_answer]
   Urgência 🔴.

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

## Perguntas inferidas

[Se `inferred_questions_detail` vazio: omitir esta secção inteira.]
Estas perguntas não têm gabarito no LearnWorlds. A resposta correcta foi inferida
automaticamente a partir do gabarito ID / Docente e usada na reconciliação.

[Para cada entrada em `inferred_questions_detail`:]
- **Q[doc_question_number se não vazio, senão "sem número"]**: "[question_text primeiros 150 chars]"
  - **Resposta inferida:** "[doc_correct_answer]"
  - **Confiança:** [confidence]
  [Se confidence != "high":] - **Acção:** Confirmar que a resposta inferida corresponde à intenção do docente antes de qualquer decisão sobre notas.

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

> **Contexto:** esta auditoria é pós-exame. As acções correctivas consistem em corrigir manualmente
> as notas dos participantes nos sistemas da instituição — não em reconfigurar o LearnWorlds.
> Para cada pergunta com problema, listar os alunos afectados e a correcção necessária.

| Prioridade | Pergunta | Acção |
|------------|----------|-------|
[Gerar linhas APENAS para as situações que existem nos dados. Uma linha por pergunta afectada. Seguir esta lógica de prioridade:]
[🔴 Se answer_key_real_discrepancies:] [Uma linha por pergunta em answer_key_real_discrepancies:] | 🔴 | Q[número]: [enunciado curto] | Divergência de gabarito confirmada — verificar manualmente e corrigir a nota dos alunos afectados se a resposta do gabarito ID / Docente for a correcta |
[🔴 Se doc_override_flags:] [Uma linha por pergunta em doc_override_flags:] | 🔴 | Q[número]: [enunciado curto] | Corrigir manualmente a nota dos alunos que responderam conforme o gabarito ID / Docente mas receberam 0 pontos |
[🔴 Se over_answered_flags:] [Uma linha por pergunta em over_answered_flags:] | 🔴 | Q[número ou "sem número"]: [enunciado curto] | Corrigir manualmente a nota dos alunos com resposta correcta rejeitada por texto adicional: [listar alunos e respostas] |
[🔴 Se flagged_rows com zero-score:] [Uma linha por pergunta única em flagged_rows:] | 🔴 | Q[número]: [enunciado curto] | Corrigir manualmente a nota dos alunos com resposta correcta mas 0 pontos atribuídos: [listar alunos] |
[🟡 Se not_accepted_but_full_flags:] [Uma linha por pergunta:] | 🟡 | Q[número]: [enunciado curto] | Verificar se a resposta com pontuação completa mas não reconhecida pelo gabarito é válida; se sim, corrigir notas dos restantes alunos |
[🔵 Se inferred com confiança não-alta (inferred_conf_breakdown.medium ou .low > 0):] | 🔵 | (inferida) | Confirmar manualmente as [N] perguntas inferidas com confiança média ou baixa antes de validar notas |
[🔵 Se answer_key_doc_not_found:] | 🔵 | — | Verificar as [N] perguntas presentes no gabarito LearnWorlds mas não encontradas no gabarito ID / Docente |

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


def _qkey(s: str) -> str:
    """Text join key (mirrors reconcile's join_key) for cross-source matching."""
    import re, unicodedata
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return re.sub(r"[^a-z0-9]", "", s)


def _build_question_index(
    config_rows: list[dict],
    ak_rows: list[dict],
    inferred_rows: list[dict],
    report_rows: list[dict],
) -> dict[str, dict]:
    """Build a per-question cross-reference from all data sources.

    Covers the FULL exam — verifiable questions (from exam_config) AND inferred
    questions (fill-in-blank / match, from inferred_answer_key) — in one table,
    so the index spans the whole 1..N live sequence with no gaps.

    Sources are joined by question TEXT, not by question_number: the
    reconciliation_report numbers questions by live exam order (1..N, counting
    inferred types too), while exam_config / manual_answer_key carry the
    answer-key-export position (which omits inferred questions). Joining by
    number would misalign them. The canonical live-order number from the report
    is used for display so the index matches the reconciliation report.
    """
    # Canonical live-order question_number per question text, from the report.
    canon_qn: dict[str, str] = {}
    for r in report_rows:
        k = _qkey(r.get("description", ""))
        if k and k not in canon_qn:
            canon_qn[k] = r.get("question_number", "").strip()

    questions: dict[str, dict] = {}

    # Seed from exam_config (authoritative for question text and LW answer)
    for r in config_rows:
        k = _qkey(r.get("description", ""))
        if not k:
            continue
        questions[k] = {
            "question_number": canon_qn.get(k, r.get("question_number", "").strip()),
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

    # Seed inferred questions (not in LW export — fill-in-blank / match). They have
    # no LW answer key; their answer comes only from the Word doc. Including them
    # here fills the gaps the exam_config leaves (e.g. live positions 5, 12, 15).
    for r in inferred_rows:
        k = _qkey(r.get("question_text", ""))
        if not k or k in questions:
            continue
        questions[k] = {
            "question_number": canon_qn.get(k, ""),
            "blockType": r.get("blockType", ""),
            "question_text": r.get("question_text", "")[:200],
            "lw_correct_answer": "",  # not configured in LW (inferred type)
            "doc_correct_answer": r.get("doc_correct_answer", ""),
            "answers_match": "inferida",
            "doc_confidence": r.get("confidence", ""),
            "needs_review": "",
            "flag": "",
            "n_flagged_students": 0,
            "flagged_student_names": "",
        }

    # Enrich from answer_key (docente cross-check), matched by question text
    for r in ak_rows:
        k = _qkey(r.get("lw_question_text", "") or r.get("question_text", ""))
        if k in questions:
            questions[k]["doc_correct_answer"] = r.get("doc_correct_answer", "")
            questions[k]["answers_match"] = r.get("answers_match", "")
            questions[k]["doc_confidence"] = r.get("confidence", "")
            questions[k]["needs_review"] = r.get("needs_review", "")

    # Enrich from reconciliation_report (flags + affected students), by text
    flag_data: dict[str, dict] = {}
    for r in report_rows:
        k = _qkey(r.get("description", ""))
        flag = r.get("flag", "").strip()
        if not k or not flag:
            continue
        if k not in flag_data:
            flag_data[k] = {"flag": flag, "students": []}
        name = r.get("username", r.get("email", ""))
        answer = r.get("submitted_answer", "")
        points = r.get("points", "")
        flag_data[k]["students"].append(f"{name} (resp: {answer!r}, pts: {points})")

    for k, data in flag_data.items():
        if k in questions:
            questions[k]["flag"] = data["flag"]
            questions[k]["n_flagged_students"] = len(data["students"])
            questions[k]["flagged_student_names"] = "; ".join(data["students"])

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

    # Canonical live-order question number per question text (from the report — the
    # only source covering verifiable AND inferred questions in true exam order).
    # Every LLM-facing field is numbered through this map so a question never shows
    # two different numbers (the XLSX answer-key position and the Word-doc number are
    # provenance-only and would otherwise leak into the report and confuse the model).
    canon_qn: dict[str, str] = {}
    for r in report_rows:
        k = _qkey(r.get("description", ""))
        if k and k not in canon_qn:
            canon_qn[k] = r.get("question_number", "").strip()

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
    questions_index = _build_question_index(config_rows, ak_rows, inferred_rows, report_rows)

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
        _ak_qn = canon_qn.get(
            _qkey(r.get("lw_question_text", "") or r.get("question_text", "")), ""
        )
        # Identify the question by its LW text — that's what the canonical number
        # refers to. The doc question_text can be a false extraction match (different
        # question with overlapping words), so showing it alongside the LW number
        # would point the reader at two different questions.
        _ak_text = r.get("lw_question_text", "") or r.get("question_text", "")
        if r.get("confidence", "") == "unmatched":
            ak_doc_not_found.append({
                "question_number": _ak_qn,
                "question_text": _ak_text[:120],
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
                "question_number": _ak_qn,
                "question_text": _ak_text[:200],
                "lw_correct_answer": lw_ans[:80],
                "doc_correct_answer": doc_ans[:80],
                "answers_match": r.get("answers_match", ""),
                "confidence": r.get("confidence", ""),
                "notes": r.get("notes", "")[:80],
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
            "question_number": canon_qn.get(_qkey(r.get("question_text", "")), ""),
            "doc_question_number": r.get("doc_question_number", ""),
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
                    "question_number": r.get("question_number", ""),
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
        "inferred_conf_breakdown": dict(Counter(r.get("confidence", "") for r in inferred_rows)),
    }


def _call_openai(api_key: str, model: str, context: dict) -> str:
    try:
        from openai import OpenAI  # type: ignore
    except ImportError:
        raise ImportError("openai package not installed — run: pip install openai>=1.0")

    client = OpenAI(api_key=api_key)

    user_msg = (
        "Dados da corrida de auditoria (JSON):\n\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
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
